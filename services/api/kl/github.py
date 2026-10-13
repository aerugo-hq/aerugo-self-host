"""GitHub App client: a short-lived app JWT is exchanged for installation tokens (about an hour),
which are cached until five minutes before they expire. Credentials come from the secrets backend."""
import time
from datetime import datetime

import httpx
import jwt

from . import config
from .vault import vault

API = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
           "User-Agent": "knowledge-layer"}
APP_REF = f"openbao://secret/kl/{config.ORG_SLUG}/connectors/github-app"
# The curator App opens compiler proposals as PRs. It's the only writer, installed only on knowledge repos.
CURATOR_REF = f"openbao://secret/kl/{config.ORG_SLUG}/connectors/curator-app"


class GitHubNotConfigured(RuntimeError):
    pass


class GitHubApp:
    def __init__(self, app_id: str, private_key: str):
        self.app_id, self._key = str(app_id), private_key
        self._tokens: dict[int, tuple[str, float]] = {}

    def app_jwt(self) -> str:
        now = int(time.time())
        return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": self.app_id}, self._key, algorithm="RS256")

    def _app_headers(self) -> dict:
        return {**HEADERS, "Authorization": f"Bearer {self.app_jwt()}"}

    def installations(self) -> list[dict]:
        found, page = [], 1
        while True:
            r = httpx.get(f"{API}/app/installations", headers=self._app_headers(),
                          params={"per_page": 100, "page": page}, timeout=20)
            r.raise_for_status()
            batch = r.json()
            found += batch
            if len(batch) < 100:
                return found
            page += 1

    def installation_token(self, installation_id: int) -> str:
        cached = self._tokens.get(installation_id)
        if cached and cached[1] - time.time() > 300:
            return cached[0]
        r = httpx.post(f"{API}/app/installations/{installation_id}/access_tokens",
                       headers=self._app_headers(), timeout=20)
        r.raise_for_status()
        body = r.json()
        expires = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")).timestamp()
        self._tokens[installation_id] = (body["token"], expires)
        return body["token"]

    def get(self, installation_id: int, path: str, **params) -> httpx.Response:
        headers = {**HEADERS, "Authorization": f"Bearer {self.installation_token(installation_id)}"}
        r = httpx.get(f"{API}{path}", headers=headers, params=params, timeout=30)
        r.raise_for_status()
        return r

    def call(self, installation_id: int, method: str, path: str, allow: tuple = (), **kwargs) -> httpx.Response:
        """Any REST call as the installation. Raises on errors except the status codes in allow."""
        headers = {**HEADERS, "Authorization": f"Bearer {self.installation_token(installation_id)}"}
        r = httpx.request(method, f"{API}{path}", headers=headers, timeout=30, **kwargs)
        if r.status_code not in allow:
            r.raise_for_status()
        return r

    def repo_installation(self, full_name: str) -> int:
        """This App's installation on one repo, or GitHubNotConfigured if it isn't installed there."""
        r = httpx.get(f"{API}/repos/{full_name}/installation", headers=self._app_headers(), timeout=20)
        if r.status_code == 404:
            raise GitHubNotConfigured(f"the App isn't installed on {full_name}")
        r.raise_for_status()
        return int(r.json()["id"])

    def repositories(self, installation_id: int) -> list[dict]:
        repos, page = [], 1
        while True:
            body = self.get(installation_id, "/installation/repositories", per_page=100, page=page).json()
            batch = body.get("repositories", [])
            repos += batch
            if not batch or len(repos) >= body.get("total_count", 0):
                return repos
            page += 1


def load_app(ref: str = APP_REF) -> GitHubApp:
    try:
        data = vault.get(ref)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            setup = "make curator-app" if ref == CURATOR_REF else "make github-app"
            raise GitHubNotConfigured(f"GitHub App not configured: run `{setup} ORG=<org>`, then `make up`")
        raise
    return GitHubApp(data["app_id"], data["private_key"])
