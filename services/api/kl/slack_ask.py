"""The bot answers privately, as the asker (C2d; docs/ENTERPRISE_PLAN.md section 6).

`/aerugo ask <question>` and a message mentioning the bot, each signed by the Slack app and only queued by its hook, are
answered here, in the answers lane (kl.answers_lane), which connects as the API's own role. The asker is the person
their Slack account is linked to (principal_aliases: a link they made by signing in, never one Slack's word alone
makes), with the groups of their last verified sign-in, only while that's recent (KL_SLACK_ASK_DAYS) and the org's
admission lets them in. Their question then goes through the API's own Ask (`main.answer_question`): the same
row-level security, OpenFGA post-check, audit, model egress, units, and context, narrowed to what a Slack reply may
carry (public and internal, unless the team's admin allows more). The answer goes only to them, privately, by the
connection's bot, in the channel or thread they asked in: never to an address a delivery names. A mention is read back
from Slack; a command's question is the one text a delivery gives (invariant 13's recorded exception). Neither the
question nor the answer is logged."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
import psycopg
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from . import admission, config, slack
from .auth import Caller
from .capture import channel_of, tell

log = logging.getLogger("kl.slack_ask")

ASK_DAYS = float(os.environ.get("KL_SLACK_ASK_DAYS", "7"))  # how recent the asker's last sign-in must be
WEB_URL = os.environ.get("KL_WEB_URL", "http://localhost:3000").rstrip("/")
REACH = ("public", "internal")  # what a Slack reply carries unless the team's admin allows more
CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")
QUESTION_CHARS, REPLY_CHARS, SOURCES = 1000, 2800, 6
SAY = {  # what the asker is told, privately, when there's no answer to give
    "not set up": "Asking isn't set up for this Slack workspace yet: a team admin chooses a model for it in Aerugo.",
    "outsider": "Only people in this workspace can ask Aerugo here.",
    "unlinked": "Aerugo doesn't know who you are yet. Sign in to Aerugo once with your work account, then ask again.",
    "left": "Aerugo's directory no longer lists you, so it can't answer you here.",
    "stale": "It's been a while since you signed in to Aerugo. Sign in again, then ask again.",
    "not onboarded": "Your team isn't onboarded to Aerugo yet: ask your Aerugo administrator.",
    "empty": "Ask a question: /aerugo ask <your question>, or mention Aerugo with it.",
    "no access": "Asking here uses its team's model, so only that team's members can ask in this workspace.",
    "busy": "You've asked a lot just now. Try again in a minute.",
    "failed": "Aerugo couldn't answer that just now. Try again, or ask in Aerugo.",
}
PERSON_SQL = "SELECT canonical, verified_by FROM principal_aliases WHERE source = %s"
SIGN_IN_SQL = "SELECT groups, signed_in_at FROM person_sign_ins WHERE username = %s"
# Someone a directory that isn't paused lists only as having left (Q6's rule), by their verified sign-in email or their
# Slack account's: it can only refuse, so a link made another way, or a sign-in still inside its week, never outlives it.
LEFT_SQL = """SELECT 1 FROM directory_people d
JOIN connections c ON c.id = d.connection_id AND c.kind = 'directory' AND c.status <> 'disabled'
WHERE d.email = ANY(ARRAY(SELECT email FROM people WHERE username = %(user)s AND email IS NOT NULL) || %(emails)s::text[])
GROUP BY d.email HAVING NOT bool_or(d.active) LIMIT 1"""


def fresh(signed_in_at: datetime | None, now: datetime, days: float = ASK_DAYS) -> bool:
    """Whether a sign-in is recent enough to ask as its person: their groups and admission are only as current as it."""
    return signed_in_at is not None and now - signed_in_at <= timedelta(days=days)


def escape(text: str) -> str:
    """Slack's three control characters, escaped, so nothing in an answer or a title becomes a link or a mention."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def reply_of(result: dict, web: str = WEB_URL) -> str:
    """The private reply: the answer, the sources it cites as links to Aerugo's reader (titles only, never their text),
    its confidence, and, when some sources weren't sent here, where to ask for the full answer."""
    answer = escape(result.get("answer") or "")
    if len(answer) > REPLY_CHARS:
        answer = answer[:REPLY_CHARS].rstrip() + "..."
    citations = result.get("citations") or []
    shown = ([c for c in citations if c.get("cited")] or citations)[:SOURCES]
    lines = [answer]
    if shown:
        lines += ["", "Sources:"] + [f"- {escape(c.get('title') or c['id'])} (knowledge)" if c.get("unit") else
                                     f"- <{web}/#/documents/{quote(c['id'], safe='')}|{escape(c.get('title') or c['id'])}>" for c in shown]
    level = (result.get("confidence") or {}).get("level")
    if level:
        lines += ["", f"Confidence: {level}"]
    if result.get("withheld_by_policy"):
        lines += ["", f"Some sources aren't shown in Slack. For the full answer, ask in Aerugo: <{web}/#/ask|open Aerugo>"]
    return "\n".join(lines)


def question_from(client: slack.SlackClient, p: dict, user: str, bot: str | None) -> str | None:
    """The question: a mention's read back from Slack (only if it's the asker's own message), or a command's as given.
    None when the message isn't theirs."""
    if p.get("via") == "mention":
        message = client.message(p["channel"], str(p.get("ts") or ""))
        if message.get("ts") != p.get("ts") or message.get("user") != user:
            return None
        return slack.question_of(message.get("text") or "", bot)[:QUESTION_CHARS]
    return str(p.get("question") or "").strip()[:QUESTION_CHARS]


def ask(conn, job) -> dict:
    """One ask, answered privately as its asker, or told why not. Tried once: a retry would answer twice."""
    p = job["payload"]
    c, user = channel_of(conn, p), str(p.get("user") or "")
    if c is None or not slack.USER.fullmatch(user):
        return {"skipped": "not a channel this connection reads"}
    thread = str(p.get("thread") or "") or None
    if thread and not slack.TS.fullmatch(thread):
        thread = None

    def refuse(why: str) -> dict:
        tell(c, p["channel"], user, SAY[why], thread)
        return {"refused": why}

    settings = (c["config"] or {}).get("answers") or {}
    if not settings.get("model"):
        return refuse("not set up")
    client = slack.client_for(c)
    team, person = client.team(), client.user(user)
    if not person or person.get("deleted") or person.get("is_bot") or person.get("team_id") != team["id"]:
        return refuse("outsider")
    question = question_from(client, p, user, team.get("bot"))
    if question is None:
        return {"skipped": "the message isn't the asker's"}
    if not question:
        return refuse("empty")
    link = conn.execute(PERSON_SQL, (f"ext:slack.{user}",)).fetchone()
    if not link or not str(link["canonical"]).startswith("user:"):
        return refuse("unlinked")
    username = link["canonical"].split(":", 1)[1]
    slack_email = str((person.get("profile") or {}).get("email") or "").lower()
    if conn.execute(LEFT_SQL, {"user": username, "emails": [slack_email] if slack_email else []}).fetchone():
        return refuse("left")
    seen = conn.execute(SIGN_IN_SQL, (username,)).fetchone()
    if not seen or not fresh(seen["signed_in_at"], datetime.now(timezone.utc)):
        return refuse("stale")
    email = (person.get("profile") or {}).get("email") if link["verified_by"] == "sso-email" else None  # proven equal at linking
    caller = Caller(username=username, groups=tuple(seen["groups"] or ()), org_slug=config.ORG_SLUG, email=email)
    try:
        if not admission.admitted(caller):  # as their last sign-in left them; an ask never refreshes it
            return refuse("not onboarded")
    except (httpx.HTTPError, psycopg.Error, RuntimeError) as e:
        log.warning("an ask's admission couldn't be checked (%s)", type(e).__name__)
        return refuse("failed")
    from .main import answer_question  # the API's own Ask, so a Slack answer is the app's answer
    reach = frozenset(settings.get("classifications") or REACH) & frozenset(CLASSIFICATIONS)
    try:
        result = answer_question(caller, question, settings["model"], reach=reach)
    except HTTPException as e:
        return refuse({404: "no access", 429: "busy"}.get(e.status_code, "failed"))
    tell(c, p["channel"], user, reply_of(result), thread)
    conn.execute("INSERT INTO audit_events (org_id, actor, action, scope, details) VALUES (%s, %s, %s, %s, %s)",
                 (c["org_id"], "process:answers", "slack.ask", [c["team"]],
                  Jsonb({"connection_id": str(c["id"]), "channel": p["channel"], "user": user, "as": username, "via": p.get("via")})))
    return {"answered": True}


def warm() -> None:
    """Load the local embedding model before the first ask, as each API process does (M3)."""
    from .main import LOCAL_MODEL, embed
    embed("local", LOCAL_MODEL, ["warm"])
