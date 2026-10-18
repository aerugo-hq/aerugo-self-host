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

Since M10 the set has traps a regression falls into: near duplicates (a superseded draft of the standards), stale
against current (an old runbook), distractors (an archive table, a support FAQ, a ticket number one digit off), answers
that need two sources (curated pages beside Jira, Confluence, and Slack, served by smoke's fakes and connected as the
company's people), lineage four hops deep, and permission traps in every kind of source. A case also fails when its first
right page falls more than RANK_SLACK places (any place at all for stale and decision cases), and Ask's citations are checked against each case for the people whose
team has a model.

This is separate from `make eval` (kl.eval, the golden set from slice M2): merging both into one scorecard is next.
The scenario sits under its own containers (scenario/migration/...) and connections (scenario-...), but the smoke suite
expects the plain seed: run `make reset && make up` before `make smoke` on a stack where the demo is loaded.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg
from psycopg.types.json import Jsonb

from .authz import fga
from .smoke import FAKE_CONFLUENCE, FAKE_JIRA, FAKE_SLACK, OWNER_DB, api, api_ready, ask_as, ensure_fake_models

SCENARIO = "migration"
K = 10  # how deep the scorecard looks
BASELINE = Path(__file__).with_name("evals_baseline.json")
MRR_TOLERANCE = 0.05  # how far MRR may fall below the baseline: ranks shift a little as the index grows
RANK_SLACK = 2  # M10: how many places a case's first right page may fall before the run fails
STRICT_KINDS = ("stale", "decision")  # where current knowledge first is the point: their first right page may not fall
ASK_MODEL = "payments/smoke-fake"  # the fake model (smoke's), registered for Payments: Ask is checked for its people


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
    # M10. Near duplicates and stale against current: the 2024 draft says 14 days and 0.5 percent; the 2023 runbook, a truncate.
    Case("checklist-current", "alice", "What is the cutover checklist for an EDP table?", "stale", expect=("kb-edp-standards",)),
    Case("dual-run-length", "bob", "How long is the dual run before a cutover?", "stale", expect=("kb-edp-standards",)),
    Case("reconcile-threshold", "olivia", "What reconciliation difference blocks a cutover?", "stale", expect=("kb-edp-standards",)),
    Case("standards-draft-asked", "olivia", "What did the 2024 draft of the platform standards say?", "lookup",
         expect=("kb-edp-standards-draft",)),
    Case("runbook-current", "alice", "How do I rerun ORD_LOAD after it fails?", "stale", expect=("kb-ord-load-runbook",)),
    Case("runbook-2023-asked", "alice", "What did the 2023 ORD_LOAD runbook say about reruns?", "lookup",
         expect=("kb-ord-load-runbook-2023",)),
    Case("runbook-on-call", "carol", "Who do I page when ORD_LOAD fails?", "owner", expect=("kb-ord-load-runbook",)),
    # Distractors: ORDER_HIST_ARCHIVE beside ORDER_HIST, customers' order history beside the table, EDP-421 beside EDP-412.
    Case("loader-not-archive", "alice", "Which job loads ORDER_HIST every night?", "distractor", expect=("kb-order-hist",)),
    Case("archive-asked", "alice", "What is in ORDER_HIST_ARCHIVE?", "lookup", expect=("kb-order-hist-archive",)),
    Case("archive-trap", "bob", "What is in ORDER_HIST_ARCHIVE?", "permission", forbid=("kb-order-hist-archive",)),
    Case("customers-history", "bob", "How far back can customers see their order history?", "distractor", expect=("kb-support-faq",)),
    Case("migration-not-faq", "carol", "What is the plan for the order history migration?", "distractor",
         expect=("kb-order-migration",)),
    Case("ticket-412", "carol", "What is the status of EDP-412?", "distractor", expect=("kb-order-migration", "jira-EDP-412")),
    Case("ticket-421", "olivia", "What does EDP-421 migrate?", "distractor", expect=("kb-returns-migration",)),
    Case("returns-owner", "dave", "Who coordinates the returns migration?", "owner", expect=("kb-returns-migration",)),
    Case("wave-2-scope", "olivia", "When do customers and returns move to the EDP?", "cross-team",
         expect=("kb-edp-program", "kb-returns-migration")),
    # Answers that need two sources: a curated page beside what was said in Jira, Confluence, or Slack.
    Case("window-and-rehearsal", "carol", "When is the order history cutover window, and how did the rehearsal go?", "two-source",
         expect=("kb-cutover-calendar", "slack-CSCNCUT-1754038800.000100")),
    Case("rehearsal-length", "carol", "How long did the cutover rehearsal take?", "two-source", expect=("slack-CSCNCUT-1754038800.000100",)),
    Case("july-reconciliation", "alice", "What was July's reconciliation difference for edp.orders, and how is it measured?",
         "two-source", expect=("jira-EDP-412", "confluence-88901")),
    Case("rollback", "alice", "How do we roll back the order history cutover, and who owns it?", "two-source",
         expect=("confluence-88902", "jira-EDP-412")),
    Case("cutover-window", "olivia", "When is the order history cutover window?", "lookup", expect=("kb-cutover-calendar",)),
    Case("wave-2-planning", "dave", "When does planning for wave 2 start?", "two-source",
         expect=("slack-CSCNANN-2025-09-15", "kb-edp-program")),
    # Permission traps in every kind of source.
    Case("backfill-trap", "alice", "How many years of history will edp.orders be backfilled?", "permission",
         expect=("kb-order-migration",), forbid=("jira-EDP-430",)),
    Case("backfill-carol", "carol", "How many years of history will edp.orders be backfilled?", "lookup", expect=("jira-EDP-430",)),
    Case("rehearsal-trap", "bob", "How did the cutover rehearsal go?", "permission", forbid=("slack-CSCNCUT-1754038800.000100",)),
    Case("reconciled-trap", "bob", "How is edp.orders reconciled?", "permission", expect=("kb-edp-standards",),
         forbid=("confluence-88901", "kb-edp-orders")),
    Case("ticket-trap", "dave", "Who owns the rollback for EDP-412?", "permission", forbid=("jira-EDP-412", "kb-order-migration")),
    Case("review-trap", "alice", "Is the order retention period changing?", "permission",
         forbid=("kb-retention-2026-review", "kb-order-retention")),
    Case("review-legal", "dave", "Is the order retention period changing?", "lookup", expect=("kb-retention-2026-review",)),
    # Lineage four hops deep: ORD_LOAD, ORDER_HIST, edp.orders, finance_mart.daily_sales, the Executive revenue dashboard.
    Case("exec-depends-on", "bob", "What does the executive revenue dashboard depend on?", "lineage",
         expect=("kb-exec-revenue", "kb-finance-mart")),
    Case("mart-built-from", "bob", "What is finance_mart.daily_sales built from?", "lineage", expect=("kb-finance-mart",)),
    Case("dashboards-on-ord-load", "carol", "Which dashboards depend on the ORD_LOAD job?", "lineage",
         expect=("kb-sales-dashboard", "kb-exec-revenue")),
    Case("edp-orders-readers", "carol", "Which tables read edp.orders?", "lineage", expect=("kb-finance-mart",)),
    Case("finance-lineage-trap", "alice", "Which dashboards read edp.orders?", "permission",
         forbid=("kb-exec-revenue", "kb-sales-dashboard", "kb-finance-mart")),
    Case("exec-owner", "bob", "Who owns the executive revenue dashboard?", "owner", expect=("kb-exec-revenue",)),
    Case("before-retiring", "carol", "What must happen before ORD_LOAD is retired?", "cross-team",
         expect=("kb-order-migration", "kb-edp-standards")),
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
    load_sources()


# M10: what the company's people wrote outside the curated pages, served by smoke's fakes (fake-llm): Payments' Jira
# project and Confluence space, and the company's Slack workspace, connected by alice for Payments. Each person has
# linked their accounts there. Each source carries its own permissions: Jira's project is alice's and carol's, with one
# issue under a security level only carol passes; Confluence's space is alice's and carol's; one Slack channel is
# alice's and carol's, another the whole company's.
ATLASSIAN = {who: f"557058:scn{who}" for who in ("alice", "bob", "carol", "dave")}
SLACK_IDS = {who: f"USCN{who.upper()}" for who in ("alice", "bob", "carol", "dave")}


def _atlassian(who: str) -> dict:
    return {"accountId": ATLASSIAN[who], "emailAddress": f"{who}@acme.test", "displayName": who.title()}


UPDATED = "2025-08-02T10:00:00.000+0000"
JIRA_SITE = {"projects": {"EDP": {"name": "EDP migration", "company_wide": False, "browsers": [_atlassian("alice"), _atlassian("carol")],
                                  "issues": [
    {"key": "EDP-412", "summary": "Migrate ORDER_HIST to edp.orders", "updated": UPDATED,
     "description": "Wave 1: dual-run ORD_LOAD and the Dataform model orders, reconcile every day, repoint the Sales "
                    "dashboard's extract, then retire ORD_LOAD.",
     "comments": [{"author": "Carol", "created": "2025-07-31T16:00:00.000+0000",
                   "body": "Reconciliation for July: the difference was 0.04 percent, under the 0.1 percent threshold."},
                  {"author": "Alice", "created": "2025-08-01T12:00:00.000+0000",
                   "body": "Rollback owner for the cutover: carol. Rolling back means re-enabling ORD_LOAD and pointing the "
                           "Sales extract at ORDER_HIST again."}]},
    {"key": "EDP-421", "summary": "Migrate RETURNS to edp.returns", "updated": UPDATED,
     "description": "Wave 2: the EDW table RETURNS and its load RET_LOAD move to edp.returns in 2026."},
    {"key": "EDP-430", "summary": "Backfill depth for edp.orders", "updated": UPDATED, "security": {"name": "Leads"},
     "viewers": [_atlassian("carol")],
     "description": "Backfill seven years of order history into edp.orders, the most Legal's retention rules allow."}]}}}
CONFLUENCE_SITE = {
    "users": {ATLASSIAN[w]: {"email": f"{w}@acme.test", "name": w.title()} for w in ATLASSIAN},
    "groups": {"payments-data": {"name": "payments-data", "members": [ATLASSIAN["carol"]]}},
    "spaces": {"PAYENG": {"id": "88900", "name": "Payments engineering", "users": [ATLASSIAN["alice"]], "groups": ["payments-data"],
                          "pages": [
        {"id": "88901", "title": "How edp.orders is reconciled", "version": 1,
         "body": "<p>Every morning a Dataform assertion compares edp.orders with ORDER_HIST: the row count per order date, and "
                 "the sum of order totals per day. The difference is reported as a percentage of ORDER_HIST's totals.</p>"},
        {"id": "88902", "title": "Order history cutover rollback plan", "version": 1,
         "body": "<p>If edp.orders is wrong after the cutover: re-enable ORD_LOAD in the scheduler, point the Sales dashboard's "
                 "extract back at ORDER_HIST, and tell Finance analytics. The rollback must start within two hours of the "
                 "problem being found.</p>"}]}}}
SLACK_WORKSPACE = {
    "users": [{"id": SLACK_IDS[w], "team_id": "T0SMOKE", "name": w, "profile": {"real_name": w.title(), "email": f"{w}@acme.test"}}
              for w in SLACK_IDS],
    "channels": {
        "CSCNCUT": {"name": "pay-cutover", "private": True, "members": [SLACK_IDS["alice"], SLACK_IDS["carol"]], "messages": [
            {"ts": "1754038800.000100", "user": SLACK_IDS["carol"], "thread_ts": "1754038800.000100", "reply_count": 1,
             "text": "Cutover rehearsal done: 41 minutes end to end, and no reconciliation differences.",
             "replies": [{"ts": "1754042400.000200", "user": SLACK_IDS["alice"], "thread_ts": "1754038800.000100",
                          "text": "Great. The cutover window stays 18 August, 01:00 to 03:00."}]}]},
        "CSCNANN": {"name": "edp-announce", "members": list(SLACK_IDS.values()), "messages": [
            {"ts": "1757930400.000100", "user": SLACK_IDS["alice"],
             "text": "Wave 2 of the EDP migration (customers and returns) starts planning in January 2026."}]}}}
CONNECTIONS = {  # kind: the connection Payments makes, as alice, with the fakes' own dev credentials; synced once a week
    "jira": {"name": "scenario-jira", "api_base": FAKE_JIRA, "email": "jira-bot@acme.test", "api_token": "smoke-jira-token",
             "projects": ["EDP"], "company_site": False, "every_minutes": 10080},
    "confluence": {"name": "scenario-confluence", "api_base": FAKE_CONFLUENCE, "email": "confluence-bot@acme.test",
                   "api_token": "smoke-confluence-token", "spaces": ["PAYENG"], "every_minutes": 10080},
    "slack": {"name": "scenario-slack", "api_base": f"{FAKE_SLACK}/api", "bot_token": "xoxb-smoke-slack-token",
              "company_workspace": True, "history_days": 3650, "every_minutes": 10080},
}


def link_accounts() -> None:
    """Each person's Atlassian and Slack accounts, linked to them (as each would link their own), in row-level security's
    aliases and in OpenFGA; only links that are missing are written, so a run again changes nothing."""
    accounts = [(who, f"ext:atlassian.{ATLASSIAN[who].replace(':', '.')}") for who in ATLASSIAN] + \
               [(who, f"ext:slack.{SLACK_IDS[who]}") for who in SLACK_IDS]
    with psycopg.connect(OWNER_DB, autocommit=True) as conn:
        org = conn.execute("SELECT id FROM kl.orgs WHERE slug = 'acme'").fetchone()[0]
        for who, source in accounts:
            conn.execute("INSERT INTO kl.principal_aliases (org_id, source, canonical, verified_by) VALUES (%s, %s, %s, 'evals') "
                         "ON CONFLICT DO NOTHING", (org, source, f"user:{who}"))
    missing = [(f"user:{who}", "owner", f"identity:{source[4:]}") for who, source in accounts
               if not fga.check(f"user:{who}", "owner", f"identity:{source[4:]}")]
    if missing:
        fga.write(writes=missing)


def load_sources() -> None:
    """Serve the scenario from the fakes, connect each source (or sync it again), and wait until each has synced."""
    for prefix, state in ((FAKE_JIRA, JIRA_SITE), (FAKE_CONFLUENCE, CONFLUENCE_SITE), (FAKE_SLACK, SLACK_WORKSPACE)):
        r = httpx.post(f"{prefix}/setup", json=state, timeout=10)
        if r.status_code != 200:
            raise SystemExit(f"the fake at {prefix} didn't take the scenario ({r.status_code}): is fake-llm running?")
    link_accounts()
    started = datetime.now(timezone.utc)
    for kind, body in CONNECTIONS.items():
        path = f"/v1/workspaces/payments/connections/{body['name']}"
        exists = api("GET", path, "alice").status_code == 200
        r = api("POST", f"{path}/refresh", "alice") if exists else api("POST", f"/v1/workspaces/payments/connections/{kind}", "alice", json=body)
        if r.status_code not in (201, 202):
            raise SystemExit(f"the scenario's {kind} connection didn't start: {r.status_code} {r.text[:200]}")
    waiting = {body["name"] for body in CONNECTIONS.values()}
    deadline = time.monotonic() + 300
    while waiting and time.monotonic() < deadline:
        for name in sorted(waiting):
            got = api("GET", f"/v1/workspaces/payments/connections/{name}", "alice").json()
            if got.get("last_synced_at") and datetime.fromisoformat(got["last_synced_at"]) > started:
                waiting.discard(name)
        time.sleep(2)
    if waiting:
        raise SystemExit(f"the scenario's sources didn't sync in five minutes: {', '.join(sorted(waiting))}")


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
        {"job": {"namespace": "dataform", "name": "daily_sales"}, "inputs": [{"namespace": "bigquery", "name": "edp.orders"}],
         "outputs": [{"namespace": "bigquery", "name": "finance_mart.daily_sales"}]},  # M10: lineage four hops deep
        {"job": {"namespace": "bi", "name": "exec_revenue_refresh"},
         "inputs": [{"namespace": "bigquery", "name": "finance_mart.daily_sales"}],
         "outputs": [{"namespace": "bi", "name": "exec_revenue_dashboard"}]},
    ],
}
FINANCE_ASSETS = {"sales_dashboard", "sales_daily_extract", "finance_mart.daily_sales", "daily_sales", "exec_revenue_refresh",
                  "exec_revenue_dashboard"}
IMPACT = {"namespace": "edw-etl", "name": "ORD_LOAD", "kind": "job"}


def impact_checks() -> list[str]:
    """What breaks if ORD_LOAD is retired, as each person (slices K3 and K8): carol, on both teams, should see Finance's
    dashboard downstream of Payments' job; alice must see only Payments' side; bob, not on the job's team, gets a 404."""
    print("\nWhat breaks if ORD_LOAD is retired, as each person\n")
    leaks = []
    for user in ("carol", "alice", "bob"):
        r = api("GET", "/v1/lineage/impact", user, params=IMPACT)
        names = [d["name"] for d in r.json()["downstream"]] if r.status_code == 200 else []
        ok = {"carol": {"sales_dashboard", "exec_revenue_dashboard"} <= set(names),
              "alice": r.status_code == 200 and not FINANCE_ASSETS & set(names), "bob": r.status_code == 404}[user]
        print(f"  {'ok' if ok else 'miss':4}  as {user:6}  " + (", ".join(names) or "nothing downstream" if r.status_code == 200 else f"HTTP {r.status_code}"))
        if user == "alice" and FINANCE_ASSETS & set(names):
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
    ("alice", ("kb-order-retention", "kb-sales-dashboard", "kb-retention-2026-review", "kb-finance-mart", "kb-exec-revenue",
               "jira-EDP-430"), ("kb-data-owners", "kb-order-hist")),
    ("bob", ("kb-order-hist", "kb-order-retention", "kb-order-hist-archive", "kb-ord-load-runbook", "jira-EDP-412",
             "confluence-88901", "slack-CSCNCUT-1754038800.000100"), None),
    ("carol", ("kb-order-retention", "kb-retention-2026-review"), ("kb-sales-dashboard", "kb-order-hist")),
    ("dave", ("kb-order-hist", "kb-sales-dashboard", "kb-exec-revenue", "jira-EDP-412", "confluence-88902"), None),
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
HIDDEN_PAGES = (("alice", "kb-sales-dashboard"), ("alice", "kb-order-retention"), ("bob", "kb-order-hist"),
                ("alice", "kb-exec-revenue"), ("bob", "kb-ord-load-runbook"), ("carol", "kb-retention-2026-review"))


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


def scorecard(rows, answers: dict | None = None) -> dict:
    """The scorecard as data: each counted case's result, the totals, and (M10) which cases Ask cited a right page for."""
    counted = [(case, found, first) for case, found, first, _ in rows if case.expect and not case.needs]
    return {
        "scenario": SCENARIO, "k": K,
        "recall": round(sum(len(found) / len(case.expect) for case, found, _ in counted) / len(counted), 4) if counted else 0.0,
        "mrr": round(sum(1 / first if first else 0 for _, _, first in counted) / len(counted), 4) if counted else 0.0,
        "cases": {case.id: {"found": len(found), "of": len(case.expect), "first": first} for case, found, first in counted},
        **({"answers": answers} if answers is not None else {}),
    }


def answer_checks() -> tuple[dict, list[str]]:
    """M10: Ask, as each case's person whose team has the fake model (Payments: alice and carol). Its citations must never
    name a page the person may not see (a leak), and should name a right one; returns which cases did, and the leaks."""
    ensure_fake_models()
    answers, leaks = {}, []
    print(f"\nAsk's citations, through {ASK_MODEL}\n")
    for case in CASES:
        if case.user not in ("alice", "carol") or case.needs or not (case.expect or case.forbid):
            continue
        r = ask_as(case.user, case.question, ASK_MODEL)
        if r.status_code != 200:
            raise SystemExit(f"Ask as {case.user} ({case.id}) answered {r.status_code}: {r.text[:200]}")
        cited = [c["id"] for c in r.json().get("citations") or []]
        leaks += [f"{case.user} was cited {key} ({case.id})" for key in cited if key in case.forbid]
        if case.expect:
            answers[case.id] = any(key in case.expect for key in cited)
        mark = "LEAK" if any(key in case.forbid for key in cited) else ("ok" if not case.expect or answers[case.id] else "miss")
        print(f"  {mark:4}  {case.id:22}  as {case.user:6}  cited {', '.join(cited[:3]) or 'nothing'}")
    return answers, leaks


def baseline_for(name: str) -> dict | None:
    """One set's recorded scorecard (Q1: "migration", the scenario's; "golden", the golden set's), or None. A file from
    before the golden set joined is the scenario's alone."""
    if not BASELINE.exists():
        return None
    held = json.loads(BASELINE.read_text())
    sets = held if "cases" not in held else {"migration": held}
    return sets.get(name)


def with_set(name: str, card: dict) -> dict:
    """The whole baseline file with one set's scorecard replaced, the other sets as they were."""
    held = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    sets = held if "cases" not in held else {"migration": held}
    return {**sets, name: card}


def golden_scorecard(report: dict) -> dict:
    """The golden set's run (kl.eval) as a scorecard of the same shape: each question found or not in the top results,
    and where its first right answer was."""
    cases = {f"{c['user']}: {c['query']}": {"found": int(c["rank"] is not None), "of": 1, "first": c["rank"]} for c in report["cases"]}
    count = max(len(cases), 1)
    return {"set": "golden", "version": report["version"], "k": K,
            "recall": round(sum(c["found"] for c in cases.values()) / count, 4),
            "mrr": round(sum(1 / c["first"] if c["first"] else 0 for c in cases.values()) / count, 4), "cases": cases}


def gate(name: str, card: dict, out) -> int:
    """Hold a scorecard to its set's baseline: say what's better and new, fail on what's worse."""
    baseline = baseline_for(name)
    if baseline is None:
        print(f"  No {name} baseline yet: make eval-baseline records one, and then a regression fails the run.", file=out)
        return 0
    worse, better, new = compare(card, baseline)
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


def compare(card: dict, baseline: dict) -> tuple[list[str], list[str], list[str]]:
    """Against the baseline: what got worse (each fails the run), what got better (record it), and what's new."""
    kinds = {case.id: case.kind for case in CASES}
    worse, better = [], []
    for case_id, was in sorted(baseline.get("cases", {}).items()):
        now = card["cases"].get(case_id)
        if now is None:
            worse.append(f"{case_id}: in the baseline, but not counted now (record the baseline again if that's deliberate)")
        elif now["found"] < was["found"]:
            worse.append(f"{case_id}: finds {now['found']} of {now['of']}, where it found {was['found']}")
        elif now["found"] > was["found"]:
            better.append(f"{case_id}: finds {now['found']} of {now['of']}, up from {was['found']}")
        elif now["first"] and was["first"] and now["first"] > was["first"] + (0 if kinds.get(case_id) in STRICT_KINDS else RANK_SLACK):
            worse.append(f"{case_id}: its first right page fell to #{now['first']}, from #{was['first']}")
        elif now["first"] and (not was["first"] or now["first"] < was["first"]):
            better.append(f"{case_id}: its first right page is now #{now['first']}" + (f", up from #{was['first']}" if was["first"] else ""))
    for case_id, cited_right in sorted(baseline.get("answers", {}).items()):
        if cited_right and not card.get("answers", {}).get(case_id, False):
            worse.append(f"{case_id}: Ask no longer cites a right page")
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
    answers, answer_leaks = answer_checks()
    card = scorecard(rows, answers)
    print(f"\n  Recall@{K} {card['recall']:.0%}   MRR {card['mrr']:.2f}   over {len(card['cases'])} counted cases; "
          f"{sum(1 for case, *_ in rows if case.needs)} wait on slices not built yet", file=out)
    print(f"  Ask cited a right page for {sum(answers.values())} of {len(answers)} cases", file=out)
    problems = [f"{case.user} saw {key} ({case.id})" for case, key in leaks] + answer_leaks + graph_checks() + people_checks() + impact_checks()
    if problems:
        for problem in problems:
            print(f"  PERMISSION LEAK: {problem}", file=out)
        return 1  # never recorded as a baseline
    print("\n  No permission leaks.", file=out)
    if recording:
        print(json.dumps(with_set("migration", card), indent=2, sort_keys=True), file=baseline_out)
        print(f"  Recorded as the scenario's baseline: commit {BASELINE.name}.", file=out)
        return 0
    return gate("migration", card, out)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
