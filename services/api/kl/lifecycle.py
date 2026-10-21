"""Lifecycle (F6, docs/ENTERPRISE_PLAN.md section 5, principle 1: nothing lives forever without review): the worker
moves a unit along as time and its evidence change, and its owners are told.

- A confirmed unit becomes stale when its review is due (confirmed longer ago than its review cadence) or when a piece
  of its evidence changed since its owner confirmed it: the text Aerugo keeps from it, where it kept text then and
  keeps it now, so a version that moves with the same text (Jira marking an issue updated) changes nothing; otherwise
  its version, since a pointer or live-only document keeps no text (F11), and switching a connection between them reads
  the same version; and a document that's gone has neither (kl.evidence_texts, migration 0070). A snapshot behind
  evidence that didn't change (a version that moved with the same text) moves forward, with no event.
- A unit past its end date, with nothing superseding it, is retired.
- Every change is recorded with its reason (unit_events); the API records the ones people make (confirm, retire, a new
  version, a resolved conflict) the same way.

A stale unit still answers, ranked after current ones and said to be due for review (F4). Its owners are told in the
app: their team's review queue (`GET /v1/workspaces/{ws}/review`) lists it, and an info alert for their team says how
many are due (kl.alerting); nothing is sent outside the app. Confirming it again restarts its review and takes a new
snapshot of its evidence."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

log = logging.getLogger("kl.lifecycle")

ACTIVE = ("proposed", "confirmed", "stale")
ACTIVE_SQL = """
SELECT u.id::text AS id, u.status, u.reviewed_at, u.review_days, u.valid_until, u.superseded_by, u.evidence_hashes,
       u.evidence_texts,
       ARRAY(SELECT coalesce(d.content_hash, '') FROM unnest(u.evidence) WITH ORDINALITY e(doc, n)
             LEFT JOIN documents d ON d.id = e.doc ORDER BY e.n) AS hashes,
       CASE WHEN u.status = 'confirmed' THEN kl.evidence_texts(u.evidence) END AS texts
FROM knowledge_units u WHERE u.status IN ('proposed', 'confirmed', 'stale')
"""
# ponytail: every active unit, once a minute, hashing each confirmed one's evidence text; an index on (status,
# reviewed_at), and each document's text hash kept at its sync, once an org holds hundreds of thousands of units.
QUEUE_SQL = """INSERT INTO jobs (org_id, kind) SELECT kl.current_org(), 'units.lifecycle'
WHERE EXISTS (SELECT 1 FROM knowledge_units WHERE status IN ('proposed', 'confirmed', 'stale'))
AND NOT EXISTS (SELECT 1 FROM jobs WHERE kind = 'units.lifecycle' AND (status IN ('queued', 'running') OR created_at > now() - interval '1 minute'))"""
# The snapshots a confirmation takes, in the evidence's order: each document's content hash, its source's version for a
# synced one ('' for one gone), and the hash of the text it keeps (NULL where it keeps none).
SNAPSHOT = ("ARRAY(SELECT coalesce(d.content_hash, '') FROM unnest(evidence) WITH ORDINALITY e(doc, n) "
            "LEFT JOIN documents d ON d.id = e.doc ORDER BY e.n)")
TEXT_SNAPSHOT = "kl.evidence_texts(evidence)"
EVENT_SQL = ("INSERT INTO unit_events (org_id, unit_id, from_status, to_status, reason, created_by) "
             "VALUES (kl.current_org(), %s, %s, %s, %s, %s)")


def evidence_changed(row: dict) -> bool:
    """Whether a piece of a confirmed unit's evidence changed since its snapshot: its kept text where there's text both
    then and now, otherwise its version (a unit confirmed before text snapshots has only versions)."""
    then_v, now_v = list(row["evidence_hashes"]), list(row["hashes"])
    then_t, now_t = list(row["evidence_texts"] or [None] * len(then_v)), list(row["texts"])
    if not len(then_v) == len(now_v) == len(then_t) == len(now_t):
        return True
    return any(t0 != t1 if t0 and t1 else v0 != v1 for v0, t0, v1, t1 in zip(then_v, then_t, now_v, now_t))


def snapshot_behind(row: dict) -> bool:
    """A confirmed unit whose evidence didn't change but whose snapshot is behind it (a version that moved with the same
    text, or text a document keeps now at the same version): its snapshot moves forward, so a later comparison by
    version alone (once its connection keeps pointers) starts from the evidence as it is."""
    if row["status"] != "confirmed" or row["evidence_hashes"] is None or evidence_changed(row):
        return False
    then_t = list(row["evidence_texts"] or [None] * len(row["evidence_hashes"]))
    return list(row["evidence_hashes"]) != list(row["hashes"]) or then_t != list(row["texts"])


def transition(row: dict, today: date, now: datetime) -> tuple[str, str] | None:
    """Where an active unit goes next, and why, or None: retired past its end date, stale when its review is due or
    its evidence changed since it was confirmed."""
    if row["valid_until"] is not None and row["valid_until"] < today and row["superseded_by"] is None:
        return "retired", f"its end date, {row['valid_until'].isoformat()}, passed"
    if row["status"] != "confirmed":
        return None
    if row["evidence_hashes"] is not None and evidence_changed(row):
        return "stale", "its evidence changed since it was confirmed"
    if row["reviewed_at"] is not None and row["reviewed_at"] + timedelta(days=row["review_days"]) < now:
        return "stale", f"due for review (every {row['review_days']} days)"
    return None


def run(conn) -> dict:
    """One pass over the active units: each that's due moves on, its event recorded, and a confirmed unit whose snapshot
    is behind its unchanged evidence has it moved forward. Returns how many moved where."""
    rows = conn.execute(ACTIVE_SQL).fetchall()
    clock = conn.execute("SELECT current_date AS today, now() AS now").fetchone()
    moves = [(r, found) for r in rows if (found := transition(r, clock["today"], clock["now"]))]
    behind = [r for r in rows if snapshot_behind(r) and not transition(r, clock["today"], clock["now"])]
    if not moves and not behind:
        return {"moved": 0}
    with conn.transaction():
        for r, (status, reason) in moves:
            conn.execute("UPDATE knowledge_units SET status = %s, updated_at = now() WHERE id = %s AND status = %s", (status, r["id"], r["status"]))
            conn.execute(EVENT_SQL, (r["id"], r["status"], status, reason, "lifecycle"))
        for r in behind:  # the same evidence: no event, and only if no one confirmed it again meanwhile
            conn.execute("UPDATE knowledge_units SET evidence_hashes = %s, evidence_texts = %s "
                         "WHERE id = %s AND status = 'confirmed' AND evidence_hashes = %s",
                         (r["hashes"], r["texts"], r["id"], r["evidence_hashes"]))
    counts: dict[str, int] = {}
    for _, (status, _) in moves:
        counts[status] = counts.get(status, 0) + 1
    log.info("lifecycle: %s, %d snapshots moved forward", counts, len(behind))
    return {"moved": len(moves), **counts, "snapshots": len(behind)}
