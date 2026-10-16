"""Google Drive connector (slice F2, ADR 0008): shared drives, their files, and who may read each one.

A connection holds an OAuth client and a refresh token (in OpenBao) for a Google account that can read the drives to
index. Each sync lists every file in each selected shared drive and indexes what the extractor reads: Google Docs,
Sheets, and Slides are exported as Word, Excel, and PowerPoint, and other files are downloaded as they are. Each file's
own permissions decide its readers, and fail closed:
- a person it's shared with is `ext:google.<email>`, which grants nothing until they link their Google identity, as a
  GitHub login does;
- the org's own Google domain (the connection's `domain`) is the whole org;
- a group reaches its members, as a Google Workspace directory lists them (S2: `directory_groups`, nested groups
  flattened), each as `ext:google.<email>`; with no such directory, or a group too big to expand, it grants nothing;
- "anyone with the link" grants nothing: a link isn't a grant, and indexing such a file org-wide would show it to
  people never sent the link.
A file left with no reader isn't indexed, and a file gone from the drive is removed with everything derived from it.
The connector talks only to the Google API addresses on KL_GOOGLE_API_BASES.
"""
import re
import time

import httpx
import jwt

from . import config
from .authz import fga
from .connectors import Container, Health
from .extract import ExtractionError, extract, supported
from .principals import is_valid
from .sync_github import leave_former_teams, team_for
from .sync_items import record_identities
from .vault import vault

GOOGLE_API = "https://www.googleapis.com"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
EXPORTS = {  # Google's own formats, exported as files the extractor reads
    "application/vnd.google-apps.document": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "application/vnd.google-apps.spreadsheet": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "application/vnd.google-apps.presentation": ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
}
CLASSIFICATION = "internal"  # every Drive file is indexed as internal; a media model must allow it (Q8)
READ_ROLES = {"reader", "commenter", "writer", "fileOrganizer", "organizer", "owner"}
FILE_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
FOLDER = "application/vnd.google-apps.folder"
FOLDER_PREFIX = "folder-"  # a container that's a folder shared with the service account, not a shared drive
SA_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
JWT_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
MAX_FOLDER_FILES = 5000
MAX_BYTES = 20 * 1024 * 1024
FIELDS = "nextPageToken,files(id,name,mimeType,modifiedTime,version,md5Checksum,size,webViewLink,permissions(type,role,emailAddress,domain))"


def service_account_token(account: dict, scopes: tuple[str, ...], token_url: str, subject: str | None = None) -> str:
    """A service account's access token (ADR 0011): a JWT it signs with its own key, for exactly these scopes, sent to
    token_url, which the caller fixes to Google's (or the allowed dev fake), never a key file's own token_uri. With a
    subject, it acts as that person through domain-wide delegation (S2: the Directory API's admin)."""
    now = int(time.time())
    claims = {"iss": account["client_email"], "scope": " ".join(scopes), "aud": token_url, "iat": now, "exp": now + 3600}
    if subject:
        claims["sub"] = subject
    r = httpx.post(token_url, data={"grant_type": JWT_GRANT,
                                    "assertion": jwt.encode(claims, account["private_key"], algorithm="RS256")}, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


class DriveClient:
    """One connection's view of Drive, to the connector contract (test, discover) plus what sync needs."""

    def __init__(self, api_base: str, client_id: str | None = None, client_secret: str | None = None,
                 refresh_token: str | None = None, service_account: dict | None = None):
        if api_base not in config.GOOGLE_API_BASES:
            raise ValueError("that Google API address isn't allowed here")
        # A service account (ADR 0011) signs its own token request with its key. A refresh token (ADR 0008) is a
        # person's, kept only so connections made before ADR 0011 keep working.
        self.base, self._sa = api_base, service_account
        self._grant = None if service_account else {"client_id": client_id, "client_secret": client_secret,
                                                    "refresh_token": refresh_token, "grant_type": "refresh_token"}
        self._token: str | None = None

    def _token_url(self) -> str:
        """Google's token endpoint for this API address: never the token_uri a key file names, so a crafted key can't
        make the worker post anywhere else."""
        return GOOGLE_TOKEN if self.base == GOOGLE_API else f"{self.base}/token"

    def _access_token(self) -> str:
        if not self._token:
            if self._sa:
                self._token = service_account_token(self._sa, (SA_SCOPE,), self._token_url())
            else:
                r = httpx.post(self._token_url(), data=self._grant, timeout=15)
                r.raise_for_status()
                self._token = r.json()["access_token"]
        return self._token

    @property
    def service_account(self) -> bool:
        return bool(self._sa)

    def _get(self, path: str, **params) -> httpx.Response:
        r = httpx.get(f"{self.base}/drive/v3/{path}", params=params, timeout=60,
                      headers={"Authorization": f"Bearer {self._access_token()}"})
        r.raise_for_status()
        return r

    def _pages(self, path: str, key: str, **params) -> list[dict]:
        items, token = [], None
        while True:
            page = self._get(path, **params, **({"pageToken": token} if token else {})).json()
            items += page.get(key, [])
            token = page.get("nextPageToken")
            if not token:
                return items

    def test(self) -> Health:
        try:
            self._access_token()
            return Health(True, "connected")
        except (httpx.HTTPError, KeyError, ValueError) as e:
            return Health(False, f"Google refused the connection's credentials ({type(e).__name__})")

    def discover(self) -> list[Container]:
        drives = self._pages("drives", "drives", pageSize=100, fields="nextPageToken,drives(id,name)")
        found = [Container(external_id=d["id"], kind="drive", visibility=None, default_ref=None, name=d.get("name"))
                 for d in drives if FILE_ID.fullmatch(d.get("id", ""))]
        if self._sa:  # and folders shared with the service account itself, which works for any Google account
            shared = self._pages("files", "files", q=f"sharedWithMe = true and mimeType = '{FOLDER}' and trashed = false",
                                 includeItemsFromAllDrives="true", supportsAllDrives="true", pageSize=100,
                                 fields="nextPageToken,files(id,name)")
            found += [Container(external_id=f"{FOLDER_PREFIX}{f['id']}", kind="folder", visibility=None, default_ref=None,
                                name=f.get("name"))
                      for f in shared if FILE_ID.fullmatch(f.get("id", ""))]
        return found

    def files(self, container: str) -> list[dict]:
        if container.startswith(FOLDER_PREFIX):
            return self.folder_files(container[len(FOLDER_PREFIX):])
        return self._pages("files", "files", corpora="drive", driveId=container, includeItemsFromAllDrives="true",
                           supportsAllDrives="true", q="trashed = false", pageSize=200, fields=FIELDS)

    def folder_files(self, folder_id: str) -> list[dict]:
        """A shared folder's files, and its subfolders' too, breadth first, up to MAX_FOLDER_FILES. Only ids Drive
        itself gave, checked against FILE_ID, ever go into a query."""
        out, queue, seen = [], [folder_id], set()
        while queue and len(out) < MAX_FOLDER_FILES:
            fid = queue.pop(0)
            if fid in seen or not FILE_ID.fullmatch(fid):
                continue
            seen.add(fid)
            for f in self._pages("files", "files", q=f"'{fid}' in parents and trashed = false", includeItemsFromAllDrives="true",
                                 supportsAllDrives="true", pageSize=200, fields=FIELDS):
                if f.get("mimeType") == FOLDER:
                    queue.append(f.get("id", ""))
                else:
                    out.append(f)
        return out[:MAX_FOLDER_FILES]

    def content(self, f: dict) -> bytes:
        if f["mimeType"] in EXPORTS:
            return self._get(f"files/{f['id']}/export", mimeType=EXPORTS[f["mimeType"]][1]).content
        return self._get(f"files/{f['id']}", alt="media", supportsAllDrives="true").content


def client_for(connection: dict) -> DriveClient:
    secret = vault.get(connection["credential_ref"])
    if secret.get("service_account"):  # ADR 0011
        return DriveClient(connection["config"]["api_base"], service_account=secret["service_account"])
    return DriveClient(connection["config"]["api_base"], connection["config"]["client_id"],
                       secret["client_secret"], secret["refresh_token"])


def service_account_key(raw: str) -> dict:
    """A service account's JSON key, reduced to what signing needs: its email and private key. Anything else in it,
    its token_uri included, is dropped. Raises ValueError for anything that isn't one."""
    import json
    try:
        key = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError("that isn't a service account's JSON key")
    if not isinstance(key, dict):
        raise ValueError("that isn't a service account's JSON key")
    email, private = key.get("client_email"), key.get("private_key")
    if key.get("type") != "service_account" or not isinstance(email, str) or "@" not in email or len(email) > 254 \
            or not isinstance(private, str) or "PRIVATE KEY-----" not in private:
        raise ValueError("that isn't a service account's JSON key")
    return {"client_email": email.strip().lower(), "private_key": private}


def file_name(f: dict) -> str | None:
    """The name the extractor reads a file by: an exported Google file gets its export's extension."""
    if f.get("mimeType") in EXPORTS:
        return f"{f['name']}.{EXPORTS[f['mimeType']][0]}"
    return f.get("name")


def readers(permissions: list | None, org_slug: str, domain: str | None, groups: dict[str, list[str]] | None = None) -> list[str]:
    """A file's readers as principals, failing closed: people (until they link), a group's members as a Google
    directory lists them, the org's own domain, nothing else."""
    out = set()
    for p in permissions or []:
        if p.get("role") not in READ_ROLES:
            continue
        if p.get("type") == "user" and p.get("emailAddress"):
            emails = [p["emailAddress"]]
        elif p.get("type") == "group" and p.get("emailAddress"):
            emails = (groups or {}).get(p["emailAddress"].lower(), [])  # an unknown group grants nothing
        elif p.get("type") == "domain" and domain and (p.get("domain") or "").lower() == domain.lower():
            out.add(f"org:{org_slug}")
            continue
        else:
            continue
        out |= {principal for e in emails if is_valid(principal := f"ext:google.{e.lower()}")}
    return sorted(out)


GROUPS_SQL = """SELECT g.group_email, array_agg(DISTINCT g.member_email ORDER BY g.member_email) AS members
FROM directory_groups g JOIN connections c ON c.id = g.connection_id
WHERE c.kind = 'directory' AND c.config->>'provider' = 'google' AND c.status <> 'disabled'
GROUP BY g.group_email"""


def directory_groups(conn) -> dict[str, list[str]]:
    """Each Google group's members, from the org's Google Workspace directories (S2): only Google's, since Drive's
    group shares name Google groups. A paused directory's groups count for nothing."""
    return {r["group_email"]: r["members"] for r in conn.execute(GROUPS_SQL).fetchall()}


def viewer_tuples(key: str, acl: list[str]) -> set:
    return {(f"identity:{p[4:]}#owner" if p.startswith("ext:") else f"{p}#member", "viewer", f"document:{key}") for p in acl}


def sync_drive(conn, connection: dict, client: DriveClient, drive_id: str) -> dict:
    """Index one shared drive: new and changed files, everyone's current access, and nothing left of removed files. With
    `readers_only` on the connection (S8), only access: as `sync_items.sync_container` does."""
    org_id, team = connection["org_id"], team_for(connection, drive_id)
    readers_only = bool(connection.get("readers_only"))
    domain = (connection["config"] or {}).get("domain")
    groups = directory_groups(conn)  # read every sync: a member taken out of a group loses the file on the next one
    files = client.files(drive_id)
    existing = {r["external_key"]: r for r in conn.execute(
        "SELECT id, external_key, content_hash FROM documents WHERE source_kind = 'gdrive' AND container_ref = %s", (drive_id,))}
    keep, changed, unreadable, writes, deletes = set(), 0, [], set(), set()
    seen: dict[str, dict] = {}  # the accounts Drive names on files, for identity matching (ADR 0011)
    for f in files:
        name, acl = file_name(f), readers(f.get("permissions"), config.ORG_SLUG, domain, groups)
        for p in f.get("permissions") or []:
            if p.get("type") == "user" and p.get("role") in READ_ROLES and p.get("emailAddress"):
                seen[p["emailAddress"].lower()] = {"email": p["emailAddress"], "name": p.get("displayName")}
        if not acl or not name or not supported(name) or not FILE_ID.fullmatch(f.get("id", "")):
            continue
        if f["mimeType"] not in EXPORTS and int(f.get("size") or 0) > MAX_BYTES:
            continue
        key, version = f"gd-{f['id']}", f.get("md5Checksum") or f"{f.get('version')}:{f.get('modifiedTime')}"
        prev = existing.get(key)
        if readers_only and not prev:  # S8: a readers refresh reads no body; a new file waits for the content sync
            continue
        with conn.transaction():
            if not readers_only and (not prev or prev["content_hash"] != version):
                try:
                    media = connection["media"](CLASSIFICATION) if connection.get("media") else None  # Q8, egress-checked
                    title, sections = extract(name, client.content(f), f["name"], media)
                except ExtractionError as e:
                    unreadable.append({"file": f["name"][:200], "reason": str(e)})
                    continue
                doc_id = conn.execute(
                    """INSERT INTO documents (org_id, source_kind, source_uri, source_version, external_key, title,
                                              content_hash, classification, acl_principals, container_ref, source_path)
                       VALUES (%s, 'gdrive', %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (org_id, external_key) DO UPDATE SET
                         source_uri = EXCLUDED.source_uri, source_version = EXCLUDED.source_version, title = EXCLUDED.title,
                         content_hash = EXCLUDED.content_hash, acl_principals = EXCLUDED.acl_principals, acl_synced_at = now()
                       WHERE documents.container_ref = EXCLUDED.container_ref
                       RETURNING id""",
                    (org_id, f.get("webViewLink") or f"https://drive.google.com/file/d/{f['id']}", version, key,
                     (title or f["name"])[:300], version, CLASSIFICATION, acl, drive_id, f["name"][:500])).fetchone()
                if not doc_id:
                    continue
                doc_id = doc_id["id"]
                conn.execute("DELETE FROM chunks WHERE document_id = %s", (doc_id,))
                for ordinal, (heading, body) in enumerate(sections):
                    conn.execute("INSERT INTO chunks (org_id, document_id, ordinal, heading_path, body, acl_principals) "
                                 "VALUES (%s, %s, %s, %s, %s, %s)", (org_id, doc_id, ordinal, heading, body, acl))
                changed += 1
            else:  # same content; its readers may have changed
                doc_id = prev["id"]
                conn.execute("UPDATE documents SET acl_principals = %s, acl_synced_at = now() WHERE id = %s", (acl, doc_id))
                conn.execute("UPDATE chunks SET acl_principals = %s WHERE document_id = %s", (acl, doc_id))
                conn.execute("UPDATE chunk_embeddings SET acl_principals = %s "
                             "WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = %s)", (acl, doc_id))
            conn.execute("UPDATE concepts SET acl_principals = %s WHERE document_id = %s", (acl, doc_id))
            conn.execute("INSERT INTO workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, %s) "
                         "ON CONFLICT DO NOTHING", (team, doc_id, org_id))
        keep.add(key)
        want = viewer_tuples(key, acl)
        have = {(t["user"], t["relation"], t["object"]) for t in fga.read({"object": f"document:{key}"})}
        writes |= want - have
        deletes |= have - want
    if seen and client.service_account:  # recorded only for service accounts; a person's token predates matching
        record_identities(conn, org_id, "google", {e: v for e, v in seen.items() if is_valid(f"ext:google.{e}")})
    gone = [(k, r["id"]) for k, r in existing.items() if k not in keep]  # deleted, unshared, or no longer readable
    with conn.transaction():
        if gone:
            conn.execute("DELETE FROM documents WHERE id = ANY(%s)", ([doc for _, doc in gone],))
        leave_former_teams(conn, connection, drive_id, team)  # a drive routed to another team moves there (slice F1)
        conn.execute("UPDATE connection_containers SET cursor = now()::text WHERE connection_id = %s AND external_id = %s",
                     (connection["id"], drive_id))
    for key, _ in gone:
        deletes |= {(t["user"], t["relation"], t["object"]) for t in fga.read({"object": f"document:{key}"})}
    if writes or deletes:
        fga.write(writes=writes, deletes=deletes)
    return {"drive": drive_id, "files": len(files), "indexed": len(keep), "changed": changed, "removed": len(gone),
            "unreadable": unreadable[:20]}
