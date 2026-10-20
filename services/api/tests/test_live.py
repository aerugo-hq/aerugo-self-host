"""Pointer mode (F11a): only Confluence, Jira, and Slack keep pointers; a pointer's passage is read live only for an asker
the source lists among its readers now, worked out as a sync works it out, and any failure reads as nothing, logging
no content; the passage is the section sharing most of the question; and its embeddings are made the backfill's way."""
import logging
from contextlib import contextmanager

import pytest
from pydantic import ValidationError

from kl import config, confluence, jira, live, slack, sync_items
from kl.connections_api import POINTER_ONLY, GitHubConnectionIn, NotionConnectionIn, SlackConnectionIn

ORG = f"org:{config.ORG_SLUG}"


def test_only_confluence_jira_and_slack_keep_pointers():
    assert sync_items.storage_of({"kind": "jira", "config": {"storage": "pointer"}}) == "pointer"
    assert sync_items.storage_of({"kind": "slack", "config": {"storage": "pointer"}}) == "pointer"
    assert sync_items.storage_of({"kind": "notion", "config": {"storage": "pointer"}}) == "index"  # set by hand: still indexed
    assert sync_items.storage_of({"kind": "confluence", "config": {}}) == "index"
    assert GitHubConnectionIn(name="github", installation_id=1).storage == "index"
    with pytest.raises(ValidationError, match=POINTER_ONLY):
        GitHubConnectionIn(name="github", installation_id=1, storage="pointer")
    with pytest.raises(ValidationError, match=POINTER_ONLY):
        NotionConnectionIn(name="notion", token="ntn_" + "x" * 20, storage="pointer")
    assert SlackConnectionIn(name="slack", bot_token="xoxb-12345678", storage="pointer").storage == "pointer"


def test_the_asker_reads_a_pointer_only_when_the_source_lists_them_now():
    me = {"user:ben", ORG, "ext:atlassian.ben-1"}
    assert live.allowed(["ext:atlassian.ben-1", "ext:atlassian.cora-2"], me)
    assert live.allowed([ORG], me)  # shared with the whole company
    assert not live.allowed(["ext:atlassian.cora-2"], me)
    assert not live.allowed([], me)


def test_the_passage_starts_at_the_section_sharing_most_of_the_question_and_carries_what_follows():
    sections = [(None, "Store opening checklist."), ("Ladder keys", "The spare ladder keys hang in the stock room."), ("Close", ""),
                ("After", "Lock the cage.")]
    assert live.best("Where are the spare ladder keys?", sections) == \
        "Ladder keys\nThe spare ladder keys hang in the stock room.\n\nAfter\nLock the cage."  # what follows: a thread's replies
    assert live.best("Unrelated question entirely?", sections, budget=30) == "Store opening checklist."  # none shared: the first
    assert live.best("Where are the spare ladder keys?", [("Keys", "x" * 50)], budget=10) == "Keys\n" + "x" * 50  # its best, whatever its size
    assert live.best("anything", [("Empty", "  ")]) is None


@contextmanager
def fake_scoped(docs, principals):
    class Conn:
        def execute(self, sql, params=None):
            rows = docs if "FROM documents" in sql else [{"p": principals}]
            return type("R", (), {"fetchall": lambda self: rows, "fetchone": lambda self: rows[0]})()
    yield Conn()


def test_a_live_read_fails_closed_and_logs_no_content(monkeypatch, caplog):
    docs = [{"external_key": k, "storage": "pointer", "container_ref": "C", "connection_id": cid, "kind": "jira",
             "config": {}, "credential_ref": "ref"} for k, cid in (("jira-A-1", 1), ("jira-A-2", 1), ("jira-A-3", 2))]
    docs.append({**docs[0], "external_key": "jira-A-4", "storage": "index"})  # indexed: not read live at all

    def fetch(client, cfg, doc):
        if doc["external_key"] == "jira-A-3":
            raise RuntimeError("the secret marker text")
        return (["user:ben"] if doc["external_key"] == "jira-A-1" else ["user:cora"]), [(None, "text")]
    made = []
    monkeypatch.setattr(live, "SOURCES", {"jira": (lambda d: made.append(d["connection_id"]) or object(), fetch)})
    monkeypatch.setattr(live, "scoped", lambda org, principals: fake_scoped(docs, ["user:ben", ORG]))
    caller = type("Caller", (), {"principals": ["user:ben", ORG]})()
    with caplog.at_level(logging.WARNING):
        got = live.read("org", caller, ["jira-A-1", "jira-A-2", "jira-A-3", "jira-A-4"])
    assert got == {"jira-A-1": [(None, "text")], "jira-A-2": live.NOT_READABLE, "jira-A-3": live.UNAVAILABLE}
    assert made == [1, 2]  # one client per connection
    assert "jira-A-3" in caplog.text and "RuntimeError" in caplog.text and "marker" not in caplog.text


class Jira:
    def __init__(self, open_project=False, moved_to=None):
        self.open_project, self.asked, self.moved_to = open_project, [], moved_to

    def issue(self, key):
        key = self.moved_to or key  # Jira answers a moved issue under its new key
        return {"key": key, "fields": {"summary": "Till drawer", "security": {"id": "1"} if key == "A-2" else None,
                                       "description": None, "comment": {"comments": []}}}

    def browsers(self, project=None, issue=None):
        self.asked.append(issue or project)
        return [{"accountId": "ben-1"}] if issue else [{"accountId": "cora-2"}]

    def open_to_everyone(self, project):
        return self.open_project


def test_jiras_live_readers_are_the_issues_own_or_its_projects():
    doc = lambda key: {"external_key": f"jira-{key}", "container_ref": "A"}
    readers, sections = jira.live(Jira(), {}, doc("A-1"))
    assert readers == ["ext:atlassian.cora-2"] and sections[0][1].startswith("A-1: Till drawer")
    assert jira.live(Jira(), {}, doc("A-2"))[0] == ["ext:atlassian.ben-1"]  # a security level: the issue's
    assert jira.live(Jira(open_project=True), {"company_site": True}, doc("A-1"))[0] == [ORG]
    assert jira.live(Jira(open_project=True), {}, doc("A-1"))[0] == ["ext:atlassian.cora-2"]  # not confirmed


def test_an_issue_jira_moved_to_another_project_reaches_nobody():
    assert jira.live(Jira(moved_to="HR-7"), {}, {"external_key": "jira-A-1", "container_ref": "A"}) == ([], [])
    assert jira.live(Jira(), {}, {"external_key": "jira-A-1", "container_ref": "A"})[0] == ["ext:atlassian.cora-2"]


class Confluence:
    def __init__(self, space_id="10", restricted=None, unreadable=()):
        self.space_id, self.restricted, self.unreadable = space_id, restricted or {}, set(unreadable)

    def item(self, cid):
        return {"id": cid, "title": "Returns", "spaceId": self.space_id, "parentId": "2" if cid == "3" else None, "_kind": "pages",
                "body": {"storage": {"value": "<h2>Refunds</h2><p>Within 30 days.</p>"}}}

    def space(self, key):
        return {"id": "10", "key": key}

    def space_grants(self, space_id):
        return {"ben", "cora", "dara"}, set(), False

    def group_name(self, group):
        return ""

    def page(self, cid):
        return {"id": cid, "parentId": None}

    def restriction(self, cid):
        if cid in self.unreadable:
            raise RuntimeError("can't read it")
        return self.restricted.get(cid)

    def attachments(self, cid, kind):
        return []


def test_confluences_live_readers_narrow_by_every_restriction_up_the_tree():
    readers, sections = confluence.live(Confluence(), {}, {"external_key": "confluence-3", "container_ref": "STORE"})
    assert readers == ["ext:atlassian.ben", "ext:atlassian.cora", "ext:atlassian.dara"] and ("Refunds", "Within 30 days.") in sections
    narrowed = Confluence(restricted={"2": ({"ben", "cora"}, set()), "3": ({"cora"}, set())})  # the parent's, then its own
    assert confluence.live(narrowed, {}, {"external_key": "confluence-3", "container_ref": "STORE"})[0] == ["ext:atlassian.cora"]
    moved = Confluence(space_id="99")
    assert confluence.live(moved, {}, {"external_key": "confluence-3", "container_ref": "STORE"}) == ([], [])
    with pytest.raises(RuntimeError):  # an ancestor's restriction that can't be read: the caller fails closed
        confluence.live(Confluence(unreadable={"2"}), {}, {"external_key": "confluence-3", "container_ref": "STORE"})


class Slack:
    def __init__(self, member=True):
        self.member = member

    def channel(self, channel):
        return {"id": channel, "is_member": self.member, "is_private": True}

    def team(self):
        return {"id": "T1", "url": "https://acme.slack.com"}

    def users(self):
        return {"U1": {"team": "T1", "gone": False, "bot": False, "name": "Ben"}, "U2": {"team": "T9", "gone": False, "bot": False, "name": "Guest"}}

    def members(self, channel):
        return ["U1", "U2"]

    def history(self, channel, oldest, latest=None):
        return [{"ts": f"{oldest + 60:.6f}", "text": "Truck at noon", "user": "U1"},
                {"ts": f"{oldest + 120:.6f}", "text": "A thread", "user": "U1", "reply_count": 2, "thread_ts": f"{oldest + 120:.6f}"},
                {"ts": f"{oldest + 90000:.6f}", "text": "The next day", "user": "U1"}]

    def replies(self, channel, ts):
        return [{"ts": ts, "text": "A thread", "user": "U1"}, {"ts": "1760000100.000200", "text": "Reply", "user": "U1"}]


def test_slacks_live_read_is_one_days_messages_or_one_thread_for_its_members_now():
    readers, sections = slack.live(Slack(), {}, {"external_key": "slack-C1ABC-2026-10-01"})
    assert readers == ["ext:slack.U1"]  # the other workspace's guest never
    assert [s[1] for s in sections] == ["Truck at noon"]  # that day's, the thread its own document
    assert [s[1] for s in slack.live(Slack(), {}, {"external_key": "slack-C1ABC-1760000000.000100"})[1]] == ["A thread", "Reply"]
    assert slack.live(Slack(member=False), {}, {"external_key": "slack-C1ABC-2026-10-01"}) == ([], [])  # the bot left


def test_a_pointer_thread_is_titled_by_its_day_never_by_its_openers_text():
    args = (Slack(), "C1ABC", "#stores", ["ext:slack.U1"], {"U1": "Ben"}, "https://acme.slack.com", 1760000000.0)
    titles = lambda **kw: {i.key: i.title for i in slack.items_for(*args, **kw) if i.key.count(".") == 1}
    assert list(titles().values()) == ["#stores: A thread"]
    assert list(titles(pointer=True).values()) == ["#stores: thread on 2025-10-09"]  # no message text kept (F11a)


def test_a_pointers_embeddings_are_made_the_backfills_way(monkeypatch):
    seen = []
    monkeypatch.setattr(sync_items, "embed", lambda provider, model, texts, **kw: seen.append(texts) or [[0.5, 0.5] for _ in texts])
    models = [{"id": "m1", "provider": "local", "model": "x", "dims": 2, "base_url": None, "credential_ref": None}]
    out = sync_items.pointer_vectors(models, "Returns", [(None, "Refunds within 30 days. More."), ("Exceptions", "Final sale items.")])
    assert out == [(models[0], [[0.5, 0.5], [0.5, 0.5]])]
    assert seen == [["Returns\nRefunds within 30 days. More.", "Returns\nExceptions\nRefunds within 30 days.\nFinal sale items."]]
    monkeypatch.setattr(sync_items, "embed", lambda *a, **kw: [[0.5]])
    with pytest.raises(RuntimeError):  # the wrong dimension, or a vector short: never stored
        sync_items.pointer_vectors(models, "Returns", [(None, "a"), (None, "b")])


# ---- F11b, live only: the source's own search is the runner
from kl.connections_api import LIVE_ONLY, JiraConnectionIn  # noqa: E402


def test_only_confluence_and_jira_can_be_live_only():
    assert sync_items.storage_of({"kind": "jira", "config": {"storage": "live"}}) == "live"
    assert sync_items.storage_of({"kind": "confluence", "config": {"storage": "live"}}) == "live"
    assert sync_items.storage_of({"kind": "slack", "config": {"storage": "live"}}) == "index"  # set by hand: still indexed
    assert JiraConnectionIn(name="jira", api_base="https://x.atlassian.net", credential_ref="r", storage="live").storage == "live"
    with pytest.raises(ValidationError, match="Slack's search reads as a person"):
        SlackConnectionIn(name="slack", bot_token="xoxb-12345678", storage="live")
    assert LIVE_ONLY.startswith("only Confluence and Jira")


def test_a_sources_search_is_asked_only_for_words_never_for_what_someone_typed():
    asked = {}
    client = jira.JiraClient("https://acme.atlassian.net", "bot@acme.test", "token")
    client._get = lambda path, **params: asked.update(params) or {"issues": [{"key": "STORE-1"}, {"key": "bad key"}]}
    assert client.search_text(["checkout", "errors"], ["STORE", "QUAL"]) == ["STORE-1"]
    assert asked["jql"] == 'project in (STORE, QUAL) AND (text ~ "checkout" OR text ~ "errors")'
    for words, projects in ((['x" OR project = HR'], None), (["ok"], ["HR) OR (x"]), ([], None)):
        with pytest.raises(ValueError):
            client.search_text(words, projects)
    conf = confluence.ConfluenceClient("https://acme.atlassian.net", "bot@acme.test", "token")
    conf._get = lambda path, **params: asked.update(params) or {"results": [{"content": {"id": "71004"}}, {"content": {"id": "x"}}]}
    assert conf.search_text(["returns"], ["STORES"]) == ["71004"]
    assert asked["cql"] == 'type in (page, blogpost) AND space in ("STORES") AND (text ~ "returns")'
    with pytest.raises(ValueError):
        conf.search_text(["returns"], ['STORES") OR space = "HR'])


def test_search_words_are_the_questions_longest_content_words_and_sources_take_turns():
    assert live.search_words("What's the runbook when checkout errors spike?") == ["checkout", "runbook", "errors", "spike"]
    assert len(live.search_words(" ".join(f"word{n}" for n in range(20)))) == live.MAX_WORDS
    assert live.interleave([["a1", "a2", "a3"], ["b1"], ["c1", "a2"]]) == ["a1", "b1", "c1", "a2", "a3"]
    assert live.interleave([]) == []


def test_a_live_search_that_fails_finds_nothing_and_the_others_still_answer(monkeypatch, caplog):
    connections = [{"id": "c-jira", "kind": "jira", "config": {}, "credential_ref": "r"},
                   {"id": "c-conf", "kind": "confluence", "config": {}, "credential_ref": "r"}]

    @contextmanager
    def scoped(org, principals):
        yield type("Conn", (), {"execute": lambda self, sql, params=None: type("R", (), {"fetchall": lambda self: connections})()})()

    def broken(client, cfg, words):
        raise RuntimeError("the secret marker text")
    monkeypatch.setattr(live, "scoped", scoped)
    monkeypatch.setattr(live, "SOURCES", {"jira": (lambda c: object(), None), "confluence": (lambda c: object(), None)})
    monkeypatch.setattr(live, "SEARCHES", {"jira": broken, "confluence": lambda client, cfg, words: ["confluence-1", "confluence-2"]})
    caller = type("Caller", (), {"principals": ["user:ben"]})()
    with caplog.at_level(logging.WARNING):
        assert live.search("org", caller, "returns window", ["stores"]) == [("c-conf", "confluence-1"), ("c-conf", "confluence-2")]
    assert "RuntimeError" in caplog.text and "marker" not in caplog.text
    assert live.search("org", caller, "the a an", ["stores"]) == []  # no content words: no source is asked
