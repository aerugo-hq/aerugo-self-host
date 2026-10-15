"""The evaluation set (slice K1): questions with known right answers, scored against the running stack.

The smoke suite proves Aerugo is safe; this measures how good it is. Each case is a question a real person would ask,
asked as that person, with the pages a right answer must draw on (`expect`) and the pages that person must never see
(`forbid`). A forbidden page in the results is a permission leak and fails the run. Cases that wait on a slice not
built yet (`needs`) are run and shown, but kept out of today's numbers, so the scorecard shows what each slice changes.

The baseline (kl/evals_baseline.json) is the scorecard as it stood when someone recorded it, deliberately:

    make eval-baseline    # record today's scorecard (never one with a permission leak); commit the file

From then on, a run fails if a case finds less than it did, or MRR falls more than MRR_TOLERANCE, and says what got
better, so the bar only rises. A case removed from the set counts as worse until the baseline is recorded again.

The questions are about the fictional migration company in sandbox/scenarios/migration (a legacy warehouse moving to
a BigQuery platform, across Payments, Finance, Legal, and Commons). It's loaded on the first run:

    make eval-scenario    # load the company if needed, then score every case
    make demo             # only load it, to explore it on the Map and in Ask

This is separate from `make eval` (kl.eval, the golden set from slice M2): merging both into one scorecard is next.
The scenario sits under its own containers (scenario/migration/...), but the smoke suite expects the plain seed: run
`make reset && make up` before `make smoke` on a stack where the demo is loaded.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from .smoke import OWNER_DB, api, api_ready

SCENARIO = "migration"
K = 10  # how deep the scorecard looks
BASELINE = Path(__file__).with_name("evals_baseline.json")
MRR_TOLERANCE = 0.05  # how far MRR may fall below the baseline: ranks shift a little as the index grows


@dataclass(frozen=True)
class Case:
    id: str
    user: str
    question: str
    kind: str                     # lookup | lineage | decision | owner | cross-team | permission
    expect: tuple[str, ...] = ()  # pages a right answer draws on, by x-kl id
    forbid: tuple[str, ...] = ()  # pages this user must never see
    needs: str = ""               # a slice this case waits on: run and shown, not counted


# alice: Payments. bob: Finance. carol: Payments and Finance. dave: Legal (private). olivia: the org admin.
CASES = (
    Case("lookup-order-hist", "alice", "What is the ORDER_HIST table and what loads it?", "lookup",
         expect=("kb-order-hist",)),
    Case("lineage-downstream", "bob", "Which dashboards read ORDER_HIST?", "lineage", expect=("kb-sales-dashboard",)),
    Case("lineage-trap", "alice", "Which dashboards read ORDER_HIST?", "permission", forbid=("kb-sales-dashboard",)),
    Case("migration-plan", "alice", "How should we migrate ORDER_HIST to the EDP?", "cross-team",
         expect=("kb-order-migration", "kb-edp-standards")),
    Case("decision-2025", "alice", "What was decided about the order history cutover in 2025?", "decision",
         expect=("kb-decision-cutover-q3", "kb-decision-cutover-q2")),
    Case("decision-latest-first", "alice", "When is the order history cutover?", "decision",
         expect=("kb-decision-cutover-q3",)),  # slice K5: the decision that replaced the Q2 plan ranks first
    Case("owner-ticket", "alice", "Who should I contact about EDP-412?", "owner",
         expect=("kb-order-migration", "kb-data-owners")),
    Case("owner-people", "carol", "Who owns the sales dashboard?", "owner", expect=("kb-sales-dashboard",)),
    Case("impact-cross-team", "carol", "What breaks if we retire the ORD_LOAD job?", "cross-team",
         expect=("kb-order-hist", "kb-sales-dashboard")),  # slice K8: the walk follows what results wrote down
    Case("retention-trap", "alice", "How long must we keep order history?", "permission",
         expect=("kb-edp-standards",), forbid=("kb-order-retention",)),
    Case("retention-legal", "dave", "How long must we keep order history?", "lookup", expect=("kb-order-retention",)),
    Case("program-waves", "olivia", "Which EDW tables move in wave 1 of the EDP migration?", "lookup",
         expect=("kb-edp-program",)),
    Case("standards-everyone", "bob", "What are the EDP platform standards for cutover?", "lookup",
         expect=("kb-edp-standards",)),
)


def load(scenario: str = SCENARIO) -> None:
    """Index the scenario's bundles through the seed job's scenario mode, on every run. Indexing is idempotent and only
    missing authorization tuples are written, so a run also repairs a scenario loaded before its tuples were."""
    pattern = f"scenario/{scenario}/%"
    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        job = conn.execute("INSERT INTO kl.jobs (org_id, kind, payload) SELECT id, 'knowledge.seed', %s FROM kl.orgs "
                           "WHERE slug = 'acme' RETURNING id", (Jsonb({"scenario": scenario}),)).fetchone()[0]
        deadline = time.monotonic() + 300
        while True:
            status, error = conn.execute("SELECT status, last_error FROM kl.jobs WHERE id = %s", (job,)).fetchone()
            if status == "failed":
                raise SystemExit(f"loading the {scenario} scenario failed: {error}")
            if status == "done" or time.monotonic() > deadline:
                break
            time.sleep(2)
        if not conn.execute("SELECT 1 FROM kl.documents WHERE container_ref LIKE %s LIMIT 1", (pattern,)).fetchone():
            raise SystemExit(f"the {scenario} scenario didn't load ({status}): is ./sandbox/scenarios mounted into "
                             "the worker? Run make up to apply the Compose change.")
    for team, events in SCENARIO_LINEAGE.items():  # slice K3: each team's pipelines, as their lineage reports them
        r = api("POST", f"/v1/workspaces/{team}/lineage", "olivia", json={"events": events})
        if r.status_code != 200:
            raise SystemExit(f"lineage for {team} didn't load: {r.status_code} {r.text[:200]}")


# The company's pipelines (slice K3), in OpenLineage's shape: Payments loads ORDER_HIST and builds edp.orders from it;
# Finance's extract reads ORDER_HIST into the Sales dashboard. ORDER_HIST is one asset, whichever team reports it.
SCENARIO_LINEAGE = {
    "payments": [
        {"job": {"namespace": "edw-etl", "name": "ORD_LOAD"}, "inputs": [{"namespace": "orders-system", "name": "orders_export"}],
         "outputs": [{"namespace": "edw", "name": "ORDER_HIST"}]},
        {"job": {"namespace": "dataform", "name": "orders"}, "inputs": [{"namespace": "edw", "name": "ORDER_HIST"}],
         "outputs": [{"namespace": "bigquery", "name": "edp.orders"}]},
    ],
    "finance": [
        {"job": {"namespace": "bi", "name": "sales_daily_extract"}, "inputs": [{"namespace": "edw", "name": "ORDER_HIST"}],
         "outputs": [{"namespace": "bi", "name": "sales_dashboard"}]},
    ],
}
IMPACT = {"namespace": "edw-etl", "name": "ORD_LOAD", "kind": "job"}


def impact_checks() -> list[str]:
    """What breaks if ORD_LOAD is retired, as each person (slices K3 and K8): carol, on both teams, should see Finance's
    dashboard downstream of Payments' job; alice must see only Payments' side; bob, not on the job's team, gets a 404."""
    print("\nWhat breaks if ORD_LOAD is retired, as each person\n")
    leaks = []
    for user in ("carol", "alice", "bob"):
        r = api("GET", "/v1/lineage/impact", user, params=IMPACT)
        names = [d["name"] for d in r.json()["downstream"]] if r.status_code == 200 else []
        ok = {"carol": "sales_dashboard" in names, "alice": r.status_code == 200 and "sales_dashboard" not in names,
              "bob": r.status_code == 404}[user]
        print(f"  {'ok' if ok else 'miss':4}  as {user:6}  " + (", ".join(names) or "nothing downstream" if r.status_code == 200 else f"HTTP {r.status_code}"))
        if user == "alice" and "sales_dashboard" in names:
            leaks.append("alice walked into Finance's lineage")
        if user == "bob" and r.status_code != 404:
            leaks.append("bob walked Payments' lineage")
    return leaks


def ranked(user: str, question: str) -> list[str]:
    r = api("GET", "/v1/search", user, params={"q": question, "limit": K})
    assert r.status_code == 200, f"search as {user}: {r.status_code} {r.text[:200]}"
    return [hit["id"] for hit in r.json()["results"]]


# The map as each person (slice K2): no node or link they can't read, and the recorded links K2 adds, across teams too.
GRAPH_CHECKS = (  # user, pages they must never see, a pair that should be linked (or None)
    ("alice", ("kb-order-retention", "kb-sales-dashboard"), ("kb-data-owners", "kb-order-hist")),
    ("bob", ("kb-order-hist", "kb-order-retention"), None),
    ("carol", ("kb-order-retention",), ("kb-sales-dashboard", "kb-order-hist")),
    ("dave", ("kb-order-hist", "kb-sales-dashboard"), None),
)


def graph_checks() -> list[str]:
    """Print each person's map; return the leaks."""
    leaks = []
    print("\nThe map, as each person\n")
    for user, hidden, pair in GRAPH_CHECKS:
        r = api("GET", "/v1/graph", user)
        assert r.status_code == 200, f"map as {user}: {r.status_code} {r.text[:200]}"
        g = r.json()
        present = {n["id"] for n in g["nodes"]} | {x for e in g["edges"] for x in (e["source"], e["target"])}
        seen = sorted(set(hidden) & present)
        leaks += [f"{user} saw {key} on the map" for key in seen]
        t = g["totals"]
        line = (f"  {'LEAK' if seen else 'ok':4}  as {user:6}  {t['nodes']} nodes, {t['edges']} links: "
                f"{t.get('reference_edges', 0)} recorded references, {t['cross_team_edges']} across teams")
        if pair:
            link = next((e for e in g["edges"] if {e["source"], e["target"]} == set(pair)), None)
            how = (link["kind"] + (f" via {link['evidence']}" if link.get("evidence") else "")) if link else "not linked"
            line += f"; {pair[0]} to {pair[1]}: {how}"
        print(line)
    return leaks


# Who to ask, as each person (slice K6): the expected person among the answers, and a 404 for every page they can't read.
PEOPLE_CHECKS = (  # user, page, a person who should be among the answers
    ("carol", "kb-sales-dashboard", "bob"),        # Finance owns it, and carol is on Finance
    ("alice", "kb-order-migration", "carol"),      # Payments owns it
    ("alice", "kb-edp-standards", "olivia"),       # olivia verified it
)
HIDDEN_PAGES = (("alice", "kb-sales-dashboard"), ("alice", "kb-order-retention"), ("bob", "kb-order-hist"))


def people_checks() -> list[str]:
    """Print who each person is told to ask; return the leaks."""
    print("\nWho to ask, as each person\n")
    for user, page, expected in PEOPLE_CHECKS:
        r = api("GET", f"/v1/concepts/{page}/people", user)
        people = [p["user"] for p in r.json()["people"]] if r.status_code == 200 else []
        shown = ", ".join(people) or ("nobody" if r.status_code == 200 else f"HTTP {r.status_code}")
        print(f"  {'ok' if expected in people else 'miss':4}  as {user:6}  {page}: {shown}")
    return [f"{user} learned who to ask about {page}, which they can't read" for user, page in HIDDEN_PAGES
            if api("GET", f"/v1/concepts/{page}/people", user).status_code != 404]


def scorecard(rows) -> dict:
    """The scorecard as data: each counted case's result, and the totals."""
    counted = [(case, found, first) for case, found, first, _ in rows if case.expect and not case.needs]
    return {
        "scenario": SCENARIO, "k": K,
        "recall": round(sum(len(found) / len(case.expect) for case, found, _ in counted) / len(counted), 4) if counted else 0.0,
        "mrr": round(sum(1 / first if first else 0 for _, _, first in counted) / len(counted), 4) if counted else 0.0,
        "cases": {case.id: {"found": len(found), "of": len(case.expect), "first": first} for case, found, first in counted},
    }


def compare(card: dict, baseline: dict) -> tuple[list[str], list[str], list[str]]:
    """Against the baseline: what got worse (each fails the run), what got better (record it), and what's new."""
    worse, better = [], []
    for case_id, was in sorted(baseline.get("cases", {}).items()):
        now = card["cases"].get(case_id)
        if now is None:
            worse.append(f"{case_id}: in the baseline, but not counted now (record the baseline again if that's deliberate)")
        elif now["found"] < was["found"]:
            worse.append(f"{case_id}: finds {now['found']} of {now['of']}, where it found {was['found']}")
        elif now["found"] > was["found"]:
            better.append(f"{case_id}: finds {now['found']} of {now['of']}, up from {was['found']}")
        elif now["first"] and (not was["first"] or now["first"] < was["first"]):
            better.append(f"{case_id}: its first right page is now #{now['first']}" + (f", up from #{was['first']}" if was["first"] else ""))
    if card["mrr"] < baseline.get("mrr", 0) - MRR_TOLERANCE:
        worse.append(f"MRR {card['mrr']:.2f}, down from {baseline['mrr']:.2f} (more than {MRR_TOLERANCE} lower)")
    new = sorted(set(card["cases"]) - set(baseline.get("cases", {})))
    return worse, better, new


def main(argv: list[str]) -> int:
    recording = "--record" in argv
    out = sys.stderr if recording else sys.stdout
    baseline_out = sys.stdout
    if recording:  # stdout becomes the baseline's file: everything but the scorecard's JSON goes to stderr instead
        sys.stdout = sys.stderr
    api_ready()
    load()
    if "--load-only" in argv:
        print(f"The {SCENARIO} scenario is loaded: open the Map, or Ask about ORDER_HIST.")
        return 0
    rows, leaks = [], []
    for case in CASES:
        ids = ranked(case.user, case.question)
        found = [key for key in case.expect if key in ids]
        first = min((ids.index(key) + 1 for key in found), default=None)
        leaked = [key for key in case.forbid if key in ids]
        leaks += [(case, key) for key in leaked]
        rows.append((case, found, first, leaked))
    print(f"\nEvaluation set: the {SCENARIO} scenario, top {K} search results per question\n", file=out)
    for case, found, first, leaked in rows:
        mark = "LEAK" if leaked else ("ok" if len(found) == len(case.expect) else "miss")
        detail = f"found {len(found)} of {len(case.expect)}" + (f", first at #{first}" if first else "")
        wait = f"  (waits on {case.needs})" if case.needs else ""
        print(f"  {mark:4}  {case.id:22}  {case.kind:10}  as {case.user:6}  {detail}{wait}", file=out)
    card = scorecard(rows)
    print(f"\n  Recall@{K} {card['recall']:.0%}   MRR {card['mrr']:.2f}   over {len(card['cases'])} counted cases; "
          f"{sum(1 for case, *_ in rows if case.needs)} wait on slices not built yet", file=out)
    problems = [f"{case.user} saw {key} ({case.id})" for case, key in leaks] + graph_checks() + people_checks() + impact_checks()
    if problems:
        for problem in problems:
            print(f"  PERMISSION LEAK: {problem}", file=out)
        return 1  # never recorded as a baseline
    print("\n  No permission leaks.", file=out)
    if recording:
        print(json.dumps(card, indent=2, sort_keys=True), file=baseline_out)
        print(f"  Recorded as the baseline: commit {BASELINE.name}.", file=out)
        return 0
    if not BASELINE.exists():
        print(f"  No baseline yet: make eval-baseline records one, and then a regression fails the run.", file=out)
        return 0
    worse, better, new = compare(card, json.loads(BASELINE.read_text()))
    for line in better:
        print(f"  BETTER: {line}", file=out)
    for case_id in new:
        print(f"  NEW: {case_id}, not in the baseline yet", file=out)
    if better or new:
        print("  The bar can rise: make eval-baseline records it.", file=out)
    for line in worse:
        print(f"  WORSE: {line}", file=out)
    if worse:
        return 1
    print("  No regression against the baseline.", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
