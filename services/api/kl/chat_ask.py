"""Answering in chat, as the asker (C2d, C1b): what every bot shares, from a chat account to the API's own Ask.

The asker is the person their chat account (`ext:slack.<user id>`, `ext:microsoft.<email>`) is linked to in
principal_aliases (a link they made by signing in, never one a chat's word alone makes), with the groups of their last
verified sign-in, only while that's recent (KL_SLACK_ASK_DAYS) and the org's admission lets them in; someone a directory
lists only as having left is refused, whatever their link or sign-in. Their question then goes through the API's own Ask
(`main.answer_question`): the same row-level security, OpenFGA post-check, audit, model egress, units, and context,
narrowed to what a chat reply may carry (public and internal, unless the team's admin allows more). Each bot reads its
asker and question back from its platform, sends the answer privately, and words the refusals; neither the question nor
the answer is logged. Run in the answers lane (kl.answers_lane), as the API's own role."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
from fastapi import HTTPException

from . import admission, config
from .auth import Caller

log = logging.getLogger("kl.chat_ask")

ASK_DAYS = float(os.environ.get("KL_SLACK_ASK_DAYS", "7"))  # how recent the asker's last sign-in must be
REACH = ("public", "internal")  # what a chat reply carries unless the team's admin allows more
CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")
QUESTION_CHARS, REPLY_CHARS, SOURCES = 1000, 2800, 6
PERSON_SQL = "SELECT canonical, verified_by FROM principal_aliases WHERE source = %s"
SIGN_IN_SQL = "SELECT groups, signed_in_at FROM person_sign_ins WHERE username = %s"
# Someone a directory that isn't paused lists only as having left (Q6's rule), by their verified sign-in email or their
# chat account's: it can only refuse, so a link made another way, or a sign-in still inside its week, never outlives it.
LEFT_SQL = """SELECT 1 FROM directory_people d
JOIN connections c ON c.id = d.connection_id AND c.kind = 'directory' AND c.status <> 'disabled'
WHERE d.email = ANY(ARRAY(SELECT email FROM people WHERE username = %(user)s AND email IS NOT NULL) || %(emails)s::text[])
GROUP BY d.email HAVING NOT bool_or(d.active) LIMIT 1"""


def fresh(signed_in_at: datetime | None, now: datetime, days: float = ASK_DAYS) -> bool:
    """Whether a sign-in is recent enough to ask as its person: their groups and admission are only as current as it."""
    return signed_in_at is not None and now - signed_in_at <= timedelta(days=days)


def cited(result: dict) -> list[dict]:
    """The sources a reply names: those the answer cites (all of them if it cites none), at most SOURCES."""
    citations = result.get("citations") or []
    return ([c for c in citations if c.get("cited")] or citations)[:SOURCES]


def caller_as(conn, account: str, email: str | None) -> tuple[str | None, Caller | None]:
    """The person a chat account is linked to, as Aerugo would let them in now: (why not, None), with `why` a refusal the
    bot words for them, or (None, their caller). `email` is the one the chat platform gives the account, used only to
    refuse a leaver, and as their own when their link was made by that verified email. A meeting's review card (C3b) is
    built, and its buttons settle units, as this caller too."""
    link = conn.execute(PERSON_SQL, (account,)).fetchone()
    if not link or not str(link["canonical"]).startswith("user:"):
        return "unlinked", None
    username = link["canonical"].split(":", 1)[1]
    email = (email or "").strip().lower() or None
    if conn.execute(LEFT_SQL, {"user": username, "emails": [email] if email else []}).fetchone():
        return "left", None
    seen = conn.execute(SIGN_IN_SQL, (username,)).fetchone()
    if not seen or not fresh(seen["signed_in_at"], datetime.now(timezone.utc)):
        return "stale", None
    caller = Caller(username=username, groups=tuple(seen["groups"] or ()), org_slug=config.ORG_SLUG,
                    email=email if link["verified_by"] == "sso-email" else None)  # proven equal at linking
    try:
        if not admission.admitted(caller):  # as their last sign-in left them; a chat never refreshes it
            return "not onboarded", None
    except (httpx.HTTPError, psycopg.Error, RuntimeError) as e:
        log.warning("a chat person's admission couldn't be checked (%s)", type(e).__name__)
        return "failed", None
    return None, caller


def answer_as(conn, account: str, email: str | None, question: str, settings: dict) -> tuple[str | None, dict | None, str | None]:
    """Ask's answer for the person a chat account is linked to (`caller_as`): (why not, None, None), with `why` a refusal
    the bot words for its asker, or (None, the result, their username)."""
    why, caller = caller_as(conn, account, email)
    if why or caller is None:
        return why or "failed", None, None
    from .main import answer_question  # the API's own Ask, so a chat answer is the app's answer
    reach = frozenset(settings.get("classifications") or REACH) & frozenset(CLASSIFICATIONS)
    try:
        return None, answer_question(caller, question, settings["model"], reach=reach), caller.username
    except HTTPException as e:
        return {404: "no access", 429: "busy"}.get(e.status_code, "failed"), None, None


def warm() -> None:
    """Load the local embedding model before the first ask, as each API process does (M3)."""
    from .main import LOCAL_MODEL, embed
    embed("local", LOCAL_MODEL, ["warm"])
