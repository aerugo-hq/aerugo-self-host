"""The Python client against a stand-in for the API (httpx.MockTransport): what it sends, and what it does with replies."""
import json

import httpx
import pytest

from aerugo import AerugoError, Client


def stand_in(seen: list):
    feed = [{"cursor": 1, "key": "a", "change": "added"}, {"cursor": 2, "key": "a", "change": "removed"}]

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("Authorization") != "Bearer t0k3n":
            return httpx.Response(401, json={"detail": "not signed in"})
        if request.url.path == "/v1/search":
            return httpx.Response(200, json={"results": [{"id": "kb-x"}], "facets": {}})
        if request.url.path == "/v1/changes":
            since = int(request.url.params["since"])
            rest = [c for c in feed if c["cursor"] > since][:1]
            return httpx.Response(200, json={"changes": rest, "next": rest[-1]["cursor"] if rest else since, "more": bool(rest)})
        if request.url.path.endswith("/content"):
            return httpx.Response(404, json={"detail": "document not found"})
        if request.url.path == "/v1/workspaces/payments/documents":
            return httpx.Response(202, json={"push": "p1"})
        if request.url.path == "/v1/workspaces/payments/documents/pushes/p1":
            return httpx.Response(200, json={"status": "done", "result": {"upserted": 1}})
        return httpx.Response(404, json={"detail": "not found"})
    return httpx.MockTransport(answer)


def test_search_sends_the_token_and_the_filters():
    seen = []
    kl = Client("http://localhost:8000", token="t0k3n", transport=stand_in(seen))
    assert kl.search("lag", source=["jira", "confluence"])["results"] == [{"id": "kb-x"}]
    assert seen[0].url.params["source"] == "jira,confluence" and seen[0].headers["Authorization"] == "Bearer t0k3n"


def test_a_refusal_is_an_error_with_its_status():
    kl = Client("http://localhost:8000", token="t0k3n", transport=stand_in([]))
    with pytest.raises(AerugoError) as e:
        kl.document("kb-hidden")
    assert e.value.status == 404 and "not found" in e.value.detail


def test_the_feed_is_followed_to_its_end():
    kl = Client("http://localhost:8000", token="t0k3n", transport=stand_in([]))
    assert [c["change"] for c in kl.changes()] == ["added", "removed"]


def test_a_push_waits_for_the_worker():
    seen = []
    kl = Client("http://localhost:8000", token="t0k3n", transport=stand_in(seen))
    state = kl.push("payments", upserts=[{"key": "a.md"}], wait=5)
    assert state["status"] == "done" and json.loads(seen[0].content)["upserts"] == [{"key": "a.md"}]


def test_plain_http_is_refused_off_localhost():
    with pytest.raises(ValueError):
        Client("http://aerugo.example.com", token="x")
