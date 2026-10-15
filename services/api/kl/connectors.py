"""Connector contract (docs/design/v1.md §7) and the GitHub connector.
Slice 1 added test() and discover(); slice 2 adds the reads that content sync needs."""
import base64
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

import httpx

from .github import GitHubApp

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


@dataclass(frozen=True)
class Health:
    ok: bool
    detail: str


@dataclass(frozen=True)
class Container:
    external_id: str
    kind: str
    visibility: str | None = None
    default_ref: str | None = None
    name: str | None = None  # how its source names it: a space, project, shared drive or folder, or repository


class Connector(Protocol):
    kind: str

    def test(self) -> Health: ...                                 # at setup and on a schedule
    def discover(self) -> list[Container]: ...                    # what this connection can reach
    def sync(self, cursor: str | None) -> Iterator[dict]: ...     # upserts and tombstones (slice 2)
    def fetch(self, ref: str) -> bytes: ...                       # raw content (slice 2)
    def acl(self, ref: str) -> list[str]: ...                     # principals, as the source sees them (slice 3)
    def on_event(self, payload: dict) -> Iterator[dict]: ...      # webhooks (slice 4)


class GitHubConnector:
    kind = "github"

    def __init__(self, app: GitHubApp, installation_id: int):
        self.app, self.installation_id = app, installation_id

    def test(self) -> Health:
        try:
            self.app.installation_token(self.installation_id)
            return Health(True, "installation token issued")
        except httpx.HTTPStatusError as e:
            return Health(False, f"GitHub refused an installation token ({e.response.status_code})")

    def discover(self) -> list[Container]:
        return [
            Container(external_id=r["full_name"], kind="repo",
                      visibility=r.get("visibility") or ("private" if r.get("private") else "public"),
                      default_ref=r.get("default_branch"), name=r["full_name"])
            for r in self.app.repositories(self.installation_id)
        ]

    # ---- reads used by content sync (values from GitHub are validated before they reach a URL path)
    @staticmethod
    def _repo(full_name: str) -> str:
        if not _REPO.fullmatch(full_name):
            raise ValueError(f"unexpected repository name {full_name!r}")
        return full_name

    @staticmethod
    def _sha(sha: str) -> str:
        if not _SHA.fullmatch(sha):
            raise ValueError(f"unexpected git object id {sha!r}")
        return sha

    def head_commit(self, full_name: str, branch: str) -> str:
        path = f"/repos/{self._repo(full_name)}/commits/{quote(branch, safe='')}"
        return self._sha(self.app.get(self.installation_id, path).json()["sha"])

    def tree(self, full_name: str, commit: str) -> list[dict]:
        body = self.app.get(self.installation_id,
                            f"/repos/{self._repo(full_name)}/git/trees/{self._sha(commit)}", recursive=1).json()
        if body.get("truncated"):
            raise RuntimeError(f"{full_name}: git tree too large to list in one call")
        return body.get("tree", [])

    def blob_bytes(self, full_name: str, sha: str) -> bytes:
        body = self.app.get(self.installation_id,
                            f"/repos/{self._repo(full_name)}/git/blobs/{self._sha(sha)}").json()
        return base64.b64decode(body["content"]) if body.get("encoding") == "base64" else body["content"].encode()

    def blob_text(self, full_name: str, sha: str) -> str:
        return self.blob_bytes(full_name, sha).decode("utf-8", errors="replace")

    def collaborators(self, full_name: str) -> list[str]:
        """Everyone GitHub lets read the repo: owners, team members, direct and outside collaborators."""
        logins, page = set(), 1
        while True:
            batch = self.app.get(self.installation_id, f"/repos/{self._repo(full_name)}/collaborators",
                                 affiliation="all", per_page=100, page=page).json()
            logins |= {c["login"].lower() for c in batch if (c.get("permissions") or {}).get("pull", True)}
            if len(batch) < 100:
                return sorted(logins)
            page += 1

    def sync(self, cursor):
        raise NotImplementedError("GitHub sync runs per repo in kl.sync_github")

    def fetch(self, ref):
        raise NotImplementedError("use blob_text()")

    def acl(self, ref):
        raise NotImplementedError("use collaborators()")

    def on_event(self, payload):
        raise NotImplementedError("webhooks arrive in M1 slice 4")
