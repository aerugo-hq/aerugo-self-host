"""Slack channel capture (C2b; docs/ENTERPRISE_PLAN.md section 6, principle 3): opt-in, visible, and never forced.

A channel is captured (its threads proposed as knowledge through F2's extraction, for curators to confirm) only with
two keys: `/aerugo capture on`, run in the channel by its creator or a workspace admin, then the connection's team admin
confirming in Aerugo. The bot then posts one notice in the channel, and another when capture stops; nothing else it
writes there. Anyone can mark a message "don't capture" (a :no_entry_sign: reaction), and it's never kept again. Each
step here is a job a signed delivery queued: the delivery is only a hint (invariant 13), so each reads Slack again,
as the connection's bot, before it changes anything. Run by the worker, as kl_ingest in its org."""
import logging

from psycopg.types.json import Jsonb

from . import slack

log = logging.getLogger(__name__)

CONTAINER_SQL = """
SELECT c.id, c.org_id, c.kind, c.status, c.config, c.credential_ref, w.slug AS team, w.name AS team_name, cc.capture
FROM connections c JOIN workspaces w ON w.id = c.workspace_id
JOIN connection_containers cc ON cc.connection_id = c.id AND cc.external_id = %(channel)s AND cc.selected
WHERE c.id = %(connection)s AND c.kind = 'slack' AND c.status <> 'disabled'
"""
DROP_PROPOSALS_SQL = """DELETE FROM knowledge_units WHERE created_by = 'extraction' AND status = 'proposed'
AND evidence && ARRAY(SELECT id FROM documents WHERE connection_id = %s AND external_key = ANY(%s))"""
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
        conn.execute(DROP_PROPOSALS_SQL, (c["id"], slack.holding(p["channel"], message)))
        if added:
            conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.sync', %s)",
                         (c["org_id"], Jsonb({"connection_id": str(c["id"]), "full": True, "reason": "capture.exclude"})))
            audit(conn, c["org_id"], "capture.exclude", {"connection_id": str(c["id"]), "channel": p["channel"], "message": ts})
    return {"excluded": bool(added)}
