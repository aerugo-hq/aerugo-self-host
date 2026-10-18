"""Load the synthetic retailer (F9, kl.company) into the running stack, the way a company's own would come in, and score it.

Loading, every run (each step only adds what's missing, so a run again changes nothing it doesn't need to):

1. Its twelve people who sign in, in the dev Keycloak, each in a group per team (`retail-<team>`), with a verified
   company email, so identity matching and G4's group sync run for real. Local stack only: the loader signs in to
   Keycloak's admin API with the dev admin password.
2. Its teams, made by the org admin, each granted to its group.
3. What each fake serves: the retailer's own Confluence site, Jira site, and Slack workspace (beside the migration
   scenario's, never over them), and its directory in the fake Entra.
4. Its connections, one per team and source, made by the org admin, and its directory (removed and made again: the
   fake mints a new app each time it starts). Each source carries only its own permissions.
5. Each person signs in once (`/v1/whoami`), which links them to their accounts in each source by their email.

Then three checks:

- Readers: each person can open exactly the documents the truth says, no more (a leak) and no fewer.
- Retrieval: each question, asked as its person, scored as kl.evals scores the migration scenario (found, first
  right page, Recall@10, MRR), held to its own baseline ("company" in kl/evals_baseline.json); a forbidden page in
  the results is a leak. Questions waiting on a slice (F8, C1 to C3) are shown, not counted.
- Ask, for the store people (their team has the fake model): a forbidden page cited is a leak.

Sensitive pages a source lets someone read (a medical note in a store channel, an HR note) are reported, not failed:
excluding them is capture's work (C2).

    make company        # load it
    make eval-company   # load it and score it
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

import httpx

from . import company, evals, smoke
from .smoke import BAO_TOKEN, BAO_URL, FAKE_LLM, FAKE_MICROSOFT, TOKEN_URL, api, api_ready, ask_as

ADMIN = "olivia"  # the org admin makes the teams and connections
KEYCLOAK = TOKEN_URL.split("/realms/")[0]
KEYCLOAK_ADMIN_PASSWORD = os.environ.get("KL_KEYCLOAK_ADMIN_PASSWORD", "")
SITES = {"confluence": f"{FAKE_LLM}/retail-confluence", "jira": f"{FAKE_LLM}/retail-jira", "slack": f"{FAKE_LLM}/retail-slack"}
CREDENTIALS = {  # the fakes' own dev credentials, as the migration scenario's connections use them
    "confluence": {"email": "confluence-bot@acme.test", "api_token": "smoke-confluence-token"},
    "jira": {"email": "jira-bot@acme.test", "api_token": "smoke-jira-token"},
    "slack": {"bot_token": "xoxb-smoke-slack-token"},
}
DIRECTORY = "retail-directory"
ASK_TEAM = "stores"
ASK_MODEL = f"{ASK_TEAM}/smoke-fake"
DEFAULT = (7, 6)  # seed and stores: the baseline is recorded for these


def password(username: str) -> str:
    return f"{username}-dev-only"


# ------------------------------------------------------------------ loading
def keycloak_people(world: company.World) -> None:
    """The people who sign in, and a group per team, in the dev realm."""
    if not KEYCLOAK_ADMIN_PASSWORD:
        raise SystemExit("KL_KEYCLOAK_ADMIN_PASSWORD isn't set: the loader makes the retailer's people in the dev Keycloak")
    r = httpx.post(f"{KEYCLOAK}/realms/master/protocol/openid-connect/token", timeout=30, data={
        "grant_type": "password", "client_id": "admin-cli", "username": "admin", "password": KEYCLOAK_ADMIN_PASSWORD})
    if r.status_code != 200:
        raise SystemExit(f"Keycloak's admin sign-in failed: {r.status_code}")
    admin = httpx.Client(base_url=f"{KEYCLOAK}/admin/realms/acme", timeout=30, headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    groups = {}
    for slug, _, _ in company.TEAMS:
        name = f"retail-{slug}"
        found = [g for g in admin.get("/groups", params={"search": name, "exact": "true"}).json() if g["name"] == name]
        if not found:
            admin.post("/groups", json={"name": name}).raise_for_status()
            found = [g for g in admin.get("/groups", params={"search": name, "exact": "true"}).json() if g["name"] == name]
        groups[slug] = found[0]["id"]
    for p in (p for p in world.people if p.username):
        first, last = p.name.split(" ", 1)
        users = admin.get("/users", params={"username": p.username, "exact": "true"}).json()
        if not users:
            admin.post("/users", json={
                "username": p.username, "email": p.email, "emailVerified": True, "enabled": True, "firstName": first, "lastName": last,
                "credentials": [{"type": "password", "value": password(p.username), "temporary": False}]}).raise_for_status()
            users = admin.get("/users", params={"username": p.username, "exact": "true"}).json()
        admin.put(f"/users/{users[0]['id']}/groups/{groups[p.team]}").raise_for_status()
        smoke.PASSWORDS[p.username] = password(p.username)


def teams() -> None:
    for slug, name, found in company.TEAMS:
        r = api("POST", "/v1/workspaces", ADMIN, json={"slug": slug, "name": name, "discoverability": found})
        if r.status_code not in (201, 409):
            raise SystemExit(f"team {slug}: {r.status_code} {r.text[:200]}")
        r = api("PUT", f"/v1/workspaces/{slug}/members", ADMIN, json={"principal": f"group:retail-{slug}", "role": "member"})
        if r.status_code != 200:
            raise SystemExit(f"team {slug}'s group: {r.status_code} {r.text[:200]}")


def serve(world: company.World) -> dict:
    """Each fake's content, and an Entra app for the directory (its certificate is new each time)."""
    for source, state in (("confluence", company.confluence_site(world)), ("jira", company.jira_site(world)),
                          ("slack", company.slack_workspace(world))):
        r = httpx.post(f"{SITES[source]}/setup", json=state, timeout=30)
        if r.status_code != 200:
            raise SystemExit(f"the fake {source} didn't take the company ({r.status_code}): is fake-llm running?")
    if httpx.post(f"{FAKE_MICROSOFT}/setup", json=company.entra_tenant(world), timeout=30).status_code != 200:
        raise SystemExit("the fake Entra didn't take the company's directory")
    return httpx.post(f"{FAKE_MICROSOFT}/app", json={"tenant_id": company.TENANT}, timeout=30).json()


def plan(world: company.World) -> list[tuple[str, str, dict]]:
    """Each team's connection to each source: (team, kind, body)."""
    out = []
    for source, scope in (("confluence", "spaces"), ("jira", "projects"), ("slack", "channels")):
        for slug, _, _ in company.TEAMS:
            held = [c.id for c in world.containers if c.source == source and c.team == slug]
            if not held:
                continue
            body = {"name": f"retail-{source}-{slug}", "api_base": SITES[source] + ("/api" if source == "slack" else ""),
                    scope: held, "every_minutes": 10080, **CREDENTIALS[source]}
            body |= {"company_site": True} if source in ("confluence", "jira") else {"company_workspace": True, "history_days": 3650}
            out.append((slug, source, body))
    return out


def connect(world: company.World, app: dict) -> list[tuple[str, str]]:
    """Make each connection, or sync it again; the directory is made again. Returns (team, name) of each."""
    made = []
    for team, kind, body in plan(world):
        path = f"/v1/workspaces/{team}/connections/{body['name']}"
        exists = api("GET", path, ADMIN).status_code == 200
        r = api("POST", f"{path}/refresh", ADMIN) if exists else api("POST", f"/v1/workspaces/{team}/connections/{kind}", ADMIN, json=body)
        if r.status_code not in (201, 202):
            raise SystemExit(f"{body['name']}: {r.status_code} {r.text[:200]}")
        made.append((team, body["name"]))
    path = f"/v1/workspaces/company/connections/{DIRECTORY}"
    if api("GET", path, ADMIN).status_code == 200:
        api("DELETE", path, ADMIN)
        deadline = time.monotonic() + 120
        while api("GET", path, ADMIN).status_code != 404 and time.monotonic() < deadline:
            time.sleep(2)
    r = api("POST", "/v1/workspaces/company/connections/directory", ADMIN, json={
        "name": DIRECTORY, "provider": "entra", "api_base": FAKE_MICROSOFT, "tenant_id": company.TENANT,
        "client_id": app["client_id"], "certificate": app["certificate"], "every_minutes": 10080})
    if r.status_code != 201:
        raise SystemExit(f"{DIRECTORY}: {r.status_code} {r.text[:200]}")
    return made + [("company", DIRECTORY)]


def wait_synced(made: list[tuple[str, str]], started: datetime) -> None:
    waiting, deadline = dict((name, team) for team, name in made), time.monotonic() + 600
    while waiting and time.monotonic() < deadline:
        for name, team in sorted(waiting.items()):
            got = api("GET", f"/v1/workspaces/{team}/connections/{name}", ADMIN).json()
            if got.get("status") == "error":
                raise SystemExit(f"{name} failed: {got.get('last_error') or got}")
            if got.get("last_synced_at") and datetime.fromisoformat(got["last_synced_at"]) > started:
                waiting.pop(name)
        time.sleep(2)
    if waiting:
        raise SystemExit(f"these didn't sync in ten minutes: {', '.join(sorted(waiting))}")


def load(world: company.World) -> None:
    wrong = company.problems(world)
    if wrong:
        raise SystemExit("the company's truth is inconsistent:\n  " + "\n  ".join(wrong))
    keycloak_people(world)
    teams()
    started = datetime.now(timezone.utc)
    made = connect(world, serve(world))
    wait_synced(made, started)
    for p in (p for p in world.people if p.username):  # signing in links each person to their accounts, by email
        r = api("GET", "/v1/whoami", p.username)
        if r.status_code != 200 or not r.json().get("admitted"):
            raise SystemExit(f"{p.username} couldn't sign in: {r.status_code} {r.text[:200]}")


# ------------------------------------------------------------------ checking
def opened(user: str) -> set[str]:
    keys, offset = set(), 0
    while offset is not None:
        r = api("GET", "/v1/documents", user, params={"limit": 100, "offset": offset})
        assert r.status_code == 200, f"documents as {user}: {r.status_code} {r.text[:200]}"
        keys |= {d["id"] for d in r.json()["documents"]}
        offset = r.json()["next_offset"]
    return keys


def readers_check(world: company.World) -> tuple[list[str], list[str]]:
    """Each person's documents against the truth: (leaks, missing)."""
    loaded = {d.key: d.name for d in world.docs if d.source in company.LOADED}
    leaks, missing = [], []
    print("\nWhat each person can open, against the truth\n")
    for p in (p for p in world.people if p.username):
        want = {world.doc(n).key for n in world.visible(p.username)}
        got = opened(p.username) & set(loaded)
        leaks += [f"{p.username} can open {loaded[k]} ({k})" for k in sorted(got - want)]
        missing += [f"{p.username} can't open {loaded[k]} ({k})" for k in sorted(want - got)]
        mark = "LEAK" if got - want else ("miss" if want - got else "ok")
        print(f"  {mark:4}  {p.username:5} {p.title:28} opens {len(got)} of the company's {len(loaded)} documents; the truth says {len(want)}")
    return leaks, missing


def model_for_stores() -> None:
    """The fake model, registered for Stores, so Ask is checked for its people (and its key put back after a restart)."""
    r = api("POST", f"/v1/workspaces/{ASK_TEAM}/models", ADMIN, json={
        "name": "smoke-fake", "provider": "openai_compatible", "model": "fake", "base_url": f"{FAKE_LLM}/v1", "api_key": "unused"})
    if r.status_code not in (201, 409):
        raise SystemExit(f"the fake model for {ASK_TEAM}: {r.status_code} {r.text[:200]}")
    httpx.post(f"{BAO_URL}/v1/secret/data/kl/acme/{ASK_TEAM}/models/smoke-fake", headers={"X-Vault-Token": BAO_TOKEN},
               json={"data": {"api_key": "unused"}}, timeout=10).raise_for_status()


def score(world: company.World, out) -> tuple[dict, list[str]]:
    key = lambda names: tuple(world.doc(n).key for n in names)
    cases = [evals.Case(q.id, q.asker, q.text, q.kind, key(q.expect), key(q.forbid), q.needs) for q in world.questions]
    rows, leaks, sensitive = [], [], []
    for case, q in zip(cases, world.questions):
        ids = evals.ranked(case.user, case.question)
        found = [k for k in case.expect if k in ids]
        first = min((ids.index(k) + 1 for k in found), default=None)
        leaked = [k for k in case.forbid if k in ids]
        leaks += [f"{case.user} saw {k} ({case.id})" for k in leaked]
        sensitive += [f"{case.user} was shown {k} ({case.id})" for k in key(q.withhold) if k in ids]
        rows.append((case, found, first, leaked))
    print(f"\nThe synthetic retailer: {len(cases)} questions, top {evals.K} search results each\n", file=out)
    for case, found, first, leaked in rows:
        mark = "LEAK" if leaked else ("ok" if len(found) == len(case.expect) else "miss")
        detail = f"found {len(found)} of {len(case.expect)}" + (f", first at #{first}" if first else "") if case.expect else "nothing to find"
        wait = f"  (waits on {case.needs})" if case.needs else ""
        print(f"  {mark:4}  {case.id:26}  {case.kind:12}  as {case.user:5}  {detail}{wait}", file=out)
    model_for_stores()
    answers = {}
    print(f"\nAsk's citations, through {ASK_MODEL}\n", file=out)
    stores_people = {p.username for p in world.people if p.username and p.team == ASK_TEAM}
    for case in cases:
        if case.user not in stores_people or case.needs or not (case.expect or case.forbid):
            continue
        r = ask_as(case.user, case.question, ASK_MODEL)
        if r.status_code != 200:
            raise SystemExit(f"Ask as {case.user} ({case.id}) answered {r.status_code}: {r.text[:200]}")
        cited = [c["id"] for c in r.json().get("citations") or []]
        leaks += [f"{case.user} was cited {k} ({case.id})" for k in cited if k in case.forbid]
        if case.expect:
            answers[case.id] = any(k in case.expect for k in cited)
        mark = "LEAK" if any(k in case.forbid for k in cited) else ("ok" if not case.expect or answers[case.id] else "miss")
        print(f"  {mark:4}  {case.id:26}  as {case.user:5}  cited {', '.join(cited[:3]) or 'nothing'}", file=out)
    card = evals.scorecard(rows, answers, scenario="company")
    print(f"\n  Recall@{evals.K} {card['recall']:.0%}   MRR {card['mrr']:.2f}   over {len(card['cases'])} counted questions; "
          f"{sum(1 for c in cases if c.needs)} wait on slices not built yet", file=out)
    print(f"  Ask cited a right page for {sum(answers.values())} of {len(answers)} questions", file=out)
    for line in sensitive:
        print(f"  SENSITIVE (excluded once C2 lands): {line}", file=out)
    return card, leaks


def main(argv: list[str]) -> int:
    recording = "--record" in argv
    out = sys.stderr if recording else sys.stdout
    baseline_out = sys.stdout
    if recording:  # stdout becomes the baseline's file: everything but the scorecard's JSON goes to stderr instead
        sys.stdout = sys.stderr
    arg = lambda name, default: int(argv[argv.index(name) + 1]) if name in argv else default
    seed, stores = arg("--seed", DEFAULT[0]), arg("--stores", DEFAULT[1])
    world = company.build(seed, stores)
    api_ready()
    load(world)
    if "--load-only" in argv:
        print(f"{company.COMPANY} is loaded ({stores} stores, seed {seed}): sign in as ana, cora, kai, or another of its people "
              "(password: <name>-dev-only).")
        return 0
    leaks, missing = readers_check(world)
    card, retrieval_leaks = score(world, out)
    problems = [f"PERMISSION LEAK: {line}" for line in leaks + retrieval_leaks] + [f"MISSING: {line}" for line in missing]
    if problems:
        for line in problems:
            print(f"  {line}", file=out)
        return 1  # never recorded as a baseline
    print("\n  No permission leaks, and everyone opens exactly what the truth says.", file=out)
    if (seed, stores) != DEFAULT:
        print(f"  Not held to the baseline: it's recorded for seed {DEFAULT[0]} and {DEFAULT[1]} stores.", file=out)
        return 0
    if recording:
        print(json.dumps(evals.with_set("company", card), indent=2, sort_keys=True), file=baseline_out)
        print(f"  Recorded as the company's baseline: commit {evals.BASELINE.name}.", file=out)
        return 0
    return evals.gate("company", card, out, {q.id: q.kind for q in world.questions})


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
