"""Lifecycle (F6, docs/ENTERPRISE_PLAN.md section 5, principle 1: nothing lives forever without review): the worker
moves a unit along as time and its evidence change, and its owners are told.

- A confirmed unit becomes stale when its review is due (confirmed longer ago than its review cadence) or when a piece
  of its evidence changed since its owner confirmed it (a document's content hash moved, or the document is gone).
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
       ARRAY(SELECT coalesce(d.content_hash, '') FROM unnest(u.evidence) WITH ORDINALITY e(doc, n)
             LEFT JOIN documents d ON d.id = e.doc ORDER BY e.n) AS hashes
FROM knowledge_units u WHERE u.status IN ('proposed', 'confirmed', 'stale')
"""
# ponytail: every active unit, once a minute; an index on (status, reviewed_at) and documents' change times once an org
# holds hundreds of thousands of units.
QUEUE_SQL = """INSERT INTO jobs (org_id, kind) SELECT kl.current_org(), 'units.lifecycle'
WHERE EXISTS (SELECT 1 FROM knowledge_units WHERE status IN ('proposed', 'confirmed', 'stale'))
AND NOT EXISTS (SELECT 1 FROM jobs WHERE kind = 'units.lifecycle' AND (status IN ('queued', 'running') OR created_at > now() - interval '1 minute'))"""
# The snapshot a confirmation takes: each evidence document's content hash, in the evidence's order ('' for one gone).
SNAPSHOT = ("ARRAY(SELECT coalesce(d.content_hash, '') FROM unnest(evidence) WITH ORDINALITY e(doc, n) "
            "LEFT JOIN documents d ON d.id = e.doc ORDER BY e.n)")
EVENT_SQL = ("INSERT INTO unit_events (org_id, unit_id, from_status, to_status, reason, created_by) "
             "VALUES (kl.current_org(), %s, %s, %s, %s, %s)")


def transition(row: dict, today: date, now: datetime) -> tuple[str, str] | None:
    """Where an active unit goes next, and why, or None: retired past its end date, stale when its review is due or
    its evidence changed since it was confirmed."""
    if row["valid_until"] is not None and row["valid_until"] < today and row["superseded_by"] is None:
        return "retired", f"its end date, {row['valid_until'].isoformat()}, passed"
    if row["status"] != "confirmed":
        return None
    if row["evidence_hashes"] is not None and list(row["evidence_hashes"]) != list(row["hashes"]):
        return "stale", "its evidence changed since it was confirmed"
    if row["reviewed_at"] is not None and row["reviewed_at"] + timedelta(days=row["review_days"]) < now:
        return "stale", f"due for review (every {row['review_days']} days)"
    return None


def run(conn) -> dict:
    """One pass over the active units: each that's due moves on, its event recorded. Returns how many moved where."""
    rows = conn.execute(ACTIVE_SQL).fetchall()
    clock = conn.execute("SELECT current_date AS today, now() AS now").fetchone()
    moves = [(r, found) for r in rows if (found := transition(r, clock["today"], clock["now"]))]
    if not moves:
        return {"moved": 0}
    with conn.transaction():
        for r, (status, reason) in moves:
            conn.execute("UPDATE knowledge_units SET status = %s, updated_at = now() WHERE id = %s AND status = %s", (status, r["id"], r["status"]))
            conn.execute(EVENT_SQL, (r["id"], r["status"], status, reason, "lifecycle"))
    counts: dict[str, int] = {}
    for _, (status, _) in moves:
        counts[status] = counts.get(status, 0) + 1
    log.info("lifecycle: %s", counts)
    return {"moved": len(moves), **counts}
