"""A local model for Ask (dev): download a small open model into the stack's Ollama, give it room for Aerugo's
prompts, check that it answers, and register it in every team, so Ask works with no API key and nothing sent to the
model leaves the machine. Run by `make llm`. Safe to run again; running it with another model switches every team.

    make llm                          # qwen2.5:1.5b, about 1 GB, fine on a laptop CPU
    make llm LLM_MODEL=qwen2.5:3b     # noticeably better answers, about 2 GB
"""
import json
import os
import sys
import time

import httpx

from .smoke import api, api_ready

OLLAMA = os.environ.get("KL_OLLAMA_URL", "http://ollama:11434")
BASE = os.environ.get("KL_LLM_MODEL") or "qwen2.5:1.5b"
NAME = "aerugo-local"  # what Aerugo calls: BASE, with a context window big enough for several sources
# Every team gets the same model, as "<team>/local". Classifications up to confidential may reach it, because nothing
# sent to a local model leaves the machine; restricted stays out until a team's admin decides otherwise.
TEAMS = ("payments", "finance", "commons", "legal")
ADMINS = {"payments": "alice", "finance": "bob", "commons": "olivia", "legal": "dave"}  # if the org admin can't
CLASSIFICATIONS = ["public", "internal", "confidential"]


def wait_for_ollama() -> None:
    for _ in range(60):
        try:
            if httpx.get(f"{OLLAMA}/api/version", timeout=5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise SystemExit(f"Ollama isn't answering at {OLLAMA}. Start it with: docker compose --profile llm up -d ollama")


def pull(model: str) -> None:
    print(f"Downloading {model} (only the first time: it's kept in the kl-ollama volume)...")
    shown = -1
    with httpx.stream("POST", f"{OLLAMA}/api/pull", json={"model": model}, timeout=None) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            event = json.loads(line)
            if event.get("error"):
                raise SystemExit(f"Ollama couldn't download {model}: {event['error']}")
            total, done = event.get("total"), event.get("completed")
            if total and done:
                percent = int(done * 100 / total) // 10 * 10
                if percent != shown:
                    shown = percent
                    print(f"  {percent}%", flush=True)
    print(f"  {model} is downloaded.")


def tune(base: str) -> str:
    """BASE with an 8K context (Ask sends several sources at once; Ollama's default window would cut them off), a
    steady temperature, and answers capped near 700 tokens so a CPU isn't busy for minutes."""
    r = httpx.post(f"{OLLAMA}/api/create", timeout=300, json={
        "model": NAME, "from": base, "stream": False,
        "parameters": {"num_ctx": 8192, "temperature": 0.2, "num_predict": 700}})
    if r.status_code != 200:
        print(f"  Couldn't give {base} a larger context ({r.status_code} {r.text[:120]}); using it as it is.")
        return base
    return NAME


def check(model: str) -> None:
    print("Checking that it answers (the first answer loads it into memory, so it's the slowest)...")
    started = time.monotonic()
    r = httpx.post(f"{OLLAMA}/v1/chat/completions", timeout=600, json={
        "model": model, "messages": [{"role": "user", "content": "Reply with one word: ready."}]})
    r.raise_for_status()
    reply = (r.json()["choices"][0]["message"].get("content") or "").strip()
    print(f"  It said {reply[:60]!r}, in {time.monotonic() - started:.0f} seconds.")


def register(model: str) -> int:
    body = {"name": "local", "provider": "openai_compatible", "model": model, "base_url": f"{OLLAMA}/v1",
            "api_key": "ollama",  # Ollama ignores keys, but Aerugo stores one for every model
            "allowed_classifications": CLASSIFICATIONS}
    ready = 0
    for team in TEAMS:
        for admin in dict.fromkeys(("olivia", ADMINS[team])):
            r = api("POST", f"/v1/workspaces/{team}/models", admin, json=body)
            if r.status_code == 409:  # registered before: point it at this model
                r = api("PATCH", f"/v1/workspaces/{team}/models/local", admin,
                        json={k: body[k] for k in ("model", "base_url", "allowed_classifications")})
            if r.status_code in (200, 201):
                print(f"  {team}/local is ready")
                ready += 1
                break
        else:
            print(f"  {team}: couldn't register it ({r.status_code} {r.text[:120]})")
    return ready


def main() -> int:
    wait_for_ollama()
    pull(BASE)
    model = tune(BASE)
    check(model)
    api_ready()
    print("Registering it in every team, as <team>/local...")
    if not register(model):
        return 1
    print("\nDone. Open Ask in the web app (make web) and choose <your team>/local as the model.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
