"""Directories (ADR 0011): who is at the company, by email, and whether they still are. A directory indexes no content.
It matters for two things. Identity matching refuses an email a directory says has left. And a leaver's matched
identities are revoked as soon as a directory says so, grants and all: someone who has left never signs in again, so
matching at sign-in would never get the chance to drop them.

Okta first, through a read-only API token. An email listed more than once counts as active if any listing is."""
import re

import httpx

from . import config
from .authz import fga
from .connectors import Container, Health
from .vault import vault

OKTA_SITE = re.compile(r"^https://[a-z0-9][a-z0-9-]{0,62}\.(okta|oktapreview|okta-emea|okta-gov)\.com$")
ACTIVE = {"ACTIVE", "PASSWORD_EXPIRED", "LOCKED_OUT", "RECOVERY"}  # can still sign in; suspended and deprovisioned can't
EMAIL = re.compile(r"^[a-z0-9._%+-]{1,64}@[a-z0-9-]{1,63}(\.[a-z0-9-]{1,63})*\.[a-z]{2,24}$")
MAX_PAGES = 500


def allowed_okta(api_base: str) -> bool:
    base = api_base.rstrip("/")
    return bool(OKTA_SITE.fullmatch(base)) or base in config.OKTA_API_BASES


class OktaClient:
    """Okta's management API, read only, with an API token."""

    def __init__(self, api_base: str, api_token: str):
        self.base = api_base.rstrip("/")
        self.headers = {"Authorization": f"SSWS {api_token}", "Accept": "application/json"}

    def _pages(self, path: str, **params) -> list[dict]:
        out, url, first = [], f"{self.base}{path}", True
        for _ in range(MAX_PAGES):
            r = httpx.get(url, params=params if first else None, headers=self.headers, timeout=30)
            r.raise_for_status()
            out += r.json()
            following = (r.links.get("next") or {}).get("url")
            if not following or not following.startswith(f"{self.base}/"):  # a next page on this Okta only
                return out
            url, first = following, False
        return out

    def test(self) -> Health:
        try:
            self._pages("/api/v1/users", limit=1)
            return Health(True, "connected")
        except httpx.HTTPStatusError as e:
            return Health(False, f"Okta returned {e.response.status_code}: check the API token")
        except httpx.HTTPError as e:
            return Health(False, f"couldn't reach Okta: {type(e).__name__}")

    def discover(self) -> list[Container]:
        return [Container(external_id="people", kind="directory", visibility=None, default_ref=None, name="People")]

    def people(self) -> list[dict]:
        """Everyone, with whether they can still sign in. Okta leaves deprovisioned people out of its default list, so
        they're asked for on their own: they're exactly the ones whose access must go."""
        users = self._pages("/api/v1/users", limit=200) + \
            self._pages("/api/v1/users", search='status eq "DEPROVISIONED"', limit=200)
        found: dict[str, dict] = {}
        for u in users:
            profile = u.get("profile") or {}
            email = str(profile.get("email") or "").strip().lower()
            if not EMAIL.fullmatch(email):
                continue
            active = u.get("status") in ACTIVE or found.get(email, {}).get("active", False)
            name = f"{profile.get('firstName') or ''} {profile.get('lastName') or ''}".strip()
            found[email] = {"email": email, "name": name[:200], "active": active}
        return list(found.values())


def client_for(connection: dict):
    secret = vault.get(connection["credential_ref"])
    provider = (connection["config"] or {}).get("provider")
    if provider == "okta":
        return OktaClient(connection["config"]["api_base"], secret["api_token"])
    raise RuntimeError(f"no directory client for {provider!r}")


LEAVERS_SQL = """
SELECT p.username, a.source
FROM people p
JOIN principal_aliases a ON a.org_id = p.org_id AND a.canonical = 'user:' || p.username AND a.verified_by = 'sso-email'
WHERE p.email IS NOT NULL
  AND EXISTS (SELECT 1 FROM directory_people d WHERE d.org_id = p.org_id AND d.email = p.email AND NOT d.active)
  AND NOT EXISTS (SELECT 1 FROM directory_people d WHERE d.org_id = p.org_id AND d.email = p.email AND d.active)
"""


def revoke_leavers(conn) -> list[str]:
    """Everyone a directory says has left loses every identity matched by their email, grant and all. Links a person
    proved by signing in to a source are theirs to remove, or an org admin's, so they stay."""
    rows = conn.execute(LEAVERS_SQL).fetchall()
    if not rows:
        return []
    with conn.transaction():
        for r in rows:
            conn.execute("DELETE FROM principal_aliases WHERE canonical = %s AND source = %s AND verified_by = 'sso-email'",
                          (f"user:{r['username']}", r["source"]))
    grants = {(f"user:{r['username']}", "owner", f"identity:{r['source'][4:]}") for r in rows}
    held = {t for t in grants if fga.read({"user": t[0], "relation": t[1], "object": t[2]})}
    if held:
        fga.write(deletes=held)
    return sorted({r["username"] for r in rows})


def sync_directory(conn, connection: dict, client) -> dict:
    people = client.people()
    with conn.transaction():
        conn.execute("DELETE FROM directory_people WHERE connection_id = %s", (connection["id"],))
        for p in people:
            conn.execute("INSERT INTO directory_people (org_id, connection_id, email, display_name, active) "
                         "VALUES (%s, %s, %s, %s, %s)", (connection["org_id"], connection["id"], p["email"], p["name"], p["active"]))
        conn.execute("UPDATE connection_containers SET cursor = now()::text WHERE connection_id = %s", (connection["id"],))
    left = revoke_leavers(conn)
    return {"container": "people", "files": 0, "people": len(people), "inactive": sum(not p["active"] for p in people),
            "revoked": left}
