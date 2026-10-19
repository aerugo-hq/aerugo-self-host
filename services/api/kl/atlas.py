"""The Atlas (F10a, docs/ORG_INTELLIGENCE_PLAN.md O1): one graph of systems, jobs, and data, read under row-level
security from its evidence (migration 0057).

Every read runs in the caller's own transaction, with their principals and the teams OpenFGA says they're on, so the
database shows only nodes and edges whose evidence they may see, and an edge only when both its ends are visible: a
walk can't pass through, or reveal, a node they can't read (invariant 15). A hidden node is a 404, never a 403, and no
answer counts what it left out (invariant 11). Walks are breadth first, at most MAX_DEPTH hops and MAX_NODES nodes, so
a dense graph can't make one unbounded.

Writing is kl.atlas_node and kl.atlas_edge's (the lineage push, catalogs, and manifests call them); `record` wraps them.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from . import config
from .auth import Caller, authenticate
from .db import resolve_org_id, scoped
from .policy import audit, memberships

MAX_DEPTH = 6
MAX_NODES = 2000
STEP_LIMIT = 5000  # edges read per hop

router = APIRouter(prefix="/v1/atlas")

NODE_COLUMNS = "n.id::text AS id, n.kind, n.namespace, n.name, n.first_seen, n.last_seen"
FIND_SQL = f"""
SELECT {NODE_COLUMNS} FROM atlas_nodes n
WHERE strpos(lower(n.namespace || '/' || n.name), lower(%(q)s)) > 0 AND (%(kind)s::text IS NULL OR n.kind = %(kind)s)
ORDER BY n.namespace, n.name LIMIT %(limit)s
"""
NODES_SQL = f"SELECT {NODE_COLUMNS} FROM atlas_nodes n WHERE n.id = ANY(%(ids)s::uuid[])"
EVIDENCE_SQL = """
SELECT x.tier, x.source, x.ref, w.slug AS team, x.first_seen, x.observed_at
FROM atlas_evidence x LEFT JOIN workspaces w ON w.id = x.team_id
WHERE x.node_id = %(id)s ORDER BY x.observed_at DESC
"""
DOWNSTREAM_SQL = "SELECT e.src::text AS src, e.dst::text AS dst, e.kind FROM atlas_edges e WHERE e.src = ANY(%(ids)s::uuid[]) LIMIT %(limit)s"
TOUCHING_SQL = """
SELECT e.src::text AS src, e.dst::text AS dst, e.kind FROM atlas_edges e
WHERE e.src = ANY(%(ids)s::uuid[]) OR e.dst = ANY(%(ids)s::uuid[]) LIMIT %(limit)s
"""

Step = Callable[[list[str]], list[tuple[str, str, str]]]


def walk(start: str, step: Step, depth: int, limit: int = MAX_NODES) -> dict[str, int]:
    """Everything downstream of `start`, each with its distance in hops, nearest first; `step` gives the edges
    (src, dst, kind) leaving a frontier. Stops at `depth` hops or `limit` nodes, whichever comes first."""
    seen, frontier, out = {start}, [start], {}
    for hop in range(1, min(depth, MAX_DEPTH) + 1):
        nxt = sorted({dst for src, dst, _ in step(frontier) if src in frontier and dst not in seen})
        for node in nxt[:max(limit - len(out), 0)]:
            seen.add(node)
            out[node] = hop
        frontier = [n for n in nxt if n in out]
        if not frontier or len(out) >= limit:
            break
    return out


def shortest_path(a: str, b: str, step: Step, depth: int, limit: int = MAX_NODES) -> list[tuple[str, str, str]]:
    """The fewest edges joining `a` and `b`, either way along each (how two things are connected), as (src, dst, kind)
    in order from `a`; [] when none within `depth` hops or `limit` nodes. `step` gives the edges touching a frontier."""
    if a == b:
        return []
    came: dict[str, tuple[str, tuple[str, str, str]]] = {a: (a, ("", "", ""))}
    frontier = [a]
    for _ in range(min(depth, MAX_DEPTH)):
        nxt = []
        for edge in sorted(step(frontier)):
            src, dst, _ = edge
            for here, there in ((src, dst), (dst, src)):
                if here in frontier and there not in came and len(came) < limit:
                    came[there] = (here, edge)
                    nxt.append(there)
        if b in came:
            path, node = [], b
            while node != a:
                node, edge = came[node]
                path.append(edge)
            return path[::-1]
        if not nxt:
            break
        frontier = nxt
    return []


def record(conn, kind: str, namespace: str, name: str, tier: str, source: str, team: str) -> str:
    """A node a team reported, with that report as its evidence; its id."""
    return str(conn.execute("SELECT kl.atlas_node(%s, %s, %s, %s, %s, %s) AS id",
                            (kind, namespace, name, tier, source, team)).fetchone()["id"])


def connect(conn, src: str, dst: str, tier: str, source: str, team: str, kinds: tuple[str, str]) -> str:
    """An edge in the direction data flows, named for its ends: a dataset into the job that reads it, a job into the
    dataset it writes, otherwise one feeding another."""
    kind = {("dataset", "job"): "reads", ("job", "dataset"): "writes"}.get(kinds, "feeds")
    return str(conn.execute("SELECT kl.atlas_edge(%s, %s, %s, %s, %s, %s) AS id",
                            (src, dst, kind, tier, source, team)).fetchone()["id"])


def _node_id(raw: str) -> str:
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        raise HTTPException(404, "node not found")


def _read(caller: Caller):
    """The caller's transaction: their principals, and the teams OpenFGA says they're on."""
    return resolve_org_id(config.ORG_SLUG), sorted(memberships(caller))


def _nodes(conn, ids: list[str]) -> dict[str, dict]:
    return {r["id"]: {**r, "first_seen": r["first_seen"].isoformat(), "last_seen": r["last_seen"].isoformat()}
            for r in conn.execute(NODES_SQL, {"ids": ids}).fetchall()} if ids else {}


@router.get("/nodes")
def find_nodes(q: str = Query(min_length=1, max_length=200), kind: str | None = Query(None, pattern="^[a-z_]{2,20}$"),
               limit: int = Query(50, ge=1, le=200), caller: Caller = Depends(authenticate)):
    """Nodes whose namespace and name hold `q`, among those the caller may see."""
    org_id, teams = _read(caller)
    with scoped(org_id, caller.principals, teams=teams) as conn:
        rows = conn.execute(FIND_SQL, {"q": q, "kind": kind, "limit": limit}).fetchall()
        audit(conn, org_id, caller.username, "atlas.find", teams, {"q": q, "kind": kind, "shown": len(rows)})
    return {"nodes": [{**r, "first_seen": r["first_seen"].isoformat(), "last_seen": r["last_seen"].isoformat()} for r in rows]}


@router.get("/nodes/{node_id}")
def node(node_id: str, caller: Caller = Depends(authenticate)):
    """A node, the evidence for it the caller may see, and its visible edges, each with the node at its other end."""
    nid = _node_id(node_id)
    org_id, teams = _read(caller)
    with scoped(org_id, caller.principals, teams=teams) as conn:
        found = _nodes(conn, [nid])
        if not found:
            raise HTTPException(404, "node not found")
        evidence = [{**r, "first_seen": r["first_seen"].isoformat(), "observed_at": r["observed_at"].isoformat()}
                    for r in conn.execute(EVIDENCE_SQL, {"id": nid}).fetchall()]
        edges = conn.execute(TOUCHING_SQL, {"ids": [nid], "limit": STEP_LIMIT}).fetchall()
        ends = _nodes(conn, sorted({e["src"] for e in edges} | {e["dst"] for e in edges} - {nid}))
        audit(conn, org_id, caller.username, "atlas.node", teams, {"node": nid})
    return {**found[nid], "evidence": evidence,
            "edges": [{"kind": e["kind"], "direction": "out" if e["src"] == nid else "in",
                       "node": ends[e["dst"] if e["src"] == nid else e["src"]]} for e in edges
                      if (e["dst"] if e["src"] == nid else e["src"]) in ends]}


@router.get("/impact")
def impact(node_id: str = Query(alias="node", min_length=1, max_length=64), depth: int = Query(3, ge=1, le=MAX_DEPTH),
           caller: Caller = Depends(authenticate)):
    """What depends on a node: everything downstream of it the caller may see, nearest first."""
    nid = _node_id(node_id)
    org_id, teams = _read(caller)
    with scoped(org_id, caller.principals, teams=teams) as conn:
        if not _nodes(conn, [nid]):
            raise HTTPException(404, "node not found")
        reached = walk(nid, lambda ids: [(r["src"], r["dst"], r["kind"]) for r in
                                         conn.execute(DOWNSTREAM_SQL, {"ids": ids, "limit": STEP_LIMIT}).fetchall()], depth)
        found = _nodes(conn, list(reached))
        audit(conn, org_id, caller.username, "atlas.impact", teams, {"node": nid, "downstream": len(reached)})
    return {"node": nid, "downstream": [{**found[n], "hops": hops} for n, hops in sorted(reached.items(), key=lambda kv: (kv[1], kv[0]))
                                       if n in found]}


@router.get("/path")
def path(start: str = Query(alias="from", min_length=1, max_length=64), end: str = Query(alias="to", min_length=1, max_length=64),
         depth: int = Query(MAX_DEPTH, ge=1, le=MAX_DEPTH), caller: Caller = Depends(authenticate)):
    """The fewest visible edges joining two nodes the caller may see, either way along each; none when they aren't
    joined within `depth` hops."""
    a, b = _node_id(start), _node_id(end)
    org_id, teams = _read(caller)
    with scoped(org_id, caller.principals, teams=teams) as conn:
        if len(_nodes(conn, [a, b])) != len({a, b}):
            raise HTTPException(404, "node not found")
        edges = shortest_path(a, b, lambda ids: [(r["src"], r["dst"], r["kind"]) for r in
                                                 conn.execute(TOUCHING_SQL, {"ids": ids, "limit": STEP_LIMIT}).fetchall()], depth)
        found = _nodes(conn, sorted({x for s, d, _ in edges for x in (s, d)}))
        audit(conn, org_id, caller.username, "atlas.path", teams, {"from": a, "to": b, "hops": len(edges)})
    return {"from": a, "to": b, "edges": [{"src": found[s], "dst": found[d], "kind": k} for s, d, k in edges]}
