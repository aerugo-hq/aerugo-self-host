"""Sensitive chat (C2a): a message speaking of one person's health, discipline, performance, or pay, or holding personal
data, is dropped from what a Slack document keeps and from what a live read gives, though the channel's members can read
it in Slack; a thread opening with one is titled by its day; and a document that held one is read again once."""
from kl import privacy, slack

NAMES = {"U1": "Dara", "U2": "Ben"}


def message(ts: str, text: str, user: str = "U1", **more) -> dict:
    return {"ts": ts, "text": text, "user": user, **more}


def test_the_gate_flags_one_persons_sensitive_matters_and_personal_data_only():
    assert privacy.sensitive("Sam Rivera is having surgery on Tuesday, so I'm covering Sam's shifts.")
    assert privacy.sensitive("Jordan Blake got a final written warning for attendance today.")
    assert privacy.sensitive("My SSN is 412-55-9876, can you update payroll?")
    assert not privacy.sensitive("The truck comes at noon; the rota is posted by the till.")
    assert not privacy.sensitive("")


def test_a_sensitive_message_is_left_out_and_the_others_kept():
    sections = slack.sections_of([message("1760000000.000100", "Sam is having surgery on Tuesday."),
                                  message("1760000060.000100", "Thanks, I'll cover the rota.", "U2")], NAMES)
    assert [body for _, body in sections] == ["Thanks, I'll cover the rota."]


class Workspace:
    def history(self, channel, oldest, latest=None):
        return [message("1760000000.000100", "Sam is having surgery on Tuesday.", reply_count=1, thread_ts="1760000000.000100"),
                message("1760000600.000100", "Truck at noon.")]

    def replies(self, channel, ts):
        return [message(ts, "Sam is having surgery on Tuesday."), message("1760000100.000100", "Wishing Sam well.", "U2")]


def test_a_thread_opening_with_one_is_titled_by_its_day_and_read_again_without_it():
    items = {i.key: i for i in slack.items_for(Workspace(), "C1ABC", "#co201", ["ext:slack.U1"], NAMES, "https://acme.slack.com", 1759990000.0)}
    thread, day = items["slack-C1ABC-1760000000.000100"], items["slack-C1ABC-2025-10-09"]
    assert thread.title == "#co201: thread on 2025-10-09" and "surgery" not in thread.title
    assert [body for _, body in thread.sections()[1]] == ["Wishing Sam well."]
    assert ":d" in thread.version and ":d" not in day.version  # only what held one changes version
