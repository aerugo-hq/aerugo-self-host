"""Model probes for the registry. M4 replaces this with the full gateway
(quotas, budgets, redaction, egress policy, more providers)."""
import time

import httpx
from fastapi import HTTPException

PROMPT = "Reply with the single word: pong"


def probe(provider: str, model: str, base_url: str | None, api_key: str) -> dict:
    started = time.monotonic()
    if provider == "openai_compatible":  # OpenAI, Ollama, vLLM, LiteLLM, and most gateways
        url = (base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
        r = httpx.post(url, headers={"Authorization": f"Bearer {api_key}"},
                       json={"model": model, "messages": [{"role": "user", "content": PROMPT}]},
                       timeout=60)
    elif provider == "anthropic":
        url = (base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
        r = httpx.post(url, headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                       json={"model": model, "max_tokens": 16,
                             "messages": [{"role": "user", "content": PROMPT}]},
                       timeout=60)
    else:
        raise HTTPException(501, f"probing {provider} arrives with the model gateway in M4")
    return {"ok": r.is_success, "status": r.status_code,
            "latency_ms": int((time.monotonic() - started) * 1000)}
