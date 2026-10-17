"""Webhooks from Jira, Confluence, and Slack (S8): hints that something changed, never truth (invariant 13).

Each connection has its own hook, /v1/hooks/<kind>/<connection id>, and its own secret: one Aerugo makes for Jira and
Confluence (their admin webhooks sign each delivery, X-Hub-Signature: sha256=<HMAC of the body>), or the Slack app's
signing secret (v0 signatures over the request's timestamp and body, within five minutes). A delivery is checked
before anything else is read; anything wrong, an unknown connection included, is the same 401. Each delivery is
recorded once (a replay is ignored) and can only queue a readers refresh of its own connection, which re-reads the
source: nothing in a payload is ever taken as content or permission. Microsoft Graph and Drive sign nothing (a shared
token only), so they get no hook; their readers come with the readers refresh's cadence."""
import hashlib
import hmac
import json
import logging
import re
import time

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from starlette.concurrency import run_in_threadpool

from . import config, telemetry
from .db import resolve_org_id, scoped
from .policy import audit
from .vault import vault

router = APIRouter()
log = logging.getLogger("kl.hooks")

HOOK_KINDS = ("jira", "confluence", "slack")
SERVICE = ["svc:hooks"]
MAX_BODY = 1024 * 1024
SLACK_WINDOW = 300  # seconds a Slack delivery's timestamp may be off, as Slack advises
CONNECTION = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ATLASSIAN_DELIVERY = re.compile(r"^[A-Za-z0-9-]{1,100}$")
SLACK_EVENT = re.compile(r"^Ev[A-Za-z0-9]{1,40}$")


async def capped_body(request: Request, limit: int) -> bytes | None:
    """A delivery's body, read only up to `limit` (M5): None as soon as it's known to be larger, from the length its
    headers declare or, streamed, from what has arrived, so an oversized one never costs more than `limit` to refuse."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            return None
    return bytes(body)


def hook_secret(kind: str, connection_id: str) -> tuple[str, str] | None:
    """The org and the secret of a connection's hook, if it has one and isn't paused; None otherwise, or on any error."""
    try:
        org_id = resolve_org_id(config.ORG_SLUG)
        with scoped(org_id, SERVICE) as conn:
            row = conn.execute("SELECT kind, status, config->>'webhook_ref' AS ref FROM connections WHERE id = %s",
                               (connection_id,)).fetchone()
        if not row or row["kind"] != kind or row["status"] == "disabled" or not row["ref"]:
            return None
        return str(org_id), vault.get(row["ref"])["secret"]
    except Exception as e:  # noqa: BLE001 - the backend unreachable or the secret gone: fail closed, and say why in the log
        log.warning("hook secret unavailable for %s %s: %s", kind, connection_id, type(e).__name__)
        return None


def atlassian_signed(secret: str, body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


def slack_signed(secret: str, body: bytes, timestamp: str, header: str, now: float | None = None) -> bool:
    if not timestamp.isdigit() or abs((now or time.time()) - int(timestamp)) > SLACK_WINDOW:
        return False  # too old, or from the future: a replay, or a clock no one should trust
    expected = "v0=" + hmac.new(secret.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


def queue_refresh(org_id: str, kind: str, connection_id: str, delivery: str) -> dict:
    """Record the delivery once, then queue its connection's readers refresh unless one is pending."""
    with scoped(org_id, SERVICE) as conn:
        claimed = conn.execute("INSERT INTO webhook_deliveries (org_id, source, delivery_id, event) VALUES (%s, %s, %s, 'change') "
                               "ON CONFLICT DO NOTHING RETURNING delivery_id", (org_id, kind, f"{connection_id}:{delivery}")).fetchone()
        if not claimed:
            return {"duplicate": True, "queued": 0}
        pending = conn.execute("SELECT 1 FROM jobs WHERE kind IN ('connection.refresh_readers', 'connection.sync', 'connection.discover') "
                               "AND status = 'queued' AND payload->>'connection_id' = %s", (connection_id,)).fetchone()
        if not pending:
            conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.refresh_readers', %s)",
                         (org_id, Jsonb({"connection_id": connection_id, "reason": f"webhook:{kind}"})))
        conn.execute("UPDATE webhook_deliveries SET queued_jobs = %s WHERE org_id = %s AND source = %s AND delivery_id = %s",
                     (0 if pending else 1, org_id, kind, f"{connection_id}:{delivery}"))
        audit(conn, org_id, SERVICE[0], f"webhook.{kind}", [], {"connection_id": connection_id, "delivery": delivery, "queued": not pending})
    return {"duplicate": False, "queued": 0 if pending else 1}


def refused(reason: str):
    telemetry.webhook_rejected(reason)
    return HTTPException(401, "invalid signature")


@router.post("/v1/hooks/{kind}/{connection_id}", status_code=202)
async def hook(kind: str, connection_id: str, request: Request,
               x_hub_signature: str = Header(default=""), x_atlassian_webhook_identifier: str = Header(default=""),
               x_slack_signature: str = Header(default=""), x_slack_request_timestamp: str = Header(default="")):
    body = await capped_body(request, MAX_BODY)
    if body is None:
        telemetry.webhook_rejected("too_large")
        raise HTTPException(413, "payload too large")
    if kind not in HOOK_KINDS or not CONNECTION.fullmatch(connection_id):
        raise refused("unknown")
    found = await run_in_threadpool(hook_secret, kind, connection_id)
    if not found:
        raise refused("unknown")
    org_id, secret = found
    if kind == "slack":
        if not slack_signed(secret, body, x_slack_request_timestamp, x_slack_signature):
            raise refused("signature")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise HTTPException(400, "body is not JSON")
        if payload.get("type") == "url_verification":  # Slack checking the address, signed: echo its challenge
            challenge = payload.get("challenge")
            if not isinstance(challenge, str) or len(challenge) > 200:
                raise HTTPException(400, "malformed challenge")
            return JSONResponse({"challenge": challenge}, status_code=200)
        delivery = str(payload.get("event_id") or "")
        if not SLACK_EVENT.fullmatch(delivery):
            raise HTTPException(400, "missing or malformed event id")
    else:
        if not atlassian_signed(secret, body, x_hub_signature):
            raise refused("signature")
        delivery = x_atlassian_webhook_identifier
        if not ATLASSIAN_DELIVERY.fullmatch(delivery):
            raise HTTPException(400, "missing or malformed delivery identifier")
    result = await run_in_threadpool(queue_refresh, org_id, kind, connection_id, delivery)
    log.info("%s hook for %s: %s", kind, connection_id, result)
    return result
