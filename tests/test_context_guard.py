"""Offline regression tests: HTTPX mocks replace LiteLLM and all GPU services."""

import asyncio
import json
import unittest

import httpx
from starlette.requests import ClientDisconnect, Request

from services.context_guard import app as guard


def completion(content="Hello"):
    return {
        "id": "chatcmpl-test", "object": "chat.completion", "model": "tiny-chat",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
    }


def chunk(delta, finish_reason=None):
    return {
        "id": "chatcmpl-test", "object": "chat.completion.chunk", "model": "tiny-chat", "created": 1,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def sse(data):
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self, chunks, error=None, block=False):
        self.chunks = chunks
        self.error = error
        self.block = block
        self.yielded = 0
        self.closed = False

    async def __aiter__(self):
        for item in self.chunks:
            self.yielded += 1
            yield item
        if self.block:
            await asyncio.Event().wait()
        if self.error:
            raise self.error

    async def aclose(self):
        self.closed = True


class ContextGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sent = []
        self.responses = []

        def handler(request):
            self.sent.append(request)
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        guard.app.state.http_client = self.upstream
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=guard.app), base_url="http://guard")
        self.payload = {"model": "tiny-chat", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 16}

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.upstream.aclose()
        self.assertEqual(guard.INFLIGHT.labels(model="tiny-chat")._value.get(), 0)

    async def post(self, payload=None):
        return await self.client.post("/v1/chat/completions", json=self.payload if payload is None else payload)

    async def direct_stream(self, payload):
        body = json.dumps(payload).encode("utf-8")

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request({"type": "http", "method": "POST", "path": "/v1/chat/completions", "headers": [], "app": guard.app}, receive)
        return await guard.chat_completions(request)

    def overflow(self):
        return httpx.Response(400, json={"error": {"code": "context_length_exceeded"}})

    async def test_success_preserves_payload_body_and_upstream_request_id(self):
        self.responses.append(httpx.Response(200, json=completion(), headers={"x-request-id": "trace-1"}))
        response = await self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), completion())
        self.assertEqual(response.headers["x-request-id"], "trace-1")
        self.assertEqual(json.loads(self.sent[0].content), self.payload)
        self.assertEqual(self.sent[0].headers["authorization"], f"Bearer {guard.LITELLM_API_KEY}")

    async def test_invalid_payloads_return_400_without_upstream_calls(self):
        bad_payloads = [[], {}, {"model": {}, "messages": []}, {**self.payload, "messages": [None]},
                        {**self.payload, "stream": "true"}, {**self.payload, "max_tokens": 0},
                        {**self.payload, "max_completion_tokens": True}]
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                response = await self.post(payload)
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.json())
        response = await self.client.post("/v1/chat/completions", content="{broken")
        self.assertEqual(response.status_code, 400)
        response = await self.client.post("/v1/chat/completions", content=json.dumps({**self.payload, "temperature": float("nan")}))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.sent)

    async def test_non_context_errors_are_forwarded_without_retry(self):
        for status, message in [(401, "context_length_exceeded"), (404, "context_length_exceeded"),
                                (422, "Input validation error: temperature must be <= 2"),
                                (429, "Too many requests")]:
            with self.subTest(status=status):
                self.responses.append(httpx.Response(status, text=message, headers={"retry-after": "10"}))
                before = len(self.sent)
                response = await self.post()
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.text, message)
                self.assertEqual(response.headers["retry-after"], "10")
                self.assertEqual(len(self.sent), before + 1)

    async def test_one_context_retry_preserves_instructions_and_output_limit(self):
        self.responses.extend([self.overflow(), httpx.Response(200, json=completion("Recovered"))])
        payload = {**self.payload, "messages": [
            {"role": "system", "content": "Answer in Russian."},
            {"role": "user", "content": "Old question"},
            {"role": "assistant", "content": "Old answer"},
            {"role": "user", "content": "Последний вопрос " * 1000},
        ]}
        response = await self.post(payload)
        self.assertEqual(response.status_code, 200)
        content = response.json()["choices"][0]["message"]["content"]
        self.assertIn("Context reset", content)
        self.assertTrue(content.endswith("Recovered"))
        reduced = json.loads(self.sent[1].content)
        self.assertEqual(reduced["max_tokens"], 16)
        self.assertEqual(reduced["messages"][0]["content"], "Answer in Russian.")
        self.assertTrue(reduced["messages"][1]["content"].startswith("Последний вопрос"))
        self.assertNotIn("Old question", json.dumps(reduced))
        self.assertEqual(len(payload["messages"]), 4)
        text_bytes = sum(len(message["content"].encode("utf-8")) for message in reduced["messages"])
        self.assertLessEqual(text_bytes + guard.TEMPLATE_TOKEN_RESERVE, guard.MODEL_POLICY["tiny-chat"]["input_tokens"])

    async def test_repeated_overflow_stops_after_one_retry(self):
        self.responses.extend([self.overflow(), self.overflow()])
        response = await self.post()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.sent), 2)

    async def test_complex_requests_do_not_discard_tools_images_or_schemas(self):
        payloads = [
            {**self.payload, "model": "custom-model-with-unknown-tokenizer"},
            {**self.payload, "tools": [{"type": "function", "function": {"name": "search"}}]},
            {**self.payload, "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "example"}}]}]},
            {**self.payload, "response_format": {"type": "json_object"}},
            {**self.payload, "messages": [{"role": "assistant", "content": "No user message"}]},
        ]
        for payload in payloads:
            self.responses.append(self.overflow())
            before = len(self.sent)
            response = await self.post(payload)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(len(self.sent), before + 1)

    async def test_fallback_invalid_json_returns_502(self):
        self.responses.extend([self.overflow(), httpx.Response(200, text="bad upstream json")])
        response = await self.post()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "upstream_invalid_response")

    async def test_transport_failures_return_safe_502_or_504(self):
        for exc, expected in [(httpx.ConnectError("secret upstream address"), 502),
                              (httpx.ReadTimeout("secret upstream address"), 504)]:
            self.responses.append(exc)
            response = await self.post()
            self.assertEqual(response.status_code, expected)
            self.assertNotIn("secret", response.text)

    async def test_models_uses_shared_client_and_handles_timeout(self):
        self.responses.append(httpx.Response(200, json={"data": [{"id": "tiny-chat"}]}))
        response = await self.client.get("/v1/models")
        self.assertEqual(response.json()["data"][0]["id"], "tiny-chat")
        self.assertTrue(str(self.sent[0].url).endswith("/v1/models"))
        self.responses.append(httpx.ConnectTimeout("offline"))
        response = await self.client.get("/v1/models")
        self.assertEqual(response.status_code, 504)

    async def test_stream_is_lazy_and_metrics_finish_after_body(self):
        pieces = [sse(chunk({"content": "Hello"})), sse(chunk({}, "stop")), b"data: [DONE]\n\n"]
        stream = TrackedStream(pieces)
        self.responses.append(httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"}))
        response = await self.direct_stream({**self.payload, "stream": True})
        self.assertEqual(stream.yielded, 0)
        self.assertEqual(guard.INFLIGHT.labels(model="tiny-chat")._value.get(), 1)
        first = await anext(response.body_iterator)
        self.assertEqual(first, pieces[0])
        self.assertEqual(stream.yielded, 1)
        rest = [part async for part in response.body_iterator]
        self.assertEqual(b"".join([first, *rest]), b"".join(pieces))
        self.assertTrue(stream.closed)
        self.assertEqual(guard.INFLIGHT.labels(model="tiny-chat")._value.get(), 0)
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        await response.background()

    async def test_fallback_stream_keeps_native_deltas_usage_and_done(self):
        usage = {"id": "chatcmpl-test", "object": "chat.completion.chunk", "choices": [], "usage": {"total_tokens": 12}}
        body = sse(chunk({"role": "assistant"})) + sse(chunk({"content": "Привет"})) + sse(chunk({}, "stop")) + sse(usage) + b"data: [DONE]\n\n"
        # Split inside arbitrary UTF-8/event boundaries, as a real network can.
        stream = TrackedStream([body[:200], body[200:333], body[333:]])
        self.responses.extend([self.overflow(), httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})])
        payload = {**self.payload, "stream": True, "stream_options": {"include_usage": True}}
        response = await self.direct_stream(payload)
        result = b"".join([part async for part in response.body_iterator]).decode("utf-8")
        events = [json.loads(line[6:]) for line in result.splitlines() if line.startswith("data: ") and "[DONE]" not in line]
        self.assertTrue(all(event["object"] == "chat.completion.chunk" for event in events))
        self.assertTrue(all(event["id"] == "chatcmpl-test" for event in events))
        self.assertEqual(events[-1], usage)
        self.assertEqual(events[2]["choices"][0]["finish_reason"], "stop")
        self.assertEqual(result.count("Context reset"), 1)
        self.assertIn("Привет", result)
        self.assertTrue(result.endswith("data: [DONE]\n\n"))
        reduced = json.loads(self.sent[1].content)
        self.assertIs(reduced["stream"], True)
        self.assertEqual(reduced["stream_options"], {"include_usage": True})
        self.assertTrue(stream.closed)

    async def test_midstream_timeout_emits_terminal_sse_error_and_closes_upstream(self):
        stream = TrackedStream([sse(chunk({"content": "Partial"}))], error=httpx.ReadTimeout("secret"))
        self.responses.append(httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"}))
        response = await self.direct_stream({**self.payload, "stream": True})
        result = b"".join([part async for part in response.body_iterator]).decode("utf-8")
        self.assertIn('"code":"upstream_timeout"', result)
        self.assertNotIn("secret", result)
        self.assertTrue(result.endswith("data: [DONE]\n\n"))
        self.assertTrue(stream.closed)

    async def test_cancelled_stream_closes_upstream_and_releases_inflight(self):
        stream = TrackedStream([sse(chunk({"content": "Partial"}))], block=True)
        self.responses.append(httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"}))
        response = await self.direct_stream({**self.payload, "stream": True})
        await anext(response.body_iterator)
        task = asyncio.create_task(anext(response.body_iterator))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stream.closed)
        self.assertEqual(guard.INFLIGHT.labels(model="tiny-chat")._value.get(), 0)

    async def test_downstream_disconnect_closes_suspended_stream_immediately(self):
        stream = TrackedStream([sse(chunk({"content": "Partial"}))], block=True)
        self.responses.append(httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"}))
        response = await self.direct_stream({**self.payload, "stream": True})

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                raise OSError("client disconnected")

        with self.assertRaises(ClientDisconnect):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        self.assertTrue(stream.closed)
        self.assertEqual(guard.INFLIGHT.labels(model="tiny-chat")._value.get(), 0)

    async def test_health_and_metrics_are_available_offline(self):
        response = await self.client.get("/health")
        self.assertEqual(response.json(), {"status": "ok"})
        response = await self.client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("llm_guard_inflight_requests", response.text)
        self.assertEqual(response.headers["content-type"].count("charset="), 1)
        self.assertFalse(self.sent)


class PolicyTests(unittest.TestCase):
    def test_multilingual_budget_and_both_output_parameter_spellings(self):
        for model, policy in guard.MODEL_POLICY.items():
            for output_key in ("max_tokens", "max_completion_tokens"):
                payload = {"model": model, "messages": [{"role": "user", "content": "🧪中文Привет " * 2000}], output_key: 4, "stream": True}
                reduced = guard._build_fallback_payload(payload)
                self.assertEqual(reduced[output_key], 4)
                self.assertNotIn("max_tokens" if output_key == "max_completion_tokens" else "max_completion_tokens", reduced)
                used = sum(len(message["content"].encode("utf-8")) for message in reduced["messages"]) + guard.TEMPLATE_TOKEN_RESERVE
                self.assertLessEqual(used, policy["input_tokens"])
                self.assertLessEqual(used + reduced[output_key], policy["total_tokens"])
                self.assertNotIn("�", reduced["messages"][1]["content"])
                self.assertIs(reduced["stream"], True)

    def test_recognizes_tgi_total_token_overflow(self):
        self.assertTrue(guard._is_context_error(422, "inputs tokens + `max_new_tokens` must be <= 512"))
        self.assertTrue(guard._is_context_error(500, "ContextWindowExceededError"))
        self.assertFalse(guard._is_context_error(422, "input validation error"))


if __name__ == "__main__":
    unittest.main()
