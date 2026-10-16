"""Directories (ADR 0011): who is at the company, by email, and whether they still are. A directory indexes no content.
It matters for two things. Identity matching refuses an email a directory says has left. And a leaver's matched
identities are revoked as soon as a directory says so, grants and all: someone who has left never signs in again, so
matching at sign-in would never get the chance to drop them.

Okta, through a read-only API token; Google Workspace (S2), through a service account acting as an admin by domain-wide
delegation, which also lists groups and their members, so a Drive file shared with a group reaches them. An email
listed more than once counts as active if any listing is, and someone a directory listed before and lists no more has
left (a listing cut short fails the sync instead, so it's never read as everyone leaving)."""
import random
import re
import time
from urllib.parse import quote

import httpx

from . import config
from .authz import fga
from .connectors import Container, Health
from .gdrive import GOOGLE_TOKEN, service_account_token
from .principals import is_valid
from .sync_items import record_identities
from .vault import vault

OKTA_SITE = re.compile(r"^https://[a-z0-9][a-z0-9-]{0,62}\.(okta|oktapreview|okta-emea|okta-gov)\.com$")
ACTIVE = {"ACTIVE", "PASSWORD_EXPIRED", "LOCKED_OUT", "RECOVERY"}  # can still sign in; suspended and deprovisioned can't
EMAIL = re.compile(r"^[a-z0-9._%+-]{1,64}@[a-z0-9-]{1,63}(\.[a-z0-9-]{1,63})*\.[a-z]{2,24}$")
MAX_PAGES = 500
GOOGLE_ADMIN = "https://admin.googleapis.com"  # the Directory API's own host, not Drive's
GOOGLE_SCOPES = ("https://www.googleapis.com/auth/admin.directory.user.readonly",
                 "https://www.googleapis.com/auth/admin.directory.group.readonly",
                 "https://www.googleapis.com/auth/admin.directory.group.member.readonly")
MAX_GROUP_MEMBERS = 2000  # a bigger group grants nothing (fail closed): each member becomes a reader of each file
RETRIES = 5


class ListingCutShort(RuntimeError):
    """A directory's listing stopped before its end: the sync fails rather than reading the missing as leavers."""


def allowed_okta(api_base: str) -> bool:
    base = api_base.rstrip("/")
    return bool(OKTA_SITE.fullmatch(base)) or base in config.OKTA_API_BASES


def allowed_google(api_base: str) -> bool:
    return api_base.rstrip("/") in config.GOOGLE_ADMIN_API_BASES


def rate_limited(r: httpx.Response) -> bool:
    """Google's rate limits: 429, or a 403 whose reason is a rate or quota (any other 403 is a refusal, not a wait)."""
    if r.status_code == 429:
        return True
    if r.status_code != 403 or not r.headers.get("content-type", "").startswith("application/json"):
        return False
    errors = ((r.json() or {}).get("error") or {}).get("errors") or []
    return any(e.get("reason") in ("userRateLimitExceeded", "quotaExceeded", "rateLimitExceeded") for e in errors)


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
        raise ListingCutShort(f"Okta's listing ran past {MAX_PAGES} pages")

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


class GoogleDirectoryClient:
    """Google Workspace's Directory API (Admin SDK), read only: a service account acting as an admin through domain-wide
    delegation, with exactly three read-only scopes. Pages follow Google's pageToken, never a URL a response names."""

    def __init__(self, api_base: str, service_account: dict, admin_email: str):
        if not allowed_google(api_base):
            raise ValueError("that Google Admin API address isn't allowed here")
        self.base, self._sa, self.admin = api_base.rstrip("/"), service_account, admin_email
        self._token: str | None = None

    def _token_url(self) -> str:
        return GOOGLE_TOKEN if self.base == GOOGLE_ADMIN else f"{self.base}/token"  # Google's, or the dev fake's

    def _get(self, path: str, **params) -> dict:
        if not self._token:
            self._token = service_account_token(self._sa, GOOGLE_SCOPES, self._token_url(), subject=self.admin)
        attempt = 0
        while True:
            r = httpx.get(f"{self.base}/admin/directory/v1/{path}", params=params, timeout=30,
                          headers={"Authorization": f"Bearer {self._token}"})
            if attempt == RETRIES or not rate_limited(r):
                r.raise_for_status()
                return r.json()
            wait = r.headers.get("Retry-After")  # Google's limits page: back off exponentially, with jitter
            time.sleep(float(wait) if wait and wait.isdigit() else 2 ** attempt + random.random())
            attempt += 1

    def _pages(self, path: str, key: str, **params) -> list[dict]:
        out, token = [], None
        for _ in range(MAX_PAGES):
            page = self._get(path, **params, **({"pageToken": token} if token else {}))
            out += page.get(key) or []
            token = page.get("nextPageToken")
            if not token:
                return out
        raise ListingCutShort(f"Google's {key} listing ran past {MAX_PAGES} pages")

    def test(self) -> Health:
        try:
            self._get("users", customer="my_customer", maxResults=1)
            return Health(True, "connected")
        except httpx.HTTPStatusError as e:
            return Health(False, f"Google returned {e.response.status_code}: check the key, its delegation, and the admin")
        except httpx.HTTPError as e:
            return Health(False, f"couldn't reach Google: {type(e).__name__}")

    def discover(self) -> list[Container]:
        return [Container(external_id="people", kind="directory", visibility=None, default_ref=None, name="People")]

    def people(self) -> list[dict]:
        """Every user, then the recently deleted ones (Google lists those only when asked, on their own). Suspended or
        archived people can't sign in: they've left, as far as access goes."""
        found: dict[str, dict] = {}
        for deleted in (False, True):
            for u in self._pages("users", "users", customer="my_customer", maxResults=500,
                                 **({"showDeleted": "true"} if deleted else {})):
                email = str(u.get("primaryEmail") or "").strip().lower()
                if not EMAIL.fullmatch(email):
                    continue
                active = (not deleted and not u.get("suspended") and not u.get("archived")) or found.get(email, {}).get("active", False)
                found[email] = {"email": email, "name": str((u.get("name") or {}).get("fullName") or "")[:200], "active": active}
        return list(found.values())

    def groups(self, active: set[str]) -> tuple[dict[str, list[str]], list[str]]:
        """Each group's people, nested groups included (Google's derived membership), only active Workspace users of
        this directory, deduplicated; and the groups too big to expand, which grant nothing. A whole-domain member
        (CUSTOMER) or an outside one (EXTERNAL) reaches no one through a group."""
        out, too_big = {}, []
        for g in self._pages("groups", "groups", customer="my_customer", maxResults=200):
            email = str(g.get("email") or "").strip().lower()
            if not EMAIL.fullmatch(email):
                continue
            members = {str(m.get("email") or "").strip().lower()
                       for m in self._pages(f"groups/{quote(email, safe='')}/members", "members",
                                            includeDerivedMembership="true", maxResults=200)
                       if m.get("type") == "USER" and m.get("status", "ACTIVE") == "ACTIVE"}
            members = sorted(m for m in members if m in active)
            if len(members) > MAX_GROUP_MEMBERS:
                too_big.append(email)
            elif members:
                out[email] = members
        return out, too_big


def client_for(connection: dict):
    secret = vault.get(connection["credential_ref"])
    cfg = connection["config"] or {}
    if cfg.get("provider") == "okta":
        return OktaClient(cfg["api_base"], secret["api_token"])
    if cfg.get("provider") == "google":
        return GoogleDirectoryClient(cfg["api_base"], secret["service_account"], cfg["admin_email"])
    raise RuntimeError(f"no directory client for {cfg.get('provider')!r}")


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


REQUEUE_DRIVE_SQL = """
INSERT INTO jobs (org_id, kind, payload)
SELECT c.org_id, 'connection.sync', jsonb_build_object('connection_id', c.id::text) FROM connections c
WHERE c.kind = 'google' AND c.status <> 'disabled'
  AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.kind = 'connection.sync' AND j.status = 'queued'
                  AND j.payload->>'connection_id' = c.id::text)"""


def sync_directory(conn, connection: dict, client) -> dict:
    """Who's here and who has left, and (for Google) each group's members. Everything is listed before anything is
    written: a listing cut short raises, so the sync fails with nothing changed rather than revoking everyone."""
    cid, org_id = connection["id"], connection["org_id"]
    people = client.people()
    groups, too_big = client.groups({p["email"] for p in people if p["active"]}) \
        if isinstance(client, GoogleDirectoryClient) else ({}, [])
    listed = {p["email"] for p in people}
    with conn.transaction():
        held = {r["email"]: r["display_name"] for r in conn.execute(
            "SELECT email, display_name FROM directory_people WHERE connection_id = %s", (cid,))}
        gone = [{"email": e, "name": n, "active": False} for e, n in sorted(held.items()) if e not in listed]  # listed before, not now: left
        conn.execute("DELETE FROM directory_people WHERE connection_id = %s", (cid,))
        conn.cursor().executemany(
            "INSERT INTO directory_people (org_id, connection_id, email, display_name, active) VALUES (%s, %s, %s, %s, %s)",
            [(org_id, cid, p["email"], p["name"], p["active"]) for p in people + gone])
        if isinstance(client, GoogleDirectoryClient):
            rows = {(g, m) for g, members in groups.items() for m in members}
            before = {(r["group_email"], r["member_email"]) for r in conn.execute(
                "SELECT group_email, member_email FROM directory_groups WHERE connection_id = %s", (cid,))}
            conn.execute("DELETE FROM directory_groups WHERE connection_id = %s", (cid,))
            conn.cursor().executemany(
                "INSERT INTO directory_groups (org_id, connection_id, group_email, member_email) VALUES (%s, %s, %s, %s)",
                [(org_id, cid, g, m) for g, m in sorted(rows)])
            if rows != before:  # someone joined or left a group: Drive's readers are re-read now, not at its next turn
                conn.execute(REQUEUE_DRIVE_SQL)
        conn.execute("UPDATE connection_containers SET cursor = now()::text WHERE connection_id = %s", (cid,))
    if isinstance(client, GoogleDirectoryClient):  # Workspace users, for identity matching to link at sign-in
        record_identities(conn, org_id, "google", {p["email"]: {"email": p["email"], "name": p["name"]}
                                                   for p in people if p["active"] and is_valid(f"ext:google.{p['email']}")})
    left = revoke_leavers(conn)
    return {"container": "people", "files": 0, "people": len(people), "inactive": sum(not p["active"] for p in people),
            "left": len(gone), "groups": len(groups), "too_big": too_big[:20], "revoked": left}
