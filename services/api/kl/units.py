"""Knowledge units (F1, docs/ENTERPRISE_PLAN.md section 5): one piece of knowledge each, typed (a fact, procedure,
policy, contact, decision, definition, answer, or lesson), with its evidence, scope, owner, trust, status, and
validity; the perspectives others hold on it; and conflicts between them (migration 0058).

A unit's text is derived from its evidence, so it's shown only to someone who can read every piece of it (invariant 16),
through the gates a document read uses: a team they can reach, the index's readers (the database's row-level security,
which already hides a unit whose evidence it hides), the authorization engine (a document it denies hides the unit, and
the drift is audited), and the audience rule. A perspective follows its own evidence and its unit; a conflict shows only
to someone who sees both sides. A hidden unit is a 404, and nothing counts what was left out (invariant 11).

Only the owner team's curators (and admins) write: they create units citing only documents they can read (proposed,
documented), confirm, retire, and supersede them with a new version on the same topic, add perspectives, and resolve
conflicts; a curator of either side may open one. F2's extraction writes as the worker; F3 merges; F4 answers from them.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from . import alerting, config, telemetry
from .auth import Caller, authenticate
from .authz import fga
from .db import resolve_org_id, scoped
from .okf import CONCEPT_KEY
from .policy import SLUG, audience_allows, audit, memberships, resolve_targets

log = logging.getLogger("kl.units")
router = APIRouter(prefix="/v1")

KINDS = ("fact", "procedure", "policy", "contact", "decision", "definition", "answer", "lesson")
STATUSES = ("proposed", "confirmed", "stale", "superseded", "retired")
TOPIC = r"^[a-z0-9][a-z0-9-]{1,79}$"
PLACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,39}$")
LIMIT = 100
# What each kind's structure may hold: key -> required. A procedure's steps are checked on their own (STEP_KEYS).
STRUCTURE = {
    "procedure": {"steps": True},
    "contact": {"role": True, "reach": False, "holder": False},
    "definition": {"term": True, "formula": False},
    "decision": {"decided_by": False, "decided_on": False},
    "policy": {"applies_to": False},
    "fact": {}, "answer": {}, "lesson": {},
}
STEP_KEYS = {"text": True, "role": False, "condition": False, "form": False}
MAX_STEPS = 100


def check_structure(kind: str, structure: dict) -> dict:
    """A unit's structure, as its kind allows: only that kind's keys, its required ones present, every value short text
    (a procedure's steps, 1 to 100 of them, each with its text and optionally its role, condition, and form). Empty is
    allowed for any kind; raises ValueError otherwise."""
    if not structure:
        return {}
    allowed = STRUCTURE[kind]
    unknown = sorted(set(structure) - set(allowed))
    missing = sorted(k for k, required in allowed.items() if required and k not in structure)
    if unknown or missing:
        raise ValueError(f"a {kind}'s structure holds {', '.join(allowed) or 'nothing'}"
                         + (f"; unknown: {', '.join(unknown)}" if unknown else "") + (f"; missing: {', '.join(missing)}" if missing else ""))
    out = {}
    for key, value in structure.items():
        if key == "steps":
            if not isinstance(value, list) or not 1 <= len(value) <= MAX_STEPS:
                raise ValueError(f"a procedure has 1 to {MAX_STEPS} steps")
            out[key] = [_fields(step, STEP_KEYS, "a step") for step in value]
        elif key == "decided_on":
            out[key] = date.fromisoformat(_text(value, key, 10)).isoformat()
        else:
            out[key] = _text(value, key, 500)
    return out


def _fields(item, allowed: dict, what: str) -> dict:
    if not isinstance(item, dict) or set(item) - set(allowed) or any(r and k not in item for k, r in allowed.items()):
        raise ValueError(f"{what} holds {', '.join(allowed)}, its {next(k for k, r in allowed.items() if r)} required")
    return {k: _text(v, k, 500) for k, v in item.items()}


def _text(value, key: str, longest: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > longest:
        raise ValueError(f"{key} is text of 1 to {longest} characters")
    return value.strip()


def shown(evidence: list[str], readable: set[str]) -> bool:
    """A unit or perspective is shown only when every piece of its evidence is readable: its text came from all of it."""
    return bool(evidence) and all(e in readable for e in evidence)


# ------------------------------------------------------------------ requests
class EvidenceIn(BaseModel):
    document: str = Field(min_length=1, max_length=128)  # a document's key, as search and the documents list give it
    section: str = Field("", max_length=300)             # where in it: its heading, or nothing for all of it

    @field_validator("document")
    @classmethod
    def a_key(cls, v: str) -> str:
        if not CONCEPT_KEY.fullmatch(v):
            raise ValueError("a document's key")
        return v


class ScopeIn(BaseModel):
    teams: list[str] = Field(default_factory=list, max_length=20)
    roles: list[str] = Field(default_factory=list, max_length=20)
    places: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("teams")
    @classmethod
    def team_slugs(cls, v: list[str]) -> list[str]:
        if any(not SLUG.fullmatch(t) for t in v):
            raise ValueError("teams are team slugs")
        return sorted(set(v))

    @field_validator("roles")
    @classmethod
    def role_names(cls, v: list[str]) -> list[str]:
        if any(not 1 <= len(r.strip()) <= 120 for r in v):
            raise ValueError("a role is 1 to 120 characters")
        return sorted({r.strip() for r in v})

    @field_validator("places")
    @classmethod
    def place_codes(cls, v: list[str]) -> list[str]:
        if any(not PLACE.fullmatch(p) for p in v):
            raise ValueError("a place is a code or name of up to 40 letters, digits, spaces, dashes, or underscores")
        return sorted(set(v))


class UnitIn(BaseModel):
    kind: Literal["fact", "procedure", "policy", "contact", "decision", "definition", "answer", "lesson"]
    topic: str = Field(pattern=TOPIC)
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20000)
    structure: dict = Field(default_factory=dict)
    scope: ScopeIn = Field(default_factory=ScopeIn)
    owner_role: str | None = Field(None, min_length=1, max_length=120)
    confidence: float = Field(0.5, ge=0, le=1)
    valid_from: date | None = None
    valid_until: date | None = None
    review_days: int = Field(90, ge=1, le=1825)
    evidence: list[EvidenceIn] = Field(min_length=1, max_length=50)
    supersedes: uuid.UUID | None = None  # the version this one replaces: same team, same topic


class PerspectiveIn(BaseModel):
    holder_kind: Literal["team", "role", "person"]
    holder: str = Field(min_length=1, max_length=120)
    context: str = Field("", max_length=500)
    text: str = Field(min_length=1, max_length=5000)
    evidence: list[EvidenceIn] = Field(min_length=1, max_length=20)


class ConflictIn(BaseModel):
    unit: uuid.UUID | None = None         # the other side: another unit...
    perspective: uuid.UUID | None = None  # ...or a perspective on this one
    note: str = Field("", max_length=1000)


class ResolveIn(BaseModel):
    kept: Literal["unit", "other", "both"]
    resolution: str = Field(min_length=1, max_length=2000)


# ------------------------------------------------------------------ reading
READABLE_SQL = """
SELECT d.id::text AS id, d.external_key, d.title, d.audience FROM documents d
WHERE d.id = ANY(%(ids)s::uuid[])
  AND EXISTS (SELECT 1 FROM workspace_documents wd JOIN workspaces w ON w.id = wd.workspace_id
              WHERE wd.document_id = d.id AND w.slug = ANY(%(targets)s))
"""
KEYS_SQL = "SELECT d.id::text AS id, d.external_key FROM documents d WHERE d.external_key = ANY(%(keys)s)"
UNIT_COLUMNS = """u.id::text AS id, u.kind, u.topic, u.title, u.body, u.structure, u.scope_teams, u.scope_roles, u.scope_places,
       w.slug AS owner, u.owner_role, u.trust, u.confidence, u.status, u.valid_from, u.valid_until, u.superseded_by::text AS superseded_by,
       u.review_days, u.reviewed_at, u.evidence::text[] AS evidence, u.sections, u.created_by, u.created_at, u.updated_at,
       u.duplicate_of::text AS duplicate_of"""
LIST_SQL = f"""
SELECT {UNIT_COLUMNS} FROM knowledge_units u JOIN workspaces w ON w.id = u.owner_team
WHERE (%(kind)s::text IS NULL OR u.kind = %(kind)s) AND (%(team)s::text IS NULL OR w.slug = %(team)s)
  AND (%(status)s::text IS NULL OR u.status = %(status)s) AND (%(topic)s::text IS NULL OR u.topic = %(topic)s)
  AND (%(like)s::text IS NULL OR u.title ILIKE %(like)s)
ORDER BY u.updated_at DESC, u.id LIMIT %(limit)s OFFSET %(offset)s
"""
UNIT_SQL = f"SELECT {UNIT_COLUMNS} FROM knowledge_units u JOIN workspaces w ON w.id = u.owner_team WHERE u.id = %(id)s"
VERSIONS_SQL = f"""
SELECT {UNIT_COLUMNS} FROM knowledge_units u JOIN workspaces w ON w.id = u.owner_team
WHERE u.topic = %(topic)s AND u.owner_team = (SELECT owner_team FROM knowledge_units WHERE id = %(id)s)
ORDER BY u.valid_from NULLS FIRST, u.created_at
"""
DUPLICATES_SQL = f"SELECT {UNIT_COLUMNS} FROM knowledge_units u JOIN workspaces w ON w.id = u.owner_team WHERE u.duplicate_of = %(id)s ORDER BY u.created_at"
TEAM_CONFLICTS_SQL = """
SELECT c.id::text AS id, c.unit_id::text AS unit_id, c.other_unit::text AS other_unit, c.perspective_id::text AS perspective_id,
       c.note, c.status, c.opened_by, c.created_at
FROM unit_conflicts c
JOIN knowledge_units u ON u.id = c.unit_id JOIN workspaces w ON w.id = u.owner_team
LEFT JOIN knowledge_units o ON o.id = c.other_unit LEFT JOIN workspaces wo ON wo.id = o.owner_team
WHERE (w.slug = %(ws)s OR wo.slug = %(ws)s) AND (%(status)s::text IS NULL OR c.status = %(status)s)
ORDER BY c.created_at DESC LIMIT 500
"""
PERSPECTIVES_SQL = """
SELECT p.id::text AS id, p.holder_kind, p.holder, p.context, p.body, p.evidence::text[] AS evidence, p.sections, p.created_by, p.created_at
FROM unit_perspectives p WHERE p.unit_id = %(id)s ORDER BY p.created_at
"""
CONFLICTS_SQL = """
SELECT c.id::text AS id, c.unit_id::text AS unit_id, c.other_unit::text AS other_unit, c.perspective_id::text AS perspective_id,
       c.note, c.status, c.kept, c.resolution, c.opened_by, c.resolved_by, c.resolved_at, c.created_at
FROM unit_conflicts c WHERE c.unit_id = %(id)s OR c.other_unit = %(id)s ORDER BY c.created_at
"""


def readable(conn, caller: Caller, ids) -> dict[str, dict]:
    """The documents among `ids` the caller may open, through the documents list's own gates: a team they can reach,
    the index's readers (this transaction's row-level security), the authorization engine, and the audience rule. A
    document the engine denies is drift: logged, counted, and left out."""
    ids = sorted(set(ids))
    if not ids:
        return {}
    rows = conn.execute(READABLE_SQL, {"ids": ids, "targets": resolve_targets(conn, caller, "org")}).fetchall()
    allowed = fga.batch_check(caller.fga_user, "viewer", [f"document:{r['external_key']}" for r in rows]) if rows else set()
    member_of, out, drifted = memberships(caller), {}, []
    for r in rows:
        if f"document:{r['external_key']}" not in allowed:
            drifted.append(r["external_key"])
        elif audience_allows(caller, r["audience"], member_of):
            out[r["id"]] = r
    if drifted:
        log.warning("ACL drift: index allowed %s for %s, authz engine denied", drifted, caller.username)
        telemetry.drift(len(drifted), "units")
        alerting.signal(conn, "drift", "units", len(drifted))
    return out


def _evidence(row: dict, docs: dict[str, dict]) -> list[dict]:
    return [{"document": docs[d]["external_key"], "title": docs[d]["title"], "section": s or None} for d, s in zip(row["evidence"], row["sections"])]


def _summary(row: dict) -> dict:
    return {"id": row["id"], "kind": row["kind"], "topic": row["topic"], "title": row["title"], "status": row["status"],
            "trust": row["trust"], "confidence": row["confidence"], "owner": {"team": row["owner"], "role": row["owner_role"]},
            "scope": {"teams": row["scope_teams"], "roles": row["scope_roles"], "places": row["scope_places"]},
            "valid_from": row["valid_from"] and row["valid_from"].isoformat(),
            "valid_until": row["valid_until"] and row["valid_until"].isoformat(), "updated_at": row["updated_at"].isoformat()}


def _unit_id(raw) -> str:
    try:
        return str(uuid.UUID(str(raw)))
    except ValueError:
        raise HTTPException(404, "unit not found")


def _visible_unit(conn, caller: Caller, unit_id: str) -> tuple[dict | None, dict[str, dict]]:
    """A unit the caller may read, and its evidence documents; (None, {}) when it's missing or hidden."""
    row = conn.execute(UNIT_SQL, {"id": unit_id}).fetchone()
    if not row:
        return None, {}
    docs = readable(conn, caller, row["evidence"])
    return (row, docs) if shown(row["evidence"], set(docs)) else (None, {})


@router.get("/units")
def list_units(kind: str | None = Query(None, pattern="^[a-z]{3,12}$"), team: str | None = Query(None, max_length=40),
               status: str | None = Query(None, pattern="^[a-z]{5,12}$"), topic: str | None = Query(None, pattern=TOPIC),
               q: str | None = Query(None, min_length=2, max_length=200), limit: int = Query(50, ge=1, le=LIMIT),
               offset: int = Query(0, ge=0, le=100000), caller: Caller = Depends(authenticate)):
    """The units the caller may read, newest first; filters only narrow. A copy of a unit listed here is left out (F3):
    its original stands for it, and its page names where else it's stated."""
    if team is not None and not SLUG.fullmatch(team):
        raise HTTPException(404, "workspace not found")
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" if q else None
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        rows = conn.execute(LIST_SQL, {"kind": kind, "team": team, "status": status, "topic": topic, "like": like,
                                       "limit": limit + 1, "offset": offset}).fetchall()
        more, rows = len(rows) > limit, rows[:limit]
        docs = readable(conn, caller, [d for r in rows for d in r["evidence"]])
        visible = [r for r in rows if shown(r["evidence"], set(docs))]
        listed = {r["id"] for r in visible}
        units = [_summary(r) for r in visible if r["duplicate_of"] not in listed]  # a copy, once, under its original
        audit(conn, org_id, caller.username, "unit.list", [], {"kind": kind, "team": team, "status": status, "topic": topic,
                                                                "query": q, "offset": offset, "shown": len(units)})
    return {"units": units, "next_offset": offset + limit if more else None}


@router.get("/units/{unit_id}")
def get_unit(unit_id: str, caller: Caller = Depends(authenticate)):
    """A unit, its evidence, the perspectives on it and the conflicts the caller may see, and its versions."""
    uid = _unit_id(unit_id)
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        row, docs = _visible_unit(conn, caller, uid)
        detail = _detail(conn, caller, uid, row, docs) if row else None
        if row:
            audit(conn, org_id, caller.username, "unit.read", [row["owner"]], {"unit": uid})
    if detail is None:
        raise HTTPException(404, "unit not found")
    return detail


def _detail(conn, caller: Caller, uid: str, row: dict, docs: dict[str, dict]) -> dict:
    """A readable unit in full: each perspective, version, and conflict's other side shown only when the caller can read
    all of its own evidence too."""
    perspectives = conn.execute(PERSPECTIVES_SQL, {"id": uid}).fetchall()
    versions = conn.execute(VERSIONS_SQL, {"topic": row["topic"], "id": uid}).fetchall()
    copies = conn.execute(DUPLICATES_SQL, {"id": uid}).fetchall()
    original = conn.execute(UNIT_SQL, {"id": row["duplicate_of"]}).fetchone() if row["duplicate_of"] else None
    conflicts = conn.execute(CONFLICTS_SQL, {"id": uid}).fetchall()
    other_ids = [c["other_unit"] if c["unit_id"] == uid else c["unit_id"] for c in conflicts if c["other_unit"]]
    others = conn.execute(f"SELECT {UNIT_COLUMNS} FROM knowledge_units u JOIN workspaces w ON w.id = u.owner_team "
                          "WHERE u.id = ANY(%s::uuid[])", (other_ids,)).fetchall()
    seen = readable(conn, caller, [d for r in [*perspectives, *versions, *others, *copies, *([original] if original else [])]
                                   for d in r["evidence"]]) | docs
    perspectives = [p for p in perspectives if shown(p["evidence"], set(seen))]
    shown_others = {o["id"]: o for o in others if shown(o["evidence"], set(seen))}
    mine = {p["id"] for p in perspectives}
    out_conflicts = []
    for c in conflicts:
        other_id = c["other_unit"] if c["unit_id"] == uid else c["unit_id"]
        if c["perspective_id"]:
            if str(c["perspective_id"]) not in mine:
                continue
            other = {"kind": "perspective", "id": str(c["perspective_id"])}
        elif other_id in shown_others:
            other = {"kind": "unit", "id": other_id, "title": shown_others[other_id]["title"]}
        else:
            continue
        out_conflicts.append({"id": c["id"], "unit": c["unit_id"], "status": c["status"], "note": c["note"], "other": other, "kept": c["kept"],
                              "resolution": c["resolution"], "opened_by": c["opened_by"], "resolved_by": c["resolved_by"],
                              "resolved_at": c["resolved_at"] and c["resolved_at"].isoformat()})
    return {**_summary(row), "text": row["body"], "structure": row["structure"], "superseded_by": row["superseded_by"],
            "review_days": row["review_days"], "reviewed_at": row["reviewed_at"] and row["reviewed_at"].isoformat(),
            "created_by": row["created_by"], "created_at": row["created_at"].isoformat(), "evidence": _evidence(row, docs),
            "perspectives": [{"id": p["id"], "holder": {"kind": p["holder_kind"], "name": p["holder"]}, "context": p["context"],
                              "text": p["body"], "evidence": _evidence(p, seen), "created_by": p["created_by"]} for p in perspectives],
            "conflicts": out_conflicts,
            "duplicate_of": original["id"] if original and shown(original["evidence"], set(seen)) else None,  # never a hidden one
            "duplicates": [{"id": d["id"], "title": d["title"], "owner": d["owner"], "evidence": _evidence(d, seen)}
                           for d in copies if shown(d["evidence"], set(seen))],
            "versions": [{**_summary(v), "superseded_by": v["superseded_by"]} for v in versions if shown(v["evidence"], set(seen))]}


# ------------------------------------------------------------------ writing
def require_curator(caller: Caller, ws: str) -> None:
    """Only a team's curators and admins (org admins too) write its units."""
    if not SLUG.fullmatch(ws):
        raise HTTPException(404, "workspace not found")
    if not fga.check(caller.fga_user, "curator", f"workspace:{ws}"):
        raise HTTPException(403, "a curator of the team required")


def cited(conn, caller: Caller, evidence: list[EvidenceIn]) -> tuple[list[str], list[str]]:
    """Evidence as document ids and sections, refused unless the caller can read every piece: they can't cite what they
    can't open. A key that doesn't exist and one they can't read are refused alike."""
    keys = [e.document for e in evidence]
    ids = {r["external_key"]: r["id"] for r in conn.execute(KEYS_SQL, {"keys": keys}).fetchall()}
    ok = readable(conn, caller, ids.values())
    refused = sorted({k for k in keys if ids.get(k) not in ok})
    if refused:
        raise HTTPException(422, "evidence must be documents you can read: " + ", ".join(refused[:10]))
    if len(set(keys)) != len(keys):
        raise HTTPException(422, "each document is cited once")
    return [ids[k] for k in keys], [e.section.strip() for e in evidence]


def _owned(conn, caller: Caller, unit_id: str) -> dict:
    """A unit the caller may read and curates the owner of: 404 when it's hidden, 403 when it isn't theirs to change."""
    row, _ = _visible_unit(conn, caller, unit_id)
    if not row:
        raise HTTPException(404, "unit not found")
    require_curator(caller, row["owner"])
    return row


@router.post("/workspaces/{ws}/units", status_code=201)
def create_unit(ws: str, body: UnitIn, caller: Caller = Depends(authenticate)):
    """A unit owned by a team, written by one of its curators from documents they can read: proposed, and documented
    (its owner's confirmation makes it confirmed). With `supersedes`, it's the next version of that unit (same team and
    topic), which then ends where this one begins."""
    require_curator(caller, ws)
    try:
        structure = check_structure(body.kind, body.structure)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if body.valid_from and body.valid_until and body.valid_until < body.valid_from:
        raise HTTPException(422, "valid_until is on or after valid_from")
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals, teams=[ws]) as conn:
        team = conn.execute("SELECT id FROM workspaces WHERE slug = %s", (ws,)).fetchone()
        if not team:
            raise HTTPException(404, "workspace not found")
        evidence, sections = cited(conn, caller, body.evidence)
        old = None
        if body.supersedes:
            old, _ = _visible_unit(conn, caller, str(body.supersedes))
            if not old:
                raise HTTPException(404, "unit not found")
            if old["owner"] != ws or old["topic"] != body.topic or old["status"] in ("superseded", "retired"):
                raise HTTPException(422, "a new version is the same team's, on the same topic, of a unit still in use")
        new = conn.execute(
            "INSERT INTO knowledge_units (org_id, owner_team, owner_role, kind, topic, title, body, structure, scope_teams, scope_roles, "
            "scope_places, trust, confidence, status, valid_from, valid_until, review_days, evidence, sections, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'documented', %s, 'proposed', %s, %s, %s, %s::uuid[], %s, %s) RETURNING id::text",
            (org_id, team["id"], body.owner_role, body.kind, body.topic, body.title, body.text, Jsonb(structure), body.scope.teams,
             body.scope.roles, body.scope.places, body.confidence, body.valid_from, body.valid_until, body.review_days,
             evidence, sections, caller.username)).fetchone()["id"]
        if old:
            conn.execute("UPDATE knowledge_units SET status = 'superseded', superseded_by = %s, updated_at = now(), "
                         "valid_until = coalesce(%s::date - 1, valid_until, current_date) WHERE id = %s",
                         (new, body.valid_from, old["id"]))
        audit(conn, org_id, caller.username, "unit.create", [ws], {"unit": new, "kind": body.kind, "topic": body.topic,
                                                                   "supersedes": old and old["id"], "evidence": len(evidence)})
    return {"id": new}


@router.post("/units/{unit_id}/confirm")
def confirm_unit(unit_id: str, caller: Caller = Depends(authenticate)):
    """The owner team's curator confirms a unit: trust confirmed, reviewed now."""
    return _set_status(unit_id, caller, "confirmed", "unit.confirm")


@router.post("/units/{unit_id}/retire")
def retire_unit(unit_id: str, caller: Caller = Depends(authenticate)):
    """The owner team's curator retires a unit: kept, with its history, but no longer in use."""
    return _set_status(unit_id, caller, "retired", "unit.retire")


def _set_status(unit_id: str, caller: Caller, status: str, action: str) -> dict:
    uid = _unit_id(unit_id)
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        row = _owned(conn, caller, uid)
    if row["status"] in ("superseded", "retired"):
        raise HTTPException(409, f"the unit is {row['status']}")
    with scoped(org_id, caller.principals, teams=[row["owner"]]) as conn:
        trust = "confirmed" if status == "confirmed" else row["trust"]
        conn.execute("UPDATE knowledge_units SET status = %s, trust = %s, updated_at = now(), "
                     "reviewed_at = CASE WHEN %s = 'confirmed' THEN now() ELSE reviewed_at END WHERE id = %s",
                     (status, trust, status, uid))
        audit(conn, org_id, caller.username, action, [row["owner"]], {"unit": uid})
    return {"id": uid, "status": status, "trust": trust}


@router.post("/units/{unit_id}/perspectives", status_code=201)
def add_perspective(unit_id: str, body: PerspectiveIn, caller: Caller = Depends(authenticate)):
    """Another team's, role's, or person's version of a unit, recorded by the unit's owner team with its own evidence:
    kept beside the unit, not flattened into it."""
    uid = _unit_id(unit_id)
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        row = _owned(conn, caller, uid)
    with scoped(org_id, caller.principals, teams=[row["owner"]]) as conn:
        evidence, sections = cited(conn, caller, body.evidence)
        pid = conn.execute("INSERT INTO unit_perspectives (org_id, unit_id, holder_kind, holder, context, body, evidence, sections, created_by) "
                           "VALUES (%s, %s, %s, %s, %s, %s, %s::uuid[], %s, %s) RETURNING id::text",
                           (org_id, uid, body.holder_kind, body.holder.strip(), body.context.strip(), body.text, evidence, sections,
                            caller.username)).fetchone()["id"]
        audit(conn, org_id, caller.username, "unit.perspective", [row["owner"]], {"unit": uid, "perspective": pid})
    return {"id": pid}


@router.post("/units/{unit_id}/conflicts", status_code=201)
def open_conflict(unit_id: str, body: ConflictIn, caller: Caller = Depends(authenticate)):
    """Two sides that disagree: this unit and another unit, or a perspective on it. A curator of either unit's owner
    team opens it; both sides must be ones the caller can read."""
    if (body.unit is None) == (body.perspective is None):
        raise HTTPException(422, "name the other side: a unit or a perspective")
    uid = _unit_id(unit_id)
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        row, _ = _visible_unit(conn, caller, uid)
        other = None
        if row and body.unit:
            other, _ = _visible_unit(conn, caller, str(body.unit))
        elif row:
            p = conn.execute(PERSPECTIVES_SQL, {"id": uid}).fetchall()
            other = next((x for x in p if x["id"] == str(body.perspective)), None)
            other = other if other and shown(other["evidence"], set(readable(conn, caller, other["evidence"]))) else None
    if not row or not other:
        raise HTTPException(404, "unit not found")
    if body.unit and str(body.unit) == uid:
        raise HTTPException(422, "a unit doesn't conflict with itself")
    teams = [t for t in dict.fromkeys([row["owner"], other.get("owner")]) if t and fga.check(caller.fga_user, "curator", f"workspace:{t}")]
    if not teams:
        raise HTTPException(403, "a curator of either side's team required")
    with scoped(org_id, caller.principals, teams=teams) as conn:
        cid = conn.execute("INSERT INTO unit_conflicts (org_id, unit_id, other_unit, perspective_id, note, opened_by) "
                           "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id::text",
                           (org_id, uid, body.unit and str(body.unit), body.perspective and str(body.perspective), body.note.strip(),
                            caller.username)).fetchone()["id"]
        audit(conn, org_id, caller.username, "unit.conflict", teams, {"unit": uid, "conflict": cid})
    return {"id": cid}


SUPERSEDE_SQL = """
UPDATE knowledge_units l SET status = 'superseded', superseded_by = %(winner)s, updated_at = now(),
       valid_until = least(coalesce(l.valid_until, greatest(coalesce(l.valid_from, current_date - 1), current_date - 1)),
                           greatest(coalesce(l.valid_from, current_date - 1), current_date - 1))
WHERE l.id = %(loser)s AND l.status <> ALL('{superseded,retired}')
  AND l.owner_team = (SELECT id FROM workspaces WHERE slug = %(team)s)
  AND (SELECT owner_team FROM knowledge_units WHERE id = %(winner)s) = l.owner_team
RETURNING l.id::text AS id
"""


@router.get("/workspaces/{ws}/conflicts")
def team_conflicts(ws: str, status: str | None = Query("open", pattern="^(open|resolved)$"), caller: Caller = Depends(authenticate)):
    """A team's conflicts (F3: what consolidation found, and what people opened), for its curators to resolve: those on
    its units whose both sides the caller can read, so a conflict never reveals a side they can't."""
    require_curator(caller, ws)
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        rows = conn.execute(TEAM_CONFLICTS_SQL, {"ws": ws, "status": status}).fetchall()
        ids = sorted({x for r in rows for x in (r["unit_id"], r["other_unit"]) if x})
        units = {u["id"]: u for u in conn.execute(f"SELECT {UNIT_COLUMNS} FROM knowledge_units u JOIN workspaces w ON w.id = u.owner_team "
                                                  "WHERE u.id = ANY(%s::uuid[])", (ids,)).fetchall()}
        views = {r["unit_id"]: conn.execute(PERSPECTIVES_SQL, {"id": r["unit_id"]}).fetchall() for r in rows if r["perspective_id"]}
        persp = {p["id"]: p for ps in views.values() for p in ps}
        docs = readable(conn, caller, [d for x in [*units.values(), *persp.values()] for d in x["evidence"]])
        side = lambda u: u is not None and shown(u["evidence"], set(docs))  # noqa: E731
        out = []
        for r in rows:
            first = units.get(r["unit_id"])
            other = units.get(r["other_unit"]) if r["other_unit"] else persp.get(r["perspective_id"])
            if side(first) and side(other):
                out.append({"id": r["id"], "status": r["status"], "note": r["note"], "opened_by": r["opened_by"],
                            "created_at": r["created_at"].isoformat(), "unit": {"id": first["id"], "title": first["title"], "owner": first["owner"]},
                            "other": ({"kind": "unit", "id": other["id"], "title": other["title"], "owner": other["owner"]} if r["other_unit"]
                                      else {"kind": "perspective", "id": r["perspective_id"], "holder": other["holder"]})})
        audit(conn, org_id, caller.username, "unit.conflicts", [ws], {"status": status, "shown": len(out)})
    return {"conflicts": out}


@router.post("/units/conflicts/{conflict_id}/resolve")
def resolve_conflict(conflict_id: str, body: ResolveIn, caller: Caller = Depends(authenticate)):
    """The first unit's owner team resolves a conflict: which side was kept, and why, recorded. When both sides are its
    own units, the side not kept is superseded by the one kept, ending the day before (F3); another team's unit is never
    changed, so across teams the resolution is recorded and both perspectives stay."""
    cid = _unit_id(conflict_id)
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        c = conn.execute("SELECT unit_id::text AS unit_id, other_unit::text AS other_unit, perspective_id, status FROM unit_conflicts WHERE id = %s",
                         (cid,)).fetchone()
        sides = [c["unit_id"]] + ([c["other_unit"]] if c and c["other_unit"] else []) if c else []
        visible = c and all(_visible_unit(conn, caller, s)[0] for s in sides)
        if visible and c["perspective_id"]:
            p = next((x for x in conn.execute(PERSPECTIVES_SQL, {"id": c["unit_id"]}).fetchall() if x["id"] == str(c["perspective_id"])), None)
            visible = bool(p) and shown(p["evidence"], set(readable(conn, caller, p["evidence"])))
        row = _visible_unit(conn, caller, c["unit_id"])[0] if visible else None
    if not row:
        raise HTTPException(404, "conflict not found")
    require_curator(caller, row["owner"])
    if c["status"] == "resolved":
        raise HTTPException(409, "the conflict is resolved")
    with scoped(org_id, caller.principals, teams=[row["owner"]]) as conn:
        conn.execute("UPDATE unit_conflicts SET status = 'resolved', kept = %s, resolution = %s, resolved_by = %s, resolved_at = now() "
                     "WHERE id = %s", (body.kept, body.resolution.strip(), caller.username, cid))
        superseded = None
        if c["other_unit"] and body.kept != "both":
            winner, loser = (c["unit_id"], c["other_unit"]) if body.kept == "unit" else (c["other_unit"], c["unit_id"])
            superseded = conn.execute(SUPERSEDE_SQL, {"winner": winner, "loser": loser, "team": row["owner"]}).fetchone()
            superseded = superseded and superseded["id"]
        audit(conn, org_id, caller.username, "unit.resolve", [row["owner"]], {"conflict": cid, "kept": body.kept, "superseded": superseded})
    return {"id": cid, "status": "resolved", "kept": body.kept, "superseded": superseded}
