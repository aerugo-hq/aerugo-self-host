"""Handover interviews (C4a; docs/ENTERPRISE_PLAN.md section 6): someone leaving or changing roles answers prompts built
from what they own, and the answers they choose to share become knowledge their successor and lead confirm.

Consent (decided 2026-10-23):

  - the person starts their own handover, or their lead (their manager, as the directory says) or one of the team's
    admins requests one, and it begins only when the person accepts; declining ends that request, and the requester sees
    only its status and how many answers are shared, never an answer;
  - prompts come only from work they own, built as them, so they name only what they can read: the curated pages they
    own or vouched for, the units they wrote or confirmed, the documents they wrote, and the questions people routed to
    them; then open prompts about what isn't written down. Never colleagues, never anyone's record;
  - drafts are theirs alone (the database's own rule); nothing leaves a draft until they share it, each answer with the
    audience they choose: the handover's team plus the successor and lead they named, by default, or the whole company;
    an answer about one person's health, discipline, performance, or pay, or holding personal data, is never kept;
  - they can withdraw a shared answer until they leave, which removes its unit, confirmed or not; when a directory says
    they've left, every unshared draft is deleted (`forget_leavers`);
  - in the web app only (decided: answers are kept knowledge, and a chat delivery can't be read back for them).

A shared answer becomes a document of kind `handover` (`keep`, the worker's step) with exactly its audience, under a
heading for its kind of prompt, never the prompt's own subject (a page's title the audience may not read), and a proposed
unit whose trust is what was said (observed), so no answer uses it until the named successor or lead, or the team's
curators, confirm it (kl.units, migration 0074's rule). The person's own words, shared by their choice: not synced
content, so invariant 12 doesn't reach it."""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import config, notion, privacy, units
from .auth import Caller, authenticate
from .authz import fga
from .db import resolve_org_id, scoped
from .markdown import parse as parse_markdown
from .pipelines import viewer_tuples
from .policy import SLUG, audit
from .principals import is_valid

log = logging.getLogger("kl.handover")
router = APIRouter(prefix="/v1")

MAX_OWNED = 20        # prompts about what they own, at most
CONCEPT_SCAN = 500    # ponytail: curated pages read to find the ones they own; an index of owners if curation grows past it
OPEN_PROMPTS = (
    ("routine", "What do you do regularly that isn't written down anywhere?"),
    ("contact", "Who should your successor call, and for what?"),
    ("breaks", "What breaks, and how do you fix it?"),
    ("in_flight", "What's in flight that your successor needs to pick up?"),
    ("where", "Where do things live that people can't find?"),
)
HEADINGS = {  # a shared answer's heading: its kind of prompt, never the prompt's subject
    "owned": "About something they own", "asked": "What people ask them", "routine": "What they do regularly",
    "contact": "Who to call", "breaks": "What breaks, and how it's fixed", "in_flight": "What's in flight",
    "where": "Where things live"}
UNIT_KINDS = {"breaks": "lesson", "asked": "answer"}  # every other answer is a fact: a procedure needs steps, a contact a role

PERSON_SQL = "SELECT 1 FROM people WHERE username = %s"
MANAGER_SQL = """SELECT 1 FROM people p JOIN directory_people d ON d.email = p.email AND d.active
JOIN connections c ON c.id = d.connection_id AND c.kind = 'directory' AND c.status <> 'disabled'
WHERE p.username = %s AND d.manager_email = %s LIMIT 1"""
MANAGED_BY_SQL = """SELECT m.username FROM people p JOIN directory_people d ON d.email = p.email AND d.active
JOIN connections c ON c.id = d.connection_id AND c.kind = 'directory' AND c.status <> 'disabled'
JOIN people m ON m.email = d.manager_email WHERE p.username = %s ORDER BY d.seen_at DESC LIMIT 1"""
HANDOVER_SQL = """SELECT h.id::text AS id, h.person, h.successor, h.lead, h.reason, h.status, h.requested_by, h.created_at,
       w.slug AS team FROM handovers h JOIN workspaces w ON w.id = h.team_id"""
UNITS_SQL = """SELECT u.id::text AS id, u.title, u.evidence::text[] AS evidence FROM knowledge_units u
WHERE u.status IN ('proposed', 'confirmed', 'stale') AND (u.created_by = %(me)s OR EXISTS (
  SELECT 1 FROM unit_events e WHERE e.unit_id = u.id AND e.created_by = %(me)s AND e.to_status = 'confirmed'))
ORDER BY u.updated_at DESC LIMIT 50"""
AUTHORED_SQL = """SELECT d.id::text AS id, d.title FROM document_authors a JOIN documents d ON d.id = a.document_id
JOIN principal_aliases p ON p.source = 'ext:github.' || lower(a.login) AND p.canonical = %(user)s
ORDER BY a.commits DESC, d.title LIMIT 20"""
ASKED_SQL = "SELECT question FROM routed_questions WHERE expert = %s ORDER BY created_at DESC LIMIT 10"


# ------------------------------------------------------------------ the prompts, built as the person
def owned_prompts(units_of: list[dict], pages: list[dict], authored: list[dict], asked: list[str]) -> list[tuple[str, str, str | None]]:
    """The prompts about what someone owns, each (kind, prompt, subject), at most MAX_OWNED, then the open ones: pages
    they own or vouched for, units they wrote or confirmed, documents they wrote, questions people routed to them. Pure:
    the caller passes only what the person can read."""
    out, seen = [], set()

    def add(kind: str, prompt: str, subject: str | None) -> None:
        if subject not in seen and len(out) < MAX_OWNED:
            seen.add(subject)
            out.append((kind, prompt[:600], subject))

    for p in pages:
        add("owned", f'You own or vouched for "{p["title"]}". What should your successor know about it that the page doesn\'t say?', p["key"])
    for u in units_of:
        add("owned", f'You wrote or confirmed "{u["title"]}". Is it still right, and what\'s behind it that isn\'t written down?', u["id"])
    for d in authored:
        add("owned", f'You wrote much of "{d["title"]}". What should your successor know about it?', d["id"])
    for q in asked:
        add("asked", f'People have asked you: "{q}". What\'s the answer, and where does it live?', f"asked:{q[:200]}")
    return out + [(kind, prompt, None) for kind, prompt in OPEN_PROMPTS]


def owned_pages(conn, org_id: str, caller: Caller) -> list[dict]:
    """The curated pages the caller owns or vouched for, each through the page read's own gates (visible_concept)."""
    from . import okf, originals
    from .main import visible_concept  # the page read's gates: scope, row-level security, and the authorization engine
    me = {f"user:{caller.username}", f"human:{caller.username}"}
    found = []
    for row in conn.execute("SELECT concept_key FROM concepts WHERE tier IN ('silver', 'gold') ORDER BY concept_key LIMIT %s",
                            (CONCEPT_SCAN,)).fetchall():
        c = visible_concept(conn, org_id, caller, row["concept_key"])
        if not c:
            continue
        v = conn.execute("SELECT content, key_ref FROM concept_versions WHERE concept_id = %s AND hash = %s",
                         (c["id"], c["latest_hash"])).fetchone()
        if not v:
            continue
        meta = okf.parse(originals.open_one(v["content"], v["key_ref"]), row["concept_key"]).meta
        owners = {str(o) for o in (meta.get("x-kl") or {}).get("owners") or []}
        vouched = {str(e.get("by", "")) for e in verified_entries(meta)}
        if me & (owners | vouched):
            found.append({"key": row["concept_key"], "title": c["title"]})
    return found


def verified_entries(meta: dict) -> list[dict]:
    v = meta.get("verified")
    return [v] if isinstance(v, dict) else [e for e in (v or []) if isinstance(e, dict)]


def build_prompts(conn, org_id: str, caller: Caller) -> list[tuple[str, str, str | None]]:
    """The person's prompts, built as them, so each names only what they can read."""
    mine = conn.execute(UNITS_SQL, {"me": caller.username}).fetchall()
    readable = units.readable(conn, caller, [d for u in mine for d in u["evidence"]])
    mine = [u for u in mine if units.shown(u["evidence"], set(readable))]
    authored = conn.execute(AUTHORED_SQL, {"user": f"user:{caller.username}"}).fetchall()
    opened = units.readable(conn, caller, [d["id"] for d in authored])
    authored = [d for d in authored if d["id"] in opened]
    asked = [r["question"] for r in conn.execute(ASKED_SQL, (caller.username,)).fetchall()]
    return owned_prompts(mine, owned_pages(conn, org_id, caller), authored, asked)


# ------------------------------------------------------------------ the API
class HandoverIn(BaseModel):
    team: str = Field(min_length=1, max_length=40)
    reason: Literal["leaving", "changing roles"]
    person: str | None = Field(default=None, max_length=64)     # someone else: a request, which they accept or decline
    successor: str | None = Field(default=None, max_length=64)
    lead: str | None = Field(default=None, max_length=64)


class AnswerIn(BaseModel):
    answer: str = Field(max_length=4000)
    audience: Literal["named", "org"] = "named"


def named(conn, who: str | None, what: str) -> str | None:
    """A person named on a handover: someone Aerugo knows (they've signed in), by their username."""
    if who is None:
        return None
    who = who.strip()
    if not is_valid(f"user:{who}") or not conn.execute(PERSON_SQL, (who,)).fetchone():
        raise HTTPException(422, f"the {what} must be someone who has signed in to Aerugo, by their username")
    return who


def summary(conn, row: dict, me: str) -> dict:
    progress = conn.execute("SELECT prompts, shared FROM kl.handover_progress(%s)", (row["id"],)).fetchone()
    return {"id": row["id"], "person": row["person"], "team": row["team"], "successor": row["successor"], "lead": row["lead"],
            "reason": row["reason"], "status": row["status"], "requested_by": row["requested_by"],
            "created_at": row["created_at"].isoformat(), "mine": row["person"] == me,
            "prompts": progress["prompts"] if progress else 0, "shared": progress["shared"] if progress else 0}


def open_prompts(conn, org_id: str, caller: Caller, handover: str) -> int:
    rows = build_prompts(conn, org_id, caller)
    for n, (kind, prompt, subject) in enumerate(rows):  # a microsecond apart, so they're read back in this order
        conn.execute("INSERT INTO handover_answers (org_id, handover_id, kind, prompt, subject, created_at) "
                     "VALUES (%s, %s, %s, %s, %s, now() + %s * interval '1 microsecond')", (org_id, handover, kind, prompt, subject, n))
    return len(rows)


@router.post("/handovers", status_code=201)
def start_handover(body: HandoverIn, caller: Caller = Depends(authenticate)):
    """Start your own handover (it opens at once, its prompts built as you), or request one of someone on a team you
    administer or whose manager you are (it waits for them to accept). The successor and lead are named by username;
    a request's lead is its requester unless named, and your own lead is your manager, as the directory says, unless
    named."""
    if not SLUG.fullmatch(body.team):
        raise HTTPException(404, "workspace not found")
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        team = conn.execute("SELECT id FROM workspaces WHERE slug = %s", (body.team,)).fetchone()
        person = named(conn, body.person, "person") if body.person and body.person != caller.username else caller.username
        if not team or not fga.check(f"user:{person}", "member", f"workspace:{body.team}"):
            raise HTTPException(404, "workspace not found")
        own = person == caller.username
        if not own and not (fga.check(caller.fga_user, "admin", f"workspace:{body.team}")
                            or (caller.email and conn.execute(MANAGER_SQL, (person, caller.email)).fetchone())):
            raise HTTPException(403, "only the person, their manager, or the team's admins can ask for a handover")
        successor = named(conn, body.successor, "successor")
        lead = named(conn, body.lead, "lead") or (None if own else caller.username)
        if lead is None:
            found = conn.execute(MANAGED_BY_SQL, (person,)).fetchone()
            lead = found["username"] if found else None
        if person in (successor, lead):
            raise HTTPException(422, "the successor and lead are other people")
        hid = conn.execute("INSERT INTO handovers (org_id, person, team_id, successor, lead, reason, status, requested_by) "
                           "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id::text",
                           (org_id, person, team["id"], successor, lead, body.reason, "open" if own else "requested",
                            caller.username)).fetchone()["id"]
        prompts = open_prompts(conn, org_id, caller, hid) if own else 0
        audit(conn, org_id, caller.username, "handover.start" if own else "handover.request", [body.team],
              {"handover": hid, "person": person, "successor": successor, "lead": lead, "prompts": prompts})
        row = conn.execute(HANDOVER_SQL + " WHERE h.id = %s", (hid,)).fetchone()
        return summary(conn, row, caller.username)


@router.get("/handovers")
def list_handovers(caller: Caller = Depends(authenticate)):
    """Your handovers, the ones you requested, and the ones naming you as successor or lead: each one's status and how
    many answers are shared, never an answer."""
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        rows = conn.execute(HANDOVER_SQL + " ORDER BY h.created_at DESC LIMIT 100").fetchall()
        return {"handovers": [summary(conn, r, caller.username) for r in rows]}


def id_of(raw: str, what: str) -> str:
    """An id from a path: anything that isn't one is a 404, as a missing one is."""
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        raise HTTPException(404, f"{what} not found")


def own_handover(conn, caller: Caller, handover: str, states: tuple[str, ...]) -> dict:
    """A handover that's the caller's own, in one of these states: a 404 for anyone else's (its drafts are theirs), a
    409 for the wrong state."""
    row = conn.execute(HANDOVER_SQL + " WHERE h.id = %s", (id_of(handover, "handover"),)).fetchone()
    if not row or row["person"] != caller.username:
        raise HTTPException(404, "handover not found")
    if row["status"] not in states:
        raise HTTPException(409, f"the handover is {row['status']}")
    return row


@router.post("/handovers/{handover}/accept")
def accept_handover(handover: str, caller: Caller = Depends(authenticate)):
    """Accept a handover someone asked of you: it opens, its prompts built as you."""
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        row = own_handover(conn, caller, handover, ("requested",))
        conn.execute("UPDATE handovers SET status = 'open', updated_at = now() WHERE id = %s", (row["id"],))
        prompts = open_prompts(conn, org_id, caller, row["id"])
        audit(conn, org_id, caller.username, "handover.accept", [row["team"]], {"handover": row["id"], "prompts": prompts})
    return {"id": row["id"], "status": "open", "prompts": prompts}


@router.post("/handovers/{handover}/decline")
def decline_handover(handover: str, caller: Caller = Depends(authenticate)):
    """Decline a handover someone asked of you: that request ends, and nothing of yours is kept."""
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        row = own_handover(conn, caller, handover, ("requested",))
        conn.execute("UPDATE handovers SET status = 'declined', updated_at = now() WHERE id = %s", (row["id"],))
        audit(conn, org_id, caller.username, "handover.decline", [row["team"]], {"handover": row["id"]})
    return {"id": row["id"], "status": "declined"}


ANSWERS_SQL = """SELECT id::text AS id, kind, prompt, answer, audience, shared_at, document_id IS NOT NULL AS kept
FROM handover_answers WHERE handover_id = %s ORDER BY created_at, id"""


@router.get("/handovers/{handover}/answers")
def handover_answers(handover: str, caller: Caller = Depends(authenticate)):
    """Your handover's prompts and your drafts: yours alone."""
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        row = own_handover(conn, caller, handover, ("open", "closed"))
        rows = conn.execute(ANSWERS_SQL, (row["id"],)).fetchall()
    return {"handover": row["id"], "answers": [{"id": r["id"], "kind": r["kind"], "prompt": r["prompt"], "answer": r["answer"],
                                                "audience": r["audience"], "shared": bool(r["kept"]),
                                                "shared_at": r["shared_at"].isoformat() if r["shared_at"] else None} for r in rows]}


def own_answer(conn, caller: Caller, handover: str, answer: str) -> tuple[dict, dict]:
    row = own_handover(conn, caller, handover, ("open",))
    found = conn.execute("SELECT id::text AS id, answer, audience, document_id FROM handover_answers "
                         "WHERE id = %s AND handover_id = %s", (id_of(answer, "answer"), row["id"])).fetchone()
    if not found:
        raise HTTPException(404, "answer not found")
    return row, found


@router.put("/handovers/{handover}/answers/{answer}")
def save_answer(handover: str, answer: str, body: AnswerIn, caller: Caller = Depends(authenticate)):
    """Save a draft, and who it's for when shared: the handover's team plus its successor and lead, or the whole company."""
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        _, found = own_answer(conn, caller, handover, answer)
        if found["document_id"]:
            raise HTTPException(409, "it's shared: withdraw it to change it")
        conn.execute("UPDATE handover_answers SET answer = %s, audience = %s, updated_at = now() WHERE id = %s",
                     (body.answer.strip() or None, body.audience, found["id"]))
    return {"id": found["id"], "audience": body.audience}


@router.post("/handovers/{handover}/answers/{answer}/share", status_code=202)
def share_answer(handover: str, answer: str, caller: Caller = Depends(authenticate)):
    """Share a draft as knowledge, with the audience it names: kept as a document, and proposed as a unit for the
    successor, the lead, or the team's curators to confirm. Never an answer about one person's personal matters."""
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        row, found = own_answer(conn, caller, handover, answer)
        if found["document_id"]:
            raise HTTPException(409, "it's already shared")
        if not (found["answer"] or "").strip():
            raise HTTPException(422, "write an answer before sharing it")
        if privacy.sensitive(found["answer"]):
            raise HTTPException(422, "it speaks of one person's health, discipline, performance, or pay, or holds personal "
                                     "data, so it can't be kept: take that out and share it again")
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'handover.share', %s)", (org_id, Jsonb({"answer": found["id"]})))
        audit(conn, org_id, caller.username, "handover.share", [row["team"]], {"handover": row["id"], "answer": found["id"],
                                                                              "audience": found["audience"]})
    return {"id": found["id"], "queued": True}


@router.post("/handovers/{handover}/answers/{answer}/withdraw", status_code=202)
def withdraw_answer(handover: str, answer: str, caller: Caller = Depends(authenticate)):
    """Withdraw a shared answer: its document and its unit go, confirmed or not, and it's a draft again."""
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        row, found = own_answer(conn, caller, handover, answer)
        if not found["document_id"]:
            raise HTTPException(409, "it isn't shared")
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'handover.withdraw', %s)", (org_id, Jsonb({"answer": found["id"]})))
        audit(conn, org_id, caller.username, "handover.withdraw", [row["team"]], {"handover": row["id"], "answer": found["id"]})
    return {"id": found["id"], "queued": True}


@router.get("/handovers/review")
def handovers_review(caller: Caller = Depends(authenticate)):
    """What handovers naming you as successor or lead proposed and no one has confirmed or dismissed yet."""
    return {"units": units.handover_proposals(caller)}


# ------------------------------------------------------------------ the worker's steps (kl_ingest, in one org)
SHARED_SQL = """SELECT a.id::text AS id, a.kind, a.answer, a.audience, a.document_id, h.id::text AS handover, h.person, h.successor,
       h.lead, h.status, w.id AS team_id, w.slug AS team, o.slug AS org
FROM handover_answers a JOIN handovers h ON h.id = a.handover_id JOIN workspaces w ON w.id = h.team_id JOIN orgs o ON o.id = h.org_id
WHERE a.id = %s"""
DOCUMENT_SQL = """
INSERT INTO documents (org_id, source_kind, source_uri, source_version, external_key, title, content_hash, classification,
                       acl_principals, container_ref, source_path)
VALUES (kl.current_org(), 'handover', %s, %s, %s, %s, %s, 'internal', %s, %s, NULL)
ON CONFLICT (org_id, external_key) DO NOTHING RETURNING id
"""
# Someone a directory that isn't paused lists only as having left (Q6's rule)
LEAVERS_SQL = """SELECT p.username FROM people p
JOIN directory_people d ON d.email = p.email AND d.org_id = p.org_id
JOIN connections c ON c.id = d.connection_id AND c.kind = 'directory' AND c.status <> 'disabled'
WHERE p.username IN (SELECT person FROM handovers WHERE status IN ('requested', 'open'))
GROUP BY p.username HAVING NOT bool_or(d.active)"""


def readers_for(row: dict) -> list[str]:
    """Who reads a shared answer: the handover's team (its own members and groups, never a grant to the whole company)
    plus the successor and lead it names, or everyone in the company."""
    if row["audience"] == "org":
        return [f"org:{row['org']}"]
    named_people = [f"user:{u}" for u in (row["successor"], row["lead"]) if u and is_valid(f"user:{u}")]
    return sorted(set(notion.team_readers(row["team"])) | set(named_people))


def document_of(kind: str, answer: str, person: str) -> tuple[str, str, str]:
    """A shared answer's heading, title, and text: the person's own words under a heading for its kind of prompt, never
    the prompt's own subject (a page's title its audience may not read)."""
    heading = HEADINGS[kind]
    return heading, f"Handover from {person}: {heading}"[:300], f"# {heading}\n\n{answer.strip()}\n\nHanded over by {person}."


def keep(conn, answer: str) -> dict:
    """The worker's step: a shared answer as a document with its audience and a proposed unit. Run once: an answer
    already kept is left as it is; one changed into something that can't be kept is refused."""
    row = conn.execute(SHARED_SQL, (answer,)).fetchone()
    if not row or row["document_id"] or row["status"] != "open" or not (row["answer"] or "").strip():
        return {"kept": 0}
    if privacy.sensitive(row["answer"]):
        return {"kept": 0, "refused": "sensitive"}
    readers = readers_for(row)
    if not readers:
        return {"kept": 0, "refused": "no readers"}
    key = f"handover-{answer}"
    heading, title, markdown = document_of(row["kind"], row["answer"], row["person"])
    with conn.transaction():
        doc = conn.execute(DOCUMENT_SQL, (f"aerugo://handovers/{row['handover']}/{answer}", answer, key, title,
                                          hashlib.sha256(markdown.encode()).hexdigest(), readers, f"handovers:{row['team']}")).fetchone()
        if not doc:
            return {"kept": 0}
        _, sections = parse_markdown(markdown, fallback_title=heading)
        for ordinal, (section_heading, body) in enumerate(sections):
            conn.execute("INSERT INTO chunks (org_id, document_id, ordinal, heading_path, body, acl_principals) "
                         "VALUES (kl.current_org(), %s, %s, %s, %s, %s)", (doc["id"], ordinal, section_heading, body, readers))
        conn.execute("INSERT INTO workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, kl.current_org()) "
                     "ON CONFLICT DO NOTHING", (row["team_id"], doc["id"]))
        title = row["answer"].strip().splitlines()[0][:120]
        unit = conn.execute(
            "INSERT INTO knowledge_units (org_id, owner_team, kind, topic, title, body, trust, status, evidence, sections, created_by) "
            "VALUES (kl.current_org(), %s, %s, %s, %s, %s, 'observed', 'proposed', %s::uuid[], %s, 'handover') RETURNING id",
            (row["team_id"], UNIT_KINDS.get(row["kind"], "fact"), f"handover-{answer[:8]}", title, row["answer"].strip(),
             [doc["id"]], [heading])).fetchone()
        conn.execute("UPDATE handover_answers SET shared_at = now(), document_id = %s, unit_id = %s WHERE id = %s",
                     (doc["id"], unit["id"], answer))
    fga.write(writes=viewer_tuples(key, readers))
    log.info("kept a handover answer for %s (%s)", row["team"], row["audience"])
    return {"kept": 1, "audience": row["audience"]}


def withdraw(conn, answer: str) -> dict:
    """The worker's step: a shared answer withdrawn: its document's authorization first, then every unit and perspective
    citing it, confirmed or not, and the document; it's a draft again."""
    row = conn.execute("SELECT a.document_id, d.external_key FROM handover_answers a JOIN documents d ON d.id = a.document_id "
                       "WHERE a.id = %s", (answer,)).fetchone()
    if not row:
        return {"withdrawn": 0}
    held = {(t["user"], t["relation"], t["object"]) for t in fga.read({"object": f"document:{row['external_key']}"})}
    if held:
        fga.write(deletes=held)
    with conn.transaction():
        conn.execute("DELETE FROM unit_perspectives WHERE evidence && %s::uuid[]", ([row["document_id"]],))
        units_gone = conn.execute("DELETE FROM knowledge_units WHERE evidence && %s::uuid[]", ([row["document_id"]],)).rowcount
        conn.execute("DELETE FROM documents WHERE id = %s", (row["document_id"],))
        conn.execute("UPDATE handover_answers SET shared_at = NULL, document_id = NULL, unit_id = NULL WHERE id = %s", (answer,))
    return {"withdrawn": 1, "units": units_gone}


def forget_leavers(conn) -> dict:
    """Someone a directory says has left: every unshared draft of theirs is deleted, and their handovers close. What
    they shared stays with the team, which is the point of a handover."""
    leavers = [r["username"] for r in conn.execute(LEAVERS_SQL).fetchall()]
    if not leavers:
        return {"leavers": 0, "drafts": 0}
    with conn.transaction():
        drafts = conn.execute("DELETE FROM handover_answers a USING handovers h WHERE a.handover_id = h.id AND a.shared_at IS NULL "
                              "AND h.person = ANY(%s)", (leavers,)).rowcount
        conn.execute("UPDATE handovers SET status = 'closed', updated_at = now() WHERE person = ANY(%s) AND status IN ('requested', 'open')",
                     (leavers,))
        conn.execute("INSERT INTO audit_events (org_id, actor, action, scope, details) VALUES (kl.current_org(), 'process:worker', "
                     "'handover.leavers', '{}', %s)", (Jsonb({"leavers": len(leavers), "drafts": drafts}),))
    return {"leavers": len(leavers), "drafts": drafts}
