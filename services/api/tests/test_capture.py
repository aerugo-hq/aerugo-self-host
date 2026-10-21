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


def test_your_own_message_saves_anywhere_and_someone_elses_only_where_capture_is_on():
    me, other = {"id": "U1", "team_id": "T1"}, {"id": "U2", "team_id": "T1"}
    mine = {"ts": "1760000060.000100", "user": "U1", "text": "The truck comes at noon."}
    theirs = {**mine, "user": "U2"}
    save = lambda saver, message, captured=False, excluded=frozenset(): slack.may_save(
        saver, ["U1", "U2"], message, "T1", captured, NAMES, excluded)
    assert save(me, mine) is None
    assert save(me, theirs) == "someone else's" and save(me, theirs, captured=True) is None
    assert save(other, theirs) is None
    assert save({"id": "U3", "team_id": "T1"}, mine, captured=True) == "saver"      # not in the channel
    assert save({**me, "team_id": "T9"}, mine) == "saver"                            # a guest from a shared channel
    assert save({**me, "deleted": True}, mine) == "saver" and save({**me, "is_bot": True}, mine) == "saver"
    assert save(me, {}) == "gone"


def test_a_marked_or_sensitive_message_is_never_saved():
    me = {"id": "U1", "team_id": "T1"}
    mine = {"ts": "1760000060.000100", "user": "U1", "text": "The truck comes at noon."}
    save = lambda message, excluded=frozenset(): slack.may_save(me, ["U1"], message, "T1", True, NAMES, excluded)
    assert save(mine, frozenset({mine["ts"]})) == "marked"
    assert save({**mine, "reactions": [{"name": "no_entry_sign", "count": 1}]}) == "marked"
    assert save({**mine, "text": "Sam is having surgery on Tuesday."}) == "sensitive"


def test_only_a_save_shortcut_with_valid_ids_is_taken_from_a_delivery():
    from kl.hooks import save_request
    import json
    shortcut = {"type": "message_action", "callback_id": "aerugo_save", "channel": {"id": "C1ABC"}, "user": {"id": "U1ABC"},
                "message_ts": "1760000060.000100", "message": {"text": "never read from here"}}
    assert save_request({"payload": json.dumps(shortcut)}) == {"channel": "C1ABC", "ts": "1760000060.000100", "user": "U1ABC"}
    assert save_request({"payload": json.dumps({**shortcut, "callback_id": "other"})}) is None
    assert save_request({"payload": json.dumps({**shortcut, "type": "block_actions"})}) is None
    assert save_request({"payload": json.dumps({**shortcut, "channel": {"id": "C1ABC; DROP"}})}) is None
    assert save_request({"payload": json.dumps({**shortcut, "channel": "C1ABC"})}) is None
    assert save_request({"payload": "not json"}) is None and save_request({}) is None
    assert slack.SAVE_COMMAND.match("save my note") and not slack.SAVE_COMMAND.match("saved") and not slack.SAVE_COMMAND.match("capture on")


def test_each_saved_message_has_a_topic_of_its_own_that_a_unit_accepts():
    import re
    from kl.capture import saved_topic
    from kl.units import TOPIC
    one, other = saved_topic("C1ABC", "1760000060.000100"), saved_topic("C1ABC", "1760000060.000200")
    assert re.fullmatch(TOPIC, one) and re.fullmatch(TOPIC, other) and one != other
