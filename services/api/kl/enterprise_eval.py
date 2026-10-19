"""Load the synthetic enterprise (F10b, kl.enterprise) into the running stack, as the data teams' own would come in,
and score the Atlas against its truth.

Loading, every run (each step adds or refreshes, so a run again changes nothing it doesn't need to):

1. The retailer, as kl.company_eval loads it, with its data teams (EDW and EDP), their pages, and their people.
2. The EDW's exports' OpenLineage, pushed for the EDW team, and Composer's, for the EDP team (a team admin's push).
3. The edp-dbt manifest, pushed from CI by a pipeline registered to the EDP team, with its workload token.
4. The EDW's DataHub (its Teradata platform), connected for the EDW team.

Then the checks, with every node in the Atlas tried (another scenario's lineage too):

- Exactly the truth: the Atlas holds each node and edge the loaded sources reveal, by the names they use, reported by
  the right teams, and nothing else from the data teams (an invented or a missing node or edge fails).
- Permissions: each person opens exactly the nodes a team of theirs reported, sees exactly the edges the truth says,
  and impact from each starting point reaches exactly what they may see.
- The measures (docs/ORG_INTELLIGENCE_PLAN.md section 11): the real nodes and edges found, per source (the ones not
  built yet named with the item that builds them), and the real nodes found under more than one name (O13's to
  resolve). Held to the "atlas" baseline: a source finding less, or more duplicates, fails; finding more says BETTER.
  Usage ranks, systems, and migration waves are in the truth (python -m kl.enterprise), measured once O7, O15, and
  O18 conclude them.

Local stack only, as kl.company_eval. The Atlas keeps a node a source stops naming (its evidence just ages), so run it
on a fresh stack: a node an earlier version of the estate pushed reads as invented.

    make enterprise   # load it
    make atlas-eval   # load it and score it
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone

import httpx
import psycopg
from psycopg.rows import dict_row

from . import company, company_eval, enterprise, evals
from .enterprise import AtlasKey as Key
from .smoke import API, FAKE_CATALOG, FAKE_CI, OWNER_DB, api, api_ready

ADMIN = company_eval.ADMIN
PIPELINE = {"name": "edp-dbt", "repository": "acme/edp-dbt"}
DATAHUB = "edw-datahub"
PEOPLE = ("nia", "otto", "gus")  # on the EDP team, on the EDW team, and on neither
DATA_TEAMS = frozenset(s.team for s in enterprise.SOURCES.values() if s.team)
DEFAULT = (7, 6, enterprise.DEFAULT_REPOS)  # seed, stores, and repositories: the baseline is recorded for these
NODES_SQL = """
SELECT n.id::text AS id, n.kind, n.namespace, n.name,
       array(SELECT w.slug FROM kl.workspaces w WHERE w.id = ANY(n.teams) ORDER BY 1) AS teams
FROM kl.atlas_nodes n
"""
EDGES_SQL = """
SELECT e.src::text AS src, e.dst::text AS dst, e.kind,
       array(SELECT w.slug FROM kl.workspaces w WHERE w.id = ANY(e.teams) ORDER BY 1) AS teams
FROM kl.atlas_edges e
"""


# ------------------------------------------------------------------ loading
def push_lineage(estate: enterprise.Estate) -> None:
    for source in ("lineage-edw", "lineage-edp"):
        team = enterprise.SOURCES[source].team
        r = api("POST", f"/v1/workspaces/{team}/lineage", ADMIN, json={"events": enterprise.lineage_events(estate, source)})
        if r.status_code != 200:
            raise SystemExit(f"{source}'s lineage for {team}: {r.status_code} {r.text[:200]}")


def push_manifest(estate: enterprise.Estate) -> None:
    """The edp-dbt manifest, as the project's CI pushes it: a pipeline registered to the team, its workload token."""
    team = enterprise.SOURCES["dbt"].team
    r = api("POST", f"/v1/workspaces/{team}/pipelines", ADMIN, json={**PIPELINE, "issuer": FAKE_CI})
    if r.status_code not in (201, 409):
        raise SystemExit(f"the {PIPELINE['name']} pipeline: {r.status_code} {r.text[:200]}")

    def headers() -> dict:
        token = httpx.post(f"{FAKE_CI}/token", json={"repository": PIPELINE["repository"]}, timeout=10).json()["token"]
        return {"Authorization": f"Bearer {token}"}
    r = httpx.post(f"{API}/v1/workspaces/{team}/manifests", headers=headers(), timeout=60, json={
        "kind": "dbt", "project": "edp", "readers": [f"group:retail-{team}"], "manifest": enterprise.dbt_manifest(estate)})
    if r.status_code != 202:
        raise SystemExit(f"the edp-dbt manifest: {r.status_code} {r.text[:200]}")
    url, deadline = f"{API}/v1/workspaces/{team}/documents/pushes/{r.json()['push']}", time.monotonic() + 300
    while time.monotonic() < deadline:
        state = httpx.get(url, headers=headers(), timeout=10).json()
        if state["status"] == "done":
            return
        if state["status"] != "queued":
            raise SystemExit(f"the edp-dbt manifest wasn't applied: {state}")
        time.sleep(2)
    raise SystemExit("the worker never applied the edp-dbt manifest")


def connect_datahub(estate: enterprise.Estate) -> tuple[str, str]:
    """The EDW's DataHub, its Teradata platform served by the fake; made once, synced again on each run."""
    team = enterprise.SOURCES["datahub"].team
    served = httpx.post(f"{FAKE_CATALOG}/datahub/setup", json={"platforms": {"teradata": enterprise.datahub_tables(estate)}}, timeout=10)
    if served.status_code != 200:
        raise SystemExit(f"the fake DataHub didn't take the EDW's tables ({served.status_code}): is fake-llm running?")
    path = f"/v1/workspaces/{team}/connections/{DATAHUB}"
    if api("GET", path, ADMIN).status_code == 200:
        r = api("POST", f"{path}/refresh", ADMIN)
    else:
        r = api("POST", f"/v1/workspaces/{team}/connections/catalog", ADMIN, json={
            "name": DATAHUB, "provider": "datahub", "api_base": f"{FAKE_CATALOG}/datahub", "platforms": ["teradata"],
            "token": served.json()["datahub_token"], "every_minutes": 10080})
    if r.status_code not in (201, 202):
        raise SystemExit(f"{DATAHUB}: {r.status_code} {r.text[:200]}")
    return team, DATAHUB


def load(estate: enterprise.Estate) -> None:
    wrong = enterprise.problems(estate)
    if wrong:
        raise SystemExit("the estate's truth is inconsistent:\n  " + "\n  ".join(wrong))
    company_eval.load(estate.world)
    started = datetime.now(timezone.utc)
    push_lineage(estate)
    push_manifest(estate)
    company_eval.wait_synced([connect_datahub(estate)], started)


# ------------------------------------------------------------------ checking
class Atlas:
    """The whole Atlas, read as its owner: each node by id, with the teams that reported it, and each edge."""

    def __init__(self) -> None:
        with psycopg.connect(OWNER_DB, row_factory=dict_row) as conn:
            nodes = conn.execute(NODES_SQL).fetchall()
            edges = conn.execute(EDGES_SQL).fetchall()
        self.nodes: dict[str, tuple[Key, frozenset[str]]] = {
            r["id"]: ((r["kind"], r["namespace"], r["name"]), frozenset(r["teams"])) for r in nodes}
        self.id_of = {key: nid for nid, (key, _) in self.nodes.items()}
        self.teams_of = dict(self.nodes.values())
        self.edges = {(self.nodes[r["src"]][0], self.nodes[r["dst"]][0], r["kind"]): frozenset(r["teams"]) for r in edges}


def show(key: Key) -> str:
    return f"{key[0]} {key[1]}/{key[2]}"


def exactness(estate: enterprise.Estate, atlas: Atlas) -> list[str]:
    """The data teams' part of the Atlas against the truth: every node and edge, with the teams that reported it."""
    want_nodes, want_edges = enterprise.atlas_view(estate)
    have_nodes = {k: t & DATA_TEAMS for k, t in atlas.nodes.values() if t & DATA_TEAMS}
    have_edges = {k: t & DATA_TEAMS for k, t in atlas.edges.items() if t & DATA_TEAMS}
    out = []
    for kind, want, have, fmt in (("node", want_nodes, have_nodes, show),
                                  ("edge", want_edges, have_edges, lambda k: f"{show(k[0])} {k[2]} {show(k[1])}")):
        out += [f"an invented {kind}: {fmt(k)}, from {', '.join(sorted(t))}" for k, t in have.items() if k not in want]
        out += [f"a missing {kind}: {fmt(k)}" for k in want if k not in have]
        out += [f"{kind} {fmt(k)} reported by {', '.join(sorted(have[k]))}, not {', '.join(sorted(t))}"
                for k, t in want.items() if k in have and have[k] != t]
    return out


def permissions(estate: enterprise.Estate, atlas: Atlas) -> tuple[list[str], list[str]]:
    """Each person, every node and edge in the Atlas: a node shown exactly when a team of theirs reported it, an edge
    when a team of theirs reported it and they see both its ends (a leak is one shown otherwise); and the data teams'
    edges each sees, against the truth (what the Atlas holds beyond the truth is exactness's to report). Returns
    (leaks, missing)."""
    view = enterprise.atlas_view(estate)
    leaks, missing = [], []
    print("\nWhat each person sees in the Atlas, against the truth\n")
    for username in PEOPLE:
        teams = {estate.world.person(username).team}
        want_nodes, want_edges = enterprise.visible(view, teams & DATA_TEAMS)
        got_nodes, got_edges, before = set(), set(), (len(leaks), len(missing))
        for nid, (key, reporters) in sorted(atlas.nodes.items()):
            r = api("GET", f"/v1/atlas/nodes/{nid}", username)
            if r.status_code not in (200, 404):
                raise SystemExit(f"the Atlas as {username}: {r.status_code} {r.text[:200]}")
            shown, should = r.status_code == 200, bool(reporters & teams)
            if shown and not should:
                leaks.append(f"{username} opened {show(key)}, reported by {', '.join(sorted(reporters))}")
            elif should and not shown:
                missing.append(f"{username} can't open {show(key)}")
            if shown:
                got_nodes.add(key)
                for e in r.json()["edges"]:
                    other = (e["node"]["kind"], e["node"]["namespace"], e["node"]["name"])
                    got_edges.add((key, other, e["kind"]) if e["direction"] == "out" else (other, key, e["kind"]))
        allowed = {k for k, t in atlas.edges.items() if t & teams and atlas.teams_of[k[0]] & teams and atlas.teams_of[k[1]] & teams}
        leaks += [f"{username} saw the edge {show(a)} {kind} {show(b)}" for a, b, kind in sorted(got_edges - allowed)]
        missing += [f"{username} can't see the edge {show(a)} {kind} {show(b)}" for a, b, kind in sorted((want_edges | allowed) - got_edges)]
        mine = {k for k in got_edges if k in view[1] or k[0] in view[0] or k[1] in view[0]}
        mark = ("LEAK" if len(leaks) > before[0] else "miss" if len(missing) > before[1]
                else "ok" if (len(got_nodes), len(mine)) == (len(want_nodes), len(want_edges)) else "diff")
        print(f"  {mark:4}  {username:5} on {', '.join(sorted(teams)):14} sees {len(got_nodes)} of the Atlas's {len(atlas.nodes)} nodes "
              f"and {len(mine)} of the {len(view[1])} edges its sources reveal; the truth says {len(want_nodes)} and {len(want_edges)}")
    return leaks, missing


def impacts(estate: enterprise.Estate, atlas: Atlas) -> list[str]:
    """Impact from each starting point, as each person: exactly what the truth's visible edges reach, hop for hop;
    a starting point they can't see is a 404."""
    view, out = enterprise.atlas_view(estate), []
    print("\nImpact, as each person\n")
    for source, key in enterprise.IMPACT_FROM:
        start = enterprise.name_in(estate.node(key), source)
        if start not in atlas.id_of:
            out.append(f"impact from {key}: the Atlas has no {show(start) if start else key}")
            continue
        for username in PEOPLE:
            seen, edges = enterprise.visible(view, {estate.world.person(username).team} & DATA_TEAMS)
            r = api("GET", "/v1/atlas/impact", username, params={"node": atlas.id_of[start], "depth": 6})
            if start not in seen:
                ok = r.status_code == 404
                detail = "can't see it" if ok else f"answered {r.status_code}"
            else:
                got = {(d["kind"], d["namespace"], d["name"]): d["hops"] for d in r.json().get("downstream", [])} if r.status_code == 200 else {}
                want = enterprise.downstream(edges, start)
                ok = got == want
                detail = f"reaches {len(got)}" + ("" if ok else f"; the truth says {len(want)}: {sorted(map(show, set(got) ^ set(want)))[:4]}")
            if not ok:
                out.append(f"impact from {show(start)} as {username}: {detail}")
            print(f"  {'ok' if ok else 'FAIL':4}  from {show(start)[:60]:60}  as {username:5} {detail}")
    return out


def measures(estate: enterprise.Estate, atlas: Atlas) -> dict:
    """Real nodes and edges found (docs/ORG_INTELLIGENCE_PLAN.md section 11), per source, and the real nodes found
    under more than one name. A source's edge is found when the Atlas has it between that source's names for its two
    ends, reported by that source's team."""
    by_key = {n.key: n for n in estate.nodes}
    names = {n.key: {(n.kind, ns, nm) for _, ns, nm in n.names} & set(atlas.id_of) for n in estate.nodes}
    by_source = {s: [0, 0] for s in enterprise.SOURCES}
    found = 0
    for e in estate.edges:
        hit = False
        for s in e.sources:
            by_source[s][1] += 1
            a, b = enterprise.name_in(by_key[e.src], s), enterprise.name_in(by_key[e.dst], s)
            if a and b and enterprise.SOURCES[s].team in atlas.edges.get((a, b, e.kind), frozenset()):
                by_source[s][0] += 1
                hit = True
        found += hit
    return {"set": "atlas", "repos": estate.repos, "nodes": [sum(1 for k in names if names[k]), len(estate.nodes)],
            "edges": [found, len(estate.edges)], "by_source": by_source,
            "duplicates": sorted(k for k, held in names.items() if len(held) > 1)}


def report(card: dict, out) -> None:
    (nf, nt), (ef, et) = card["nodes"], card["edges"]
    print(f"\nThe synthetic enterprise against the Atlas ({card['repos']} repositories)\n", file=out)
    print(f"  Real nodes found  {nf} of {nt} ({nf / nt:.0%})", file=out)
    print(f"  Real edges found  {ef} of {et} ({ef / et:.0%}), by source:", file=out)
    for s, (f, of) in card["by_source"].items():
        src = enterprise.SOURCES[s]
        state = f"waits on {src.waits_on}" if src.waits_on else f"loaded, for {src.team}"
        print(f"    {s:14} {f:4} of {of:<4} {state}", file=out)
    print(f"  Found under more than one name (O13 resolves them): {len(card['duplicates'])}", file=out)
    for key in card["duplicates"]:
        print(f"    {key}", file=out)


def gate(card: dict, out) -> int:
    """Hold the measures to the "atlas" baseline: a source finding less, or more duplicates, fails."""
    baseline = evals.baseline_for("atlas")
    if baseline is None:
        print("  No atlas baseline yet: make eval-baseline records one, and then a regression fails the run.", file=out)
        return 0
    pairs = [("nodes found", card["nodes"][0], baseline["nodes"][0]), ("edges found", card["edges"][0], baseline["edges"][0])]
    pairs += [(f"{s}'s edges", f, baseline["by_source"].get(s, [0, 0])[0]) for s, (f, _) in card["by_source"].items()]
    pairs += [("nodes found under more than one name", -len(card["duplicates"]), -len(baseline["duplicates"]))]
    worse = [f"{label}: {abs(now)}, was {abs(was)}" for label, now, was in pairs if now < was]
    better = [f"{label}: {abs(now)}, was {abs(was)}" for label, now, was in pairs if now > was]
    if (card["nodes"][1], card["edges"][1]) != (baseline["nodes"][1], baseline["edges"][1]):
        print(f"  The truth changed: {card['nodes'][1]} nodes and {card['edges'][1]} edges, "
              f"was {baseline['nodes'][1]} and {baseline['edges'][1]}.", file=out)
    for line in better:
        print(f"  BETTER: {line}", file=out)
    if better:
        print("  The bar can rise: make eval-baseline records it.", file=out)
    for line in worse:
        print(f"  WORSE: {line}", file=out)
    if worse:
        return 1
    print("  No regression against the baseline.", file=out)
    return 0


def main(argv: list[str]) -> int:
    recording = "--record" in argv
    out = sys.stderr if recording else sys.stdout
    baseline_out = sys.stdout
    if recording:  # stdout becomes the baseline's file: everything but the scorecard's JSON goes to stderr instead
        sys.stdout = sys.stderr
    arg = lambda name, default: int(argv[argv.index(name) + 1]) if name in argv else default
    size = (arg("--seed", DEFAULT[0]), arg("--stores", DEFAULT[1]), arg("--repos", DEFAULT[2]))
    estate = enterprise.build(company.build(size[0], size[1]), size[2])
    api_ready()
    load(estate)
    if "--load-only" in argv:
        print(f"{company.COMPANY}'s data estate is loaded: sign in as nia (EDP) or otto (EDW), password <name>-dev-only, "
              "and see the Atlas at /v1/atlas.")
        return 0
    atlas = Atlas()
    wrong = exactness(estate, atlas)
    leaks, missing = permissions(estate, atlas)
    wrong += impacts(estate, atlas)
    card = measures(estate, atlas)
    report(card, out)
    problems = [f"PERMISSION LEAK: {line}" for line in leaks] + [f"MISSING: {line}" for line in missing] + wrong
    if problems:
        for line in problems:
            print(f"  {line}", file=out)
        return 1  # never recorded as a baseline
    print("\n  No permission leaks: everyone sees exactly what the truth says, and the Atlas holds exactly what its sources reveal.", file=out)
    if size != DEFAULT:
        print(f"  Not held to the baseline: it's recorded for seed {DEFAULT[0]}, {DEFAULT[1]} stores, and {DEFAULT[2]} repositories.", file=out)
        return 0
    if recording:
        print(json.dumps(evals.with_set("atlas", card), indent=2, sort_keys=True), file=baseline_out)
        print(f"  Recorded as the Atlas's baseline: commit {evals.BASELINE.name}.", file=out)
        return 0
    return gate(card, out)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
