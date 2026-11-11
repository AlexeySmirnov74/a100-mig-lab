"""A small, text-only context recovery proxy for the three lab model tiers."""

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.background import BackgroundTask

LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://litellm:4000").rstrip("/")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "dummy-local-key")


@asynccontextmanager
async def lifespan(application: FastAPI):
    # Reuse connections across requests; the read timeout permits slow generation.
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=10.0, pool=10.0),
        limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
    ) as client:
        application.state.http_client = client
        yield


app = FastAPI(title="context-guard", version="0.2.0", lifespan=lifespan)

REQUESTS_TOTAL = Counter(
    "llm_guard_requests_total", "Total requests handled by context-guard", ["model", "status"]
)
REQUEST_DURATION = Histogram(
    "llm_guard_request_duration_seconds",
    "End-to-end request latency seen by context-guard",
    ["model", "status"],
    buckets=(0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1, 1.5, 2, 3, 5, 8, 13, 21, 34),
)
CONTEXT_RESETS = Counter(
    "llm_guard_context_resets_total",
    "How many times context-guard retried with a reduced context",
    ["model", "reason"],
)
FALLBACK_FAILURES = Counter(
    "llm_guard_fallback_failures_total", "How many fallback retries still failed", ["model", "reason"]
)
INFLIGHT = Gauge(
    "llm_guard_inflight_requests", "Current in-flight requests inside context-guard", ["model"]
)

# Keep these in sync with the TGI input/total limits in compose/docker-compose.yml.
# Byte counts intentionally overestimate text tokens for the lab's Qwen byte-level
# BPE tokenizers. This avoids loading a tokenizer/model in this lightweight proxy.
# It is a conservative recovery heuristic, not an exact tokenizer or a guarantee
# for arbitrary replacement models/chat templates.
MODEL_POLICY = {
    "general-chat": {"input_tokens": 1536, "total_tokens": 2048, "max_tokens": 256},
    "fast-chat": {"input_tokens": 768, "total_tokens": 1024, "max_tokens": 128},
    "tiny-chat": {"input_tokens": 384, "total_tokens": 512, "max_tokens": 32},
}
TEMPLATE_TOKEN_RESERVE = 64
RESET_PROMPT = "Answer the latest user request. Earlier conversation was removed."
FORWARDED_RESPONSE_HEADERS = ("content-type", "retry-after", "x-request-id", "x-litellm-call-id")


def _auth_headers(incoming_headers: dict[str, str]) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {LITELLM_API_KEY}", "Content-Type": "application/json"}
    if "accept" in incoming_headers:
        headers["Accept"] = incoming_headers["accept"]
    return headers


def _response_headers(response: httpx.Response) -> dict[str, str]:
    return {key: response.headers[key] for key in FORWARDED_RESPONSE_HEADERS if key in response.headers}


def _error(status_code: int, message: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": code, "code": code}},
    )


def _transport_error(exc: httpx.RequestError) -> JSONResponse:
    if isinstance(exc, httpx.TimeoutException):
        return _error(504, "The upstream model service timed out.", "upstream_timeout")
    return _error(502, "The upstream model service is unavailable.", "upstream_unavailable")


def _is_context_error(status_code: int, body_text: str) -> bool:
    # Authentication, routing, and generic input validation errors must not trigger
    # a lossy retry. LiteLLM/TGI may wrap a genuine overflow in HTTP 500.
    if status_code not in (400, 413, 422, 500):
        return False
    text = body_text.lower()
    return any(
        pattern in text
        for pattern in (
            "contextwindowexceedederror",
            "context_length_exceeded",
            "maximum context length",
            "context window exceeded",
            "inputs must have less than",
            "inputs tokens + `max_new_tokens` must be <=",
            "inputs tokens + max_new_tokens must be <=",
        )
    )


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            item["text"]
            for item in content
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
        ).strip()
    return ""


def _truncate_utf8(text: str, byte_limit: int) -> str:
    # Ignore only an incomplete trailing UTF-8 code point, never split a character.
    return text.encode("utf-8")[:max(0, byte_limit)].decode("utf-8", errors="ignore")


def _can_reset(payload: dict[str, Any]) -> bool:
    # Tool definitions, tool results and multimodal input require different token
    # accounting and must not be silently discarded by this text-only lab proxy.
    if payload["model"] not in MODEL_POLICY:
        return False
    if payload.get("tools") or payload.get("functions") or payload.get("response_format"):
        return False
    for message in payload["messages"]:
        if message.get("role") in ("tool", "function") or message.get("tool_calls") or message.get("function_call"):
            return False
        content = message.get("content")
        if isinstance(content, list) and any(
            not isinstance(item, dict) or item.get("type") != "text" for item in content
        ):
            return False
    return any(message.get("role") == "user" for message in payload["messages"])


def _build_fallback_payload(payload: dict[str, Any]) -> dict[str, Any]:
    policy = MODEL_POLICY[payload["model"]]
    requested = [policy["max_tokens"]]
    requested.extend(payload[key] for key in ("max_tokens", "max_completion_tokens") if key in payload)
    output_tokens = min(requested)
    budget = min(policy["input_tokens"], policy["total_tokens"] - output_tokens) - TEMPLATE_TOKEN_RESERVE

    instructions = "\n".join(
        _text_content(message.get("content"))
        for message in payload["messages"]
        if message.get("role") in ("system", "developer")
    ) or RESET_PROMPT
    # Reserve at least half of the remaining budget for the latest user request.
    instructions = _truncate_utf8(instructions, budget // 2)
    last_user = next(message for message in reversed(payload["messages"]) if message.get("role") == "user")
    last_content = _truncate_utf8(_text_content(last_user.get("content")), budget - len(instructions.encode("utf-8")))
    reduced = dict(payload)
    reduced["messages"] = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": last_content},
    ]
    # Preserve whichever output-limit spelling the caller used, without sending
    # conflicting limits or increasing the caller's requested generation length.
    output_key = "max_completion_tokens" if "max_completion_tokens" in payload else "max_tokens"
    reduced.pop("max_tokens", None)
    reduced.pop("max_completion_tokens", None)
    reduced[output_key] = output_tokens
    return reduced


def _notice(model: str) -> str:
    return f"[Context reset for {model}: earlier messages were removed and long input may be truncated.]\n\n"


def _prepend_notice_to_json_response(data: dict[str, Any], model: str) -> dict[str, Any]:
    for choice in data.get("choices", []):
        message = choice.get("message", {})
        content = message.get("content")
        if isinstance(content, str) or content is None:
            message["content"] = _notice(model) + (content or "")
    return data


class _RequestMetrics:
    def __init__(self, model: str):
        # Unknown model names share a label to avoid unbounded metric cardinality.
        self.model = model if model in MODEL_POLICY else "other"
        self.started = time.perf_counter()
        self.done = False
        INFLIGHT.labels(model=self.model).inc()

    def finish(self, status: str) -> None:
        if not self.done:
            self.done = True
            REQUESTS_TOTAL.labels(model=self.model, status=status).inc()
            REQUEST_DURATION.labels(model=self.model, status=status).observe(time.perf_counter() - self.started)
            INFLIGHT.labels(model=self.model).dec()


async def _close_stream(response: httpx.Response, tracker: _RequestMetrics) -> None:
    with anyio.CancelScope(shield=True):
        try:
            await response.aclose()
        finally:
            tracker.finish("cancelled")


class _ProxyStreamingResponse(StreamingResponse):
    """Close a suspended upstream iterator even if sending to the client fails."""

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Starlette can be cancelled while sending a yielded chunk, outside
            # the generator. Explicit closure avoids waiting for garbage collection.
            with anyio.CancelScope(shield=True):
                try:
                    await self.body_iterator.aclose()
                finally:
                    await self.background()


async def _stream_body(response: httpx.Response, model: str, reset: bool, tracker: _RequestMetrics):
    status = "ok_reset" if reset else "ok"
    try:
        if not reset:
            async for chunk in response.aiter_bytes():
                yield chunk
        else:
            # Keep native chat.completion.chunk objects, IDs and usage events.
            # Only prefix the first text delta of each choice with the reset notice.
            notified: set[int] = set()
            async for line in response.aiter_lines():
                if line.startswith("data:") and line[5:].strip() != "[DONE]":
                    try:
                        data = json.loads(line[5:])
                        for choice in data.get("choices", []):
                            index = choice.get("index", 0)
                            delta = choice.get("delta")
                            if index not in notified and isinstance(delta, dict):
                                content = delta.get("content")
                                if content is None or isinstance(content, str):
                                    delta["content"] = _notice(model) + (content or "")
                                    notified.add(index)
                        line = f"data: {json.dumps(data, ensure_ascii=False)}"
                    except (ValueError, TypeError, AttributeError):
                        pass  # Preserve upstream comments/non-JSON events unchanged.
                yield (line + "\n").encode("utf-8")
    except httpx.RequestError as exc:
        if reset:
            FALLBACK_FAILURES.labels(model=tracker.model, reason="transport_error").inc()
        error = _transport_error(exc)
        status = f"http_{error.status_code}"
        # HTTP headers have already been sent; SSE clients receive a terminal error.
        yield b"data: " + error.body + b"\n\ndata: [DONE]\n\n"
    except asyncio.CancelledError:
        status = "cancelled"
        raise
    except GeneratorExit:
        status = "cancelled"
        raise
    finally:
        try:
            await response.aclose()
        finally:
            tracker.finish(status)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})


@app.get("/v1/models")
async def models(request: Request):
    try:
        response = await request.app.state.http_client.get(
            f"{LITELLM_BASE_URL}/v1/models", headers=_auth_headers(dict(request.headers))
        )
    except httpx.RequestError as exc:
        return _transport_error(exc)
    return Response(content=response.content, status_code=response.status_code, headers=_response_headers(response))


def _validate_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "The request body must be a JSON object."
    if not isinstance(payload.get("model"), str) or not payload["model"].strip():
        return "model must be a non-empty string."
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages or any(not isinstance(message, dict) for message in messages):
        return "messages must be a non-empty array of message objects."
    if "stream" in payload and not isinstance(payload["stream"], bool):
        return "stream must be a boolean."
    for key in ("max_tokens", "max_completion_tokens"):
        if key in payload and (type(payload[key]) is not int or payload[key] <= 0):
            return f"{key} must be a positive integer."
    return None


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        return _error(400, "The request body must be valid JSON.", "invalid_request_error")
    problem = _validate_payload(payload)
    if problem:
        return _error(400, problem, "invalid_request_error")

    model = payload["model"]
    tracker = _RequestMetrics(model)
    status = "http_502"
    streaming = False
    response = None
    reset = False
    client = request.app.state.http_client
    try:
        for attempt in range(2):
            try:
                upstream_request = client.build_request(
                    "POST", f"{LITELLM_BASE_URL}/v1/chat/completions",
                    headers=_auth_headers(dict(request.headers)), json=payload,
                )
            except (TypeError, ValueError):
                status = "http_400"
                return _error(400, "The request body must contain valid JSON values.", "invalid_request_error")
            response = await client.send(upstream_request, stream=True)
            if response.is_success and payload.get("stream", False):
                headers = _response_headers(response)
                headers.update({"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
                streaming_response = _ProxyStreamingResponse(
                    _stream_body(response, model, reset, tracker),
                    status_code=response.status_code,
                    media_type="text/event-stream", headers=headers,
                    background=BackgroundTask(_close_stream, response, tracker),
                )
                streaming = True
                return streaming_response

            await response.aread()
            await response.aclose()
            if response.is_success:
                status = "ok_reset" if reset else "ok"
                if reset:
                    try:
                        data = response.json()
                        if not isinstance(data, dict) or not isinstance(data.get("choices"), list):
                            raise ValueError("Invalid completion response")
                        data = _prepend_notice_to_json_response(data, model)
                    except (ValueError, TypeError, AttributeError):
                        FALLBACK_FAILURES.labels(model=tracker.model, reason="invalid_response").inc()
                        status = "http_502"
                        return _error(502, "The upstream model service returned invalid JSON.", "upstream_invalid_response")
                    return JSONResponse(content=data, status_code=response.status_code)
                return Response(content=response.content, status_code=response.status_code, headers=_response_headers(response))

            if attempt == 0 and _is_context_error(response.status_code, response.text) and _can_reset(payload):
                CONTEXT_RESETS.labels(model=tracker.model, reason="context_overflow").inc()
                payload = _build_fallback_payload(payload)
                reset = True
                continue
            if reset:
                FALLBACK_FAILURES.labels(model=tracker.model, reason="retry_failed").inc()
            status = f"http_{response.status_code}"
            return Response(content=response.content, status_code=response.status_code, headers=_response_headers(response))
    except httpx.RequestError as exc:
        if reset:
            FALLBACK_FAILURES.labels(model=tracker.model, reason="transport_error").inc()
        error = _transport_error(exc)
        status = f"http_{error.status_code}"
        return error
    except asyncio.CancelledError:
        status = "cancelled"
        raise
    finally:
        if not streaming:
            try:
                if response is not None:
                    await response.aclose()
            finally:
                tracker.finish(status)
