"""Postgres access. Content queries run inside scoped(): one transaction whose RLS context
(org and caller principals) is set with set_config(..., is_local=true) and vanishes at commit.

Connections come from one pool per process (H1), so a request borrows one instead of opening its own. A connection
goes back clean: its RLS context ended with its transaction, and RESET ALL clears anything set for the whole session.
Each role's time limits (migration 0052) bound what one query or one open transaction can hold."""
import os
import threading
from contextlib import contextmanager
from functools import lru_cache

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config
from .principals import is_team, is_valid

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def _clean(conn: psycopg.Connection) -> None:
    """What a connection keeps of the request that used it, once it's back in the pool: nothing."""
    conn.execute("RESET ALL")
    conn.commit()


def new_pool(max_size: int = config.DB_POOL_MAX) -> ConnectionPool:
    """A pool as this process uses it: each connection checked before it's handed out (a Postgres restart leaves only
    dead ones) and cleaned when it comes back."""
    return ConnectionPool(config.DATABASE_URL, min_size=min(config.DB_POOL_MIN, max_size), max_size=max_size,
                          timeout=config.DB_POOL_TIMEOUT, check=ConnectionPool.check_connection, reset=_clean, open=True,
                          kwargs={"row_factory": dict_row, "connect_timeout": 5,
                                  "application_name": f"{config.SERVICE_NAME}:{os.getpid()}"})  # one name per process (M3)


def connect(timeout: float | None = None):
    """A connection from this process's pool, for a with block: its transaction is committed (or rolled back on an
    error) and the connection goes back at the block's end. Waiting longer than `timeout` (the pool's, by default)
    raises psycopg_pool.PoolTimeout, which the API answers with a 503."""
    global _pool
    pool = _pool
    if pool is None:
        with _pool_lock:
            pool = _pool = _pool or new_pool()
    return pool.connection(timeout=timeout)


@lru_cache(maxsize=8)
def resolve_org_id(slug: str) -> str:
    with connect() as conn:
        row = conn.execute("SELECT kl.resolve_org(%s) AS id", (slug,)).fetchone()
    if not row or not row["id"]:
        raise RuntimeError(f"org {slug!r} not found")
    return str(row["id"])


@contextmanager
def scoped(org_id: str, principals: list[str], teams: list[str] = ()):
    """One transaction in the caller's RLS context. `teams` names the teams whose own rows (asset owners, review
    history, media choices) this transaction may touch, as the API decided in OpenFGA before opening it; with none,
    those rows stay hidden (migration 0053)."""
    bad = [p for p in principals if not is_valid(p)] + [t for t in teams if not is_team(t)]
    if bad or not principals:
        raise ValueError(f"refusing to build an RLS context from principals {principals!r} and teams {list(teams)!r}")
    with connect() as conn:
        with conn.transaction():
            conn.execute(
                "SELECT set_config('kl.org_id', %s, true), set_config('kl.principals', %s, true), set_config('kl.teams', %s, true)",
                (org_id, ",".join(principals), ",".join(teams)),
            )
            # Add the caller's linked source identities (ext:github.<login>, ...) so ACL arrays that
            # hold source identities match. Links live in principal_aliases; unlinked grants nothing.
            linked = [r["source"] for r in conn.execute(
                "SELECT source FROM principal_aliases WHERE canonical = ANY(%s)", (principals,)) if is_valid(r["source"])]
            if linked:
                conn.execute("SELECT set_config('kl.principals', %s, true)", (",".join(principals + linked),))
            yield conn
