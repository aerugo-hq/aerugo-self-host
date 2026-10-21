"""The bot answers privately, as the asker (C2d): only someone whose Slack account is linked to them, recently signed
in, and let in by the org's admission is answered, as them; a mention is answered only for its own author; the reply
escapes Slack's control characters, links sources to Aerugo's reader by title only, and says when some weren't sent;
and asks run only in their own lane, never in the worker."""
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from kl import slack, slack_ask, worker

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def test_a_sign_in_counts_for_seven_days():
    assert slack_ask.fresh(NOW - timedelta(days=6, hours=23), NOW)
    assert not slack_ask.fresh(NOW - timedelta(days=7, minutes=1), NOW)
    assert not slack_ask.fresh(None, NOW)


def test_the_reply_escapes_slack_and_links_only_titles():
    result = {"answer": "Use <https://evil.example|this> & ask @here [1]", "withheld_by_policy": 1,
              "confidence": {"level": "medium"},
              "citations": [{"id": "pay-runbook", "title": "Runbook <b>", "cited": True},
                            {"id": "unit:0b1d", "title": "Refund cap", "cited": True, "unit": {"kind": "fact"}},
                            {"id": "legal-hold", "title": "Not cited", "cited": False}]}
    reply = slack_ask.reply_of(result, "https://aerugo.example")
    assert "<https://evil.example|this>" not in reply and "&lt;https://evil.example|this&gt; &amp; ask" in reply
    assert "<https://aerugo.example/#/documents/pay-runbook|Runbook &lt;b&gt;>" in reply
    assert "- Refund cap (knowledge)" in reply and "unit%3A0b1d" not in reply  # a unit has no reader page yet (D1)
    assert "Not cited" not in reply and "Confidence: medium" in reply and "Some sources aren't shown in Slack" in reply
    long = slack_ask.reply_of({"answer": "x" * 5000, "citations": []}, "https://aerugo.example")
    assert len(long) < 3000 and long.endswith("...")


def test_a_mention_s_question_drops_only_the_bot_s_own_mention():
    assert slack.question_of("<@UAERUGOBOT> what is the refund cap? cc <@U2ABC>", "UAERUGOBOT") == "what is the refund cap? cc <@U2ABC>"
    assert slack.question_of("hey <@UAERUGOBOT|aerugo>  where's the key?", "UAERUGOBOT") == "hey where's the key?"
    assert slack.question_of("no bot here", None) == "no bot here"


def test_only_ask_with_a_question_is_a_question():
    assert slack.ASK_COMMAND.match("ask where is the gate key?")[1] == "where is the gate key?"
    assert slack.ASK_COMMAND.match("  Ask ")[1] == ""
    assert not slack.ASK_COMMAND.match("asking around") and not slack.ASK_COMMAND.match("capture on")


def test_asks_run_only_in_their_own_lane():
    from kl import answers_lane
    assert set(answers_lane.HANDLERS) == set(worker.LANE_KINDS) and "slack.ask" not in worker.HANDLERS
    assert "NOT (kind = ANY(%s))" in worker.WORKER_CLAIM_SQL and "AND kind = ANY(%s)" in worker.LANE_CLAIM_SQL


class Conn:
    """Stands in for the lane's connection: the link and the sign-in it's asked for."""

    def __init__(self, link=None, seen=None):
        self.rows = {slack_ask.PERSON_SQL: link, slack_ask.SIGN_IN_SQL: seen}
        self.audits = []

    def execute(self, sql, params=()):
        if sql.startswith("INSERT INTO audit_events"):
            self.audits.append(params)
        row = self.rows.get(sql)

        class Result:
            def fetchone(self):
                return row
        return Result()


class Client:
    def __init__(self, message=None):
        self.message_ = message or {}

    def team(self):
        return {"id": "T1", "url": "https://acme.slack.com", "bot": "UAERUGOBOT"}

    def user(self, user):
        return {"id": user, "team_id": "T1", "profile": {"email": "alice@acme.test"}}

    def message(self, channel, ts):
        return self.message_


@pytest.fixture
def lane(monkeypatch):
    told, asked = [], []
    monkeypatch.setattr(slack_ask, "channel_of", lambda conn, p: {"id": "c1", "org_id": "o1", "team": "payments",
                                                                  "config": {"answers": {"model": "payments/fake"}}})
    monkeypatch.setattr(slack_ask, "tell", lambda c, channel, user, text, thread=None: told.append(text))
    monkeypatch.setattr(slack_ask.admission, "admitted", lambda caller: caller.username != "nora")
    api = types.ModuleType("kl.main")  # the API's own Ask, stood in for: the lane imports it when it answers
    api.answer_question = lambda caller, q, model, reach: asked.append((caller, q, reach)) or {
        "answer": "the answer", "citations": [], "confidence": {"level": "high"}}
    monkeypatch.setitem(sys.modules, "kl.main", api)
    return told, asked


def job(**payload):
    return {"payload": {"connection_id": "c1", "channel": "C1ABC", "user": "U1ABC", **payload}}


def test_only_a_linked_recently_signed_in_admitted_person_is_answered_as_themselves(lane, monkeypatch):
    told, asked = lane
    monkeypatch.setattr(slack, "client_for", lambda c: Client())
    linked = {"canonical": "user:alice", "verified_by": "sso-email"}
    assert slack_ask.ask(Conn(), job(question="refund cap?", via="command")) == {"refused": "unlinked"}
    assert slack_ask.ask(Conn(linked, {"groups": ["payments"], "signed_in_at": datetime.now(timezone.utc) - timedelta(days=8)}),
                         job(question="refund cap?", via="command")) == {"refused": "stale"}
    nora = {"canonical": "user:nora", "verified_by": "sso-email"}
    recent = {"groups": ["pilot"], "signed_in_at": datetime.now(timezone.utc)}
    assert slack_ask.ask(Conn(nora, recent), job(question="refund cap?", via="command")) == {"refused": "not onboarded"}
    conn = Conn(linked, {"groups": ["payments"], "signed_in_at": datetime.now(timezone.utc)})
    assert slack_ask.ask(conn, job(question="refund cap?", via="command")) == {"answered": True}
    caller, question, reach = asked[-1]
    assert (caller.username, caller.groups, caller.email, question) == ("alice", ("payments",), "alice@acme.test", "refund cap?")
    assert reach == frozenset({"public", "internal"}) and len(conn.audits) == 1
    assert told[:3] == [slack_ask.SAY["unlinked"], slack_ask.SAY["stale"], slack_ask.SAY["not onboarded"]] and "the answer" in told[3]


def test_a_mention_is_answered_only_for_its_own_author_and_read_back_from_slack(lane, monkeypatch):
    told, asked = lane
    linked = {"canonical": "user:alice", "verified_by": "admin:olivia"}
    recent = {"groups": [], "signed_in_at": datetime.now(timezone.utc)}
    monkeypatch.setattr(slack, "client_for", lambda c: Client({"ts": "1760000001.000001", "user": "U2CAROL", "text": "<@UAERUGOBOT> q"}))
    assert slack_ask.ask(Conn(linked, recent), job(ts="1760000001.000001", via="mention")) == {"skipped": "the message isn't the asker's"}
    monkeypatch.setattr(slack, "client_for", lambda c: Client({"ts": "1760000001.000001", "user": "U1ABC", "text": "<@UAERUGOBOT> where?"}))
    assert slack_ask.ask(Conn(linked, recent), job(ts="1760000001.000001", via="mention", question="never from here")) == {"answered": True}
    caller, question, _ = asked[-1]
    assert question == "where?" and caller.email is None  # an admin's link proves no email
