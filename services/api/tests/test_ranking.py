"""Ranking: a curated page wins a near tie with the evidence, never against evidence that clearly matches better, and
says why it moved; everything else keeps the order fusion gave it."""
from kl.ranking import K, fuse, prefer_curated


def ranked(channels):
    return prefer_curated(fuse(channels), boost=1.1)


def test_a_curated_page_that_barely_matches_never_outranks_the_page_that_answers():
    vector = [{"external_key": "the-runbook", "tier": "evidence"}] + [{"external_key": f"other-{i}"} for i in range(14)] \
        + [{"external_key": "kb-unrelated", "tier": "silver"}]  # the meaning channel found it sixteenth
    assert [r["external_key"] for r in ranked([("vector", vector)])][0] == "the-runbook"


def test_a_curated_page_wins_a_near_tie_and_says_why():
    vector = [{"external_key": "a-thread", "tier": "evidence"}, {"external_key": "kb-page", "tier": "gold"}]
    rows = ranked([("vector", vector)])
    assert [r["external_key"] for r in rows] == ["kb-page", "a-thread"]
    assert rows[0]["matched"] == ["vector", "curated"] and rows[1]["matched"] == ["vector"]
    assert abs(rows[0]["score"] - 1.1 / (K + 2)) < 1e-12


def test_evidence_that_matches_in_two_channels_beats_a_curated_page_in_one():
    lexical = [{"external_key": "the-policy", "tier": "evidence"}]
    vector = [{"external_key": "kb-page", "tier": "silver"}, {"external_key": "the-policy", "tier": "evidence"}]
    assert [r["external_key"] for r in ranked([("lexical", lexical), ("vector", vector)])] == ["the-policy", "kb-page"]


def test_without_a_curated_page_the_order_is_fusions():
    vector = [{"external_key": f"doc-{i}", "tier": "evidence"} for i in range(5)]
    assert ranked([("vector", vector)]) == fuse([("vector", vector)])
