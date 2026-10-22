"""The answers lane (C2d): runs the jobs someone is waiting on (`slack.ask`), apart from the worker.

It connects as the API's own database role and refuses to start as any other, so row-level security narrows each ask
to its asker exactly as it narrows the API (the worker's role, kl_ingest, isn't narrowed to a person), and a long sync
in the worker never holds an answer up. It schedules nothing and claims nothing else; the worker never claims these
jobs, and its reaper puts back one whose lane stopped mid-ask (tried once, so it fails rather than answering twice)."""
from __future__ import annotations

import logging
import os
import signal
import time

import psycopg
from psycopg.rows import dict_row

from . import chat_ask, config, shutdown, slack_ask, teams_ask, telemetry, worker

POLL_SECONDS = float(os.environ.get("KL_ANSWERS_POLL_SECONDS", "1"))
HANDLERS = {"slack.ask": slack_ask.ask, "teams.ask": teams_ask.ask}
assert set(HANDLERS) == set(worker.LANE_KINDS)

log = logging.getLogger("kl.answers_lane")


def _stop(*_) -> None:
    shutdown.requested.set()


def main() -> None:
    telemetry.setup("kl-answers")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    warmed = False
    while not shutdown.requested.is_set():
        try:
            with psycopg.connect(config.DATABASE_URL, row_factory=dict_row, autocommit=True) as conn:
                if conn.execute("SELECT current_user AS who").fetchone()["who"] != "kl_app":
                    raise SystemExit("answers lane: it runs only as the API's role (kl_app), so row-level security "
                                     "narrows each ask to its asker")
                org_id = conn.execute("SELECT kl.resolve_org(%s) AS id", (config.ORG_SLUG,)).fetchone()["id"]
                if org_id is None:
                    raise SystemExit(f"answers lane: org {config.ORG_SLUG!r} not found")
                conn.execute("SELECT set_config('kl.org_id', %s, false)", (str(org_id),))
                if not warmed:
                    chat_ask.warm()
                    warmed = True
                log.info("answers lane ready (org %s)", config.ORG_SLUG)
                while not shutdown.requested.is_set():
                    with conn.transaction():
                        job = conn.execute(worker.LANE_CLAIM_SQL, (worker.LEASE_SECONDS, list(worker.LANE_KINDS))).fetchone()
                    if job is None:
                        time.sleep(POLL_SECONDS)
                        continue
                    worker.run(conn, job, HANDLERS, config.DATABASE_URL)
        except psycopg.OperationalError as e:
            log.warning("database unavailable (%s); retrying in 3s", type(e).__name__)
            time.sleep(3)


if __name__ == "__main__":
    main()
