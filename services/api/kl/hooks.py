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

from urllib.parse import parse_qs

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from starlette.concurrency import run_in_threadpool

from . import config, telemetry
from .db import resolve_org_id, scoped
from .policy import audit
from .slack import DONT_CAPTURE, capture_action as slack_capture_action
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
CHANNEL = re.compile(r"^[CG][A-Z0-9]{2,20}$")
SLACK_USER = re.compile(r"^[UW][A-Z0-9]{2,20}$")
SLACK_TS = re.compile(r"^[0-9]{9,11}\.[0-9]{1,6}$")
COMMAND_REPLY = {  # private to whoever ran it (Slack shows an ephemeral reply only to them); it promises nothing it can't check
    "on": "Asked. If you created this channel or administer this workspace, its team's admins are asked to confirm capture "
          "in Aerugo; the channel is told when it starts.",
    "off": "Asked. If you created this channel or administer this workspace, capture stops, and the channel is told.",
    None: "Use /aerugo capture on or /aerugo capture off in a channel Aerugo reads. React :no_entry_sign: to keep a message out.",
}


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


def queue_job(org_id: str, kind: str, connection_id: str, delivery: str, job: str, payload: dict) -> dict:
    """Record a Slack delivery once, then queue the one job it asks for (C2b), for the worker to check against Slack."""
    with scoped(org_id, SERVICE) as conn:
        claimed = conn.execute("INSERT INTO webhook_deliveries (org_id, source, delivery_id, event) VALUES (%s, %s, %s, %s) "
                               "ON CONFLICT DO NOTHING RETURNING delivery_id", (org_id, kind, f"{connection_id}:{delivery}", job)).fetchone()
        if not claimed:
            return {"duplicate": True, "queued": 0}
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, %s, %s)", (org_id, job, Jsonb({"connection_id": connection_id, **payload})))
        conn.execute("UPDATE webhook_deliveries SET queued_jobs = 1 WHERE org_id = %s AND source = %s AND delivery_id = %s",
                     (org_id, kind, f"{connection_id}:{delivery}"))
        audit(conn, org_id, SERVICE[0], f"webhook.{kind}", [], {"connection_id": connection_id, "delivery": delivery, "job": job})
    return {"duplicate": False, "queued": 1}


@router.post("/v1/hooks/slack/{connection_id}/commands")
async def slack_command(connection_id: str, request: Request, x_slack_signature: str = Header(default=""),
                        x_slack_request_timestamp: str = Header(default="")):
    """`/aerugo capture on|off` (C2b), signed by the Slack app as its events are; it only queues the worker's check, and
    answers privately (ephemeral, only the person who ran it sees it), the same whoever ran it."""
    body = await capped_body(request, MAX_BODY)
    if body is None:
        telemetry.webhook_rejected("too_large")
        raise HTTPException(413, "payload too large")
    if not CONNECTION.fullmatch(connection_id):
        raise refused("unknown")
    found = await run_in_threadpool(hook_secret, "slack", connection_id)
    if not found or not slack_signed(found[1], body, x_slack_request_timestamp, x_slack_signature):
        raise refused("signature" if found else "unknown")
    form = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
    channel, user = form.get("channel_id", ""), form.get("user_id", "")
    action = slack_capture_action(form.get("text", ""))
    if action and CHANNEL.fullmatch(channel) and SLACK_USER.fullmatch(user):
        delivery = "cmd-" + hashlib.sha256(x_slack_signature.encode()).hexdigest()[:32]  # a replay is the same signature
        await run_in_threadpool(queue_job, found[0], "slack", connection_id, delivery, "slack.capture",
                                {"channel": channel, "user": user, "action": action})
    return JSONResponse({"response_type": "ephemeral", "text": COMMAND_REPLY[action]}, status_code=200)


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
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if event.get("type") == "reaction_added" and event.get("reaction") == DONT_CAPTURE and item.get("type") == "message" \
                and CHANNEL.fullmatch(str(item.get("channel") or "")) and SLACK_TS.fullmatch(str(item.get("ts") or "")):
            result = await run_in_threadpool(queue_job, org_id, kind, connection_id, delivery, "slack.exclude",
                                             {"channel": item["channel"], "ts": item["ts"]})  # a hint: the worker reads the reaction back
            log.info("slack don't-capture hook for %s: %s", connection_id, result)
            return result
    else:
        if not atlassian_signed(secret, body, x_hub_signature):
            raise refused("signature")
        delivery = x_atlassian_webhook_identifier
        if not ATLASSIAN_DELIVERY.fullmatch(delivery):
            raise HTTPException(400, "missing or malformed delivery identifier")
    result = await run_in_threadpool(queue_refresh, org_id, kind, connection_id, delivery)
    log.info("%s hook for %s: %s", kind, connection_id, result)
    return result
