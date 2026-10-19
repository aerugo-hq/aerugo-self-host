"""Extraction (F2): chat threads are never sent; a document becomes numbered sections, cut to the prompt's size; and a
model's reply is kept only as units F1 accepts, each sentence and structured value stated by the section it cites, and
nothing about one person's health or discipline; everything else is dropped and counted by why."""
import json

from kl.extraction import MAX_PROMPT_CHARS, MAX_UNITS, checked, extractable, prompt_for, sections_of, topic_of

SECTIONS = {1: ("Restart", "Restart the settlement worker from the admin console, then confirm the queue drains within 10 minutes."),
            2: ("Cover", "Jamie Doe had surgery on Tuesday, so Lee covers the settlement queue."),
            3: ("Who to call", "For refunds over $500, call the finance lead on 555-0170.")}


def reply(*units):
    return json.dumps({"units": list(units)})


def unit(text, kind="fact", **kw):
    return {"kind": kind, "topic": "settlement", "title": "Settlement", "text": text, **kw}


def test_chat_threads_are_never_sent_until_their_channels_opt_in():
    assert extractable("confluence") and extractable("jira") and extractable("github") and extractable("import")
    assert not extractable("slack") and not extractable("teams") and not extractable("meeting")


def test_a_document_becomes_numbered_sections_joined_under_their_headings_and_cut_to_size():
    got = sections_of("Runbook", [("Restart", "Step one."), ("Restart", "Step two."), (None, "Overview."), ("Cover", "x" * MAX_PROMPT_CHARS)])
    assert got == {1: ("Restart", "Step one.\nStep two."), 2: ("Runbook", "Overview.")}
    assert prompt_for("Runbook", got).startswith("Document: Runbook\n\n[1] Restart\nStep one.")


def test_a_supported_unit_is_kept_with_its_section_and_confidence():
    out = checked(reply(unit("Restart the settlement worker from the admin console [1].")), "Runbook", SECTIONS)
    assert [p.text for p in out.kept] == ["Restart the settlement worker from the admin console [1]."] and not out.dropped
    assert out.kept[0].section == "Restart" and out.kept[0].confidence == 1.0


def test_a_sentence_its_section_doesnt_state_is_dropped_as_unsupported():
    out = checked(reply(unit("The settlement queue moves to the 2031 platform on Friday [1]."),
                        unit("Restart the settlement worker from the admin console.")), "Runbook", SECTIONS)  # cites nothing
    assert not out.kept and out.dropped["unsupported"] == 2


def test_a_different_number_or_a_negation_is_unsupported_however_many_words_it_shares():
    out = checked(reply(unit("Confirm the queue drains within 15 minutes, from the admin console [1]."),
                        unit("For refunds over $50, call the finance lead on 555-0170 [3]."),
                        unit("Do not restart the settlement worker from the admin console [1].")), "Runbook", SECTIONS)
    assert not out.kept and out.dropped["unsupported"] == 3


def test_a_unit_about_one_persons_health_is_dropped_even_when_its_section_says_it():
    out = checked(reply(unit("Jamie Doe had surgery on Tuesday, so Lee covers the settlement queue [2].")), "Runbook", SECTIONS)
    assert not out.kept and out.dropped == {"sensitive": 1}


def test_a_structured_value_its_section_doesnt_state_is_unsupported():
    steps = {"steps": [{"text": "Restart the settlement worker from the admin console"}, {"text": "Page the payments director"}]}
    out = checked(reply(unit("Restart the settlement worker from the admin console [1].", kind="procedure", structure=steps)), "Runbook", SECTIONS)
    assert not out.kept and out.dropped["unsupported"] == 1
    good = {"steps": [{"text": "Restart the settlement worker from the admin console"}, {"text": "Confirm the queue drains within 10 minutes"}]}
    out = checked(reply(unit("Restart the settlement worker from the admin console [1].", kind="procedure", structure=good)), "Runbook", SECTIONS)
    assert len(out.kept) == 1 and out.kept[0].structure == good


def test_a_reply_that_isnt_units_is_dropped_as_invalid():
    assert checked("Sure! Here are some facts.", "Runbook", SECTIONS).dropped == {"invalid": 1}
    out = checked(reply(unit("Restart the settlement worker [1].", kind="rumor"), unit("Restart the worker [1].", kind="contact", structure={"reach": "x"}),
                        unit("x" * 2001)), "Runbook", SECTIONS)
    assert not out.kept and out.dropped == {"invalid": 3}


def test_a_document_keeps_at_most_ten_proposals():
    many = [unit("Restart the settlement worker from the admin console [1].") for _ in range(MAX_UNITS + 5)]
    assert len(checked(reply(*many), "Runbook", SECTIONS).kept) == MAX_UNITS


def test_a_topic_is_a_slug_from_what_the_model_wrote_or_the_title():
    assert topic_of("Settlement Restart!", "x") == "settlement-restart"
    assert topic_of("", "Payments: on-call") == "payments-on-call"
