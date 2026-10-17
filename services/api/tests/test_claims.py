"""The claim checker (M4, H4): an answer's sentences are supported only by the passages they cite, and a contradiction
is never supported, however many words it shares with its source."""
import pytest

from kl import claims

PASSAGE = "When payments are down, page the payments on-call first, then open an incident channel."


def test_a_sentence_its_cited_passage_states_is_supported():
    got = claims.check("Page the payments on-call first [1]. Then open an incident channel [1].", {1: PASSAGE})
    assert [c["supported"] for c in got] == [True, True]


def test_a_sentence_its_passage_doesnt_state_is_unsupported():
    assert [c["supported"] for c in claims.check("The CFO approves every refund personally [1].", {1: PASSAGE})] == [False]


def test_a_sentence_that_cites_nothing_is_unsupported():
    assert claims.check("Open an incident channel right away.", {1: PASSAGE})[0]["supported"] is False


@pytest.mark.parametrize(("claim", "supported"), claims.CONTRADICTIONS)
def test_a_negation_or_a_different_number_is_never_supported(claim: str, supported: bool):
    got = claims.check(claim, {1: claims.REFUNDS})
    assert got and got[0]["supported"] is supported, got


def test_calibration_needs_both_right_and_wrong_examples():
    fitted = claims.calibrate([(0.9, True)] * 5 + [(0.1, False)] * 5)
    assert fitted and fitted["accuracy"] == 1.0
    assert claims.calibrate([(0.9, True)] * 3) is None
