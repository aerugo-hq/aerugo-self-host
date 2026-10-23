"""Meetings (C3a, C3b): only "capture on", "don't capture", and "review" are commands; a meeting with anyone but the
company's own is never captured; a transcript keeps who said each line, a section a minute, and never a sensitive line;
only a mention of the bot in a meeting's chat, or a press on a review card in a 1:1 chat, is taken from a delivery, as
ids; a review card lists each proposal with its buttons and no link in its text; a unit from meetings alone is settled
by the people in them; and extraction sends a long document in parts, a meeting proposing decisions, lessons, and facts
alone."""
import json
import uuid

from kl import extraction, meetings, teams, teams_bot, units

APP, TENANT = str(uuid.uuid4()), str(uuid.uuid4())
CHAT = "19:meeting_NzQ5abc-DEF_1@thread.v2"


def member(email, *roles, tenant=TENANT):
    return {"userId": str(uuid.uuid4()), "email": email, "displayName": email.split("@")[0], "roles": list(roles), "tenantId": tenant}


def test_only_capture_on_and_dont_capture_are_commands():
    assert meetings.intent("capture on") == "on" and meetings.intent("  Capture ON. ") == "on"
    assert meetings.intent("don't capture") == "off" and meetings.intent("Don’t capture!") == "off"
    assert meetings.intent("capture off") == "off" and meetings.intent("do not capture") == "off"
    for other in ("", "capture", "capture everything", "please capture on now", "what's the refund cutoff?"):
        assert meetings.intent(other) is None, other


def test_a_meeting_with_a_guest_or_another_tenant_or_no_email_holds_outsiders():
    ours = [member("ana@acme.test", "owner"), member("eli@acme.test")]
    assert not meetings.outsiders(ours, TENANT)
    assert meetings.outsiders(ours + [member("vendor@else.test", "guest")], TENANT)
    assert meetings.outsiders(ours + [member("pat@partner.test", tenant=str(uuid.uuid4()))], TENANT)
    assert meetings.outsiders(ours + [{"userId": str(uuid.uuid4()), "roles": [], "tenantId": TENANT}], TENANT)


def test_a_transcript_keeps_who_said_each_line_a_section_a_minute_and_no_sensitive_line():
    vtt = ("WEBVTT\n\n00:00:05.000 --> 00:00:09.000\n<v Kai Moreno>Northpeak confirmed the poles ship on 20 May.</v>\n\n"
           "00:00:12.500 --> 00:00:15.000\n<v Priya Shah>Sam is having surgery on Tuesday.</v>\n\n"
           "00:00:20.000 --> 00:00:22.000\n<v.loud Priya Shah>Then stores have them by 27 May.</v>\n\n"
           "NOTE a comment\n\n00:01:30.000 --> 00:01:33.000\nNo speaker named.\n")
    assert meetings.sections_of(vtt) == [
        ("At 00:00:05", "Kai Moreno: Northpeak confirmed the poles ship on 20 May.\nPriya Shah: Then stores have them by 27 May."),
        ("At 00:01:30", "No speaker named.")]
    assert meetings.version(meetings.sections_of(vtt)) != meetings.version(meetings.sections_of(vtt.replace("27 May", "28 May")))
    assert meetings.sections_of("WEBVTT\n") == []


def test_a_transcripts_key_is_its_chat_and_id_by_a_hash():
    key = meetings.key_of(CHAT, "MSo1Ny9Z+/a=")
    assert key == meetings.key_of(CHAT, "MSo1Ny9Z+/a=") and key != meetings.key_of(CHAT, "other")
    assert key.startswith("meeting-") and set(key) <= set("meeting-0123456789abcdef")


def activity(**more):
    base = {"type": "message", "id": "1761000000001", "conversation": {"id": CHAT, "conversationType": "groupChat"},
            "from": {"id": "29:abc-1", "aadObjectId": "11111111-2222-3333-4444-555555555555"},
            "channelData": {"tenant": {"id": TENANT}}, "text": "Aerugo capture on",
            "entities": [{"type": "mention", "mentioned": {"id": f"28:{APP}"}}]}
    return {**base, **more}


def test_only_a_mention_of_the_bot_in_a_meetings_chat_is_taken_and_only_its_ids():
    assert teams_bot.meeting_request(activity(), TENANT, APP) == {
        "chat": CHAT, "message": "1761000000001", "user": "11111111-2222-3333-4444-555555555555", "user_id": "29:abc-1"}
    refused = {"an ordinary group chat": activity(conversation={"id": "19:abc_def@thread.v2", "conversationType": "groupChat"}),
               "no mention of the bot": activity(entities=[{"type": "mention", "mentioned": {"id": "28:someone-else"}}]),
               "another tenant": activity(channelData={"tenant": {"id": str(uuid.uuid4())}}),
               "a channel": activity(conversation={"id": CHAT, "conversationType": "channel"}),
               "an action, not a message": activity(type="invoke"),
               "a message id that isn't one": activity(id="../evil")}
    for problem, a in refused.items():
        assert teams_bot.meeting_request(a, TENANT, APP) is None, problem
    assert teams.MEETING_CHAT.fullmatch(CHAT) and not teams.CHANNEL.fullmatch(CHAT)
    assert teams.CONVERSATION.fullmatch(CHAT), "the Bot Connector can't read back who's in a meeting's chat"


def test_a_unit_from_meetings_alone_is_settled_by_the_people_in_them():
    meeting, page = {"source_kind": "meeting"}, {"source_kind": "confluence"}
    assert units.from_meetings({"evidence": ["a", "b"]}, {"a": meeting, "b": meeting})
    assert not units.from_meetings({"evidence": ["a", "b"]}, {"a": meeting, "b": page}), "a unit citing a page too isn't theirs alone"
    assert not units.from_meetings({"evidence": ["a", "b"]}, {"a": meeting}), "evidence they can't read isn't theirs"
    assert not units.from_meetings({"evidence": []}, {})


def test_extraction_sends_a_long_document_in_parts_each_within_its_limits():
    chunks = [(f"At 00:{n:02d}:00", "word " * 400) for n in range(60)]  # about 2,000 characters a minute
    parts = extraction.parts_of("Stand-up", chunks)
    assert 1 < len(parts) <= extraction.MAX_PARTS
    for part in parts:
        assert list(part) == list(range(1, len(part) + 1)) and len(part) <= extraction.MAX_SECTIONS
        assert sum(len(h) + len(b) for h, b in part.values()) <= extraction.MAX_PROMPT_CHARS
    assert extraction.sections_of("Stand-up", chunks) == parts[0]
    assert len(extraction.parts_of("Stand-up", chunks * 10)) == extraction.MAX_PARTS, "a document past six parts is cut there"
    assert extraction.parts_of("Short", [("Intro", "One line.")]) == [{1: ("Intro", "One line.")}]


def test_a_meeting_proposes_decisions_lessons_and_facts_alone():
    sections = {1: ("At 00:00:05", "Kai Moreno: We decided to recall the TrailLite 2 tent on 14 May.")}
    reply = ('{"units": [{"kind": "decision", "topic": "recall", "title": "Recall", "text": "We decided to recall the TrailLite 2 tent on 14 May [1]."},'
             ' {"kind": "procedure", "topic": "steps", "title": "Steps", "text": "We decided to recall the TrailLite 2 tent on 14 May [1].",'
             ' "structure": {"steps": [{"text": "recall the TrailLite 2 tent"}]}}]}')
    got = extraction.for_meeting(extraction.checked(reply, "Stand-up", sections))
    assert [p.kind for p in got.kept] == ["decision"] and got.dropped["invalid"] == 1
    assert [p.kind for p in extraction.checked(reply, "A page", sections).kept] == ["decision", "procedure"], "a page proposes every kind"
    assert "never action items" in extraction.prompt_for("Stand-up", sections, extraction.MEETING_NOTE)


def test_review_is_a_command_too():
    assert meetings.intent("review") == "review" and meetings.intent("Review.") == "review"
    assert meetings.intent("review everything") is None


def press(**more):
    base = {"type": "message", "id": "f:press-1", "value": {"aerugo": "review", "verb": "confirm", "unit": str(uuid.uuid4())},
            "conversation": {"id": "a:abc-1", "conversationType": "personal"},
            "from": {"id": "29:abc-1", "aadObjectId": "11111111-2222-3333-4444-555555555555"}, "channelData": {"tenant": {"id": TENANT}}}
    return {**base, **more}


def test_a_press_on_a_review_card_is_taken_only_as_ids_from_a_1_1_chat():
    pressed = press()
    assert teams_bot.review_request(pressed, TENANT) == {"unit": pressed["value"]["unit"], "verb": "confirm", "conversation": "a:abc-1",
                                                          "user": "11111111-2222-3333-4444-555555555555", "user_id": "29:abc-1"}
    refused = {"a channel": press(conversation={"id": "19:abc@thread.tacv2", "conversationType": "channel"}),
               "a meeting's chat": press(conversation={"id": CHAT, "conversationType": "groupChat"}),
               "a verb that isn't one": press(value={"aerugo": "review", "verb": "delete", "unit": str(uuid.uuid4())}),
               "a unit that isn't an id": press(value={"aerugo": "review", "verb": "confirm", "unit": "1 OR 1=1"}),
               "another card's data": press(value={"verb": "confirm", "unit": str(uuid.uuid4())}),
               "another tenant": press(channelData={"tenant": {"id": str(uuid.uuid4())}})}
    for problem, a in refused.items():
        assert teams_bot.review_request(a, TENANT) is None, problem


def test_a_review_card_lists_each_proposal_with_its_buttons_and_no_link_in_its_text():
    from kl import meeting_review
    found = [{"id": str(uuid.uuid4()), "title": f"Point {n}", "text": "See [the playbook](https://evil.example/x) or <https://evil.example/y>."}
             for n in range(12)]
    card = meeting_review.card_of("Refunds sync", found)
    text = json.dumps(card)
    assert "](" not in text and "<" not in text and "(the playbook)" in text, "a card's text can become a link"
    actions = [a for b in card["body"] if b["type"] == "ActionSet" for a in b["actions"]]
    assert len(actions) == 2 * meeting_review.MAX_CARD_UNITS
    assert {a["title"] for a in actions} == {"Confirm", "Not right"}
    assert {a["data"]["verb"] for a in actions} == {"confirm", "dismiss"} and all(a["data"]["aerugo"] == "review" for a in actions)
    assert card["body"][-1]["text"].startswith("And 2 more")
    assert meeting_review.plain("x" * 500, 10) == "x" * 10 + "..."
