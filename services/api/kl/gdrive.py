"""Google Drive connector (slice F2, ADR 0008): shared drives, their files, and who may read each one.

A connection holds an OAuth client and a refresh token (in OpenBao) for a Google account that can read the drives to
index. Each sync lists every file in each selected shared drive and indexes what the extractor reads: Google Docs,
Sheets, and Slides are exported as Word, Excel, and PowerPoint, and other files are downloaded as they are. Each file's
own permissions decide its readers, and fail closed:
- a person it's shared with is `ext:google.<email>`, which grants nothing until they link their Google identity, as a
  GitHub login does;
- the org's own Google domain (the connection's `domain`) is the whole org;
- a group grants nothing yet (its members need Google's Directory API), and "anyone with the link" grants nothing: a
  link isn't a grant, and indexing such a file org-wide would show it to people never sent the link.
A file left with no reader isn't indexed, and a file gone from the drive is removed with everything derived from it.
The connector talks only to the Google API addresses on KL_GOOGLE_API_BASES.
"""
import re

import httpx

from . import config
from .authz import fga
from .connectors import Container, Health
from .extract import ExtractionError, extract, supported
from .principals import is_valid
from .sync_github import leave_former_teams, team_for
from .vault import vault

GOOGLE_API = "https://www.googleapis.com"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
EXPORTS = {  # Google's own formats, exported as files the extractor reads
    "application/vnd.google-apps.document": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "application/vnd.google-apps.spreadsheet": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "application/vnd.google-apps.presentation": ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
}
READ_ROLES = {"reader", "commenter", "writer", "fileOrganizer", "organizer", "owner"}
FILE_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
MAX_BYTES = 20 * 1024 * 1024
FIELDS = "nextPageToken,files(id,name,mimeType,modifiedTime,version,md5Checksum,size,webViewLink,permissions(type,role,emailAddress,domain))"


class DriveClient:
    """One connection's view of Drive, to the connector contract (test, discover) plus what sync needs."""

    def __init__(self, api_base: str, client_id: str, client_secret: str, refresh_token: str):
        if api_base not in config.GOOGLE_API_BASES:
            raise ValueError("that Google API address isn't allowed here")
        self.base, self._grant = api_base, {"client_id": client_id, "client_secret": client_secret,
                                            "refresh_token": refresh_token, "grant_type": "refresh_token"}
        self._token: str | None = None

    def _access_token(self) -> str:
        if not self._token:
            r = httpx.post(GOOGLE_TOKEN if self.base == GOOGLE_API else f"{self.base}/token", data=self._grant, timeout=15)
            r.raise_for_status()
            self._token = r.json()["access_token"]
        return self._token

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
        return [Container(external_id=d["id"], kind="drive", visibility=None, default_ref=None)
                for d in drives if FILE_ID.fullmatch(d.get("id", ""))]

    def files(self, drive_id: str) -> list[dict]:
        return self._pages("files", "files", corpora="drive", driveId=drive_id, includeItemsFromAllDrives="true",
                           supportsAllDrives="true", q="trashed = false", pageSize=200, fields=FIELDS)

    def content(self, f: dict) -> bytes:
        if f["mimeType"] in EXPORTS:
            return self._get(f"files/{f['id']}/export", mimeType=EXPORTS[f["mimeType"]][1]).content
        return self._get(f"files/{f['id']}", alt="media", supportsAllDrives="true").content


def client_for(connection: dict) -> DriveClient:
    secret = vault.get(connection["credential_ref"])
    return DriveClient(connection["config"]["api_base"], connection["config"]["client_id"],
                       secret["client_secret"], secret["refresh_token"])


def file_name(f: dict) -> str | None:
    """The name the extractor reads a file by: an exported Google file gets its export's extension."""
    if f.get("mimeType") in EXPORTS:
        return f"{f['name']}.{EXPORTS[f['mimeType']][0]}"
    return f.get("name")


def readers(permissions: list | None, org_slug: str, domain: str | None) -> list[str]:
    """A file's readers as principals, failing closed: people (until they link), the org's own domain, nothing else."""
    out = set()
    for p in permissions or []:
        if p.get("role") not in READ_ROLES:
            continue
        if p.get("type") == "user" and p.get("emailAddress"):
            principal = f"ext:google.{p['emailAddress'].lower()}"
            if is_valid(principal):
                out.add(principal)
        elif p.get("type") == "domain" and domain and (p.get("domain") or "").lower() == domain.lower():
            out.add(f"org:{org_slug}")
    return sorted(out)


def viewer_tuples(key: str, acl: list[str]) -> set:
    return {(f"identity:{p[4:]}#owner" if p.startswith("ext:") else f"{p}#member", "viewer", f"document:{key}") for p in acl}


def sync_drive(conn, connection: dict, client: DriveClient, drive_id: str) -> dict:
    """Index one shared drive: new and changed files, everyone's current access, and nothing left of removed files."""
    org_id, team = connection["org_id"], team_for(connection, drive_id)
    domain = (connection["config"] or {}).get("domain")
    files = client.files(drive_id)
    existing = {r["external_key"]: r for r in conn.execute(
        "SELECT id, external_key, content_hash FROM documents WHERE source_kind = 'gdrive' AND container_ref = %s", (drive_id,))}
    keep, changed, unreadable, writes, deletes = set(), 0, [], set(), set()
    for f in files:
        name, acl = file_name(f), readers(f.get("permissions"), config.ORG_SLUG, domain)
        if not acl or not name or not supported(name) or not FILE_ID.fullmatch(f.get("id", "")):
            continue
        if f["mimeType"] not in EXPORTS and int(f.get("size") or 0) > MAX_BYTES:
            continue
        key, version = f"gd-{f['id']}", f.get("md5Checksum") or f"{f.get('version')}:{f.get('modifiedTime')}"
        prev = existing.get(key)
        with conn.transaction():
            if not prev or prev["content_hash"] != version:
                try:
                    title, sections = extract(name, client.content(f), f["name"])
                except ExtractionError as e:
                    unreadable.append({"file": f["name"][:200], "reason": str(e)})
                    continue
                doc_id = conn.execute(
                    """INSERT INTO documents (org_id, source_kind, source_uri, source_version, external_key, title,
                                              content_hash, classification, acl_principals, container_ref, source_path)
                       VALUES (%s, 'gdrive', %s, %s, %s, %s, %s, 'internal', %s, %s, %s)
                       ON CONFLICT (org_id, external_key) DO UPDATE SET
                         source_uri = EXCLUDED.source_uri, source_version = EXCLUDED.source_version, title = EXCLUDED.title,
                         content_hash = EXCLUDED.content_hash, acl_principals = EXCLUDED.acl_principals, acl_synced_at = now()
                       WHERE documents.container_ref = EXCLUDED.container_ref
                       RETURNING id""",
                    (org_id, f.get("webViewLink") or f"https://drive.google.com/file/d/{f['id']}", version, key,
                     (title or f["name"])[:300], version, acl, drive_id, f["name"][:500])).fetchone()
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
