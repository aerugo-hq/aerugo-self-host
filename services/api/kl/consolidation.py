"""Consolidation (F3, docs/ENTERPRISE_PLAN.md section 5): units that repeat each other are linked, and units that
contradict each other become conflicts their owners see; nothing is overwritten silently (principle 2).

Two active units (not superseded or retired, neither superseding the other) are compared only when they apply to the
same people (the same scope: Colorado's rule is never Oregon's, nor the company-wide one's) at overlapping times (two
versions of a policy are never a conflict). Then, by their content words (the claim checker's):

- a duplicate: the same kind, the same team, each stating at least DUPLICATE of the other's words, and nothing
  contradicting. The copy is linked to the original (the higher trust, then the earlier), each keeping its own
  evidence, so each keeps its own readers; lists hide a copy only from someone who can see its original.
- a conflict: one stating at least CONFLICT of the other's words, and the two disagreeing: each giving a number the
  other doesn't (45 days against 60), or one negated where the other's closest sentence isn't. One leaving out a number
  the other gives is no disagreement (the claim checker's one-sided rule is for a claim against its source, not for two
  peers). Teams may differ; F1's rule shows the conflict only to someone who reads both sides.

Words, not a model: a disagreement in meaning with no number or negation (one team's "units on hand" against
another's "units received") isn't caught; a team's model can judge those later, on top of this, never instead.

The worker's pass (`run`) compares the units changed since it last read them against every active unit sharing at
least two content words, at most once a minute while any are due.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from . import claims

log = logging.getLogger("kl.consolidation")

DUPLICATE = 0.8  # each states at least this share of the other's content words
CONFLICT = 0.8   # one states at least this share of the other's, and they differ in a number or a negation
TRUST = {"confirmed": 0, "documented": 1, "observed": 2, "inferred": 3}
INACTIVE = ("superseded", "retired")
MIN_WORDS = 4  # a fragment ("For store leaders only") shares too few words to say what it repeats or contradicts


@dataclass(frozen=True)
class Unit:
    id: str
    team: str
    kind: str
    text: str
    trust: str
    created: str                     # its creation time, ISO, to order a tie
    places: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    teams: tuple[str, ...] = ()
    valid_from: date | None = None
    valid_until: date | None = None
    superseded_by: str | None = None
    status: str = "proposed"
    duplicate_of: str | None = None  # already linked, by an earlier pass


def plain(text: str) -> str:
    """A unit's text without its citation marks ([1]), which say where, not what."""
    return " ".join(claims.MARK.sub("", text or "").split())


def same_scope(a: Unit, b: Unit) -> bool:
    return (set(a.places), set(a.roles), set(a.teams)) == (set(b.places), set(b.roles), set(b.teams))


def overlapping(a: Unit, b: Unit) -> bool:
    """Whether two units apply at some same time (no start or end is open-ended)."""
    return (a.valid_until is None or b.valid_from is None or b.valid_from <= a.valid_until) and \
           (b.valid_until is None or a.valid_from is None or a.valid_from <= b.valid_until)


def disagreement(ta: str, tb: str) -> str | None:
    """Why two texts disagree, or None: each gives a number the other doesn't, or one is negated where the other's
    sentence closest to it isn't."""
    na, nb = claims.numbers(ta), claims.numbers(tb)
    if na - nb and nb - na:
        return "they give different numbers"
    wa = claims.words(ta)
    closest = max(claims.SENTENCE.findall(tb) or [tb], key=lambda s: len(wa & claims.words(s)))
    if claims.negated(ta) != claims.negated(closest):
        return "one says not where the other doesn't"
    return None


def relation(a: Unit, b: Unit) -> tuple[str, str] | None:
    """('duplicate', '') or ('conflict', why) when two units repeat or contradict each other, else None."""
    if a.id == b.id or a.status in INACTIVE or b.status in INACTIVE or a.superseded_by == b.id or b.superseded_by == a.id:
        return None
    if not same_scope(a, b) or not overlapping(a, b):
        return None
    ta, tb = plain(a.text), plain(b.text)
    wa, wb = claims.words(ta), claims.words(tb)
    if len(wa) < MIN_WORDS or len(wb) < MIN_WORDS:
        return None
    ab, ba = claims.support(wa, tb), claims.support(wb, ta)
    why = disagreement(ta, tb) or disagreement(tb, ta)
    if why and max(ab, ba) >= CONFLICT:
        return "conflict", why
    if not why and min(ab, ba) >= DUPLICATE and a.kind == b.kind and a.team == b.team:
        return "duplicate", ""
    return None


def first_of(a: Unit, b: Unit) -> tuple[Unit, Unit]:
    """(the one that stays first, the other): the higher trust, then the earlier, then the id."""
    return tuple(sorted((a, b), key=lambda u: (TRUST.get(u.trust, 9), u.created, u.id)))  # type: ignore[return-value]


def candidates(units: list[Unit]) -> dict[str, set[str]]:
    """Each unit's possible partners: those sharing at least two content words. ponytail: in memory per pass; an
    inverted index in the database once an org holds hundreds of thousands of units."""
    index: dict[str, set[str]] = defaultdict(set)
    words = {u.id: claims.words(plain(u.text)) for u in units}
    for uid, ws in words.items():
        for w in ws:
            index[w].add(uid)
    out = {}
    for uid, ws in words.items():
        counts: dict[str, int] = defaultdict(int)
        for w in ws:
            for other in index[w]:
                if other != uid:
                    counts[other] += 1
        out[uid] = {o for o, n in counts.items() if n >= 2}
    return out


def decide(units: list[Unit], due: set[str], linked: set[frozenset]) -> tuple[dict[str, str], list[tuple[str, str, str]]]:
    """What a pass concludes for the due units: {copy: original} links, and (first, other, why) conflicts not already
    open between the pair (`linked`, any status)."""
    by_id = {u.id: u for u in units}
    near = candidates(units)
    links: dict[str, str] = {}
    conflicts: list[tuple[str, str, str]] = []
    seen: set[frozenset] = set()
    for uid in sorted(due):
        for other in sorted(near.get(uid, ())):
            pair = frozenset((uid, other))
            if pair in seen:
                continue
            seen.add(pair)
            found = relation(by_id[uid], by_id[other])
            if not found:
                continue
            first, second = first_of(by_id[uid], by_id[other])
            if found[0] == "duplicate":
                root = links.get(first.id) or first.duplicate_of or first.id  # a copy of a copy links to the original
                if second.duplicate_of is None and root != second.id:
                    links.setdefault(second.id, root)
            elif pair not in linked:
                conflicts.append((first.id, second.id, found[1]))
    return links, conflicts


# ------------------------------------------------------------------ the worker's pass (kl_ingest, in one org)
ACTIVE_SQL = """
SELECT u.id::text AS id, u.owner_team::text AS team, u.kind, u.body, u.trust, u.created_at, u.scope_places, u.scope_roles,
       u.scope_teams, u.valid_from, u.valid_until, u.superseded_by::text AS superseded_by, u.status, u.duplicate_of::text AS duplicate_of,
       u.consolidated_at IS NULL OR u.updated_at > u.consolidated_at AS due
FROM knowledge_units u WHERE u.status <> ALL(%s)
"""
LINKED_SQL = "SELECT unit_id::text AS a, other_unit::text AS b FROM unit_conflicts WHERE other_unit IS NOT NULL"
QUEUE_SQL = """INSERT INTO jobs (org_id, kind) SELECT kl.current_org(), 'units.consolidate'
WHERE EXISTS (SELECT 1 FROM knowledge_units WHERE status <> ALL('{superseded,retired}') AND (consolidated_at IS NULL OR updated_at > consolidated_at))
AND NOT EXISTS (SELECT 1 FROM jobs WHERE kind = 'units.consolidate' AND (status IN ('queued', 'running') OR created_at > now() - interval '1 minute'))"""


def run(conn) -> dict:
    """One pass over the units changed since they were last compared. Returns how many were linked and opened."""
    rows = conn.execute(ACTIVE_SQL, (list(INACTIVE),)).fetchall()
    due = {r["id"] for r in rows if r["due"]}
    if not due:
        return {"compared": 0}
    units = [Unit(r["id"], r["team"], r["kind"], r["body"], r["trust"], r["created_at"].isoformat(), tuple(r["scope_places"]),
                  tuple(r["scope_roles"]), tuple(r["scope_teams"]), r["valid_from"], r["valid_until"], r["superseded_by"], r["status"],
                  r["duplicate_of"])
             for r in rows]
    linked = {frozenset((r["a"], r["b"])) for r in conn.execute(LINKED_SQL).fetchall()}
    links, conflicts = decide(units, due, linked)
    with conn.transaction():
        for copy, original in links.items():
            conn.execute("UPDATE knowledge_units SET duplicate_of = %s WHERE id = %s AND duplicate_of IS NULL", (original, copy))
        for first, other, why in conflicts:
            conn.execute("INSERT INTO unit_conflicts (org_id, unit_id, other_unit, note, opened_by) "
                         "VALUES (kl.current_org(), %s, %s, %s, 'consolidation')", (first, other, f"They differ: {why}."))
        conn.execute("UPDATE knowledge_units SET consolidated_at = now() WHERE id = ANY(%s::uuid[])", (sorted(due),))
    return {"compared": len(due), "linked": len(links), "conflicts": len(conflicts)}
