"""GitHub webhooks (M1 slice 3).

Events are hints, never truth. A delivery is accepted only with a valid HMAC signature, is recorded
once (replays are ignored), and can only queue a re-sync of the affected connections. The sync then
re-reads GitHub's real state, so a forged or stale payload can never grant access. With no webhook
secret configured, every request is rejected."""
import hashlib
import hmac
import json
import logging
import re
import time

from fastapi import APIRouter, Header, HTTPException, Request
from psycopg.types.json import Jsonb
from starlette.concurrency import run_in_threadpool

from . import config, telemetry
from .db import resolve_org_id, scoped
from .github import APP_REF
from .policy import audit
from .vault import vault

router = APIRouter()
log = logging.getLogger("kl.webhooks")

MAX_BODY = 5 * 1024 * 1024
SERVICE = ["svc:github-webhook"]
ACCESS_EVENTS = {"member", "membership", "team", "team_add", "organization", "repository",
                 "installation_repositories"}
_DELIVERY = re.compile(r"^[A-Za-z0-9-]{1,100}$")
_EVENT = re.compile(r"^[a-z_]{1,50}$")
_secret: tuple[str, float] | None = None


def webhook_secret() -> str | None:
    global _secret
    if _secret and time.monotonic() - _secret[1] < 60:
        return _secret[0]
    try:
        value = vault.get(APP_REF).get("webhook_secret") or None
    except Exception:  # noqa: BLE001 - not configured or backend unreachable: fail closed
        value = None
    _secret = (value, time.monotonic()) if value else None
    return value


def verify_signature(secret: str | None, body: bytes, header: str) -> bool:
    if not secret or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def job_for(event: str, payload: dict) -> tuple[str | None, str | None]:
    """(job kind, repo filter). Content changes on a default branch re-sync that repo's connections;
    access changes re-discover the installation's connections, which then re-sync."""
    repo = payload.get("repository") or {}
    if event == "push":
        default = repo.get("default_branch")
        if default and payload.get("ref") == f"refs/heads/{default}":
            return "connection.sync", repo.get("full_name")
        return None, None
    if event in ACCESS_EVENTS:
        return "connection.discover", None
    return None, None


def handle(event: str, delivery: str, payload: dict) -> dict:
    installation = (payload.get("installation") or {}).get("id")
    kind, repo = job_for(event, payload)
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, SERVICE) as conn:
        claimed = conn.execute(
            "INSERT INTO webhook_deliveries (org_id, source, delivery_id, event) VALUES (%s, 'github', %s, %s) "
            "ON CONFLICT DO NOTHING RETURNING delivery_id", (org_id, delivery, event)).fetchone()
        if not claimed:
            return {"duplicate": True, "queued": 0}
        queued = 0
        if event == "installation" and payload.get("action") in ("deleted", "suspend") and installation:
            conn.execute("UPDATE connections SET status = 'error', last_error = %s, updated_at = now() "
                         "WHERE kind = 'github' AND (config->>'installation_id')::bigint = %s",
                         (f"GitHub App installation {payload['action']}", installation))
        elif kind and isinstance(installation, int):
            targets = conn.execute(
                """SELECT c.id FROM connections c
                   WHERE c.kind = 'github' AND c.status <> 'disabled'
                     AND (c.config->>'installation_id')::bigint = %s
                     AND (%s::text IS NULL OR EXISTS (
                           SELECT 1 FROM connection_containers cc
                           WHERE cc.connection_id = c.id AND cc.external_id = %s AND cc.selected))""",
                (installation, repo, repo)).fetchall()
            for t in targets:
                already = conn.execute(
                    "SELECT 1 FROM jobs WHERE kind IN ('connection.sync', 'connection.discover') "
                    "AND status = 'queued' AND payload->>'connection_id' = %s", (str(t["id"]),)).fetchone()
                if not already:
                    conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, %s, %s)",
                                 (org_id, kind, Jsonb({"connection_id": str(t["id"]), "reason": f"webhook:{event}"})))
                    queued += 1
        conn.execute("UPDATE webhook_deliveries SET queued_jobs = %s "
                     "WHERE org_id = %s AND source = 'github' AND delivery_id = %s", (queued, org_id, delivery))
        audit(conn, org_id, SERVICE[0], "webhook.github", [],
              {"event": event, "delivery": delivery, "repo": repo, "queued": queued})
    return {"duplicate": False, "queued": queued}


@router.post("/v1/webhooks/github", status_code=202)
async def github_webhook(request: Request,
                         x_github_event: str = Header(default=""),
                         x_github_delivery: str = Header(default=""),
                         x_hub_signature_256: str = Header(default="")):
    body = await request.body()
    if len(body) > MAX_BODY:
        telemetry.webhook_rejected("too_large")
        raise HTTPException(413, "payload too large")
    if not verify_signature(await run_in_threadpool(webhook_secret), body, x_hub_signature_256):
        telemetry.webhook_rejected("signature")
        raise HTTPException(401, "invalid signature")
    if not _DELIVERY.fullmatch(x_github_delivery) or not _EVENT.fullmatch(x_github_event):
        telemetry.webhook_rejected("headers")
        raise HTTPException(400, "missing or malformed delivery or event header")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        telemetry.webhook_rejected("not_json")
        raise HTTPException(400, "body is not JSON")
    result = await run_in_threadpool(handle, x_github_event, x_github_delivery, payload)
    log.info("github webhook %s %s: %s", x_github_event, x_github_delivery, result)
    return result
