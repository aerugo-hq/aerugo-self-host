"""Microsoft 365 (S5, ADR 0011): SharePoint sites an admin grants the app one by one (`Sites.Selected`, never
tenant-wide), each site's document libraries as containers, and their files through the extractors. The app (an Entra
app registration signing in with a certificate, as the directory's does) only sets what's indexed; each file's own
sharing decides its readers, failing closed:

  - a person a file is shared with is `ext:microsoft.<email>`, reaching someone only once identity matching or the
    person links it;
  - an Entra group it's shared with reaches the group's members, as an Entra directory lists them (S3:
    `directory_groups`, by the group's id); with no such directory, a group grants nothing;
  - a SharePoint site group (a site's Members, Owners, Visitors) grants nothing: Graph doesn't list its members to an
    app with this little access, so its files reach only the people and groups shared with directly;
  - a sharing link grants nothing, whatever its scope, as Drive's don't: a link isn't a grant, and indexing a file
    org-wide would show it to people never sent the link.

A file nobody may read isn't indexed; one larger than MAX_BYTES is indexed by its name only. Content is downloaded
from the address Graph redirects to only when that's the tenant's own SharePoint host (or the dev fake)."""
import hashlib
import re

import httpx

from .connectors import Container, Health
from .directory import EMAIL, GUID, GraphClient
from .extract import extract, supported
from .principals import is_valid
from .sync_items import Item, record_identities, sync_container
from .vault import vault
from . import source_http

SITE = re.compile(r"^[a-z0-9-]+\.sharepoint\.com,[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12},"
                  r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")  # hostname,site collection id,web id
DRIVE = re.compile(r"^b![A-Za-z0-9_-]{10,200}$")
ITEM = re.compile(r"^[A-Za-z0-9!_-]{1,200}$")
SHAREPOINT = re.compile(r"^https://[a-z0-9-]+\.sharepoint\.com/")
READ_ROLES = {"read", "write", "owner"}
MAX_BYTES = 20 * 1024 * 1024
FIELDS = "id,name,file,folder,root,size,cTag,eTag,lastModifiedDateTime,webUrl,parentReference,deleted"


class M365Client(GraphClient):
    """The sites an admin granted this app, their libraries, files, and each file's sharing."""

    def __init__(self, api_base: str, tenant_id: str, client_id: str, certificate: dict, sites: list[str]):
        super().__init__(api_base, tenant_id, client_id, certificate)
        self.sites = [s for s in sites if SITE.fullmatch(s)]

    def test(self) -> Health:
        try:
            self._access_token()
            return Health(True, "connected")
        except httpx.HTTPStatusError as e:
            return Health(False, f"Microsoft returned {e.response.status_code}: check the tenant, the app, and its certificate")
        except httpx.HTTPError as e:
            return Health(False, f"couldn't reach Microsoft: {type(e).__name__}")

    def discover(self) -> list[Container]:
        """Each granted site's document libraries. A site the app wasn't granted answers 403 and is skipped: there's
        nothing of it the app may read."""
        found = []
        for site in self.sites:
            try:
                meta = self._get(f"{self.base}/v1.0/sites/{site}", {"$select": "id,displayName"})
                drives = self._pages(f"sites/{site}/drives", {"$select": "id,name,driveType"})
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (403, 404):
                    continue
                raise
            found += [Container(external_id=d["id"], kind="library", visibility=None, default_ref=None,
                                name=f"{meta.get('displayName') or site} / {d.get('name') or 'Documents'}")
                      for d in drives if DRIVE.fullmatch(d.get("id") or "") and d.get("driveType", "documentLibrary") == "documentLibrary"]
        return found

    def files(self, drive: str) -> list[dict]:
        """Every file in a library now (a delta listing from the start: the only listing Graph guarantees complete),
        each with the path of folders it sits in, built from their ids (delta gives no paths)."""
        # a facet's presence is what it says, even empty: Graph sends "deleted": {} for a deleted item
        items = [i for i in self._pages(f"drives/{drive}/root/delta", {"$select": FIELDS}) if "deleted" not in i]
        folders = {i["id"]: i for i in items if "folder" in i or "root" in i}

        def path(item: dict) -> list[str]:
            names, parent, seen = [], (item.get("parentReference") or {}).get("id"), set()
            while parent in folders and parent not in seen and "root" not in folders[parent]:
                seen.add(parent)
                names.insert(0, folders[parent].get("name") or "")
                parent = (folders[parent].get("parentReference") or {}).get("id")
            return names
        return [{**i, "_path": path(i)} for i in items if "file" in i and ITEM.fullmatch(i.get("id") or "")]

    def permissions(self, drive: str, item: str) -> list[dict]:
        return self._pages(f"drives/{drive}/items/{item}/permissions", {})

    def content(self, drive: str, item: str) -> bytes:
        """A file's bytes: Graph answers with a redirect to a short-lived download address, followed only on the
        tenant's own SharePoint host (or the dev fake), without the Graph token."""
        r = source_http.get(f"{self.base}/v1.0/drives/{drive}/items/{item}/content", timeout=60, follow_redirects=False,
                      headers={"Authorization": f"Bearer {self._access_token()}"})
        if r.status_code == 200:
            return r.content
        where = r.headers.get("location") or ""
        if r.status_code not in (301, 302, 303, 307) or not (SHAREPOINT.match(where) or where.startswith(f"{self.base}/")):
            raise ValueError("Graph's download address isn't SharePoint's")
        got = source_http.get(where, timeout=120, follow_redirects=False)
        got.raise_for_status()
        return got.content


def client_for(connection: dict) -> M365Client:
    cfg = connection["config"] or {}
    return M365Client(cfg["api_base"], cfg["tenant_id"], cfg["client_id"], vault.get(connection["credential_ref"])["certificate"],
                      cfg.get("sites") or [])


GROUPS_SQL = """SELECT g.group_id, array_agg(DISTINCT g.member_email ORDER BY g.member_email) AS members
FROM directory_groups g JOIN connections c ON c.id = g.connection_id
WHERE c.kind = 'directory' AND c.config->>'provider' = 'entra' AND c.status <> 'disabled' AND g.group_id IS NOT NULL
GROUP BY g.group_id"""


def entra_groups(conn) -> dict[str, list[str]]:
    """Each Entra group's members, by the group's id, from the org's Entra directories (S3); a paused one counts for nothing."""
    return {r["group_id"]: r["members"] for r in conn.execute(GROUPS_SQL).fetchall()}


def email_of(identity: dict) -> str | None:
    """A person's email in a sharing identity: the user's, the SharePoint site user's, or the one in its login name."""
    user, site_user = identity.get("user") or {}, identity.get("siteUser") or {}
    login = str(site_user.get("loginName") or "")
    email = str(user.get("email") or site_user.get("email") or (login.rsplit("|", 1)[-1] if "|membership|" in login else "")).strip().lower()
    return email if EMAIL.fullmatch(email) else None


def readers(permissions: list[dict], groups: dict[str, list[str]], seen: dict[str, dict]) -> list[str]:
    """A file's readers as principals, failing closed: the people and Entra groups it's shared with. A site group, a
    sharing link, and an identity without an email grant nothing (a link's named people do)."""
    out = set()
    for p in permissions:
        if not READ_ROLES & set(p.get("roles") or []):
            continue
        identities = (p.get("grantedToIdentitiesV2") or []) if p.get("link") else [p.get("grantedToV2") or {}]
        for identity in identities:
            email = email_of(identity)
            if email and is_valid(principal := f"ext:microsoft.{email}"):
                out.add(principal)
                seen[email] = {"email": email, "name": ((identity.get("user") or {}).get("displayName") or "")[:200]}
                continue
            group = str((identity.get("group") or {}).get("id") or "").lower()
            if GUID.fullmatch(group):
                out |= {pr for m in groups.get(group, []) if is_valid(pr := f"ext:microsoft.{m}")}
    return sorted(out)


def sync_library(conn, connection: dict, client: M365Client, drive: str) -> dict:
    groups, seen, items = entra_groups(conn), {}, []
    for f in client.files(drive):
        if not supported(f.get("name") or ""):
            continue
        acl = readers(client.permissions(drive, f["id"]), groups, seen)
        if not acl:
            continue
        size, title = int(f.get("size") or 0), f["name"]
        uri = f.get("webUrl") if str(f.get("webUrl") or "").startswith("https://") else ""

        def sections(f=f, size=size, title=title):
            if size > MAX_BYTES:  # listed, not read: its name is all that's indexed
                return title, [(None, f"{title} ({size // (1024 * 1024)} MB, too large to read)")]
            return extract(title, client.content(drive, f["id"]), title)
        items.append(Item(key=f"m365-{hashlib.sha256(drive.encode()).hexdigest()[:10]}-{f['id']}", uri=uri or f"m365:{f['id']}",
                          version=str(f.get("cTag") or f.get("eTag") or f.get("lastModifiedDateTime") or ""),
                          title=title, path=" › ".join(f["_path"]), acl=acl, sections=sections))
    if seen:
        record_identities(conn, connection["org_id"], "microsoft", seen)
    return sync_container(conn, connection, drive, "microsoft", items)
