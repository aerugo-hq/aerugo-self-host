"""A meeting's people confirm what it proposed, in Teams (C3b; docs/ENTERPRISE_PLAN.md section 6).

When a meeting's proposals are ready, its organizer gets one review card, privately, in their 1:1 chat with the bot;
anyone in the meeting who writes "@Aerugo review" in its chat gets the same (decided 2026-10-22: the bot's only writes
besides capture's notices and private replies). Each card is built here, in the answers lane, as its person: who they
are is read back from the Bot Connector (the person with that Teams id in the meeting's chat, or in their own 1:1
chat), they're the Aerugo user their Microsoft account is linked to, let in now as an ask would let them in
(kl.chat_ask.caller_as), and the card lists only the meeting's proposals they read in full (kl.units), each with Confirm
and Not right. A press arrives as a signed delivery whose button data names the unit; its presser is read back again,
and the unit is settled as them, only if it came from meetings alone. Card text is the units' own, with Markdown's link
characters replaced, so nothing in it becomes a link with a hidden address."""
from __future__ import annotations

import logging

from psycopg.types.json import Jsonb

from . import chat_ask, teams, teams_ask, teams_bot, units
from .directory import EMAIL, GUID

log = logging.getLogger("kl.meeting_review")

MAX_CARD_UNITS = 10  # proposals on one card; "@Aerugo review" again shows the rest once these are settled
TITLE_CHARS, TEXT_CHARS = 120, 400
CONTAINER_SQL = """SELECT display_name, default_ref, organizer_ref FROM connection_containers
WHERE connection_id = %s AND external_id = %s AND container_kind = 'meeting' AND capture = 'on'"""
SAY = {
    "not captured": "Aerugo isn't capturing this meeting, so it has nothing from it to review.",
    "nothing": "Nothing from this meeting is waiting for you to confirm.",
    "confirmed": "Confirmed: {title}. Aerugo can answer from it now, for the people in its meeting.",
    "dismissed": "Marked not right: {title}. Aerugo won't answer from it.",
    "already": "That one's already been confirmed or marked not right.",
    "not yours": "That isn't something proposed from a meeting you were in.",
}


def plain(text: str, limit: int) -> str:
    """Card text that can't become a link or markup: Adaptive Cards render a subset of Markdown, so the characters a link
    is made of ([text](address), <address>) are replaced, and whitespace is one space."""
    text = " ".join(str(text or "").split()).translate(str.maketrans({"[": "(", "]": ")", "<": "(", ">": ")"}))
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def card_of(meeting: str, found: list[dict]) -> dict:
    """The review card: what the meeting proposed, each with Confirm and Not right, at most MAX_CARD_UNITS."""
    body = [{"type": "TextBlock", "text": f"Proposed from {plain(meeting, TITLE_CHARS)}", "weight": "Bolder", "size": "Medium", "wrap": True},
            {"type": "TextBlock", "wrap": True, "isSubtle": True,
             "text": "Confirm what's right, so Aerugo can answer from it, or mark what isn't. Only the people in this meeting see these."}]
    for u in found[:MAX_CARD_UNITS]:
        press = lambda verb: {"aerugo": "review", "verb": verb, "unit": u["id"]}
        body += [{"type": "TextBlock", "text": plain(u["title"], TITLE_CHARS), "weight": "Bolder", "wrap": True, "separator": True, "spacing": "Medium"},
                 {"type": "TextBlock", "text": plain(u["text"], TEXT_CHARS), "wrap": True},
                 {"type": "ActionSet", "actions": [{"type": "Action.Submit", "title": "Confirm", "data": press("confirm")},
                                                   {"type": "Action.Submit", "title": "Not right", "data": press("dismiss")}]}]
    if len(found) > MAX_CARD_UNITS:
        body.append({"type": "TextBlock", "wrap": True, "isSubtle": True,
                     "text": f"And {len(found) - MAX_CARD_UNITS} more: write \"@Aerugo review\" in the meeting's chat once these are settled."})
    return {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.4", "body": body}


def person_of(c: dict, conversation: str, user_id: str, user: str) -> str | None:
    """The company email of the person with this Teams id in this conversation, as Teams says now, when they're who the
    delivery or the meeting names, in the connection's tenant, and not a guest; None otherwise."""
    person = teams_bot.bot_for(c).member(conversation, user_id)
    email = str(person.get("email") or person.get("userPrincipalName") or "").strip().lower()
    if str(person.get("aadObjectId") or "").lower() != user.lower() or str(person.get("tenantId") or "").lower() != c["config"]["tenant_id"] \
            or str(person.get("userRole") or "user").lower() == "guest" or not EMAIL.fullmatch(email):
        return None
    return email


def audit(conn, c: dict, action: str, details: dict) -> None:
    conn.execute("INSERT INTO audit_events (org_id, actor, action, scope, details) VALUES (%s, %s, %s, %s, %s)",
                 (c["org_id"], "process:answers", action, [c["team"]], Jsonb({"connection_id": str(c["id"]), **details})))


def review(conn, job) -> dict:
    """A meeting's review card for one of its people: its organizer once its proposals are ready, or anyone in it who
    asked. Tried once: a retry would send it twice."""
    p = job["payload"]
    c = conn.execute(teams_ask.CONNECTION_SQL, (p.get("connection_id"),)).fetchone()
    chat, user, user_id, asked = str(p.get("chat") or ""), str(p.get("user") or ""), str(p.get("user_id") or ""), p.get("via") == "asked"
    if c is None or not teams.MEETING_CHAT.fullmatch(chat) or not GUID.fullmatch(user) or not teams.USER_ID.fullmatch(user_id):
        return {"skipped": "not a meeting this connection's bot is in"}

    def tell(text: str, outcome: str) -> dict:
        if asked:  # the organizer's own card says nothing when there's nothing to send
            teams_bot.tell(c, user_id, text)
        return {"outcome": outcome}

    held = conn.execute(CONTAINER_SQL, (c["id"], chat)).fetchone()
    if held is None or not (c["config"] or {}).get("meetings"):
        return tell(SAY["not captured"], "not captured")
    email = person_of(c, chat, user_id, user)
    if email is None:
        return tell(teams_ask.SAY["outsider"], "outsider")
    why, caller = chat_ask.caller_as(conn, f"ext:microsoft.{email}", email)
    if why or caller is None:
        return tell(teams_ask.SAY.get(why or "failed", teams_ask.SAY["failed"]), why or "failed")
    found = units.meeting_proposals(caller, str(c["id"]), chat)
    if not found:
        return tell(SAY["nothing"], "nothing")
    teams_bot.bot_for(c).card(user_id, card_of(held["display_name"] or "the meeting", found))
    audit(conn, c, "meetings.review.card", {"chat": chat, "user": user, "as": caller.username, "units": len(found), "via": p.get("via")})
    return {"sent": len(found)}


def settle(conn, job) -> dict:
    """A press of Confirm or Not right on a review card: its presser read back from their own 1:1 chat with the bot, the
    unit settled as them (kl.units.settle_from_meeting), and they're told what became of it. Tried once."""
    p = job["payload"]
    c = conn.execute(teams_ask.CONNECTION_SQL, (p.get("connection_id"),)).fetchone()
    user, user_id, chat = str(p.get("user") or ""), str(p.get("user_id") or ""), str(p.get("conversation") or "")
    if c is None or not GUID.fullmatch(user) or not teams.USER_ID.fullmatch(user_id) or not chat.startswith("a:") \
            or not teams.CONVERSATION.fullmatch(chat) or not GUID.fullmatch(str(p.get("unit") or "")) or p.get("verb") not in ("confirm", "dismiss"):
        return {"skipped": "not a press this connection's bot can read"}
    email = person_of(c, chat, user_id, user)
    if email is None:
        return {"skipped": "the presser isn't who the delivery names, in their own chat"}
    why, caller = chat_ask.caller_as(conn, f"ext:microsoft.{email}", email)
    if why or caller is None:
        teams_bot.tell(c, user_id, teams_ask.SAY.get(why or "failed", teams_ask.SAY["failed"]))
        return {"refused": why or "failed"}
    outcome, title = units.settle_from_meeting(caller, p["unit"], p["verb"])
    teams_bot.tell(c, user_id, SAY[outcome].format(title=plain(title, TITLE_CHARS)))
    audit(conn, c, "meetings.review.press", {"user": user, "as": caller.username, "unit": p["unit"], "verb": p["verb"], "outcome": outcome})
    return {"outcome": outcome}
