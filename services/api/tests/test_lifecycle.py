"""Lifecycle (F6): a confirmed unit goes stale when its review is due or its evidence changed since it was confirmed; a
unit past its end date, with nothing superseding it, is retired; and nothing else moves."""
from datetime import date, datetime, timedelta, timezone

from kl.lifecycle import transition

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
TODAY = NOW.date()


def unit(status="confirmed", reviewed_days_ago=10, review_days=90, valid_until=None, superseded_by=None,
         snapshot=("a", "b"), hashes=("a", "b")):
    return {"status": status, "reviewed_at": NOW - timedelta(days=reviewed_days_ago) if reviewed_days_ago is not None else None,
            "review_days": review_days, "valid_until": valid_until, "superseded_by": superseded_by,
            "evidence_hashes": list(snapshot) if snapshot is not None else None, "hashes": list(hashes)}


def test_a_confirmed_unit_whose_review_is_due_goes_stale():
    assert transition(unit(reviewed_days_ago=91), TODAY, NOW) == ("stale", "due for review (every 90 days)")
    assert transition(unit(reviewed_days_ago=89), TODAY, NOW) is None


def test_a_confirmed_unit_whose_evidence_changed_or_went_goes_stale():
    assert transition(unit(hashes=("a", "c")), TODAY, NOW) == ("stale", "its evidence changed since it was confirmed")
    assert transition(unit(hashes=("a", "")), TODAY, NOW)[0] == "stale"  # a document gone
    assert transition(unit(snapshot=None, hashes=("a", "c")), TODAY, NOW) is None  # confirmed before snapshots: its cadence decides


def test_a_unit_past_its_end_date_is_retired_unless_something_supersedes_it():
    assert transition(unit(valid_until=TODAY - timedelta(days=1)), TODAY, NOW) == ("retired", f"its end date, {TODAY - timedelta(days=1)}, passed")
    assert transition(unit(status="proposed", valid_until=date(2026, 1, 1), reviewed_days_ago=None), TODAY, NOW)[0] == "retired"
    assert transition(unit(valid_until=date(2026, 1, 1), superseded_by="next"), TODAY, NOW) is None
    assert transition(unit(valid_until=TODAY), TODAY, NOW) is None  # its last day is still in force


def test_proposals_and_stale_units_wait_for_people():
    assert transition(unit(status="proposed", reviewed_days_ago=None, hashes=("x", "y")), TODAY, NOW) is None
    assert transition(unit(status="stale", reviewed_days_ago=400), TODAY, NOW) is None
