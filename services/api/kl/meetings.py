"""Meeting transcripts (C3a; docs/ENTERPRISE_PLAN.md section 6): a Teams meeting whose organizer opts in has its
transcripts kept, readable only by the people in the meeting's chat, and decisions, lessons, and facts proposed from
them (F2) for those people to confirm.

Consent (decided 2026-10-22):

  - two keys: the meeting's organizer, with the company's Teams app added to the meeting, writes "@Aerugo capture on"
    in its chat; and the Teams connection's team admin has turned its Meetings setting on. A delivery only queues a job,
    which reads back from Graph the chat (a meeting's, never an ordinary group chat), the message and its author, the
    meeting's organizer, and the chat's members, and acts only if the author is the organizer: a presenter's consent to
    the app alone captures nothing;
  - the bot posts one notice in the meeting's chat when capture starts and one when it stops, and nothing else there;
  - anyone in the meeting's chat can write "@Aerugo don't capture" (or "capture off"): capture ends for the meeting (a
    series shares one chat) and every transcript kept from it, and everything proposed from them, confirmed or not, is
    removed. Teams keeps its own transcript;
  - a meeting whose chat holds a guest or someone from another tenant is never captured, nor kept while one is there.

A transcript's readers are the chat's members as Graph lists them now, which is who Teams shows the transcript to
(invariant 12), each `ext:microsoft.<email>`; a line the sensitive gate flags (C2a) is left out of every section.
Aerugo reads the meeting's transcripts and never its recordings, and of its chat only the messages that mention the
bot, one at a time, by id. Transcripts are indexed only, and polled on the connection's cadence: a captured meeting is
a container of its Teams connection, so the sync, the readers refresh, and retiring carry it."""
from __future__ import annotations

import hashlib
import logging
import re

import httpx
from psycopg.types.json import Jsonb

from . import privacy, teams, teams_bot
from .authz import fga
from .directory import GUID
from .media import CUE, SECTION_SECONDS, stamp
from .sync_items import Item, record_identities, sync_container

log = logging.getLogger("kl.meetings")

KINDS = ("decision", "lesson", "fact")  # what a meeting proposes (decided 2026-10-22: no action items)
SOURCE = "meeting"  # a transcript's source kind
NOTICE_ON = ("Aerugo now keeps this meeting's transcripts and proposes decisions, lessons, and facts from them, for the "
             "people in this chat to confirm; only they can read them. Anyone here can write \"@Aerugo don't capture\" to "
             "stop it and remove everything Aerugo kept from this meeting.")
NOTICE_OFF = "Aerugo no longer captures this meeting, and removed everything it kept from it."
SAY = {  # what the person who wrote the command is told, privately, in their 1:1 chat with the bot
    "on": "Capture is on for this meeting. Aerugo posted a notice in its chat, and keeps each transcript once Teams has it.",
    "already": "Capture is already on for this meeting.",
    "off": "Capture is off for this meeting, and everything Aerugo kept from it is removed.",
    "not captured": "Aerugo wasn't capturing this meeting, so there's nothing to remove.",
    "organizer": "Only this meeting's organizer can turn capture on. Anyone in it can write \"@Aerugo don't capture\".",
    "switched off": "Meeting capture isn't turned on for this Teams app: a team admin turns it on in Aerugo.",
    "outsiders": "This meeting's chat has a guest or someone from another organization, so Aerugo doesn't capture it.",
    "no meeting": "Aerugo couldn't find this chat's meeting in Teams.",
    "not a meeting": "Aerugo reads only meeting chats it's added to, never other group chats.",
    "help": "In a meeting's chat, the organizer can write \"@Aerugo capture on\", and anyone can write \"@Aerugo don't "
            "capture\". To ask a question, write to me in our 1:1 chat.",
}
VOICE = re.compile(r"^<v(?:\.[^\s>]*)?\s+([^>]{1,120})>(.*)$")

CONNECTION_SQL = """SELECT c.id, c.org_id, c.kind, c.status, c.config, c.credential_ref, c.workspace_id AS team_id, w.slug AS team
FROM connections c JOIN workspaces w ON w.id = c.workspace_id WHERE c.id = %s AND c.kind = 'teams' AND c.status <> 'disabled'"""
CONTAINER_SQL = "SELECT capture FROM connection_containers WHERE connection_id = %s AND external_id = %s"
TRANSCRIPTS = "SELECT id FROM documents WHERE connection_id = %(connection)s AND source_kind = 'meeting' AND container_ref = %(chat)s"
# Everything proposed from a meeting's transcripts, confirmed or not: it never outlives them, nor goes hidden and kept
FORGET_SQL = (f"DELETE FROM unit_perspectives WHERE evidence && ARRAY({TRANSCRIPTS})",
              f"DELETE FROM knowledge_units WHERE evidence && ARRAY({TRANSCRIPTS})")


def intent(text: str) -> str | None:
    """What a mention asks of the bot, from its text as Graph holds it (its mention of the app taken out): on, off, or
    None for anything else."""
    said = re.sub(r"[^a-z' ]+", " ", text.lower().replace("’", "'"))
    said = " ".join(said.split())
    if said in ("capture on", "capture this meeting"):
        return "on"
    if said in ("capture off", "don't capture", "dont capture", "do not capture"):
        return "off"
    return None


def outsiders(members: list[dict], tenant: str) -> bool:
    """Whether a chat holds anyone who isn't the company's own: a guest, someone from another tenant, or anyone Graph
    gives no company email for. Such a meeting is never captured."""
    return any(teams.member_email(m) is None or str(m.get("tenantId") or tenant).lower() != tenant.lower() for m in members)


def cues(vtt: str) -> list[tuple[float, str, str]]:
    """A Teams transcript's cues as (start in seconds, speaker, text): WebVTT, each speaker in its voice tag."""
    out, start, speaker, buf = [], None, "", []

    def flush():
        if start is not None and buf:
            out.append((start, speaker, " ".join(buf)))

    for line in vtt.replace("\r", "").split("\n") + [""]:
        m = CUE.search(line)
        if m:
            flush()
            hours, minutes, seconds, millis = (int(g or 0) for g in m.groups())
            start, speaker, buf = hours * 3600 + minutes * 60 + seconds + millis / 1000, "", []
        elif not line.strip():
            flush()
            start, buf = None, []
        elif start is not None and not line.startswith(("WEBVTT", "NOTE")):
            v = VOICE.match(line.strip())
            if v:
                speaker, line = v[1].strip(), v[2]
            text = re.sub(r"<[^>]+>", "", line).strip()
            if text:
                buf.append(text)
    return out


def sections_of(vtt: str) -> list[tuple[str | None, str]]:
    """A transcript as a section a minute or so, each headed by when it starts and each line by who spoke it, leaving
    out every line the sensitive gate flags (C2a): never kept, searched, or sent to a model."""
    out: list[tuple[str | None, str]] = []
    window = None
    for start, speaker, text in sorted(cues(vtt), key=lambda c: c[0]):
        text = " ".join(text.split())
        if not text or privacy.sensitive(text):
            continue
        line = f"{speaker}: {text}" if speaker else text
        if window is None or start - window >= SECTION_SECONDS:
            window = start
            out.append((f"At {stamp(start)}", line))
        else:
            out[-1] = (out[-1][0], out[-1][1] + "\n" + line)
    return out


def key_of(chat: str, transcript: str) -> str:
    """A transcript's document: its chat and its id, each by a hash (both hold characters a key can't)."""
    return f"meeting-{hashlib.sha256(chat.encode()).hexdigest()[:10]}-{hashlib.sha256(transcript.encode()).hexdigest()[:16]}"


def version(sections: list[tuple[str | None, str]]) -> str:
    """Changes when what's kept changes: a transcript Teams replaced, or a line the gate leaves out since."""
    return hashlib.sha256("\n".join(f"{h}\n{b}" for h, b in sections).encode()).hexdigest()


def items_for(client: teams.TeamsClient, chat: str, organizer: str, meeting: str, subject: str, acl: list[str], uri: str,
              readers_only: bool = False) -> list[Item]:
    """A meeting's transcripts, one document each. A readers refresh (S8) reads no transcript: it gives each its readers."""
    items = []
    for t in client.transcripts(organizer, meeting):
        tid = str(t.get("id") or "")
        if not teams.MEETING_PART.fullmatch(tid):
            continue
        day = str(t.get("createdDateTime") or "")[:10]
        title = f"{subject}, {day}" if day else subject
        if readers_only:
            items.append(Item(key=key_of(chat, tid), uri=uri, version="", title=title, path=subject, acl=acl, sections=lambda: (None, [])))
            continue
        sections = sections_of(client.transcript_text(organizer, meeting, tid))
        if sections:
            items.append(Item(key=key_of(chat, tid), uri=uri, version=version(sections), title=title, path=subject, acl=acl,
                              sections=lambda title=title, sections=sections: (title, sections)))
    return items


def forget(conn, connection: dict, chat: str) -> int:
    """Every unit and perspective citing a meeting's transcripts, confirmed or not: how many units went."""
    with conn.transaction():
        conn.execute(FORGET_SQL[0], {"connection": connection["id"], "chat": chat})
        return conn.execute(FORGET_SQL[1], {"connection": connection["id"], "chat": chat}).rowcount


def sync_meeting(conn, connection: dict, client: teams.TeamsClient, chat: str, ref: str | None) -> dict:
    """A captured meeting: its chat's members as its transcripts' readers, and its transcripts. A chat that now holds an
    outsider, or one the app was removed from (Graph's 403), keeps nothing: no transcript, and nothing proposed from one,
    confirmed or not."""
    organizer, _, meeting = (ref or "").partition("/")
    tenant = (connection["config"] or {})["tenant_id"]
    row = conn.execute("SELECT display_name FROM connection_containers WHERE connection_id = %s AND external_id = %s",
                       (connection["id"], chat)).fetchone()
    subject = str((row or {}).get("display_name") or "Meeting")
    try:
        if not GUID.fullmatch(organizer) or not teams.MEETING_PART.fullmatch(meeting) or not teams.MEETING_CHAT.fullmatch(chat):
            raise ValueError("unexpected meeting")
        members = client.chat_members(chat)
        if outsiders(members, tenant):
            log.info("meeting chat %s holds an outsider: it keeps nothing", chat)
            forget(conn, connection, chat)
            return sync_container(conn, connection, chat, SOURCE, [])
        web = str(client.chat(chat).get("webUrl") or "")
        items = items_for(client, chat, organizer, meeting, subject, teams.readers(members),
                          web if web.startswith("https://") else f"teams:meeting/{chat}", bool(connection.get("readers_only")))
    except httpx.HTTPStatusError as e:
        if e.response.status_code not in (403, 404):
            raise
        log.info("Teams refused meeting chat %s (%s): it keeps nothing", chat, e.response.status_code)
        forget(conn, connection, chat)
        return sync_container(conn, connection, chat, SOURCE, [])
    people = {email: {"email": email, "name": str(m.get("displayName") or "")[:200]} for m in members if (email := teams.member_email(m))}
    if people:
        record_identities(conn, connection["org_id"], "microsoft", people)
    return sync_container(conn, connection, chat, SOURCE, items)


def audit(conn, org_id, action: str, details: dict) -> None:
    conn.execute("INSERT INTO audit_events (org_id, actor, action, scope, details) VALUES (%s, %s, %s, %s, %s)",
                 (org_id, "process:worker", action, [], Jsonb(details)))


def command(conn, job) -> dict:
    """A mention of the bot in a meeting's chat, read back from Graph: "capture on" from the organizer, with the
    connection's Meetings setting on and no outsider in the chat, starts capture; "don't capture" from anyone in the
    chat ends it and removes what it kept. Whoever wrote it is told privately what became of it."""
    p = job["payload"]
    c = conn.execute(CONNECTION_SQL, (p.get("connection_id"),)).fetchone()
    chat, message, user, user_id = (str(p.get(k) or "") for k in ("chat", "message", "user", "user_id"))
    if c is None or not teams.MEETING_CHAT.fullmatch(chat) or not teams.MESSAGE.fullmatch(message) or not GUID.fullmatch(user) \
            or not teams.USER_ID.fullmatch(user_id):
        return {"skipped": "not a meeting chat this connection's bot is in"}
    cfg, user = c["config"] or {}, user.lower()

    def tell(why: str, **details) -> dict:
        teams_bot.tell(c, user_id, SAY[why])
        with conn.transaction():
            audit(conn, c["org_id"], "meetings.command", {"connection_id": str(c["id"]), "chat": chat, "user": user, "outcome": why, **details})
        return {"outcome": why}

    client = teams.client_for(c)
    try:
        held = client.chat(chat)
        if held.get("chatType") != "meeting":
            return tell("not a meeting")
        said = client.chat_message(chat, message)
        members = client.chat_members(chat)
    except httpx.HTTPStatusError as e:
        if e.response.status_code not in (403, 404):
            raise
        return {"skipped": "Graph doesn't let the app read this chat"}
    if str(said.get("id") or "") != message or teams.author(said) != user \
            or not any(str(m.get("userId") or "").lower() == user for m in members):
        return {"skipped": "the message isn't its sender's, or they aren't in the chat"}
    wanted = intent(teams.question_of(said, cfg["client_id"]))
    if wanted == "off":
        removed = remove(conn, c, chat)
        return tell("off" if removed is not None else "not captured", **(removed or {}))
    if wanted != "on":
        return tell("help")
    info = held.get("onlineMeetingInfo") or {}
    organizer = str((info.get("organizer") or {}).get("id") or "").lower()
    if not cfg.get("meetings"):
        return tell("switched off")
    if user != organizer or not GUID.fullmatch(organizer):
        return tell("organizer")
    if outsiders(members, cfg["tenant_id"]):
        return tell("outsiders")
    meeting = str(client.meeting_of(organizer, str(info.get("joinWebUrl") or "")).get("id") or "")
    if not teams.MEETING_PART.fullmatch(meeting):
        return tell("no meeting")
    subject = (str(held.get("topic") or "").strip() or "Meeting")[:300]
    with conn.transaction():
        was = conn.execute(CONTAINER_SQL, (c["id"], chat)).fetchone()
        if was and was["capture"] == "on":
            already = True
        else:
            already = False
            conn.execute(
                """INSERT INTO connection_containers (connection_id, org_id, external_id, container_kind, visibility, default_ref,
                                                      selected, display_name, capture, capture_changed_at)
                   VALUES (%s, %s, %s, 'meeting', 'private', %s, true, %s, 'on', now())
                   ON CONFLICT (connection_id, external_id) DO UPDATE SET capture = 'on', capture_changed_at = now(),
                     default_ref = EXCLUDED.default_ref, display_name = EXCLUDED.display_name, selected = true""",
                (c["id"], c["org_id"], chat, f"{organizer}/{meeting}", subject))
            conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'meetings.notice', %s)",
                         (c["org_id"], Jsonb({"connection_id": str(c["id"]), "chat": chat, "state": "on"})))
            conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.sync', %s)",
                         (c["org_id"], Jsonb({"connection_id": str(c["id"]), "reason": "meetings.capture"})))
    return tell("already" if already else "on")


def remove(conn, c: dict, chat: str) -> dict | None:
    """Capture ends for a meeting: its transcripts, authorization first, then every unit and perspective citing them,
    confirmed or not, and the meeting itself; its chat is told. None when it wasn't captured."""
    docs = conn.execute("SELECT id, external_key FROM documents WHERE connection_id = %s AND source_kind = %s AND container_ref = %s",
                        (c["id"], SOURCE, chat)).fetchall()
    held = {(t["user"], t["relation"], t["object"]) for d in docs for t in fga.read({"object": f"document:{d['external_key']}"})}
    if held:
        fga.write(deletes=held)
    ids = [d["id"] for d in docs]
    with conn.transaction():
        conn.execute(FORGET_SQL[0], {"connection": c["id"], "chat": chat})
        units = conn.execute(FORGET_SQL[1], {"connection": c["id"], "chat": chat}).rowcount
        conn.execute("DELETE FROM documents WHERE id = ANY(%s)", (ids,))
        gone = conn.execute("DELETE FROM connection_containers WHERE connection_id = %s AND external_id = %s RETURNING capture",
                            (c["id"], chat)).fetchone()
        if gone:
            conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'meetings.notice', %s)",
                         (c["org_id"], Jsonb({"connection_id": str(c["id"]), "chat": chat, "state": "off"})))
            audit(conn, c["org_id"], "meetings.removed", {"connection_id": str(c["id"]), "chat": chat, "transcripts": len(ids), "units": units})
    return {"transcripts": len(ids), "units": units} if gone else None


def stop(conn, job) -> dict:
    """The team admin turned the connection's Meetings setting off: each meeting it captured ends as a veto ends it."""
    p = job["payload"]
    c = conn.execute(CONNECTION_SQL, (p.get("connection_id"),)).fetchone()
    if c is None or not teams.MEETING_CHAT.fullmatch(str(p.get("chat") or "")):
        return {"skipped": "not a meeting this connection captures"}
    return {"removed": remove(conn, c, p["chat"])}


def notice(conn, job) -> dict:
    """The one message the bot posts in a meeting's chat when capture starts or stops, if it's still in that state."""
    p = job["payload"]
    c = conn.execute(CONNECTION_SQL, (p.get("connection_id"),)).fetchone()
    if c is None or not teams.MEETING_CHAT.fullmatch(str(p.get("chat") or "")):
        return {"skipped": "not a meeting this connection captures"}
    row = conn.execute(CONTAINER_SQL, (c["id"], p["chat"])).fetchone()
    if (p.get("state") == "on") != bool(row and row["capture"] == "on"):
        return {"skipped": "capture changed since"}
    teams_bot.bot_for(c).say(p["chat"], NOTICE_ON if p["state"] == "on" else NOTICE_OFF)
    return {"posted": p["state"]}
