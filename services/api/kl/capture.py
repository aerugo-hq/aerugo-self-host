"""Slack channel capture (C2b; docs/ENTERPRISE_PLAN.md section 6, principle 3): opt-in, visible, and never forced.

A channel is captured (its threads proposed as knowledge through F2's extraction, for curators to confirm) only with
two keys: `/aerugo capture on`, run in the channel by its creator or a workspace admin, then the connection's team admin
confirming in Aerugo. The bot then posts one notice in the channel, and another when capture stops; nothing else it
writes there. Anyone can mark a message "don't capture" (a :no_entry_sign: reaction), and it's never kept again. Each
step here is a job a signed delivery queued: the delivery is only a hint (invariant 13), so each reads Slack again,
as the connection's bot, before it changes anything. Run by the worker, as kl_ingest in its org."""
import logging

import httpx
from psycopg.types.json import Jsonb

from . import slack

log = logging.getLogger(__name__)

CONTAINER_SQL = """
SELECT c.id, c.org_id, c.kind, c.status, c.config, c.credential_ref, w.id AS team_id, w.slug AS team, w.name AS team_name, cc.capture
FROM connections c JOIN workspaces w ON w.id = c.workspace_id
JOIN connection_containers cc ON cc.connection_id = c.id AND cc.external_id = %(channel)s AND cc.selected
WHERE c.id = %(connection)s AND c.kind = 'slack' AND c.status <> 'disabled'
"""
DROP_PROPOSALS_SQL = """DELETE FROM knowledge_units WHERE status = 'proposed'
AND evidence && ARRAY(SELECT id FROM documents WHERE connection_id = %s AND external_key = ANY(%s))
AND (created_by = 'extraction' OR (created_by = 'slack-save' AND topic = %s))"""  # a save of that message alone (C2c)
# The section holding a saved message, in a document the connection keeps for its channel (C2c).
HELD_SQL = """SELECT d.id, k.heading_path FROM documents d JOIN chunks k ON k.document_id = d.id
WHERE d.connection_id = %s AND d.external_key = ANY(%s) AND d.storage = 'index' AND k.heading_path = %s AND k.body = %s
ORDER BY d.external_key LIMIT 1"""
SAVE_TRIES = 5  # a minute apart: a message newer than the last sync waits for the one this queues
SET_SQL = "UPDATE connection_containers SET capture = %s, capture_changed_at = now() WHERE connection_id = %s AND external_id = %s"


def audit(conn, org_id, action: str, details: dict) -> None:
    conn.execute("INSERT INTO audit_events (org_id, actor, action, scope, details) VALUES (%s, %s, %s, %s, %s)",
                 (org_id, "process:worker", action, [], Jsonb(details)))


def channel_of(conn, payload: dict):
    """The connection and the capture state of a channel it syncs, or None: an unknown, paused, or unsynced one does nothing."""
    if not slack.CHANNEL.fullmatch(str(payload.get("channel") or "")):
        return None
    return conn.execute(CONTAINER_SQL, {"channel": payload["channel"], "connection": payload.get("connection_id")}).fetchone()


def request(conn, job) -> dict:
    """`/aerugo capture on|off` from someone in the channel: the first key, or turning it off, only for the channel's
    creator or a workspace admin, as Slack says now; the bot must be in the channel."""
    p = job["payload"]
    c = channel_of(conn, p)
    if c is None or p.get("action") not in ("on", "off"):
        return {"skipped": "not a channel this connection syncs"}
    client = slack.client_for(c)
    info, team = client.channel(p["channel"]), client.team()
    if not info.get("is_member") or not slack.may_turn(client.user(str(p.get("user") or "")), info, team["id"]):
        with conn.transaction():
            audit(conn, c["org_id"], "capture.refused", {"connection_id": str(c["id"]), "channel": p["channel"], "user": p.get("user")})
        return {"refused": "only the channel's creator or a workspace admin, with the bot in the channel"}
    with conn.transaction():
        if p["action"] == "on" and c["capture"] == "off":
            conn.execute(SET_SQL, ("requested", c["id"], p["channel"]))
        elif p["action"] == "off" and c["capture"] != "off":
            conn.execute(SET_SQL, ("off", c["id"], p["channel"]))
            if c["capture"] == "on":
                conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'slack.notice', %s)",
                             (c["org_id"], Jsonb({"connection_id": str(c["id"]), "channel": p["channel"], "state": "off"})))
        audit(conn, c["org_id"], f"capture.{p['action']}", {"connection_id": str(c["id"]), "channel": p["channel"], "user": p.get("user"),
                                                            "was": c["capture"]})
    return {"channel": p["channel"], "was": c["capture"], "asked": p["action"]}


def notice(conn, job) -> dict:
    """The one message the bot posts when capture turns on or off, if it's still in that state."""
    p = job["payload"]
    c = channel_of(conn, p)
    if c is None or c["capture"] != p.get("state"):
        return {"skipped": "capture changed since"}
    text = slack.NOTICE_ON.format(team=c["team_name"]) if p["state"] == "on" else slack.NOTICE_OFF
    slack.client_for(c).post(p["channel"], text)
    return {"posted": p["state"]}


def saved_topic(channel: str, ts: str) -> str:
    """A saved message's unit's topic: one per message, so a save is never made twice and a mark finds it."""
    return f"slack-save-{channel.lower()}-{ts.replace('.', '-')}"  # a unit's topic is lower case, digits, and hyphens


def tell(c, channel: str, user: str, text: str) -> None:
    """A private reply to the person who saved; failing to send it never undoes the save."""
    try:
        slack.client_for(c).whisper(channel, user, text)
    except (slack.SlackError, httpx.HTTPError, ValueError) as e:
        log.warning("couldn't tell a Slack user about their save: %s", type(e).__name__)


def save(conn, job) -> dict:
    """"Save to Aerugo" on a message (C2c): read again from Slack (who saved it, the message, the channel), and, as the
    rules allow (`slack.may_save`), proposed as a unit citing the document that holds it, so it has exactly that
    channel's readers, for the connection's team's curators to confirm. The person who saved is told privately."""
    p = job["payload"]
    c, ts, user = channel_of(conn, p), str(p.get("ts") or ""), str(p.get("user") or "")
    if c is None or not slack.TS.fullmatch(ts) or not slack.USER.fullmatch(user):
        return {"skipped": "not a message this connection syncs"}
    if (c["config"] or {}).get("storage", "index") != "index":  # a pointer or live-only connection keeps no text (F11)
        tell(c, p["channel"], user, slack.NOT_SAVED["no text"])
        return {"refused": "no text"}
    client = slack.client_for(c)
    team, users = client.team(), client.users()
    names = {uid: u["name"] for uid, u in users.items()}
    excluded = frozenset(r["message"] for r in conn.execute(
        "SELECT message FROM capture_exclusions WHERE connection_id = %s AND container = %s", (c["id"], p["channel"])))
    message = client.message(p["channel"], ts)
    why = slack.may_save(client.user(user), client.members(p["channel"]), message if message.get("ts") == ts else {},
                         team["id"], c["capture"] == "on", names, excluded)
    if why:
        tell(c, p["channel"], user, slack.NOT_SAVED[why])
        with conn.transaction():
            audit(conn, c["org_id"], "capture.save.refused", {"connection_id": str(c["id"]), "channel": p["channel"],
                                                              "message": ts, "user": user, "why": why})
        return {"refused": why}
    heading, body = slack.sections_of([message], names)[0]
    held = conn.execute(HELD_SQL, (c["id"], slack.holding(p["channel"], message), heading, body)).fetchone()
    if not held:  # newer than the last sync, or edited since: read the channel again, and try once more in a minute
        tries = int(p.get("tries") or 0)
        if tries + 1 >= SAVE_TRIES:
            tell(c, p["channel"], user, slack.NOT_SAVED["not found"])
            return {"refused": "not found"}
        with conn.transaction():
            conn.execute("INSERT INTO jobs (org_id, kind, payload) SELECT %s, 'connection.sync', %s WHERE NOT EXISTS ("
                         "SELECT 1 FROM jobs WHERE kind = 'connection.sync' AND status = 'queued' AND payload->>'connection_id' = %s)",
                         (c["org_id"], Jsonb({"connection_id": str(c["id"]), "reason": "capture.save"}), str(c["id"])))
            conn.execute("INSERT INTO jobs (org_id, kind, payload, run_after) VALUES (%s, 'slack.save', %s, now() + interval '1 minute')",
                         (c["org_id"], Jsonb({**p, "tries": tries + 1})))
        return {"waiting": tries + 1}
    topic = saved_topic(p["channel"], ts)
    with conn.transaction():
        if conn.execute("SELECT 1 FROM knowledge_units WHERE owner_team = %s AND topic = %s", (c["team_id"], topic)).fetchone():
            added = None
        else:
            added = conn.execute(
                "INSERT INTO knowledge_units (org_id, owner_team, kind, topic, title, body, trust, status, evidence, sections, created_by) "
                "VALUES (%s, %s, 'fact', %s, %s, %s, 'documented', 'proposed', %s::uuid[], %s, 'slack-save') RETURNING id",
                (c["org_id"], c["team_id"], topic, body.splitlines()[0][:120] or "Saved from Slack", body, [held["id"]],
                 [held["heading_path"]])).fetchone()
            audit(conn, c["org_id"], "capture.save", {"connection_id": str(c["id"]), "channel": p["channel"], "message": ts,
                                                      "user": user, "unit": str(added["id"])})
    tell(c, p["channel"], user, slack.SAVED.format(team=c["team_name"]) if added else slack.NOT_SAVED["already"])
    return {"saved": bool(added)}


def exclude(conn, job) -> dict:
    """A message marked "don't capture": recorded once Slack shows the reaction on it, then the channel is read again
    in full, so its document is kept without it (and F2's proposals from that document are replaced)."""
    p = job["payload"]
    c = channel_of(conn, p)
    ts = str(p.get("ts") or "")
    if c is None or not slack.TS.fullmatch(ts):
        return {"skipped": "not a message this connection syncs"}
    message = slack.client_for(c).message(p["channel"], ts)
    if message.get("ts") != ts or not any(r.get("name") == slack.DONT_CAPTURE and r.get("count", 0) > 0 for r in message.get("reactions") or []):
        return {"skipped": "no don't-capture mark on it"}
    with conn.transaction():
        added = conn.execute("INSERT INTO capture_exclusions (org_id, connection_id, container, message) VALUES (%s, %s, %s, %s) "
                             "ON CONFLICT DO NOTHING RETURNING message", (c["org_id"], c["id"], p["channel"], ts)).fetchone()
        # A model's proposals from the documents holding it go now, whatever capture and the team's model are by then;
        # once its document is read again without it, a captured channel's next extraction proposes from the rest.
        # One a curator confirmed stays, and F6 sends it back to them when its evidence changes.
        conn.execute(DROP_PROPOSALS_SQL, (c["id"], slack.holding(p["channel"], message), saved_topic(p["channel"], ts)))
        if added:
            conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.sync', %s)",
                         (c["org_id"], Jsonb({"connection_id": str(c["id"]), "full": True, "reason": "capture.exclude"})))
            audit(conn, c["org_id"], "capture.exclude", {"connection_id": str(c["id"]), "channel": p["channel"], "message": ts})
    return {"excluded": bool(added)}
