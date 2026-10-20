"""Question sets per team (F7, docs/ENTERPRISE_PLAN.md sections 4 and 10; principle 8: every change to how Aerugo
answers is held to its question sets, never shipped untested).

Each team keeps its questions in the company's onboarding repository, beside its team.yaml and sources.yaml:
teams/<team>/questions.yaml, owned by the team through CODEOWNERS (docs/question-sets.md says how to write one). A file
names its team, optionally the model its people ask through ("<team>/<name>", for Ask), and its cases: who asks (a test
account with that role and place), the question, its kind, the pages a right answer draws on (`expect`), the knowledge
units it should lead with, by title (`units`), what that asker must never see (`forbid`), and a slice it waits on
(`needs`, run and shown, not counted). The synthetic retailer's are generated from its truth
(sandbox/scenarios/retail/teams, `--write-retail`), and a unit test holds them to it.

`make eval-teams` runs every file in a folder: each question as its asker, through search and, when the team names a
model, Ask; scored per team (found among the top K, the first right page's rank, Ask citing a right page, leading with
a right unit); anything the asker must never see, in results or citations, is a leak and fails the run. Each team's
scorecard is held to its own baseline (`teams` in evals_baseline.json), so a regression in one team's questions fails
by name; `--record` prints the baseline file with the teams' scorecards replaced. `--mine <team>` prints candidate
cases from what that team's people asked: only wordings at least KL_GAP_MIN_ASKERS different people used and were
answered well (what wasn't is a gap: content to write, not a case), with the asker left for a person to fill with a
test account, never a real person's name.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

import yaml

from . import evals

FOLDER = Path(os.environ.get("KL_QUESTION_SETS", "/question-sets"))
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
CASE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
USER = re.compile(r"^[A-Za-z0-9._@-]{1,120}$")
MODEL = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}/[a-z0-9][a-z0-9-]{0,39}$")
LISTS = ("expect", "units", "forbid")
TEAM_WINDOW_SQL = """SELECT q.id, q.username, q.text, q.words, q.teams, q.found, q.confidence, q.helpful, q.asked_at FROM kl.questions q
JOIN kl.orgs o ON o.id = q.org_id
WHERE o.slug = %s AND q.text IS NOT NULL AND %s = ANY(q.teams) AND q.asked_at > now() - make_interval(days => %s)
ORDER BY q.asked_at, q.id"""


def checked(spec: dict, where: str) -> dict:
    """A team's question set as its file says it, or ValueError naming what's wrong: it comes from a repository people
    edit, so it's checked before anything runs."""
    if not isinstance(spec, dict) or spec.get("version") != 1 or not SLUG.fullmatch(str(spec.get("team", ""))):
        raise ValueError(f"{where}: a question set has version 1 and its team's slug")
    if "model" in spec and not MODEL.fullmatch(str(spec["model"])):
        raise ValueError(f"{where}: model is '<team>/<name>' from a team's own registry")
    cases, seen = spec.get("cases"), set()
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{where}: a question set has cases")
    for n, case in enumerate(cases, 1):
        at = f"{where}, case {n}"
        if not isinstance(case, dict) or set(case) - {"id", "asker", "question", "kind", "needs", *LISTS}:
            raise ValueError(f"{at}: a case holds id, asker, question, kind, expect, units, forbid, and needs")
        if not CASE_ID.fullmatch(str(case.get("id", ""))) or case["id"] in seen:
            raise ValueError(f"{at}: an id is lowercase letters, digits, and dashes, once per team")
        seen.add(case["id"])
        if not USER.fullmatch(str(case.get("asker", ""))):
            raise ValueError(f"{at}: asker is the username of a test account")
        if not isinstance(case.get("question"), str) or not 3 <= len(case["question"].strip()) <= 2000:
            raise ValueError(f"{at}: a question is 3 to 2000 characters")
        if not isinstance(case.get("kind", ""), str) or len(case.get("kind", "")) > 30:
            raise ValueError(f"{at}: kind is a short word")
        for key in LISTS:
            value = case.get(key, [])
            if not isinstance(value, list) or len(value) > 50 or not all(isinstance(v, str) and 0 < len(v) <= 200 for v in value):
                raise ValueError(f"{at}: {key} is a list of up to 50 names")
    return spec


def load(folder: Path) -> list[dict]:
    """Every team's question set under a folder (<team>/questions.yaml), each checked, one per team."""
    sets, teams = [], set()
    for path in sorted(folder.glob("*/questions.yaml")):
        spec = checked(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))
        if spec["team"] in teams or spec["team"] != path.parent.name:
            raise ValueError(f"{path}: one file per team, in the team's own folder")
        teams.add(spec["team"])
        sets.append(spec)
    if not sets:
        raise ValueError(f"{folder}: no teams/<team>/questions.yaml here")
    return sets


def score(spec: dict, search: Callable[[str, str], list[str]], ask: Callable[[str, str, str], dict] | None) -> tuple[dict, list[str]]:
    """One team's scorecard, and the leaks: each case's search results as its asker, and, when the team names a model and
    `ask` is given, its answer's citations."""
    rows, answers, units, leaks = [], {}, {}, []
    for c in spec["cases"]:
        case = evals.Case(c["id"], c["asker"], c["question"], c.get("kind", ""), tuple(c.get("expect", [])),
                          tuple(c.get("forbid", [])), c.get("needs", ""))
        found_ids = search(case.user, case.question)
        found = [k for k in case.expect if k in found_ids]
        first = min((found_ids.index(k) + 1 for k in found), default=None)
        leaked = [k for k in case.forbid if k in found_ids]
        leaks += [f"{spec['team']}: {case.user} saw {k} ({case.id})" for k in leaked]
        rows.append((case, found, first, leaked))
        if ask and spec.get("model") and not case.needs and (case.expect or case.forbid or c.get("units")):
            cited = ask(case.user, case.question, spec["model"]).get("citations") or []
            ids = [x["id"] for x in cited]
            leaks += [f"{spec['team']}: {case.user} was cited {k} ({case.id})" for k in ids if k in case.forbid]
            if case.expect:
                answers[case.id] = any(k in case.expect for k in ids)
            if c.get("units"):
                first_unit = next((x["unit"]["title"] for x in cited if x.get("unit")), None)
                units[case.id] = first_unit in c["units"]
    card = evals.scorecard(rows, answers if answers else None, scenario=f"team {spec['team']}", units=units if units else None)
    return card, leaks


def gate(team: str, card: dict, baseline: dict | None, kinds: dict, out) -> int:
    """A team's scorecard against its own baseline: what's worse fails, by name."""
    if baseline is None:
        print(f"  {team}: no baseline yet (make eval-baseline records one)", file=out)
        return 0
    worse, better, new = evals.compare(card, baseline, kinds)
    for line in better:
        print(f"  {team}: BETTER: {line}", file=out)
    for case_id in new:
        print(f"  {team}: NEW: {case_id}, not in the baseline yet", file=out)
    for line in worse:
        print(f"  {team}: WORSE: {line}", file=out)
    return 1 if worse else 0


def mine(conn, org: str, team: str) -> dict:
    """Candidate cases from what a team's people asked (at least GAP_MIN_ASKERS different people, answered well), and the
    gaps (what they weren't), from the questions F5a keeps; never who asked."""
    from . import config, gaps
    rows = conn.execute(TEAM_WINDOW_SQL, (org, team, config.QUESTION_DAYS)).fetchall()
    out = {"cases": [], "gaps": []}
    for group in gaps.group(rows):
        askers = {r["username"] for r in group}
        if len(askers) < config.GAP_MIN_ASKERS:
            continue
        answered = [r for r in group if not gaps.unanswered(r)]
        wording = max(group, key=lambda r: sum(gaps.norm(x["text"]) == gaps.norm(r["text"]) for x in group))["text"]
        if len({r["username"] for r in answered}) >= config.GAP_MIN_ASKERS:
            out["cases"].append({"id": f"mined-{len(out['cases']) + 1}", "asker": "TODO-test-account", "question": wording,
                                 "kind": "mined", "expect": [], "units": [], "forbid": []})
        else:
            out["gaps"].append(wording)
    return out


def write_retail(folder: Path) -> list[Path]:
    """The synthetic retailer's question sets, one file per team, from its truth."""
    from . import company
    written = []
    for team, spec in company.question_sets(company.build()).items():
        path = folder / team / "questions.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# The synthetic retailer's {team} questions, generated from kl/company.py (python -m kl.team_evals "
                        "--write-retail): don't edit by hand.\n" + yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=120),
                        encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str]) -> int:
    if "--write-retail" in argv:
        for path in write_retail(Path(argv[argv.index("--write-retail") + 1])):
            print(path)
        return 0
    if "--mine" in argv:
        import psycopg
        from psycopg.rows import dict_row
        team = argv[argv.index("--mine") + 1]
        if not SLUG.fullmatch(team):
            raise SystemExit("--mine takes a team's slug")
        from . import config
        with psycopg.connect(os.environ["KL_OWNER_DATABASE_URL"], row_factory=dict_row) as conn:  # the eval tooling's owner
            found = mine(conn, config.ORG_SLUG, team)
        print(yaml.safe_dump({"cases": found["cases"]}, sort_keys=False, width=120))
        for wording in found["gaps"]:
            print(f"# gap (content to write, not a case): {wording}")
        return 0
    recording = "--record" in argv
    out = sys.stderr if recording else sys.stdout
    stdout = sys.stdout
    if recording:
        sys.stdout = sys.stderr
    from . import smoke
    from .company_eval import model_for_stores, password
    from .smoke import api_ready, ask_as
    folder = Path(argv[argv.index("--folder") + 1]) if "--folder" in argv else FOLDER
    try:
        sets = load(folder)
    except ValueError as e:
        raise SystemExit(f"question sets: {e}")
    for spec in sets:  # the evaluation environment's test accounts sign in as the dev stack's convention has it
        for c in spec["cases"]:
            smoke.PASSWORDS.setdefault(c["asker"], password(c["asker"]))
    api_ready()
    held = json.loads(evals.BASELINE.read_text()).get("teams", {}) if evals.BASELINE.exists() else {}
    cards, leaks, failed = {}, [], 0
    for spec in sets:
        if spec.get("model", "").endswith("/smoke-fake"):
            model_for_stores(spec["team"])  # the evaluation environment's fake model, for this team
        card, found = score(spec, evals.ranked, lambda user, q, model: ask_as(user, q, model).json())
        cards[spec["team"]], leaks = card, leaks + found
        units = card.get("units") or {}
        print(f"  {spec['team']:16} {len(spec['cases']):3} questions  Recall@{evals.K} {card['recall']:.0%}  MRR {card['mrr']:.2f}"
              + (f"  Ask right {sum(card['answers'].values())} of {len(card['answers'])}" if card.get("answers") else "")
              + (f"  right unit first {sum(units.values())} of {len(units)}" if units else ""), file=out)
        failed |= gate(spec["team"], card, held.get(spec["team"]), {c["id"]: c.get("kind", "") for c in spec["cases"]}, out)
    for line in leaks:
        print(f"  PERMISSION LEAK: {line}", file=out)
    if leaks:
        return 1
    if recording:
        print(json.dumps(evals.with_set("teams", cards), indent=2, sort_keys=True), file=stdout)
        return 0
    print("  No leak, and every team at its baseline." if not failed else "  A team fell below its baseline.", file=out)
    return failed


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
