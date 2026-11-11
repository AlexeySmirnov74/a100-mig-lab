"""Closed-loop concurrency experiment; latency is measured only for successful calls."""
import asyncio
import math
import os
import random
import time
from collections import Counter, deque

import httpx

API_BASE = os.getenv("LOAD_API_BASE", "http://context-guard:4010/v1").rstrip("/")
API_KEY = os.getenv("LOAD_API_KEY", "dummy-local-key")
USERS = int(os.getenv("LOAD_USERS", "10"))
DURATION_SEC = float(os.getenv("LOAD_DURATION_SEC", "120"))
THINK_TIME_SEC = float(os.getenv("LOAD_THINK_TIME_SEC", "0.5"))
TIMEOUT_SEC = float(os.getenv("LOAD_TIMEOUT_SEC", "120"))
MODEL_MODE = os.getenv("LOAD_MODEL_MODE", "all").lower()
MAX_TOKENS = int(os.getenv("LOAD_MAX_TOKENS", "64"))
LATENCY_WINDOW = 100_000

PROMPTS = [
    "Explain NVIDIA MIG in two sentences.",
    "List three benefits of GPU memory isolation.",
    "Explain the difference between throughput and latency.",
    "What does p95 latency measure?",
    "What should an LLMOps dashboard show during a load increase?",
    "Explain what happens when one MIG slice runs out of memory.",
    "How would you check that other MIG slices remain healthy after an OOM?",
    "Why are Prometheus and Grafana useful for LLM inference?",
]


def validate_config() -> None:
    for name, value in (("LOAD_USERS", USERS), ("LOAD_DURATION_SEC", DURATION_SEC),
                        ("LOAD_TIMEOUT_SEC", TIMEOUT_SEC), ("LOAD_MAX_TOKENS", MAX_TOKENS)):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be positive and finite")
    if not math.isfinite(THINK_TIME_SEC) or THINK_TIME_SEC < 0:
        raise ValueError("LOAD_THINK_TIME_SEC must be nonnegative and finite")
    if MODEL_MODE not in {"all", "general", "fast", "tiny"}:
        raise ValueError("LOAD_MODEL_MODE must be all, general, fast, or tiny")


def choose_model(counter: int) -> str:
    if MODEL_MODE != "all":
        return f"{MODEL_MODE}-chat"
    return ("general-chat", "fast-chat", "tiny-chat")[counter % 3]


def quantiles(values) -> tuple[float, float, float]:
    ordered = sorted(values)
    if not ordered:
        return 0.0, 0.0, 0.0
    return tuple(ordered[max(0, math.ceil(p * len(ordered)) - 1)] for p in (0.5, 0.95, 0.99))


async def user_loop(client: httpx.AsyncClient, user_id: int, stop_at: float, stats: dict) -> None:
    req_num = 0
    while (remaining := stop_at - time.monotonic()) > 0:
        model = choose_model(req_num + user_id)
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": random.choice(PROMPTS)}],
            "max_tokens": MAX_TOKENS,
            "temperature": 0.2,
            "stream": False,
        }
        started = time.monotonic()
        status = "ok"
        try:
            response = await client.post(
                f"{API_BASE}/chat/completions", json=payload,
                timeout=TIMEOUT_SEC,
            )
            if response.status_code != 200:
                status = f"http_{response.status_code}"
            else:
                text = response.json()["choices"][0]["message"]["content"]
                if not isinstance(text, str) or not text.strip():
                    status = "invalid_completion"
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            status = f"exc_{type(exc).__name__}"
        latency = time.monotonic() - started
        # No await in this update: asyncio tasks cannot interleave these writes.
        if status == "ok":
            stats["latencies"].append(latency)
            stats["latency_sum"] += latency
        stats["status_counts"][status] += 1
        stats["model_counts"][model] += 1
        stats["requests_total"] += 1
        req_num += 1
        remaining = stop_at - time.monotonic()
        if THINK_TIME_SEC > 0 and remaining > 0:
            await asyncio.sleep(min(THINK_TIME_SEC, remaining))


async def periodic_report(started_at: float, stats: dict, done: asyncio.Event) -> None:
    last_total, last_time = 0, started_at
    while not done.is_set():
        try:
            await asyncio.wait_for(done.wait(), timeout=5)
            return
        except asyncio.TimeoutError:
            pass
        total = stats["requests_total"]
        now = time.monotonic()
        p50, p95, p99 = quantiles(stats["latencies"])
        print(
            f"[load] total={total} rps_now={(total-last_total)/max(now-last_time, 0.001):.2f} "
            f"success_p50={p50:.3f}s success_p95={p95:.3f}s success_p99={p99:.3f}s "
            f"statuses={dict(stats['status_counts'])} models={dict(stats['model_counts'])}",
            flush=True,
        )
        last_total, last_time = total, now


async def main() -> int:
    validate_config()
    print(
        f"Starting closed-loop text load: users={USERS} duration={DURATION_SEC}s "
        f"think={THINK_TIME_SEC}s mode={MODEL_MODE} max_tokens={MAX_TOKENS} api={API_BASE}",
        flush=True,
    )
    stats = {
        "latencies": deque(maxlen=LATENCY_WINDOW),
        "latency_sum": 0.0,
        "status_counts": Counter(), "model_counts": Counter(), "requests_total": 0,
    }
    # One bounded connection pool is shared across all virtual users.
    limits = httpx.Limits(max_connections=USERS, max_keepalive_connections=USERS)
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {API_KEY}"},
        limits=limits, timeout=TIMEOUT_SEC, trust_env=False,
    ) as client:
        # Client setup can be noticeable for short runs; start the window afterward.
        started_at = time.monotonic()
        stop_at = started_at + DURATION_SEC
        done = asyncio.Event()
        reporter = asyncio.create_task(periodic_report(started_at, stats, done))
        try:
            await asyncio.gather(*(user_loop(client, i, stop_at, stats) for i in range(USERS)))
        finally:
            done.set()
            await reporter
    elapsed = max(time.monotonic() - started_at, 0.001)
    total = stats["requests_total"]
    success = stats["status_counts"]["ok"]
    p50, p95, p99 = quantiles(stats["latencies"])
    avg = stats["latency_sum"] / success if success else 0.0
    print(
        "\nFINAL LOAD TEST SUMMARY\n"
        f"elapsed={elapsed:.2f}s total_requests={total} successful_requests={success}\n"
        f"avg_rps={total/elapsed:.2f} successful_rps={success/elapsed:.2f}\n"
        f"success_avg_latency={avg:.3f}s success_p50={p50:.3f}s "
        f"success_p95={p95:.3f}s success_p99={p99:.3f}s\n"
        f"latency_window={len(stats['latencies'])} (last {LATENCY_WINDOW} successful requests)\n"
        f"statuses={dict(stats['status_counts'])} models={dict(stats['model_counts'])}",
        flush=True,
    )
    return 0 if total > 0 and success == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
