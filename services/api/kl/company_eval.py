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
6. Its facts as knowledge units (F1), each written through the API by someone who signs in, curates their own team, and
   can read all of its evidence (preferring the team whose space holds it), and confirmed; the returns policy's two
   versions on one topic; and the planner's version of sell-through as a perspective, with its conflict. A fact no
   one who signs in can fully read (two stores' bike keys) is named, not loaded; sensitive facts never are.

Then six checks:

- Readers: each person can open exactly the documents the truth says, no more (a leak) and no fewer.
- Units: each person reads exactly the units whose evidence they can all read, listed and by id, and a perspective and
  its conflict only when they read both sides.
- Extraction (F2): Stores and Product quality opt in with the fake model; every proposal cites one of their pages or
  tickets, never a chat thread, and each person sees exactly the proposals whose document they can open.
- Consolidation (F3): exactly the conflicts the truth's stale pages make, no fact linked as another's copy, and each
  conflict shown exactly to the people who read both sides.
- Lifecycle (F6): one unit's review comes due and one page changes under another; exactly those two go stale, each in
  its owner team's review queue, refused to another team.
- Gaps (F5a): the store people ask what no page answers; exactly the truth's gaps reach Stores' curator, never naming
  who asked, and a question about someone's health is never kept.
- Retrieval: each question, asked as its person, scored as kl.evals scores the migration scenario (found, first
  right page, Recall@10, MRR), held to its own baseline ("company" in kl/evals_baseline.json); a forbidden page in
  the results is a leak. Questions waiting on a slice (F8, C1 to C3) are shown, not counted.
- Ask, for the store people (their team has the fake model): a forbidden page cited is a leak; so is a knowledge unit
  whose evidence the asker can't read all of, and a superseded unit fails (F4). Which questions Ask leads with a right
  unit for is held to the baseline.

Sensitive pages a source lets someone read (a medical note in a store channel, an HR note) are reported, not failed:
excluding them is capture's work (C2).

    make company        # load it
    make eval-company   # load it and score it
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import psycopg
from psycopg.rows import dict_row

from . import company, evals, slack, smoke, teams_bot
from .smoke import BAO_TOKEN, BAO_URL, FAKE_LLM, FAKE_MICROSOFT, OWNER_DB, TOKEN_URL, api, api_ready, ask_as

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
TEAMS_BOT = f"{FAKE_LLM}/teams-bot"  # the fake Bot Connector, where the Teams bot posts capture's notice (C1a)
CHATS = ("slack", "teams")  # the sources whose documents are conversations: proposed from only where captured
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
    """Each fake's content, an Entra app for the directory, and the company's Teams app, whose team's owner consented to
    it (C1a). Each app's certificate is new each time; the Teams app keeps its id, so its connection keeps its channels."""
    for source, state in (("confluence", company.confluence_site(world)), ("jira", company.jira_site(world)),
                          ("slack", company.slack_workspace(world))):
        r = httpx.post(f"{SITES[source]}/setup", json=state, timeout=30)
        if r.status_code != 200:
            raise SystemExit(f"the fake {source} didn't take the company ({r.status_code}): is fake-llm running?")
    if httpx.post(f"{FAKE_MICROSOFT}/setup", json=company.entra_tenant(world), timeout=30).status_code != 200:
        raise SystemExit("the fake Entra didn't take the company's directory")
    if httpx.post(f"{FAKE_MICROSOFT}/teams", json=company.teams_tenant(world), timeout=30).status_code != 200:
        raise SystemExit("the fake Graph didn't take the company's Teams")
    return {"directory": httpx.post(f"{FAKE_MICROSOFT}/app", json={"tenant_id": company.TENANT}, timeout=30).json(),
            "teams": httpx.post(f"{FAKE_MICROSOFT}/app", json={"tenant_id": company.TENANT, "client_id": company.TEAMS_APP}, timeout=30).json()}


def plan(world: company.World, storage: str = "index") -> list[tuple[str, str, dict]]:
    """Each team's connection to each source: (team, kind, body), keeping what it reads as `storage` says (F11a)."""
    out = []
    for source, scope in (("confluence", "spaces"), ("jira", "projects"), ("slack", "channels")):
        for slug, _, _ in company.TEAMS:
            held = [c.id for c in world.containers if c.source == source and c.team == slug]
            if not held:
                continue
            kept = "pointer" if storage == "live" and source == "slack" else storage  # F11b: Slack can't be live only
            body = {"name": f"retail-{source}-{slug}", "api_base": SITES[source] + ("/api" if source == "slack" else ""),
                    scope: held, "every_minutes": 10080, "storage": kept, **CREDENTIALS[source]}
            body |= {"company_site": True} if source in ("confluence", "jira") else {"company_workspace": True, "history_days": 3650}
            out.append((slug, source, body))
    for slug, _, _ in company.TEAMS:  # C1a: Teams is always indexed, whatever this run keeps the others as
        held = [c.id for c in world.containers if c.source == "teams" and c.team == slug]
        if held:
            out.append((slug, "teams", {"name": f"retail-teams-{slug}", "api_base": FAKE_MICROSOFT, "tenant_id": company.TENANT,
                                        "client_id": company.TEAMS_APP, "team_ids": sorted({company.teams_team(c) for c in held}),
                                        "channels": held, "history_days": 3650, "bot_service": TEAMS_BOT, "every_minutes": 10080}))
    return out


def connect(world: company.World, apps: dict, storage: str = "index") -> list[tuple[str, str]]:
    """Make each connection, or sync it again, switching it to this run's storage when it keeps the other (F11a: its
    full read rewrites everything); a Teams connection takes its app's new certificate (C1a); the directory is made
    again. Returns (team, name) of each."""
    made, app = [], apps["directory"]
    for team, kind, body in plan(world, storage):
        if kind == "teams":
            body = {**body, "certificate": apps["teams"]["certificate"]}
        path = f"/v1/workspaces/{team}/connections/{body['name']}"
        held = api("GET", path, ADMIN)
        if held.status_code == 200 and held.json()["status"] == "error":  # an earlier run's error (its fake restarted empty):
            api("PATCH", path, ADMIN, json={"enabled": False})            # paused and resumed, it's pending again, so it
            api("PATCH", path, ADMIN, json={"enabled": True})             # never reads as this run's
            held = api("GET", path, ADMIN)
        if held.status_code != 200:
            r = api("POST", f"/v1/workspaces/{team}/connections/{kind}", ADMIN, json=body)
        elif kind == "teams":  # its app's certificate is new with the fake: rotating it reads everything again
            r = api("PATCH", path, ADMIN, json={"certificate": body["certificate"]})
        elif (held.json()["settings"].get("storage") or "index") != body["storage"]:
            r = api("PATCH", path, ADMIN, json={"storage": body["storage"]})
        else:
            r = api("POST", f"{path}/refresh", ADMIN)
        if r.status_code not in (200, 201, 202):
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


@dataclass(frozen=True)
class UnitSpec:
    fact: company.Fact
    topic: str
    writer: str          # who signs in, curates their own team, and reads all of the topic's evidence
    team: str
    valid_from: str = ""


def unit_plan(world: company.World) -> tuple[list[UnitSpec], list[str]]:
    """Each fact to load as a unit, older versions before what supersedes them, and the facts no one who signs in can
    fully read. A topic's writer reads every version's and perspective's evidence, from the team whose space holds the
    current version's first piece when someone there can."""
    loaded = {d.name for d in world.docs if d.source in company.LOADED}
    facts = {f.id: f for f in world.facts if f.status != "sensitive" and set(f.evidence) <= loaded}
    older = {f.supersedes: f.id for f in facts.values() if f.supersedes}
    topic = {fid: older.get(fid, fid).lower() for fid in facts}  # a topic is lowercase; the truth's ids keep their case
    personas = [p for p in world.people if p.username]
    seen = {p.username: world.visible(p.username) for p in personas}
    plan, nobody = [], []
    for current in (f for f in facts.values() if f.id not in older):
        versions = ([facts[current.supersedes]] if current.supersedes else []) + [current]
        evidence = {e for f in versions for e in f.evidence} | {e for fid, *_, ev in company.PERSPECTIVES if fid == current.id for e in ev}
        able = [p for p in personas if evidence <= seen[p.username]]
        if not able:
            nobody.append(current.id)
            continue
        first = world.doc(current.evidence[0])
        home = world.container(first.source, first.container).team
        writer = next((p for p in able if p.team == home), able[0])
        for f in versions:
            after = next((v.valid_until for v in versions if v.id == f.supersedes), "")
            plan.append(UnitSpec(f, topic[f.id], writer.username, writer.team,
                                 (date.fromisoformat(after) + timedelta(days=1)).isoformat() if after else ""))
    return plan, nobody


def unit_body(world: company.World, spec: UnitSpec) -> dict:
    places = {p.code for p in world.places}
    where = [s for s in spec.fact.scope if s in places or re.fullmatch(r"[A-Z]{2}", s)]
    return {"kind": spec.fact.kind, "topic": spec.topic, "title": company.unit_title(spec.fact.id),
            "text": spec.fact.text, "evidence": [{"document": world.doc(e).key} for e in spec.fact.evidence],
            "scope": {"places": where, "roles": [s for s in spec.fact.scope if s not in where]},
            **({"structure": {"role": spec.fact.role}} if spec.fact.role else {}),  # F8: a contact's role, resolved per asker
            **({"valid_until": spec.fact.valid_until} if spec.fact.valid_until else {}),
            **({"valid_from": spec.valid_from} if spec.valid_from else {})}


def load_units(world: company.World) -> dict[str, str]:
    """The facts as units, through the API as their writers; a run again finds what's there and adds nothing. Returns
    each fact's unit id."""
    plan, _ = unit_plan(world)
    for writer, team in sorted({(s.writer, s.team) for s in plan}):
        r = api("PUT", f"/v1/workspaces/{team}/members", ADMIN, json={"principal": f"user:{writer}", "role": "curator"})
        if r.status_code != 200:
            raise SystemExit(f"{writer} as {team}'s curator: {r.status_code} {r.text[:200]}")
    ids: dict[str, str] = {}
    for spec in plan:
        body = unit_body(world, spec) | ({"supersedes": ids[spec.fact.supersedes]} if spec.fact.supersedes else {})
        there = api("GET", "/v1/units", spec.writer, params={"topic": spec.topic}).json()["units"]
        found = [u["id"] for u in there if u["title"] == body["title"]]  # each fact's title is its own, versions included
        if found:
            ids[spec.fact.id] = found[0]
            continue
        r = api("POST", f"/v1/workspaces/{spec.team}/units", spec.writer, json=body)
        if r.status_code != 201:
            raise SystemExit(f"the unit for {spec.fact.id}: {r.status_code} {r.text[:200]}")
        ids[spec.fact.id] = r.json()["id"]
        if spec.fact.status != "superseded" and api("POST", f"/v1/units/{ids[spec.fact.id]}/confirm", spec.writer).status_code != 200:
            raise SystemExit(f"{spec.writer} couldn't confirm the unit for {spec.fact.id}")
    writers = {s.fact.id: s.writer for s in plan}
    for fid, kind, holder, context, text, evidence in company.PERSPECTIVES:
        unit, writer = ids[fid], writers[fid]
        detail = api("GET", f"/v1/units/{unit}", writer).json()
        pid = next((p["id"] for p in detail["perspectives"] if p["holder"]["name"] == holder), None)
        if not pid:
            r = api("POST", f"/v1/units/{unit}/perspectives", writer, json={"holder_kind": kind, "holder": holder, "context": context,
                                                                           "text": text, "evidence": [{"document": world.doc(e).key} for e in evidence]})
            if r.status_code != 201:
                raise SystemExit(f"the perspective on {fid}: {r.status_code} {r.text[:200]}")
            pid = r.json()["id"]
        if not any(c["other"]["id"] == pid for c in detail["conflicts"]):
            r = api("POST", f"/v1/units/{unit}/conflicts", writer, json={"perspective": pid, "note": f"{holder}'s version differs."})
            if r.status_code != 201:
                raise SystemExit(f"the conflict on {fid}: {r.status_code} {r.text[:200]}")
    return ids


def load(world: company.World, storage: str = "index") -> dict[str, str]:
    wrong = company.problems(world)
    if wrong:
        raise SystemExit("the company's truth is inconsistent:\n  " + "\n  ".join(wrong))
    keycloak_people(world)
    teams()
    started = datetime.now(timezone.utc)
    made = connect(world, serve(world), storage)
    wait_synced(made, started)
    for p in (p for p in world.people if p.username):  # signing in links each person to their accounts, by email
        r = api("GET", "/v1/whoami", p.username)
        if r.status_code != 200 or not r.json().get("admitted"):
            raise SystemExit(f"{p.username} couldn't sign in: {r.status_code} {r.text[:200]}")
    return load_units(world)


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


def units_check(world: company.World, ids: dict[str, str]) -> tuple[list[str], list[str]]:
    """Each person's units against the truth, listed and by id: a unit shows exactly when they can read all of its
    evidence; the perspective and its conflict exactly when they also read the perspective's. Returns (leaks, missing)."""
    plan, nobody = unit_plan(world)
    evidence = {s.fact.id: set(s.fact.evidence) for s in plan}
    by_id = {uid: fid for fid, uid in ids.items()}
    leaks, missing = [], []
    print(f"\nWhat each person reads of the company's {len(ids)} knowledge units, against the truth\n")
    for p in (p for p in world.people if p.username):
        seen = world.visible(p.username)
        want = {fid for fid, ev in evidence.items() if ev <= seen}
        listed, offset = set(), 0
        while offset is not None:
            r = api("GET", "/v1/units", p.username, params={"limit": 100, "offset": offset})
            assert r.status_code == 200, f"units as {p.username}: {r.status_code} {r.text[:200]}"
            listed |= {by_id[u["id"]] for u in r.json()["units"] if u["id"] in by_id}
            offset = r.json()["next_offset"]
        opened = {fid for fid, uid in ids.items() if api("GET", f"/v1/units/{uid}", p.username).status_code == 200}
        for how, got in (("listed", listed), ("opened", opened)):
            leaks += [f"{p.username} {how} the unit for {fid}" for fid in sorted(got - want)]
            missing += [f"{p.username} never {how} the unit for {fid}" for fid in sorted(want - got)]
        for fid, *_, ev in company.PERSPECTIVES:
            if fid in want:
                detail = api("GET", f"/v1/units/{ids[fid]}", p.username).json()
                should = set(ev) <= seen
                for what, got in (("perspective", bool(detail["perspectives"])), ("conflict", bool(detail["conflicts"]))):
                    if got and not should:
                        leaks.append(f"{p.username} saw the {what} on {fid}")
                    elif should and not got:
                        missing.append(f"{p.username} can't see the {what} on {fid}")
        mark = "LEAK" if (listed | opened) - want else ("miss" if want - (listed & opened) else "ok")
        print(f"  {mark:4}  {p.username:5} {p.title:28} is shown {len(listed | opened)} of {len(ids)} units; the truth says {len(want)}")
    if nobody:
        print(f"  Not loaded, since no one who signs in can read all of their evidence: {', '.join(nobody)}")
    return leaks, missing


DUE_SQL = """SELECT count(*) AS n FROM kl.knowledge_units WHERE status <> ALL('{superseded,retired}')
AND (consolidated_at IS NULL OR updated_at > consolidated_at)"""
FOUND_SQL = "SELECT unit_id::text AS a, other_unit::text AS b FROM kl.unit_conflicts WHERE opened_by = 'consolidation' AND other_unit IS NOT NULL"
LINKS_SQL = "SELECT id::text AS id, duplicate_of::text AS original FROM kl.knowledge_units WHERE duplicate_of IS NOT NULL"


def consolidation_check(world: company.World, ids: dict[str, str]) -> tuple[list[str], list[str]]:
    """F3 on the retailer: once every unit has been compared, consolidation opened exactly the conflicts the truth's stale
    pages make (each stale fact against the current facts it contradicts), linked none of the truth's
    facts as copies of each other, and each person sees a conflict exactly when they can read both of its sides.
    Returns (leaks, missing); a conflict the truth doesn't hold counts as missing precision, and fails too."""
    by_id = {uid: fid for fid, uid in ids.items()}
    facts = {f.id: f for f in world.facts}
    want = {frozenset((f.id, g)) for f in world.facts if f.id in ids for g in f.contradicts if g in ids}
    deadline = time.monotonic() + 300
    with psycopg.connect(OWNER_DB, row_factory=dict_row) as conn:
        while conn.execute(DUE_SQL).fetchone()["n"] and time.monotonic() < deadline:
            time.sleep(5)
        found = conn.execute(FOUND_SQL).fetchall()
        links = conn.execute(LINKS_SQL).fetchall()
    among = {frozenset((by_id[r["a"]], by_id[r["b"]])) for r in found if r["a"] in by_id and r["b"] in by_id}
    leaks, missing = [], [f"consolidation never found {' against '.join(sorted(pair))}" for pair in sorted(want - among, key=sorted)]
    missing += [f"consolidation opened a conflict the truth doesn't hold: {' against '.join(sorted(pair))}" for pair in sorted(among - want, key=sorted)]
    missing += [f"{by_id[r['id']]} was linked as a copy of {by_id[r['original']]}" for r in links if r["id"] in by_id and r["original"] in by_id]
    print(f"\nConsolidation (F3): {len(among)} conflicts among the company's units (the truth holds {len(want)}), "
          f"{len(found) - len(among)} more involving proposals, {len(links)} copies linked; who sees each\n")
    for pair in sorted(among & want, key=sorted):
        first, second = sorted(pair)
        for person in (p for p in world.people if p.username):
            seen = world.visible(person.username)
            should = set(facts[first].evidence) <= seen and set(facts[second].evidence) <= seen
            r = api("GET", f"/v1/units/{ids[first]}", person.username)
            shows = r.status_code == 200 and any(c["other"].get("id") == ids[second] or c["unit"] == ids[second]
                                                 for c in r.json()["conflicts"] if c["opened_by"] == "consolidation")
            if shows and not should:
                leaks.append(f"{person.username} saw the conflict between {first} and {second}")
            elif should and not shows:
                missing.append(f"{person.username} can't see the conflict between {first} and {second}")
        readers = [p.username for p in world.people if p.username and set(facts[first].evidence) | set(facts[second].evidence) <= world.visible(p.username)]
        print(f"  {first} against {second}: shown to {', '.join(readers) or 'no one who signs in'}, as the truth says")
    return leaks, missing


LIFECYCLE_SQL = "SELECT id::text AS id, status FROM kl.knowledge_units WHERE id = ANY(%s::uuid[])"
HASH_SQL = "SELECT content_hash FROM kl.documents WHERE external_key = %s"


def lifecycle_check(world: company.World, unit_ids: dict[str, str], out) -> tuple[list[str], list[str]]:
    """F6 on the retailer: time passes for one unit (its review comes due) and one page changes; exactly that unit and
    every unit citing the page go stale, each in its owner team's review queue with why, and another team's people are
    refused the queue. Their owners confirm them first, as owners do, so each has its review restarted and its evidence
    snapshotted. The page is put back afterwards."""
    fact_due, (_, page, (old, new)) = company.LIFECYCLE_DUE, company.LIFECYCLE_CHANGED
    writers = {spec.fact.id: spec for spec in unit_plan(world)[0]}
    citing = {f.id for f in world.facts if page in f.evidence and f.id in unit_ids and f.status != "superseded"}
    want = {fact_due} | citing
    missing: list[str] = []
    for fid in sorted(want):
        r = api("POST", f"/v1/units/{unit_ids[fid]}/confirm", writers[fid].writer)
        if r.status_code != 200:
            raise SystemExit(f"confirming {fid} as {writers[fid].writer}: {r.status_code} {r.text[:200]}")
    doc = world.doc(page)
    team = world.container(doc.source, doc.container).team

    def serve_page(text: str, version: int) -> str:
        state = company.confluence_site(world)
        for space in state["spaces"].values():
            for p in space["pages"]:
                if p["id"] == doc.key.split("-", 1)[1]:
                    p |= {"body": company.storage(text), "version": version, "modified": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")}
        httpx.post(f"{SITES['confluence']}/setup", json=state, timeout=30).raise_for_status()
        with psycopg.connect(OWNER_DB, row_factory=dict_row) as conn:
            before = conn.execute(HASH_SQL, (doc.key,)).fetchone()["content_hash"]
        api("POST", f"/v1/workspaces/{team}/connections/retail-confluence-{team}/refresh", ADMIN)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            with psycopg.connect(OWNER_DB, row_factory=dict_row) as conn:
                now = conn.execute(HASH_SQL, (doc.key,)).fetchone()["content_hash"]
            if now != before:
                return now
            time.sleep(3)
        raise SystemExit(f"the changed page {doc.key} was never synced again")

    with psycopg.connect(OWNER_DB, autocommit=True) as conn:  # its review came due: confirmed longer ago than its cadence
        conn.execute("UPDATE kl.knowledge_units SET reviewed_at = now() - make_interval(days => review_days + 1) WHERE id = %s",
                     (unit_ids[fact_due],))
    serve_page(doc.text.replace(old, new), 2)
    deadline, stale = time.monotonic() + 180, set()
    while time.monotonic() < deadline:
        with psycopg.connect(OWNER_DB, row_factory=dict_row) as conn:
            stale = {r["id"] for r in conn.execute(LIFECYCLE_SQL, (sorted(set(unit_ids.values())),)) if r["status"] == "stale"}
        if {unit_ids[f] for f in want} <= stale:
            break
        time.sleep(3)
    by_id = {uid: fid for fid, uid in unit_ids.items()}
    got = {by_id[u] for u in stale}
    missing += [f"lifecycle never made {f} stale" for f in sorted(want - got)]
    missing += [f"lifecycle made {f} stale, which the truth leaves as it is" for f in sorted(got - want)]
    print(f"\nLifecycle (F6): {len(got)} of the company's {len(unit_ids)} units went stale; the truth says {len(want)}\n", file=out)
    for fid in sorted(want & got):
        spec = writers[fid]
        queue = api("GET", f"/v1/workspaces/{spec.team}/review", spec.writer)
        why = {u["id"]: u["why"] for u in queue.json().get("stale", [])} if queue.status_code == 200 else {}
        if unit_ids[fid] not in why:
            missing.append(f"{spec.writer} doesn't find {fid} in {spec.team}'s review queue")
        outsider = next(p.username for p in world.people if p.username and p.team != spec.team)
        if api("GET", f"/v1/workspaces/{spec.team}/review", outsider).status_code != 403:
            missing.append(f"{outsider} read {spec.team}'s review queue")
        print(f"  {fid}: stale, {why.get(unit_ids[fid])!r}; in {spec.team}'s queue for {spec.writer}", file=out)
    serve_page(doc.text, 3)  # put the page back
    return [], missing


def gaps_check(world: company.World, out) -> tuple[list[str], list[str]]:
    """F5a on the retailer: the store people ask what no page answers (company.GAP_QUESTIONS) and say whether it helped;
    exactly the truth's gaps reach Stores' curator, each by how many people and never by whom, another team is refused
    (every store persona is a curator here, so smoke proves a member's refusal), and a question about someone's health
    was never kept."""
    model_for_stores()
    curators = sorted({spec.writer for spec in unit_plan(world)[0] if spec.team == ASK_TEAM})  # the loader made each a curator
    curator = curators[0]
    leaks, missing = [], []
    for _, _, helped, asked in company.GAP_QUESTIONS:
        for user, question in asked:
            r = ask_as(user, question, ASK_MODEL)
            if r.status_code != 200 or not r.json().get("question"):
                raise SystemExit(f"Ask as {user}: {r.status_code} {r.text[:200]}")
            api("POST", "/v1/feedback", user, json={"kind": "ask", "query": question, "verdict": "helpful" if helped else "not_helpful",
                                                    "question": r.json()["question"]})
    wordings = {gid: {q for _, q in asked} for gid, _, _, asked in company.GAP_QUESTIONS}
    want = {gid for gid, gap, _, _ in company.GAP_QUESTIONS if gap}
    got: dict[str, dict] = {}
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        listed = api("GET", f"/v1/workspaces/{ASK_TEAM}/gaps", curator).json().get("gaps", [])
        got = {gid: g for g in listed for gid, qs in wordings.items() if g["question"] in qs}
        if want <= set(got):
            break
        time.sleep(5)
    missing += [f"{gid} never became a gap" for gid in sorted(want - set(got))]
    missing += [f"{gid} became a gap, which the truth says it isn't" for gid in sorted(set(got) - want)]
    names = {p.username for p in world.people if p.username} | {p.name for p in world.people}
    leaks += [f"the gap {gid} named who asked" for gid, g in got.items() if any(n in json.dumps(g) for n in names)]
    outsider = next(p.username for p in world.people if p.username and p.team != ASK_TEAM)
    if api("GET", f"/v1/workspaces/{ASK_TEAM}/gaps", outsider).status_code != 403:
        leaks.append(f"{outsider} read {ASK_TEAM}'s gaps without being its curator")
    with psycopg.connect(OWNER_DB) as conn:
        if conn.execute("SELECT 1 FROM kl.questions WHERE text ILIKE %s", ("%surgery%",)).fetchone():
            leaks.append("a question about someone's health was kept")
    print(f"\nGaps (F5a): {len(got)} of the truth's questions became gaps; the truth says {len(want)}\n", file=out)
    for gid, g in sorted(got.items()):
        print(f"  {gid}: {g['question']!r}, asked {g['asked']} times by {g['askers']} people; seen by {curator}, refused to {outsider}", file=out)
    return leaks, missing


def model_for_stores(team: str = ASK_TEAM) -> None:
    """The fake model, registered for a team (Stores, so Ask is checked for its people), its key put back after a restart."""
    r = api("POST", f"/v1/workspaces/{team}/models", ADMIN, json={
        "name": "smoke-fake", "provider": "openai_compatible", "model": "fake", "base_url": f"{FAKE_LLM}/v1", "api_key": "unused"})
    if r.status_code not in (201, 409):
        raise SystemExit(f"the fake model for {team}: {r.status_code} {r.text[:200]}")
    httpx.post(f"{BAO_URL}/v1/secret/data/kl/acme/{team}/models/smoke-fake", headers={"X-Vault-Token": BAO_TOKEN},
               json={"data": {"api_key": "unused"}}, timeout=10).raise_for_status()


EXTRACT_TEAMS = ("stores", "product-quality", "distribution")  # F2: opted in with the fake model, which measures the pipeline,
# not quality; distribution's for its Teams channel (C1a)
TEAM_DOCUMENTS_SQL = """
SELECT count(*) FILTER (WHERE s.content_hash = d.content_hash) AS read, count(*) AS of
FROM kl.workspace_documents wd JOIN kl.workspaces w ON w.id = wd.workspace_id JOIN kl.documents d ON d.id = wd.document_id
LEFT JOIN kl.extraction_state s ON s.workspace_id = w.id AND s.document_id = d.id
WHERE w.slug = %s AND (d.source_kind = ANY(%s) OR (d.source_kind IN ('slack', 'teams') AND d.container_ref = ANY(%s)))
"""
PROPOSALS_SQL = """
SELECT u.id::text AS id, d.external_key, d.source_kind, w.slug AS team, u.duplicate_of::text AS duplicate_of FROM kl.knowledge_units u
JOIN kl.documents d ON d.id = u.evidence[1] JOIN kl.workspaces w ON w.id = u.owner_team
WHERE u.created_by IN ('extraction', 'slack-save') AND u.status = 'proposed' AND w.slug = ANY(%s)
"""


def signed_post(url: str, raw: bytes, signing: str) -> httpx.Response:
    """A delivery as the Slack app sends it, signed with its signing secret."""
    stamp = str(int(time.time()))
    signature = "v0=" + hmac.new(signing.encode(), b"v0:" + stamp.encode() + b":" + raw, hashlib.sha256).hexdigest()
    return httpx.post(url, content=raw, timeout=10, headers={
        "Content-Type": "application/x-www-form-urlencoded", "X-Slack-Request-Timestamp": stamp, "X-Slack-Signature": signature})


def save_messages(world: company.World, channel: str, hook: str, signing: str) -> list[str]:
    """C2c on the retailer: each message the truth saves in this channel, by its thread's first author through "Save to
    Aerugo" (signed as Slack sends it), until its proposed unit exists. Returns the saves that never landed."""
    from .capture import saved_topic
    missing = []
    for name, i in (s for s in company.SAVES if world.doc(s[0]).container == channel):
        doc = world.doc(name)
        ts, saver = company._ts(doc.messages[i][1].replace("T", " "), i), next(p for p in world.people if p.name == doc.messages[0][0]).slack
        topic = saved_topic(channel, ts)
        if not saved_unit(topic):
            payload = {"type": "message_action", "callback_id": slack.SAVE_SHORTCUT, "channel": {"id": channel},
                       "user": {"id": saver}, "team": {"id": company.SLACK_TEAM}, "message_ts": ts}
            r = signed_post(hook + "/interactions", urlencode({"payload": json.dumps(payload)}).encode(), signing)
            if r.status_code != 200:
                raise SystemExit(f"saving {name}: {r.status_code} {r.text[:200]}")
            deadline = time.monotonic() + 300
            while not saved_unit(topic) and time.monotonic() < deadline:
                time.sleep(3)
        if not saved_unit(topic):
            missing.append(f"the save of {name}'s message {i} never became a proposed unit")
    return missing


def saved_unit(topic: str) -> bool:
    with psycopg.connect(OWNER_DB) as conn:
        return bool(conn.execute("SELECT 1 FROM kl.knowledge_units WHERE created_by = 'slack-save' AND topic = %s", (topic,)).fetchone())


SLACK_ASKER = "ana"  # C2d: a store associate, asking in her own store's channel


def slack_ask_check(world: company.World, out) -> tuple[list[str], list[str]]:
    """C2d on the retailer: a store person asks through Slack, in her own store's channel, as Slack sends it (a signed
    command); she's answered privately, with her own Ask's sources, never a page the truth says she may not see.
    Returns (leaks, missing)."""
    q = next(q for q in world.questions if q.asker == SLACK_ASKER and q.facts and not q.needs)
    me = world.person(SLACK_ASKER)
    channel, path = f"CRTS{me.place}", "/v1/workspaces/stores/connections/retail-slack-stores"
    if api("PUT", path + "/answers", ADMIN, json={"model": ASK_MODEL}).status_code != 200:
        raise SystemExit(f"{path}: Slack answers couldn't be set up")
    signing = secrets.token_hex(16)
    hook = api("POST", path + "/webhook", ADMIN, json={"signing_secret": signing}).json()["url"]
    replies = lambda: [x["text"] for x in httpx.get(f"{SITES['slack']}/posted", timeout=10).json()
                       if x.get("ephemeral") and x.get("user") == me.slack and x["channel"] == channel]
    before = len(replies())
    raw = urlencode({"command": "/aerugo", "text": f"ask {q.text}", "channel_id": channel, "team_id": company.SLACK_TEAM,
                     "user_id": me.slack}).encode()
    if signed_post(hook + "/commands", raw, signing).status_code != 200:
        raise SystemExit("the ask command was refused")
    deadline = time.monotonic() + 180
    while len(replies()) == before and time.monotonic() < deadline:
        time.sleep(2)
    if len(replies()) == before:
        return [], [f"{SLACK_ASKER}'s Slack ask ({q.id}) got no private reply"]
    reply = replies()[-1]
    linked = set(re.findall(r"#/documents/([^|>]+)[|]", reply))
    own = {c["id"] for c in ask_as(SLACK_ASKER, q.text, ASK_MODEL).json()["citations"]}
    by_key, seen = {d.key: d for d in world.docs}, world.visible(SLACK_ASKER)
    forbidden = {by_key[k].key for k in by_key if by_key[k].name in q.forbid}
    leaks = [f"{SLACK_ASKER}'s Slack answer named {k}, which she may not see" for k in sorted(linked)
             if k in forbidden or (k in by_key and by_key[k].name not in seen)]
    leaks += [f"{SLACK_ASKER}'s Slack answer named {k}, which her own Ask didn't" for k in sorted(linked - own)]
    missing = [] if linked or not own else [f"{SLACK_ASKER}'s Slack answer named none of her Ask's sources"]
    print(f"\nSlack ask (C2d): {SLACK_ASKER} asked {q.id} in {channel}, answered privately with {len(linked)} of her "
          f"Ask's {len(own)} sources linked, {len(leaks)} she may not see or her Ask didn't cite", file=out)
    return leaks, missing


def capture_on(world: company.World) -> list[str]:
    """C2b on the retailer: each channel the truth captures is turned on as a company would: its creator runs `/aerugo
    capture on` in it (a command signed with the app's signing secret, as Slack sends it), then its team's admin
    confirms. Returns what went wrong: the bot posting anything but the notice, anywhere but a captured channel."""
    names, problems, saves = {slug: name for slug, name, _ in company.TEAMS}, [], []
    for channel in company.CAPTURED:
        c = next(c for c in world.containers if c.id == channel)
        if c.source == "teams":
            problems += teams_capture_on(c, names[c.team])
            continue
        path = f"/v1/workspaces/{c.team}/connections/retail-slack-{c.team}"
        state = lambda: next((x["capture"] for x in api("GET", path + "/capture", ADMIN).json()["channels"] if x["channel"] == channel), None)
        signing = secrets.token_hex(16)  # the app's signing secret, set on the connection's webhook each run
        hook = api("POST", path + "/webhook", ADMIN, json={"signing_secret": signing})
        if hook.status_code != 201:
            raise SystemExit(f"{path}: no webhook for the capture command ({hook.status_code})")
        if state() == "on":
            saves += save_messages(world, channel, hook.json()["url"], signing)
            continue
        raw = urlencode({"command": "/aerugo", "text": "capture on", "channel_id": channel, "team_id": company.SLACK_TEAM,
                         "user_id": company.creator_of(world, c).slack}).encode()
        r = signed_post(hook.json()["url"] + "/commands", raw, signing)  # the address the app's admin is given
        if r.status_code != 200:
            raise SystemExit(f"the capture command for {channel}: {r.status_code} {r.text[:200]}")
        deadline = time.monotonic() + 120
        while state() != "requested" and time.monotonic() < deadline:
            time.sleep(2)
        r = api("POST", f"{path}/capture/{channel}", ADMIN, json={"action": "confirm"})
        if r.status_code != 200:
            raise SystemExit(f"confirming capture of {channel}: {r.status_code} {r.text[:200]}")
        notice = slack.NOTICE_ON.format(team=names[c.team])
        while not any(p["channel"] == channel and p["text"] == notice for p in httpx.get(f"{SITES['slack']}/posted", timeout=10).json()) \
                and time.monotonic() < deadline + 120:
            time.sleep(2)
        saves += save_messages(world, channel, hook.json()["url"], signing)  # someone else's message: only once capture is on
    ours = [p for p in httpx.get(f"{TEAMS_BOT}/posted", timeout=10).json() if p.get("app") == company.TEAMS_APP]  # not smoke's
    teams_notices = [p for p in ours if p["channel"] in company.CAPTURED]
    problems += [f"the Teams bot posted in {p['channel']}: {p['text'][:80]}" for p in ours if p["channel"] not in company.CAPTURED
                 or p["text"] != teams_bot.NOTICE_ON.format(team=names[world.container("teams", p["channel"]).team])]
    posted = [p for p in httpx.get(f"{SITES['slack']}/posted", timeout=10).json() if not p.get("ephemeral")]  # C2c's replies aside
    for p in posted:
        team = next((c.team for c in world.containers if c.id == p["channel"] and c.id in company.CAPTURED), None)
        if not team or p["text"] != slack.NOTICE_ON.format(team=names[team]):
            problems.append(f"the bot posted in {p['channel']}: {p['text'][:80]}")
    print(f"\nCapture (C2b, C1a): {', '.join(company.CAPTURED)} on, with both keys; the Slack bot posted {len(posted)} message(s) and "
          f"the Teams bot {len(teams_notices)}, {len(problems)} of them anything but a captured channel's notice; "
          f"{len(company.SAVES) - len(saves)} of {len(company.SAVES)} saves (C2c) proposed")
    return problems + saves


def teams_capture_on(c: company.Container, team_name: str) -> list[str]:
    """C1a on the retailer: the Teams channel arrives requested, its team's owner having consented to the company's app
    (the first key); its team's admin confirms, and the bot posts its one notice there. Returns what went wrong."""
    path = f"/v1/workspaces/{c.team}/connections/retail-teams-{c.team}"
    state = lambda: next((x["capture"] for x in api("GET", path + "/capture", ADMIN).json()["channels"] if x["channel"] == c.id), None)
    deadline = time.monotonic() + 120
    while state() not in ("requested", "on") and time.monotonic() < deadline:
        time.sleep(2)
    if state() not in ("requested", "on"):
        return [f"{c.id} never arrived requesting capture, though its team's owner consented: {state()}"]
    if state() == "on":
        return []
    r = api("POST", f"{path}/capture/{c.id}", ADMIN, json={"action": "confirm"})
    if r.status_code != 200:
        raise SystemExit(f"confirming capture of {c.id}: {r.status_code} {r.text[:200]}")
    notice = teams_bot.NOTICE_ON.format(team=team_name)
    while not any(p["channel"] == c.id and p["text"] == notice for p in httpx.get(f"{TEAMS_BOT}/posted", timeout=10).json()) \
            and time.monotonic() < deadline + 120:
        time.sleep(2)
    return []


def extraction_check(world: company.World, pointer: bool = False) -> tuple[list[str], list[str]]:
    """F2 on the retailer: two teams opt in with the fake model; every proposal cites one of their pages or tickets, or
    a thread of a channel captured with both keys (C2b), never other chat, and each person sees exactly the proposals
    whose document they can open. Which documents are pages, tickets, and captured threads comes from the truth, never
    from the code under test. With pointers only (F11a), nothing is proposed at all: a pointer keeps no text to extract
    from. Returns (leaks, missing)."""
    pages = sorted({d.source for d in world.docs if d.source in company.LOADED} - set(CHATS))
    captured = list(company.CAPTURED)
    for team in EXTRACT_TEAMS:
        model_for_stores(team)
        r = api("PUT", f"/v1/workspaces/{team}/extraction", ADMIN, json={"model": "smoke-fake"})
        if r.status_code != 200:
            raise SystemExit(f"extraction for {team}: {r.status_code} {r.text[:200]}")
    if pointer:
        time.sleep(90)  # a pass a minute: long enough for one to run, had it anything to read
        with psycopg.connect(OWNER_DB, row_factory=dict_row) as conn:  # Teams keeps its text whatever the others keep (C1a)
            found = [p for p in conn.execute(PROPOSALS_SQL, (list(EXTRACT_TEAMS),)).fetchall() if p["source_kind"] != "teams"]
        print(f"\nExtraction (F2) with no text kept: {len(found)} proposals (none expected)\n")
        return [f"a proposal ({p['id']}) was extracted from {p['external_key']}, a pointer" for p in found], []
    leaks = capture_on(world)
    deadline, counts = time.monotonic() + 600, {}
    with psycopg.connect(OWNER_DB, row_factory=dict_row) as conn:
        while time.monotonic() < deadline:
            counts = {t: conn.execute(TEAM_DOCUMENTS_SQL, (t, pages, captured)).fetchone() for t in EXTRACT_TEAMS}
            if all(c["read"] == c["of"] for c in counts.values()):
                break
            time.sleep(5)
        else:
            raise SystemExit(f"extraction didn't read every page and ticket in ten minutes: {counts}")
        found = conn.execute(PROPOSALS_SQL, (list(EXTRACT_TEAMS),)).fetchall()
    by_key = {d.key: d for d in world.docs}
    allowed = lambda d: d.source in pages or (d.source in CHATS and d.container in captured)
    outside = [p for p in found if p["external_key"] not in by_key or not allowed(by_key[p["external_key"]])]
    leaks += [f"a proposal ({p['id']}) cites {p['external_key']}, which isn't a page, a ticket, or a captured thread" for p in outside]
    missing = []
    chat = sum(1 for p in found if p["external_key"] in by_key and by_key[p["external_key"]].source in CHATS)
    missing += [f"nothing was proposed from {c}, captured with both keys" for c in captured
                if not any(p["external_key"] in by_key and by_key[p["external_key"]].container == c for p in found)]
    print(f"\nExtraction (F2), the fake model for {' and '.join(EXTRACT_TEAMS)}: {len(found)} proposals from "
          f"{len({p['external_key'] for p in found})} documents ({chat} from captured threads), {len(outside)} of them "
          "outside pages, tickets, and captured threads; who sees them, against the truth\n")
    for person in (p for p in world.people if p.username):
        seen = world.visible(person.username)
        readable_ids = {p["id"] for p in found if p["external_key"] in by_key and by_key[p["external_key"]].name in seen}
        want = {i for i in readable_ids if next(p["duplicate_of"] for p in found if p["id"] == i) not in readable_ids}  # F3: a copy shows under its original
        got, offset = set(), 0
        while offset is not None:
            r = api("GET", "/v1/units", person.username, params={"status": "proposed", "limit": 100, "offset": offset})
            assert r.status_code == 200, f"proposals as {person.username}: {r.status_code} {r.text[:200]}"
            got |= {u["id"] for u in r.json()["units"]}
            offset = r.json()["next_offset"]
        got &= {p["id"] for p in found}
        leaks += [f"{person.username} saw the proposal {i}" for i in sorted(got - want)]
        missing += [f"{person.username} can't see the proposal {i}" for i in sorted(want - got)]
        mark = "LEAK" if got - want else ("miss" if want - got else "ok")
        print(f"  {mark:4}  {person.username:5} {person.title:28} is shown {len(got)} of the {len(found)} proposals; the truth says {len(want)}")
    return leaks, missing


def expected_holder(world: company.World, asker: str, fact: company.Fact) -> str | None:
    """F8's truth for a contact's current holder: up the asker's managers, else the one person still here with that title
    in the fact's places (or the asker's), as kl.context decides it from the directory."""
    from .context import role_matches
    me, state = world.person(asker), {p.code: p.state for p in world.places}
    boss, seen = company.manager_of(world, me), set()
    while boss and boss.email not in seen:
        seen.add(boss.email)
        if role_matches(fact.role, boss.title):
            return boss.name
        boss = company.manager_of(world, boss)
    where = {s for s in fact.scope if s in state or s in state.values()} or {me.place, state[me.place]}
    here = [p for p in world.people if p.active and p != me and role_matches(fact.role, p.title) and {p.place, state[p.place]} & where]
    return here[0].name if len(here) == 1 else None


def score(world: company.World, out, unit_ids: dict[str, str]) -> tuple[dict, list[str], list[str]]:
    key = lambda names: tuple(world.doc(n).key for n in names)
    cases = [evals.Case(q.id, q.asker, q.text, q.kind, key(q.expect), key(q.forbid), q.needs) for q in world.questions]
    rows, leaks = [], []
    for case, q in zip(cases, world.questions):
        ids = evals.ranked(case.user, case.question)
        found = [k for k in case.expect if k in ids]
        first = min((ids.index(k) + 1 for k in found), default=None)
        leaked = [k for k in case.forbid if k in ids]
        leaks += [f"{case.user} saw {k} ({case.id})" for k in leaked]
        rows.append((case, found, first, leaked))
    print(f"\nThe synthetic retailer: {len(cases)} questions, top {evals.K} search results each\n", file=out)
    for case, found, first, leaked in rows:
        mark = "LEAK" if leaked else ("ok" if len(found) == len(case.expect) else "miss")
        detail = f"found {len(found)} of {len(case.expect)}" + (f", first at #{first}" if first else "") if case.expect else "nothing to find"
        wait = f"  (waits on {case.needs})" if case.needs else ""
        print(f"  {mark:4}  {case.id:26}  {case.kind:12}  as {case.user:5}  {detail}{wait}", file=out)
    model_for_stores()
    answers, units, wrong_units, resolved = {}, {}, [], []
    facts, fact_of = {f.id: f for f in world.facts}, {f"unit:{uid}": fid for fid, uid in unit_ids.items()}
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
        led = [fact_of.get(k, k) for k in cited if k.startswith("unit:")]  # F4: each unit cited, as the fact it holds
        seen = world.visible(case.user)
        leaks += [f"{case.user} was cited the unit {f} ({case.id}), built from what they can't read" for f in led
                  if f not in facts or not set(facts[f].evidence) <= seen]
        wrong_units += [f"Ask cited the superseded unit {f} ({case.id})" for f in led if f in facts and facts[f].status == "superseded"]
        q = next(x for x in world.questions if x.id == case.id)
        if q.facts:
            units[case.id] = bool(led) and led[0] in q.facts
        for c in r.json().get("citations") or []:  # F8: each contact cited names its role's current holder for this asker
            f = facts.get(fact_of.get(c["id"], ""))
            if f and f.role:
                want, got = expected_holder(world, case.user, f), (c["unit"].get("holder") or {}).get("name")
                (resolved.append if want == got else wrong_units.append)(
                    f"{f.id} for {case.user}: holder {got or 'none'}" + ("" if want == got else f", where the truth says {want or 'none'}"))
        if case.expect:
            answers[case.id] = any(k in case.expect for k in cited)
        mark = "LEAK" if any(k in case.forbid for k in cited) else ("ok" if not case.expect or answers[case.id] else "miss")
        print(f"  {mark:4}  {case.id:26}  as {case.user:5}  cited {', '.join(fact_of.get(k, k) for k in cited[:3]) or 'nothing'}", file=out)
    card = evals.scorecard(rows, answers, scenario="company", units=units)
    print(f"\n  Recall@{evals.K} {card['recall']:.0%}   MRR {card['mrr']:.2f}   over {len(card['cases'])} counted questions; "
          f"{sum(1 for c in cases if c.needs)} wait on slices not built yet", file=out)
    print(f"  Ask cited a right page for {sum(answers.values())} of {len(answers)} questions", file=out)
    print(f"  Ask led with a right knowledge unit for {sum(units.values())} of {len(units)} questions with facts", file=out)
    for line in resolved:
        print(f"  CONTACT: {line}, as the truth says", file=out)
    for line in wrong_units:
        print(f"  WRONG: {line}", file=out)
    return card, leaks, wrong_units


SENSITIVE_STORED_SQL = """
SELECT (SELECT count(*) FROM kl.chunks WHERE body LIKE %(text)s OR heading_path LIKE %(text)s)
     + (SELECT count(*) FROM kl.documents WHERE title LIKE %(text)s) AS n
"""


def sensitive_check(world: company.World, out) -> list[str]:
    """C2a on the retailer: the chat messages the truth names as sensitive (one person's surgery, another's written
    warning) are in no chunk and no title, in nothing their channel's own members find when they ask about them, and
    never in a prompt Ask sends; their channel's other messages still are. Returns the leaks, as things no answer may
    use."""
    leaks = []
    print("\nSensitive chat (C2a): never kept, found, or answered from\n", file=out)
    for name, i in company.SENSITIVE_MESSAGES:
        text, before = world.doc(name).messages[i][2], len(leaks)
        with psycopg.connect(OWNER_DB, row_factory=dict_row) as conn:
            stored = conn.execute(SENSITIVE_STORED_SQL, {"text": f"%{text[:60]}%"}).fetchone()["n"]
        leaks += [f"{name}'s sensitive message is stored ({stored} rows)"] if stored else []
        for q in (q for q in world.questions if name in q.withhold):
            hits = api("GET", "/v1/search", q.asker, params={"q": q.text, "scope": "org", "limit": 20}).json()["results"]
            leaks += [f"{q.asker} found {name}'s sensitive message ({q.id})" for h in hits
                      if text[:60] in f"{h['title']} {h.get('snippet') or ''}"]
            if q.asker in {p.username for p in world.people if p.username and p.team == ASK_TEAM}:
                smoke.reset_fake()
                ask_as(q.asker, q.text, ASK_MODEL)
                leaks += [f"Ask sent {name}'s sensitive message to the model ({q.id})"] if any(text[:60] in p for p in smoke.fake_prompts()) else []
        print(f"  {'LEAK' if len(leaks) > before else 'ok':4}  {name}: kept nowhere, found by no one, sent to no model", file=out)
    return leaks


def main(argv: list[str]) -> int:
    recording = "--record" in argv
    out = sys.stderr if recording else sys.stdout
    baseline_out = sys.stdout
    if recording:  # stdout becomes the baseline's file: everything but the scorecard's JSON goes to stderr instead
        sys.stdout = sys.stderr
    arg = lambda name, default: int(argv[argv.index(name) + 1]) if name in argv else default
    seed, stores = arg("--seed", DEFAULT[0]), arg("--stores", DEFAULT[1])
    world = company.build(seed, stores)
    # F11: pointers only (Confluence, Jira, and Slack), or live only (Confluence and Jira; Slack keeps pointers), each
    # measured as its own set
    storage = "live" if "--live" in argv else "pointer" if "--pointer" in argv else "index"
    pointer, name = storage != "index", {"index": "company", "pointer": "company-pointer", "live": "company-live"}[storage]
    api_ready()
    ids = load(world, storage)
    if "--load-only" in argv:
        print(f"{company.COMPANY} is loaded ({stores} stores, seed {seed}): sign in as ana, cora, kai, or another of its people "
              "(password: <name>-dev-only).")
        return 0
    leaks, missing = readers_check(world)
    unit_leaks, unit_missing = units_check(world, ids)
    extract_leaks, extract_missing = extraction_check(world, pointer)
    merge_leaks, merge_missing = consolidation_check(world, ids)
    leaks, missing = leaks + unit_leaks + extract_leaks + merge_leaks, missing + unit_missing + extract_missing + merge_missing
    card, retrieval_leaks, wrong_units = score(world, out, ids)
    retrieval_leaks += sensitive_check(world, out)
    if not pointer:  # C2d: a Slack ask, answered as its asker
        ask_leaks, ask_missing = slack_ask_check(world, out)
        retrieval_leaks, missing = retrieval_leaks + ask_leaks, missing + ask_missing
    life_leaks, life_missing = lifecycle_check(world, ids, out)  # after Ask's: a stale unit ranks differently
    gap_leaks, gap_missing = gaps_check(world, out)
    leaks, missing = leaks + life_leaks + gap_leaks, missing + life_missing + gap_missing
    problems = ([f"PERMISSION LEAK: {line}" for line in leaks + retrieval_leaks] + [f"MISSING: {line}" for line in missing]
                + [f"WRONG: {line}" for line in wrong_units])
    if problems:
        for line in problems:
            print(f"  {line}", file=out)
        return 1  # never recorded as a baseline
    print("\n  No permission leaks, and everyone opens exactly what the truth says.", file=out)
    if (seed, stores) != DEFAULT:
        print(f"  Not held to the baseline: it's recorded for seed {DEFAULT[0]} and {DEFAULT[1]} stores.", file=out)
        return 0
    if recording:
        print(json.dumps(evals.with_set(name, card), indent=2, sort_keys=True), file=baseline_out)
        print(f"  Recorded as the {name} baseline: commit {evals.BASELINE.name}.", file=out)
        return 0
    return evals.gate(name, card, out, {q.id: q.kind for q in world.questions})


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
