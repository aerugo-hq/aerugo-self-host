"""Restore drill (slice I4): `make restore-drill`.

`mark` writes a marker row into the live database. The Makefile then backs the database up, restores the newest
backup into a throwaway Postgres (the pg-drill service, which reads the backups read-only and archives nothing),
and `verify` checks the restored copy against the live cell:
- the marker is there: the backup holds what was committed before it began;
- the schema matches: the same migrations, every table with the same row-level security, the same policies;
- the data is there: the core tables hold rows;
- the encrypted originals in the copy still open with the live keys, since a backup is only as good as its keys.
The result is recorded in the live database (kl.backup_drills) as evidence, and a failed drill exits non-zero.
"""
import json
import os
import secrets
import sys

import psycopg

LIVE = os.environ["KL_OWNER_DATABASE_URL"]
DRILL = LIVE.replace("@postgres:", "@pg-drill:")
CORE = ("documents", "chunks", "chunk_embeddings", "concepts", "concept_versions", "audit_events", "workspaces")


def snapshot(conn) -> dict:
    """A cell's schema: applied migrations, each table with its row-level security flag, policies per table."""
    return {
        "migrations": sorted(r[0] for r in conn.execute("SELECT version FROM kl.schema_migrations")),
        "tables": {name: rls for name, rls in conn.execute(
            "SELECT c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'kl' AND c.relkind = 'r'")},
        "policies": {name: n for name, n in conn.execute(
            "SELECT tablename, count(*) FROM pg_policies WHERE schemaname = 'kl' GROUP BY tablename")},
    }


def compare(expected: dict, got: dict, marker_found: bool, counts: dict, opened: int, unopened: int) -> list[str]:
    """Every way the restored copy falls short of the live cell, in words; an empty list is a passing drill."""
    problems = []
    if not marker_found:
        problems.append("the marker written before the backup isn't in the copy")
    if expected["migrations"] != got["migrations"]:
        missing = sorted(set(expected["migrations"]) - set(got["migrations"]))
        problems.append(f"migrations differ (missing {missing})" if missing else "the copy has migrations the live cell doesn't")
    for table, rls in sorted(expected["tables"].items()):
        if table not in got["tables"]:
            problems.append(f"table {table} is missing")
        elif got["tables"][table] != rls:
            problems.append(f"row-level security on {table} is {'off' if rls else 'on'} in the copy")
    for table, n in sorted(expected["policies"].items()):
        if got["policies"].get(table, 0) != n:
            problems.append(f"{table} has {got['policies'].get(table, 0)} policies in the copy, {n} live")
    problems += [f"{table} is empty in the copy" for table in CORE if not counts.get(table)]
    if unopened:
        problems.append(f"{unopened} encrypted original(s) in the copy didn't open with the live keys")
    if not opened:
        problems.append("no encrypted originals in the copy to open")
    return problems


def mark() -> int:
    token = secrets.token_hex(8)
    with psycopg.connect(LIVE) as conn:
        conn.execute("INSERT INTO kl.backup_drills (marker) VALUES (%s)", (token,))
    print(f"drill marker {token} written; backing up next")
    return 0


def verify() -> int:
    from . import originals  # needs the secrets backend, so only verify imports it
    with psycopg.connect(LIVE) as live:
        row = live.execute("SELECT id, marker FROM kl.backup_drills WHERE finished_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            print("no drill in progress: run `make restore-drill`", file=sys.stderr)
            return 1
        drill_id, token = row
        expected = snapshot(live)
    with psycopg.connect(DRILL) as copy:
        marker_found = copy.execute("SELECT 1 FROM kl.backup_drills WHERE marker = %s", (token,)).fetchone() is not None
        got = snapshot(copy)
        counts = {t: copy.execute(f"SELECT count(*) FROM kl.{t}").fetchone()[0] for t in CORE}
        sample = copy.execute("(SELECT content, key_ref FROM kl.concept_versions WHERE key_ref IS NOT NULL LIMIT 40) "
                              "UNION ALL (SELECT content, key_ref FROM kl.proposals WHERE key_ref IS NOT NULL LIMIT 10)").fetchall()
    opened = unopened = 0
    for content, key_ref in sample:
        try:
            ok = originals.open_one(content, key_ref).lstrip().startswith("---")
        except Exception:  # noqa: BLE001 - a key that no longer opens it
            ok = False
        opened, unopened = opened + ok, unopened + (not ok)
    problems = compare(expected, got, marker_found, counts, opened, unopened)
    details = {"marker": token, "migrations": len(got["migrations"]), "tables": len(got["tables"]),
               "rows": counts, "originals_opened": opened, "problems": problems}
    with psycopg.connect(LIVE) as live:
        live.execute("UPDATE kl.backup_drills SET finished_at = now(), ok = %s, details = %s::jsonb WHERE id = %s",
                     (not problems, json.dumps(details), drill_id))
    if problems:
        print("RESTORE DRILL FAILED:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    print(f"restore drill passed: marker found, {len(got['migrations'])} migrations and {len(got['tables'])} tables "
          f"match with their row-level security and policies, {counts['documents']} documents, "
          f"{opened} encrypted originals opened with the live keys")
    return 0


if __name__ == "__main__":
    sys.exit({"mark": mark, "verify": verify}[sys.argv[1]]())
