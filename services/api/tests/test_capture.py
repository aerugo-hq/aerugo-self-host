"""Slack channel capture (C2b): only `/aerugo capture on|off` is a command; the first key belongs to the channel's
creator or a workspace admin, never a guest, a leaver, or a bot; a message marked "don't capture" is dropped like a
sensitive one, and each new mark moves its document's version so it's read again without it."""
from kl import slack
from kl.slack import capture_action, may_turn

NAMES = {"U1": "Dara"}


def test_only_capture_on_or_off_is_a_command():
    assert capture_action("capture on") == "on" and capture_action("  Capture OFF ") == "off"
    assert capture_action("capture") is None and capture_action("capture on now") is None and capture_action("") is None


def test_the_first_key_is_the_channels_creator_or_a_workspace_admin():
    channel = {"id": "C1ABC", "creator": "U1"}
    person = lambda uid, **more: {"id": uid, "team_id": "T1", **more}
    assert may_turn(person("U1"), channel, "T1")                        # its creator
    assert may_turn(person("U2", is_admin=True), channel, "T1")         # a workspace admin
    assert may_turn(person("U3", is_owner=True), channel, "T1")
    assert not may_turn(person("U4"), channel, "T1")                    # a member, no more
    assert not may_turn(person("U1", team_id="T9"), channel, "T1")      # a guest from a shared channel's other side
    assert not may_turn(person("U1", deleted=True), channel, "T1")      # someone who has left
    assert not may_turn(person("U2", is_admin=True, is_bot=True), channel, "T1")
    assert not may_turn({}, channel, "T1")


def test_a_message_marked_dont_capture_is_dropped_and_each_mark_reads_its_document_again():
    messages = [{"ts": "1760000000.000100", "text": "Truck at noon.", "user": "U1"},
                {"ts": "1760000060.000100", "text": "Gate code changes Friday.", "user": "U1"}]
    assert [b for _, b in slack.sections_of(messages, NAMES, frozenset({"1760000060.000100"}))] == ["Truck at noon."]
    plain, one, both = (slack.kept_version(messages, NAMES, frozenset(x)) for x in ((), {"1760000060.000100"},
                                                                                    {"1760000060.000100", "1760000000.000100"}))
    assert len({plain, one, both}) == 3  # a new mark always moves the version, so the sync rewrites it


def test_a_marked_message_names_every_document_it_can_be_in():
    assert slack.holding("C1ABC", {"ts": "1760000060.000100"}) == ["slack-C1ABC-1760000060.000100", "slack-C1ABC-2025-10-09"]
    assert slack.holding("C1ABC", {"ts": "1760000060.000100", "thread_ts": "1760000000.000100"}) == [
        "slack-C1ABC-1760000000.000100", "slack-C1ABC-1760000060.000100", "slack-C1ABC-2025-10-09"]  # a reply: its thread
    assert slack.holding("C1ABC", {"ts": "not-a-ts"}) == [] and slack.holding("C1ABC", {}) == []
