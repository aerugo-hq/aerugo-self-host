"""Handovers (C4a): prompts come from what someone owns, at most so many, then the open ones; a shared answer reaches its
team and the successor and lead it names, or the whole company, and its document never names what its prompt was about;
each kind of prompt makes its kind of unit; and a unit from a handover alone is one its successor or lead may settle."""
from kl import handover, units


def test_prompts_come_from_what_someone_owns_then_the_open_ones():
    pages = [{"key": "kb-sev1", "title": "Sev1 playbook"}]
    mine = [{"id": "u1", "title": "Refund cutoff"}]
    authored = [{"id": "d1", "title": "settle.py"}]
    asked = ["When do refunds post?", "When do refunds post?"]
    got = handover.owned_prompts(mine, pages, authored, asked)
    assert [k for k, _, _ in got] == ["owned", "owned", "owned", "asked"] + [k for k, _ in handover.OPEN_PROMPTS]
    assert '"Sev1 playbook"' in got[0][1] and got[0][2] == "kb-sev1" and '"When do refunds post?"' in got[3][1]
    assert all(subject is None for _, _, subject in got[4:]), "the open prompts are about no one thing"
    many = handover.owned_prompts([{"id": f"u{n}", "title": f"Unit {n}"} for n in range(40)], [], [], [])
    assert len(many) == handover.MAX_OWNED + len(handover.OPEN_PROMPTS)


def test_a_shared_answer_reaches_its_team_and_the_people_it_names_or_the_whole_company(monkeypatch):
    monkeypatch.setattr(handover.notion, "team_readers", lambda team: ["group:payments", "user:alice"])
    row = {"audience": "named", "team": "payments", "successor": "dave", "lead": "alice", "org": "acme"}
    assert handover.readers_for(row) == ["group:payments", "user:alice", "user:dave"]
    assert not any(r.startswith("org:") for r in handover.readers_for(row)), "the team's answer never reaches the whole org"
    assert handover.readers_for({**row, "successor": None, "lead": None}) == ["group:payments", "user:alice"]
    assert handover.readers_for({**row, "audience": "org"}) == ["org:acme"]


def test_a_shared_answers_document_never_names_what_its_prompt_was_about():
    heading, title, text = handover.document_of("owned", "Check the float before close.", "carol")
    assert heading == "About something they own" and title == "Handover from carol: About something they own"
    assert "Sev1" not in title + text and "Check the float before close." in text
    assert {handover.document_of(k, "x", "carol")[0] for k in handover.HEADINGS} == set(handover.HEADINGS.values())


def test_each_kind_of_prompt_makes_its_kind_of_unit():
    assert handover.UNIT_KINDS.get("breaks", "fact") == "lesson" and handover.UNIT_KINDS.get("asked", "fact") == "answer"
    assert all(handover.UNIT_KINDS.get(k, "fact") == "fact" for k in ("owned", "routine", "contact", "in_flight", "where"))


def test_a_unit_from_a_handover_alone_is_its_successors_or_leads_to_settle():
    kept, page = {"source_kind": "handover"}, {"source_kind": "confluence"}
    assert units.from_handover({"evidence": ["a"]}, {"a": kept})
    assert not units.from_handover({"evidence": ["a", "b"]}, {"a": kept, "b": page}), "a unit citing a page too isn't theirs"
    assert not units.from_handover({"evidence": ["a"]}, {}), "evidence they can't read isn't theirs"
    assert not units.from_handover({"evidence": []}, {})
    assert handover.verified_entries({"verified": {"by": "user:carol"}}) == [{"by": "user:carol"}]
