"""The Teams bot answers privately, as the asker (C1b; docs/ENTERPRISE_PLAN.md section 6).

A message to the bot in its 1:1 chat, or a channel post mentioning it, each delivered on Microsoft's signed token and
only queued by its hook (kl.teams_bot), is answered here, in the answers lane (kl.answers_lane), as the API's own role.
Who's asking is read back from the Bot Connector (the person in that conversation, by the ids the delivery names: their
Entra id, tenant, role, and email), never taken from the delivery's word; a mention's question is read back from Graph,
and only answered for its own author; a 1:1 question is the one text taken from a delivery (invariant 13's recorded
exception), the asker's own words to the bot, never kept. Then kl.chat_ask, the path every bot shares, answers as them.
The answer goes only to them, in their own 1:1 chat with the bot, opened by their id: never in a channel, and never to an
address a delivery names. Neither the question nor the answer is logged."""
from __future__ import annotations

import os
from urllib.parse import quote

from psycopg.types.json import Jsonb

from . import chat_ask, teams, teams_bot
from .directory import EMAIL, GUID

WEB_URL = os.environ.get("KL_WEB_URL", "http://localhost:3000").rstrip("/")
CONNECTION_SQL = """SELECT c.id, c.org_id, c.kind, c.status, c.config, c.credential_ref, w.slug AS team
FROM connections c JOIN workspaces w ON w.id = c.workspace_id
WHERE c.id = %s AND c.kind = 'teams' AND c.status <> 'disabled'"""
SAY = {  # what the asker is told, privately, when there's no answer to give
    "not set up": "Asking isn't set up for this Teams app yet: a team admin chooses a model for it in Aerugo.",
    "outsider": "Only people in your company's tenant, not guests, can ask Aerugo here.",
    "unlinked": "Aerugo doesn't know who you are yet. Sign in to Aerugo once with your work account, then ask again.",
    "left": "Aerugo's directory no longer lists you, so it can't answer you here.",
    "stale": "It's been a while since you signed in to Aerugo. Sign in again, then ask again.",
    "not onboarded": "Your team isn't onboarded to Aerugo yet: ask your Aerugo administrator.",
    "empty": "Ask a question: write it to me here, or mention Aerugo with it in a channel.",
    "no access": "Asking here uses its team's model, so only that team's members can ask through this app.",
    "busy": "You've asked a lot just now. Try again in a minute.",
    "failed": "Aerugo couldn't answer that just now. Try again, or ask in Aerugo.",
}


def reply_of(result: dict, web: str = WEB_URL) -> str:
    """The private reply, in plain text: the answer, the sources it cites by title with their address in Aerugo's reader
    (never their text), its confidence, and, when some sources weren't sent here, where to ask for the full answer."""
    answer = str(result.get("answer") or "")
    if len(answer) > chat_ask.REPLY_CHARS:
        answer = answer[:chat_ask.REPLY_CHARS].rstrip() + "..."
    shown = chat_ask.cited(result)
    lines = [answer]
    if shown:
        lines += ["", "Sources:"] + [f"- {c.get('title') or c['id']} (knowledge)" if c.get("unit") else
                                     f"- {c.get('title') or c['id']}: {web}/#/documents/{quote(c['id'], safe='')}" for c in shown]
    level = (result.get("confidence") or {}).get("level")
    if level:
        lines += ["", f"Confidence: {level}"]
    if result.get("withheld_by_policy"):
        lines += ["", f"Some sources aren't shown in Teams. For the full answer, ask in Aerugo: {web}/#/ask"]
    return "\n".join(lines)


def ask(conn, job) -> dict:
    """One ask, answered privately as its asker, or told why not. Tried once: a retry would answer twice."""
    p = job["payload"]
    c = conn.execute(CONNECTION_SQL, (p.get("connection_id"),)).fetchone()
    user, user_id, conversation = str(p.get("user") or ""), str(p.get("user_id") or ""), str(p.get("conversation") or "")
    if c is None or not GUID.fullmatch(user) or not teams.USER_ID.fullmatch(user_id) or not teams.CONVERSATION.fullmatch(conversation):
        return {"skipped": "not a conversation this connection's bot is in"}

    def refuse(why: str) -> dict:
        teams_bot.tell(c, user_id, SAY[why])
        return {"refused": why}

    cfg = c["config"] or {}
    settings = cfg.get("answers") or {}
    if not settings.get("model"):
        return refuse("not set up")
    person = teams_bot.bot_for(c).member(conversation, user_id)  # who this is, as Teams says now
    email = str(person.get("email") or person.get("userPrincipalName") or "").strip().lower()
    if str(person.get("aadObjectId") or "").lower() != user.lower() or str(person.get("tenantId") or "").lower() != cfg["tenant_id"] \
            or str(person.get("userRole") or "user").lower() == "guest" or not EMAIL.fullmatch(email):
        return refuse("outsider")
    if p.get("via") == "mention":  # read back from Graph, and answered only for its own author
        held = teams_bot.channel_of(conn, p)
        if held is None or not GUID.fullmatch(str(held["ms_team"] or "")):
            return {"skipped": "not a channel this connection reads"}
        post = p.get("post") if isinstance(p.get("post"), str) else None
        message = teams.client_for(c).message(held["ms_team"], p["channel"], str(p.get("message") or ""), post)
        if str(message.get("id") or "") != p.get("message") or teams.author(message) != user.lower():
            return {"skipped": "the message isn't the asker's"}
        question = teams.question_of(message, cfg["client_id"])[:chat_ask.QUESTION_CHARS]
    else:
        question = str(p.get("question") or "").strip()[:chat_ask.QUESTION_CHARS]
    if not question:
        return refuse("empty")
    why, result, username = chat_ask.answer_as(conn, f"ext:microsoft.{email}", email, question, settings)
    if why:
        return refuse(why)
    teams_bot.tell(c, user_id, reply_of(result))
    conn.execute("INSERT INTO audit_events (org_id, actor, action, scope, details) VALUES (%s, %s, %s, %s, %s)",
                 (c["org_id"], "process:answers", "teams.ask", [c["team"]],
                  Jsonb({"connection_id": str(c["id"]), "user": user, "as": username, "via": p.get("via")})))
    return {"answered": True}
