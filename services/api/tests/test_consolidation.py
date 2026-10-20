"""Consolidation (F3): a unit repeating another of its team's is linked to it, never across teams; two units for the
same people at the same time that differ in a number or a negation conflict, but versions in time, rules for other
places, and units that merely share some words never do; and on the synthetic retailer exactly its stale page conflicts."""
import re
from datetime import date

from kl import company
from kl.consolidation import Unit, decide, first_of, relation


def unit(uid, text, team="payments", kind="fact", trust="documented", created="2026-01-01", **kw):
    return Unit(uid, team, kind, text, trust, created, **kw)


REFUND = "Refunds over $500 are approved by the finance lead [1]."


def test_a_unit_repeating_another_of_its_team_is_a_duplicate_citations_aside():
    assert relation(unit("a", REFUND), unit("b", "Refunds over $500 are approved by the finance lead.")) == ("duplicate", "")


def test_the_same_words_in_another_team_or_of_another_kind_are_no_duplicate():
    assert relation(unit("a", REFUND), unit("b", REFUND, team="commons")) is None
    assert relation(unit("a", REFUND), unit("b", REFUND, kind="policy")) is None


def test_a_different_number_or_a_negation_is_a_conflict_across_teams_too():
    assert relation(unit("a", REFUND), unit("b", "Refunds over $50 are approved by the finance lead.", team="commons"))[0] == "conflict"
    assert relation(unit("a", REFUND), unit("b", "Refunds over $500 are not approved by the finance lead."))[0] == "conflict"


def test_versions_in_time_never_conflict_but_overlapping_ones_do():
    old = unit("a", "Chargebacks are answered within 10 days.", valid_until=date(2025, 12, 31))
    assert relation(old, unit("b", "Chargebacks are answered within 7 days.", valid_from=date(2026, 1, 1))) is None
    assert relation(old, unit("b", "Chargebacks are answered within 7 days.", valid_from=date(2025, 6, 1)))[0] == "conflict"


def test_rules_for_other_places_and_a_general_rule_beside_a_local_one_never_conflict():
    co = unit("a", "Store refunds are capped at $300 a day.", places=("CO",))
    assert relation(co, unit("b", "Store refunds are capped at $400 a day.", places=("OR",))) is None
    assert relation(co, unit("b", "Store refunds are capped at $400 a day.")) is None


def test_leaving_out_a_number_the_other_gives_is_no_conflict_and_a_fragment_is_never_compared():
    full = unit("a", "Entrance mats go down before the doors open (decided after the Denver fall, September 2026).")
    assert relation(full, unit("b", "Decision: entrance mats go down before the doors open [3].")) is None
    rule = unit("a", "Without a receipt, store credit at the lowest price in 45 days, up to $50; above $50 a store leader approves.")
    assert relation(rule, unit("b", "For store leaders only [1].")) is None


def test_superseded_retired_and_chained_units_are_left_alone():
    assert relation(unit("a", REFUND, status="superseded"), unit("b", "Refunds over $50 are approved by the finance lead.")) is None
    assert relation(unit("a", REFUND, superseded_by="b"), unit("b", "Refunds over $50 are approved by the finance lead.")) is None


def test_sharing_some_words_and_a_number_is_no_conflict():
    a = unit("a", "Kai Moreno decided on 11 May 2026 to recall every TrailLite 2 tent sold since 1 February 2026.")
    assert relation(a, unit("b", "The TrailLite 2 was delisted online at 16:20 on 11 May 2026.")) is None


def test_the_original_is_the_higher_trust_then_the_earlier_and_a_copy_of_a_copy_links_to_it():
    a, b = unit("a", REFUND, trust="documented", created="2026-01-01"), unit("b", REFUND, trust="confirmed", created="2026-02-01")
    assert first_of(a, b) == (b, a)
    c = unit("c", REFUND, created="2026-03-01", duplicate_of="b")
    links, conflicts = decide([a, b, c], {"a"}, set())
    assert links == {"a": "b"} and not conflicts


def test_a_conflict_already_opened_between_two_units_isnt_opened_again():
    a, b = unit("a", REFUND), unit("b", "Refunds over $50 are approved by the finance lead.", created="2026-02-01")
    assert decide([a, b], {"b"}, set())[1] == [("a", "b", "they give different numbers")]
    assert decide([a, b], {"b"}, {frozenset(("a", "b"))})[1] == []


def test_on_the_retailer_exactly_the_stale_page_conflicts_and_nothing_is_a_duplicate():
    world = company.build()
    places = {p.code for p in world.places}
    units = []
    for f in (f for f in world.facts if f.status != "sensitive"):
        where = tuple(s for s in f.scope if s in places or re.fullmatch(r"[A-Z]{2}", s))
        units.append(unit(f.id, f.text, team="one", kind=f.kind, places=where, roles=tuple(s for s in f.scope if s not in where),
                          valid_from=date(2026, 3, 1) if f.supersedes else None,
                          valid_until=date.fromisoformat(f.valid_until) if f.valid_until else None,
                          status="superseded" if f.status == "superseded" else "proposed"))
    links, conflicts = decide(units, {u.id for u in units}, set())
    assert not links and {frozenset(c[:2]) for c in conflicts} == {frozenset(("cc-returns-stale", "returns-window"))}
