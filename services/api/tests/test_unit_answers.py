"""Answers from knowledge units first (F4): a unit answers a question when it states enough of the question's rarer
words; the one that fits the asker's scope comes first, then the closer match, the more trusted, and the one in force
most recently; another role's or place's unit never reaches someone whose role or place is known; and a unit goes to
the model as its text, its steps, who it applies to, and the other views on it."""
import re
from datetime import date, datetime, timezone

from kl import company
from kl.unit_answers import level, ranked, scope_fit, source_text, tsquery

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
OPEN = {"teams": set(), "roles": None, "places": None}


def unit(uid, title, body, trust="confirmed", status="confirmed", teams=(), roles=(), places=(), valid_from=None,
         kind="fact", structure=None, updated=NOW):
    return {"id": uid, "title": title, "body": body, "kind": kind, "trust": trust, "status": status, "structure": structure or {},
            "scope_teams": list(teams), "scope_roles": list(roles), "scope_places": list(places), "valid_from": valid_from,
            "updated_at": updated, "duplicate_of": None}


REFUNDS = unit("refunds", "Refund approvals", "Refunds over $500 are approved by the finance lead.")
MATS = unit("mats", "Entrance mats", "Entrance mats go down before the doors open on wet days.")


def ids(rows):
    return [r["id"] for r in rows]


def test_a_unit_stating_the_questions_words_answers_it_and_one_sharing_a_word_doesnt():
    assert ids(ranked("Who approves refunds over $500?", [REFUNDS, MATS], OPEN)) == ["refunds"]
    assert ranked("When does the sale open?", [REFUNDS, MATS], OPEN) == []


def test_a_rarer_word_decides_between_units_that_share_the_common_ones():
    regulator = unit("regulator", "Regulator notified", "The regulator was notified about the TrailLite 2 recall on 12 May.")
    decision = unit("decision", "Recall decision", "We recall every TrailLite 2 tent sold since February.")
    delisted = unit("delisted", "Delisted", "The TrailLite 2 recall: delisted online on 11 May.")
    got = ranked("Has the regulator been notified about the TrailLite 2 recall?", [decision, delisted, regulator], OPEN)
    assert ids(got)[0] == "regulator"


def test_the_asking_words_of_a_question_dont_count():
    assert ranked("How much does it cost?", [REFUNDS], OPEN) == []


def test_scope_fits_the_asker_and_a_known_role_or_place_that_differs_never_reaches_them():
    co = unit("co", "Sick leave", "Colorado: 1 hour per 30 worked.", places=("CO",))
    assert scope_fit(co, OPEN) == 0                                                 # place not known yet (F8): open
    assert scope_fit(co, {"teams": set(), "roles": None, "places": {"CO"}}) == 1     # theirs
    assert scope_fit(co, {"teams": set(), "roles": None, "places": {"OR"}}) is None  # another place's rule
    ours = unit("ours", "Close", "Close steps.", teams=("finance",))
    assert scope_fit(ours, {"teams": {"finance"}, "roles": None, "places": None}) == 1
    assert scope_fit(ours, {"teams": {"stores"}, "roles": None, "places": None}) == -1  # another team's, still readable
    assert scope_fit(unit("x", "X", "x", roles=("Store leader",)), {"teams": set(), "roles": {"associate"}, "places": None}) is None


def test_the_closer_scope_then_the_closer_match_then_trust_then_the_newer_version_come_first():
    general = unit("general", "Sick leave", "Store employees earn sick leave per hours worked.", trust="confirmed")
    mine = unit("mine", "Sick leave", "Store employees earn sick leave per hours worked.", trust="documented", places=("CO",))
    assert ids(ranked("How much sick leave do store employees earn?", [general, mine],
                      {"teams": set(), "roles": None, "places": {"CO"}})) == ["mine", "general"]
    documented = unit("documented", "Refund approvals", REFUNDS["body"], trust="documented")
    assert ids(ranked("Who approves refunds over $500?", [documented, REFUNDS], OPEN)) == ["refunds", "documented"]
    old = unit("old", "Returns window", "Customers have 60 days to return an item.")
    new = unit("new", "Returns window", "Customers have 45 days to return an item.", valid_from=date(2026, 3, 1))
    assert ids(ranked("How many days do customers have to return an item?", [old, new], OPEN))[0] == "new"


def test_a_stale_unit_comes_after_a_current_one_and_says_its_due_for_review():
    stale = unit("stale", "Refund approvals", REFUNDS["body"], status="stale")
    assert ids(ranked("Who approves refunds over $500?", [stale, REFUNDS], OPEN)) == ["refunds", "stale"]
    assert "Due for review" in source_text(stale, [])


def test_a_unit_reaches_the_model_as_its_steps_scope_and_the_other_views_on_it():
    steps = {"steps": [{"text": "Make it safe", "role": "Associate"}, {"text": "Call Risk", "condition": "they went to hospital"}]}
    injury = unit("injury", "Customer injury", "When a customer is hurt [1].", kind="procedure", structure=steps, places=("CA",))
    text = source_text(injury, [{"holder": "Contact center", "context": "phone", "body": "Log it first."}])
    assert "[1]" not in text
    assert "1. Make it safe (Associate)." in text and "2. Call Risk. If they went to hospital." in text
    assert "Applies to: CA." in text
    assert "Another view, held by Contact center (phone): Log it first." in text
    contact = unit("risk", "Risk", "Risk on call.", kind="contact", structure={"role": "Risk on-call", "reach": "555-0170"})
    assert "Contact: Risk on-call, 555-0170." in source_text(contact, [])


def test_a_unit_shows_its_most_restricted_evidence_and_a_question_cant_write_query_syntax():
    assert level({"public", "confidential", "internal"}) == "confidential"
    assert tsquery("refunds & !approvals | (finance):*") == "approval | finance | refund"


def test_on_the_retailer_the_first_unit_is_one_of_the_questions_facts():
    """Every current fact the asker may read as a unit, weighed among those alone (as the database's row-level security
    hands them over); the first unit for each question must be one of its facts, but for questions F8 decides (whose
    sick leave, whose district manager), one whose first unit also answers it (the injury procedure says when to call
    Risk), and three about the recall whose right fact the asker can't read, where the first is another unit about it
    they can."""
    world = company.build()
    places = {p.code for p in world.places}

    def as_unit(f):
        where = [s for s in f.scope if s in places or re.fullmatch(r"[A-Z]{2}", s)]
        return unit(f.id, (f.id[0].upper() + f.id[1:]).replace("-", " "), f.text, kind=f.kind, places=where,
                    roles=[s for s in f.scope if s not in where], valid_from=date(2026, 3, 1) if f.supersedes else None)
    units = [(f, as_unit(f)) for f in world.facts if f.status not in ("sensitive", "superseded")]
    right, wrong = [], []
    for q in world.questions:
        seen = world.visible(q.asker)
        got = ranked(q.text, [u for f, u in units if set(f.evidence) <= seen], OPEN)
        if got and q.facts:
            (right if got[0]["id"] in q.facts else wrong).append(q.id)
        elif got:
            wrong.append(q.id)
    assert len(right) >= 24, right
    assert set(wrong) <= {"sick-mine-or", "district-manager-mine", "risk-hospital", "recall-script", "recall-decision", "recall-poles"}, wrong
