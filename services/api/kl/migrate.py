"""Apply pending SQL migrations (kl/migrations/NNNN_*.sql) in order, as the schema owner.
The M0 baseline comes from deploy/postgres/sql; migrations evolve it without a reset."""
import os
import pathlib

import psycopg

OWNER_DB = os.environ.get("KL_OWNER_DATABASE_URL", "")
MIGRATIONS = pathlib.Path(__file__).parent / "migrations"


def apply_pending() -> list[str]:
    if not OWNER_DB:
        raise SystemExit("migrate: KL_OWNER_DATABASE_URL is not set")
    applied = []
    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS kl.schema_migrations "
                     "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        done = {row[0] for row in conn.execute("SELECT version FROM kl.schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.stem in done:
                continue
            with conn.transaction():  # each migration is all-or-nothing
                conn.execute(path.read_text())
                conn.execute("INSERT INTO kl.schema_migrations (version) VALUES (%s)", (path.stem,))
            applied.append(path.stem)
    return applied


if __name__ == "__main__":
    print("migrate: applied", apply_pending() or "nothing (up to date)")
