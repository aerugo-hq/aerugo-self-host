"""Aerugo's Python client (A2): search, Ask, documents, the change feed, and pipeline pushes.

Every call acts as whoever the token belongs to (a person's OIDC access token, or a pipeline's workload token for
pushes), so it sees exactly what they may see: the client adds nothing, and holds no key of its own.

    from aerugo import Client
    kl = Client("https://aerugo.example.com", token=access_token)
    for hit in kl.search("replication lag", source=["confluence"])["results"]:
        print(hit["title"], hit["container"])
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from urllib.parse import quote

import httpx

__all__ = ["Client", "AerugoError"]
__version__ = "0.1.0"


class AerugoError(RuntimeError):
    """A request Aerugo refused or couldn't serve: its status, and what it said."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"{status}: {detail}")
        self.status, self.detail = status, detail


class Client:
    def __init__(self, base_url: str, token: str | Callable[[], str], timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None):
        """token: a bearer token, or a function returning a fresh one for each request (to refresh it yourself)."""
        if not base_url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise ValueError("Aerugo is reached over https (http only for a local stack)")
        self._token = token if callable(token) else (lambda: token)
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)

    def _call(self, method: str, path: str, **kw) -> dict:
        r = self._http.request(method, path, headers={"Authorization": f"Bearer {self._token()}"}, **kw)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text[:200]
            raise AerugoError(r.status_code, detail if isinstance(detail, str) else str(detail)[:200])
        return r.json()

    def search(self, q: str, scope: str = "org", limit: int = 10, source: list[str] | None = None,
               container: list[str] | None = None) -> dict:
        params = {"q": q, "scope": scope, "limit": limit}
        if source:
            params["source"] = ",".join(source)
        if container:
            params["container"] = ",".join(container)
        return self._call("GET", "/v1/search", params=params)

    def ask(self, question: str, model: str, scope: str = "org", sources: list[str] | None = None,
            containers: list[str] | None = None) -> dict:
        """An answer from what the caller may read, with its citations, confidence, and claims."""
        body: dict[str, object] = {"question": question, "model": model, "scope": scope}
        if sources:
            body["sources"] = sources
        if containers:
            body["containers"] = containers
        return self._call("POST", "/v1/ask", json=body)

    def documents(self, scope: str = "org", source: str | None = None, q: str | None = None, limit: int = 50) -> dict:
        params = {k: v for k, v in {"scope": scope, "source": source, "q": q, "limit": limit}.items() if v is not None}
        return self._call("GET", "/v1/documents", params=params)

    def document(self, key: str) -> dict:
        """A document's sections, if the caller may read it; otherwise AerugoError(404)."""
        return self._call("GET", f"/v1/documents/{quote(key, safe='')}/content")

    def changes(self, since: int = 0, limit: int = 100) -> Iterator[dict]:
        """Every change since a cursor, following the feed to its end; each carries its own cursor to resume from."""
        while True:
            page = self._call("GET", "/v1/changes", params={"since": since, "limit": limit})
            yield from page["changes"]
            since = page["next"]
            if not page["more"]:
                return

    def push(self, team: str, upserts: list[dict] | None = None, deletes: list[str] | None = None,
             wait: float = 0.0) -> dict:
        """A pipeline's push (its workload token as the client's token). With wait, polls until the worker applied it."""
        accepted = self._call("POST", f"/v1/workspaces/{quote(team, safe='')}/documents",
                              json={"upserts": upserts or [], "deletes": deletes or []})
        deadline = time.monotonic() + wait
        while wait and time.monotonic() < deadline:
            state = self._call("GET", f"/v1/workspaces/{quote(team, safe='')}/documents/pushes/{accepted['push']}")
            if state["status"] != "queued":
                return state
            time.sleep(1)
        return accepted

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *_) -> None:
        self.close()
