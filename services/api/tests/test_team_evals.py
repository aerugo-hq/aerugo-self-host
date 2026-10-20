"""Question sets per team (F7): a team's file is checked before anything runs; the retailer's committed files are exactly
what its truth generates; a page the asker must never see, in results or citations, is a leak; Ask's citations and the
unit it leads with are scored; and a team that finds less than its baseline fails by name."""
import io
from pathlib import Path

import pytest
import yaml

from kl import company
from kl.team_evals import FOLDER, checked, gate, load, score

GOOD = {"version": 1, "team": "stores", "model": "stores/smoke-fake",
        "cases": [{"id": "keys", "asker": "ana", "question": "Where are the keys?", "kind": "lookup",
                   "expect": ["doc-a"], "units": ["Keys"], "forbid": ["doc-secret"]}]}


def with_case(**changes):
    return {**GOOD, "cases": [{**GOOD["cases"][0], **changes}]}


@pytest.mark.parametrize("spec", [
    {**GOOD, "version": 2},                                    # an unknown format
    {**GOOD, "team": "Stores!"},                               # not a slug
    {**GOOD, "model": "smoke-fake"},                           # a model without its team
    {**GOOD, "cases": []},                                     # no cases
    {**GOOD, "cases": GOOD["cases"] * 2},                      # the same id twice
    with_case(asker="ana smith"),                              # not a username
    with_case(question="?"),                                   # too short
    with_case(expect="doc-a"),                                 # not a list
    with_case(owner="ana"),                                    # a key a case doesn't hold
])
def test_a_question_set_its_format_doesnt_allow_is_refused(spec):
    with pytest.raises(ValueError):
        checked(spec, "teams/stores/questions.yaml")


def test_the_retailers_committed_question_sets_are_exactly_what_its_truth_generates():
    committed = {s["team"]: s for s in load(FOLDER)}
    assert committed == company.question_sets(company.build()), "regenerate with: python -m kl.team_evals --write-retail"
    assert sum(len(s["cases"]) for s in committed.values()) == len(company.build().questions)


def test_a_folder_holds_one_file_per_team_in_its_own_folder(tmp_path: Path):
    (tmp_path / "finance").mkdir()
    (tmp_path / "finance" / "questions.yaml").write_text(yaml.safe_dump(GOOD))  # stores' set in finance's folder
    with pytest.raises(ValueError):
        load(tmp_path)


def test_a_forbidden_page_in_results_or_citations_is_a_leak_and_answers_and_units_are_scored():
    search = lambda user, q: ["doc-x", "doc-a"]
    answer = {"citations": [{"id": "unit:1", "unit": {"title": "Keys"}}, {"id": "doc-a"}]}
    card, leaks = score(GOOD, search, lambda user, q, model: answer)
    assert leaks == [] and card["cases"]["keys"] == {"found": 1, "of": 1, "first": 2}
    assert card["answers"] == {"keys": True} and card["units"] == {"keys": True}
    _, leaks = score(GOOD, lambda user, q: ["doc-secret"], lambda user, q, model: {"citations": [{"id": "doc-secret"}]})
    assert leaks == ["stores: ana saw doc-secret (keys)", "stores: ana was cited doc-secret (keys)"]
    card, _ = score({k: v for k, v in GOOD.items() if k != "model"}, search, None)  # a team that names no model: search only
    assert "answers" not in card and "units" not in card


def test_a_team_that_finds_less_than_its_baseline_fails_by_name():
    card, _ = score(GOOD, lambda user, q: ["doc-a"], None)
    out = io.StringIO()
    assert gate("stores", card, {**card, "cases": {"keys": {"found": 1, "of": 1, "first": 1}}}, {"keys": "lookup"}, out) == 0
    worse, _ = score(GOOD, lambda user, q: ["doc-x"], None)
    assert gate("stores", worse, card, {"keys": "lookup"}, out) == 1 and "stores: WORSE: keys" in out.getvalue()
