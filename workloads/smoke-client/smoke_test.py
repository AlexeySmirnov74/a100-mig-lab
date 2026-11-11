"""GPU-backed route smoke test. Any failed route makes the process fail."""
import os
import sys

import requests

BASE_URL = os.getenv("BASE_URL", "http://context-guard:4010/v1").rstrip("/")
API_KEY = os.getenv("OPENAI_API_KEY", "dummy-local-key")
MODELS = ["general-chat", "fast-chat", "tiny-chat"]
PROMPT = "Say hello in one short sentence."


def check(client: requests.Session, model: str) -> None:
    response = client.post(
        f"{BASE_URL}/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": 64,
            "temperature": 0.1,
            "stream": False,
        },
        timeout=(10, 120),
    )
    response.raise_for_status()
    text = response.json()["choices"][0]["message"]["content"]
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty completion")
    print(f"[{model}] {text}", flush=True)


def main() -> int:
    failures = 0
    with requests.Session() as client:
        client.headers.update({"Authorization": f"Bearer {API_KEY}"})
        for model in MODELS:
            try:
                check(client, model)
            except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
                failures += 1
                print(f"[{model}] FAILED: {exc}", file=sys.stderr, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
