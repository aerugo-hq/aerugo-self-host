"""Postgres access. Content queries run inside scoped(): one transaction whose RLS context
(org and caller principals) is set with set_config(..., is_local=true) and vanishes at commit."""
from contextlib import contextmanager
from functools import lru_cache

import psycopg
from psycopg.rows import dict_row

from . import config
from .principals import is_valid


def connect() -> psycopg.Connection:
    return psycopg.connect(config.DATABASE_URL, row_factory=dict_row, connect_timeout=5)


@lru_cache(maxsize=8)
def resolve_org_id(slug: str) -> str:
    with connect() as conn:
        row = conn.execute("SELECT kl.resolve_org(%s) AS id", (slug,)).fetchone()
    if not row or not row["id"]:
        raise RuntimeError(f"org {slug!r} not found")
    return str(row["id"])


@contextmanager
def scoped(org_id: str, principals: list[str]):
    bad = [p for p in principals if not is_valid(p)]
    if bad or not principals:
        raise ValueError(f"refusing to build an RLS context from principals {principals!r}")
    with connect() as conn:
        with conn.transaction():
            conn.execute(
                "SELECT set_config('kl.org_id', %s, true), set_config('kl.principals', %s, true)",
                (org_id, ",".join(principals)),
            )
            # Add the caller's linked source identities (ext:github.<login>, ...) so ACL arrays that
            # hold source identities match. Links live in principal_aliases; unlinked grants nothing.
            linked = [r["source"] for r in conn.execute(
                "SELECT source FROM principal_aliases WHERE canonical = ANY(%s)", (principals,)) if is_valid(r["source"])]
            if linked:
                conn.execute("SELECT set_config('kl.principals', %s, true)", (",".join(principals + linked),))
            yield conn
