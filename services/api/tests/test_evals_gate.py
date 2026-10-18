"""The evaluation set's gate (M10): a run fails when a case finds less, when its first right page falls too far (any
place at all where current knowledge first is the point), or when Ask stops citing a right page."""
from kl.evals import compare


def card(first: dict, answers: dict | None = None, mrr: float = 1.0) -> dict:
    return {"mrr": mrr, "cases": {case: {"found": 1, "of": 1, "first": rank} for case, rank in first.items()},
            **({"answers": answers} if answers is not None else {})}


def test_a_stale_case_whose_current_page_falls_one_place_fails():
    worse, _, _ = compare(card({"checklist-current": 2}), card({"checklist-current": 1}))
    assert worse == ["checklist-current: its first right page fell to #2, from #1"]


def test_a_lookup_may_fall_two_places_but_not_three():
    assert compare(card({"archive-asked": 3}), card({"archive-asked": 1}))[0] == []
    assert compare(card({"archive-asked": 4}), card({"archive-asked": 1}))[0] == ["archive-asked: its first right page fell to #4, from #1"]


def test_a_case_ask_no_longer_cites_a_right_page_for_fails():
    worse, _, _ = compare(card({"rollback": 1}, answers={"rollback": False}), card({"rollback": 1}, answers={"rollback": True}))
    assert worse == ["rollback: Ask no longer cites a right page"]


def test_a_page_that_ranks_higher_is_said_and_never_fails():
    worse, better, _ = compare(card({"ticket-421": 1}), card({"ticket-421": 3}))
    assert worse == [] and better == ["ticket-421: its first right page is now #1, up from #3"]
