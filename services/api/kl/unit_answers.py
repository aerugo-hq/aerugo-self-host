"""Answers from knowledge units first (F4, docs/ENTERPRISE_PLAN.md section 5): the units that answer a question lead
Ask's sources, ahead of documents.

A unit is used only when it's in force: confirmed (or stale, ranked last and said so), or written by a curator from its
evidence (proposed, documented); never a model's proposal no one confirmed (inferred), never one superseded, retired,
or outside its dates, and never a copy whose original is already used (F3). It's found by the question's content words
(the claim checker's), each weighed by how rare it is among the units that state any of them (a word every candidate
states tells them apart least): at least MIN_SHARED of them, and MIN_SHARE of their weight, in its title and text. Then, through
F1's rule, only when the asker reads every piece of its evidence; and it goes to a model only when the model may
receive every one of its evidence's classifications (invariant 7).

Among those, the closer match (to a tenth) comes first, then the unit that fits the asker's scope most closely, then
the more trusted, then the one in force most recently. Scope is the asker's context (kl.context, F8): their teams, and
their role and place from the directory. A unit's scope says whom it applies to, not who may ask, so a unit for
another role or place still answers (a people partner asking a store associate's pay range), its scope stated, unless
the asker's own version answers at least as well: then the other is dropped (a Colorado associate asking about their
sick leave gets Colorado's rule, never Oregon's). One the directory doesn't say is open. A contact names a role; its
current holder for this asker is read from the directory (F8).

A unit becomes a source as its text, its structure (a procedure's numbered steps, each with its role and condition; a
contact's role and how to reach it), its scope, and the perspectives on it the asker reads (another team's view, said
as theirs), and a citation that carries the same, so a client can show a procedure as a checklist and a contact as a
card."""
from __future__ import annotations

import math
import uuid
from datetime import date

from . import claims, context as asking
from .units import PERSPECTIVES_SQL, UNIT_COLUMNS, readable, shown

UNIT_SOURCES = 3   # units that may lead an answer's sources, ahead of the documents
MIN_SHARED = 2     # content words of the question a unit must state...
MIN_SHARE = 0.5    # ...and the share of their weight
CANDIDATES = 200
TRUST = {"confirmed": 0, "documented": 1, "observed": 2, "inferred": 3}
LEVELS = ("public", "internal", "confidential", "restricted")
# Words a question asks with, not about: "How much sick leave do I earn?" is about sick leave and earning.
QUESTION_WORDS = {"how", "much", "many", "does", "did", "why", "where", "whom", "whose", "get", "got", "need", "tell",
                  "know", "mine", "happen", "happens", "doe"}
PREFIX = "unit:"

FIND_SQL = f"""
SELECT {UNIT_COLUMNS} FROM knowledge_units u JOIN workspaces w ON w.id = u.owner_team
WHERE u.search @@ to_tsquery('english', %(q)s)
  AND (u.status IN ('confirmed', 'stale') OR (u.status = 'proposed' AND u.trust = 'documented'))
  AND u.superseded_by IS NULL
  AND (u.valid_from IS NULL OR u.valid_from <= current_date) AND (u.valid_until IS NULL OR u.valid_until >= current_date)
ORDER BY ts_rank_cd(u.search, to_tsquery('english', %(q)s)) DESC, u.id LIMIT {CANDIDATES}
"""
LEVELS_SQL = "SELECT id::text AS id, classification FROM documents WHERE id = ANY(%s::uuid[])"
BY_ID_SQL = f"SELECT {UNIT_COLUMNS} FROM knowledge_units u JOIN workspaces w ON w.id = u.owner_team WHERE u.id = ANY(%s::uuid[])"


def question_words(question: str) -> set[str]:
    return claims.words(question) - QUESTION_WORDS


def unit_words(row: dict) -> set[str]:
    """A unit's content words: its title, its text, and what its structure names (a contact's role, a term, the steps)."""
    structure = row.get("structure") or {}
    named = " ".join([structure.get("role") or "", structure.get("term") or "", *(s.get("text", "") for s in structure.get("steps") or [])])
    return claims.words(f"{row['title']} {claims.MARK.sub('', row['body'])} {named}")


def weights(words: set[str], texts: list[set[str]]) -> dict[str, float]:
    """Each question word's weight among these units: a word fewer of them state weighs more; one none states, most."""
    n = len(texts)
    return {w: math.log(1 + n / max(1, sum(1 for t in texts if w in t))) for w in words}


def match(words: set[str], stated: set[str], weight: dict[str, float]) -> float | None:
    """The weighted share of the question's words a unit states, or None when it states too few."""
    shared = words & stated
    total = sum(weight[w] for w in words)
    share = sum(weight[w] for w in shared) / total if total else 0.0
    return share if len(shared) >= MIN_SHARED and share >= MIN_SHARE else None


def scope_fit(row: dict, context: dict) -> tuple[int, set[str], set[str]]:
    """How closely a unit fits the asker: +1 for each of its scopes (teams, roles, places) that names the asker's, 0 for
    one it leaves open, -1 for one that names someone else; with which scopes named the asker's, and which another's.
    A context entry of None isn't known, and fits as open."""
    fit, on, off = 0, set(), set()
    for key, column in (("teams", "scope_teams"), ("roles", "scope_roles"), ("places", "scope_places")):
        wanted, mine = {s.lower() for s in row[column] or []}, context.get(key)
        if not wanted or mine is None:
            continue
        mine = {m.lower() for m in mine}
        if wanted & mine or (key == "roles" and any(asking.role_matches(w, m) for w in wanted for m in mine)):
            fit, on = fit + 1, on | {key}
        else:
            fit, off = fit - 1, off | {key}
    return fit, on, off


def ranked(question: str, rows: list[dict], context: dict) -> list[dict]:
    """The rows that answer the question, best first, each with its match and fit."""
    words = question_words(question)
    stated = [(row, unit_words(row)) for row in rows]
    stated = [(row, ws) for row, ws in stated if ws & words]
    if not words or not stated:
        return []
    weight = weights(words, [ws for _, ws in stated])
    out = []
    for row, ws in stated:
        share = match(words, ws, weight)
        if share is not None:
            fit, on, off = scope_fit(row, context)
            out.append({**row, "match": share, "fit": fit, "on": on, "off": off})
    own = {key: max((r["match"] for r in out if key in r["on"]), default=None) for key in ("roles", "places")}
    out = [r for r in out if not any(own[k] is not None and own[k] >= r["match"] for k in r["off"] & {"roles", "places"})]
    return sorted(out, key=lambda r: (-round(r["match"], 1), -r["fit"], TRUST.get(r["trust"], 9), r["status"] == "stale",
                                      -(r["valid_from"] or date.min).toordinal(), -r["updated_at"].timestamp(), r["id"]))


def tsquery(question: str) -> str:
    """Any of the question's words, as a text-search query. Words are letters and digits only, so nothing in a question
    can be read as the query's own syntax."""
    return " | ".join(sorted(question_words(question)))


def scope_said(row: dict) -> str:
    parts = [", ".join(row[c]) for c in ("scope_places", "scope_roles", "scope_teams") if row[c]]
    return f"Applies to: {'; '.join(parts)}." if parts else ""


def source_text(row: dict, perspectives: list[dict], holder: dict | None = None) -> str:
    """What the model reads of a unit: its text, its structure, a contact's current holder, who it applies to, and the
    other views on it."""
    lines = [claims.MARK.sub("", row["body"]).strip()]
    structure = row["structure"] or {}
    for n, step in enumerate(structure.get("steps") or [], 1):
        who = f" ({step['role']})" if step.get("role") else ""
        when = f" If {step['condition']}." if step.get("condition") else ""
        lines.append(f"{n}. {step['text']}{who}.{when}")
    if row["kind"] == "contact" and structure.get("role"):
        lines.append(f"Contact: {structure['role']}" + (f", {structure['reach']}" if structure.get("reach") else "") + ".")
        if holder:
            lines.append(f"Its current holder for the person asking: {holder['name']} ({holder['title']}).")
    if row["kind"] == "definition" and structure.get("term"):
        lines.append(f"Term: {structure['term']}" + (f" = {structure['formula']}" if structure.get("formula") else "") + ".")
    if scope_said(row):
        lines.append(scope_said(row))
    if row["status"] == "stale":
        lines.append("Due for review: its owner hasn't confirmed it lately.")
    for p in perspectives:
        lines.append(f"Another view, held by {p['holder']}" + (f" ({p['context']})" if p["context"] else "") + f": {p['body']}")
    return "\n".join(lines)


def level(levels: set[str]) -> str:
    """The most restricted of a unit's evidence classifications, to show with it."""
    return max(levels, key=lambda c: LEVELS.index(c) if c in LEVELS else len(LEVELS))


def find(conn, caller, question: str, context: dict, limit: int = UNIT_SOURCES) -> list[dict]:
    """The units that answer a question for this asker, best first, as Ask's sources: each with the fields a document
    source has (its key is `unit:<id>`), its classifications (every evidence document's), and what its citation shows.
    In the caller's transaction: row-level security has already hidden any unit whose evidence it hides, and each is
    checked again here through the documents list's own gates (F1)."""
    q = tsquery(question)
    if not q:
        return []
    rows = ranked(question, conn.execute(FIND_SQL, {"q": q}).fetchall(), context)
    docs = readable(conn, caller, [d for r in rows for d in r["evidence"]])
    rows = [r for r in rows if shown(r["evidence"], set(docs))]
    used = {r["id"] for r in rows}
    rows = [r for r in rows if r["duplicate_of"] not in used][:limit]  # a copy, never beside its original
    if not rows:
        return []
    views = {r["id"]: conn.execute(PERSPECTIVES_SQL, {"id": r["id"]}).fetchall() for r in rows}
    seen = readable(conn, caller, [d for ps in views.values() for p in ps for d in p["evidence"]]) | docs
    levels = {r["id"]: r["classification"] for r in conn.execute(
        LEVELS_SQL, (sorted({d for r in rows for d in r["evidence"]} | {d for ps in views.values() for p in ps for d in p["evidence"]}),))}
    out = []
    for r in rows:
        perspectives = [p for p in views[r["id"]] if shown(p["evidence"], set(seen))]
        held = {levels[d] for d in r["evidence"]} | {levels[d] for p in perspectives for d in p["evidence"]}
        role = (r["structure"] or {}).get("role") if r["kind"] == "contact" else None
        holder = asking.holder(conn, context, role, r["scope_places"] or []) if role else None
        out.append({
            "external_key": f"{PREFIX}{r['id']}", "title": f"{r['title']} (knowledge unit: {r['kind']}, {r['trust']})",
            "heading_path": None, "body": source_text(r, perspectives, holder), "classification": level(held),
            "classifications": held, "source_uri": None, "source_kind": "unit",
            "source_version": r["updated_at"].isoformat(), "tier": "gold" if r["status"] == "confirmed" else "silver",
            "unit": {"id": r["id"], "kind": r["kind"], "title": r["title"], "trust": r["trust"], "status": r["status"],
                     "owner": r["owner"], "structure": r["structure"] or {}, **({"holder": holder} if holder else {}),
                     "scope": {"teams": r["scope_teams"], "roles": r["scope_roles"], "places": r["scope_places"]},
                     "evidence": [{"document": seen[d]["external_key"], "title": seen[d]["title"], "section": s or None}
                                  for d, s in zip(r["evidence"], r["sections"])],
                     "perspectives": [{"holder": p["holder"], "holder_kind": p["holder_kind"], "context": p["context"],
                                       "text": p["body"]} for p in perspectives]}})
    return out


def visible(conn, caller, keys: set[str]) -> set[str]:
    """Which of these `unit:<id>` keys the caller may read now (F1's rule), for stored answers that cite them."""
    ids = sorted({k[len(PREFIX):] for k in keys if k.startswith(PREFIX) and _uuid(k[len(PREFIX):])})
    if not ids:
        return set()
    rows = conn.execute(BY_ID_SQL, (ids,)).fetchall()
    docs = readable(conn, caller, [d for r in rows for d in r["evidence"]])
    return {f"{PREFIX}{r['id']}" for r in rows if shown(r["evidence"], set(docs))}


def classifications(conn, keys: set[str]) -> dict[str, set[str]]:
    """Each `unit:<id>` key's evidence classifications, as the caller's row-level security lets it see them."""
    ids = sorted({k[len(PREFIX):] for k in keys if k.startswith(PREFIX) and _uuid(k[len(PREFIX):])})
    if not ids:
        return {}
    rows = conn.execute(BY_ID_SQL, (ids,)).fetchall()
    levels = {r["id"]: r["classification"] for r in conn.execute(LEVELS_SQL, (sorted({d for r in rows for d in r["evidence"]}),))}
    return {f"{PREFIX}{r['id']}": {levels[d] for d in r["evidence"] if d in levels} for r in rows
            if all(d in levels for d in r["evidence"])}


def _uuid(raw: str) -> bool:
    try:
        uuid.UUID(raw)
        return True
    except ValueError:
        return False
