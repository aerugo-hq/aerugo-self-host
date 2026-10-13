"""Importing Confluence and Notion exports (slice J2).

A team's admins upload a space or workspace export as a zip, name the import, and choose its readers (each must hold a
role on the team, as for pipeline pushes: an export carries no permissions to trust) and its classification. Each page
becomes a document through the same path as connectors. Pages are identified by the stable ids in the export's file
names, so uploading a newer export under the same name updates pages in place, adds new ones, and removes the ones no
longer in it.

- Notion's "Markdown & CSV" export: `Title <32-hex id>.md` pages, nested in folders (and, for big workspaces, in zips
  inside the zip), and `Title <id>.csv` databases. Each document links to its page in Notion.
- Confluence's HTML space export: `Title_<page id>.html` pages. Only a page's main content is read, not its breadcrumbs
  or footer, and attachments are skipped. A document links to its page when the import knows the site's address.
Archives are read in memory, never unpacked to disk, within limits on size and file count, so neither a zip bomb nor a
crafted path goes anywhere.
"""
import hashlib
import html
import io
import re
import zipfile
from dataclasses import dataclass

from .authz import fga
from .extract import ExtractionError, extract
from .pipelines import viewer_tuples

MAX_ARCHIVE = 50 * 1024 * 1024      # an upload
MAX_UNPACKED = 500 * 1024 * 1024    # everything in it, nested zips included
MAX_FILES = 10000
NOTION_PAGE = re.compile(r"^(?P<title>.+?) (?P<id>[0-9a-f]{32})\.(?P<ext>md|csv)$")
CONFLUENCE_PAGE = re.compile(r"^(?P<title>.*?)_?(?P<id>\d+)\.html$")
MAIN = re.compile(r"<div[^>]*\bid=[\"']main-content[\"'][^>]*>", re.I)
FOOTER = re.compile(r"<div[^>]*\bid=[\"']footer[\"']", re.I)
TITLE = re.compile(r"<title>(.*?)</title>", re.I | re.S)


class ImportFailed(Exception):
    """The export can't be imported. The message says why, and is safe to show."""


@dataclass
class Page:
    page_id: str
    title: str
    path: str
    sections: list
    uri: str


def members(data: bytes) -> list[tuple[str, bytes]]:
    """(path, bytes) for every file in the archive, opening zips inside it one level deep, within the limits."""
    budget = [MAX_UNPACKED]

    def read(blob: bytes, prefix: str, depth: int) -> list[tuple[str, bytes]]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(blob))
        except zipfile.BadZipFile:
            raise ImportFailed("not a zip archive")
        infos = [i for i in archive.infolist() if not i.is_dir()]
        if len(infos) > MAX_FILES:
            raise ImportFailed(f"the archive holds more than {MAX_FILES:,} files")
        if sum(i.file_size for i in infos) > budget[0]:
            raise ImportFailed("the archive would unpack past the size limit, so it wasn't opened")
        out = []
        for info in infos:
            body = archive.read(info)
            budget[0] -= len(body)
            if info.filename.lower().endswith(".zip") and depth == 0:
                out += read(body, f"{prefix}{info.filename}/", depth + 1)
            else:
                out.append((f"{prefix}{info.filename}", body))
        return out

    return read(data, "", 0)


def names(paths) -> list[str]:
    return [p.rsplit("/", 1)[-1] for p in paths]


def detect(paths) -> str | None:
    """Which export this is, from its file names alone."""
    files = names(paths)
    if any(NOTION_PAGE.match(n) for n in files):
        return "notion"
    if "index.html" in files and any(CONFLUENCE_PAGE.match(n) for n in files if n != "index.html"):
        return "confluence"
    return None


def notion_pages(files: list[tuple[str, bytes]]) -> list[Page]:
    pages = []
    for path, body in files:
        m = NOTION_PAGE.match(path.rsplit("/", 1)[-1])
        if not m:
            continue
        try:
            title, sections = extract(f"page.{m['ext']}", body, m["title"])
        except ExtractionError:
            continue  # an empty page: nothing to import
        suffix = "-db" if m["ext"] == "csv" else ""
        pages.append(Page(m["id"] + suffix, title or m["title"], path, sections, f"https://www.notion.so/{m['id']}"))
    return pages


def confluence_pages(files: list[tuple[str, bytes]], base_url: str | None) -> list[Page]:
    pages = []
    for path, body in files:
        name = path.rsplit("/", 1)[-1]
        m = CONFLUENCE_PAGE.match(name) if name != "index.html" else None
        if not m:
            continue
        page = body.decode("utf-8", errors="replace")
        t = TITLE.search(page)
        raw_title = t.group(1).split(" : ", 1)[-1].strip() if t else m["title"].replace("-", " ")
        start = MAIN.search(page)
        end = FOOTER.search(page, start.end()) if start else None
        content = page[start.start():end.start() if end else None] if start else page
        try:
            _, sections = extract("page.html", f"<html><head><title>{raw_title}</title></head><body>{content}</body></html>".encode(),
                                  html.unescape(raw_title))
        except ExtractionError:
            continue
        uri = f"{base_url.rstrip('/')}/pages/viewpage.action?pageId={m['id']}" if base_url else f"confluence:{m['id']}"
        pages.append(Page(m["id"], html.unescape(raw_title) or m["id"], path, sections, uri))
    return pages


def read(data: bytes, kind: str, base_url: str | None) -> list[Page]:
    """The export's pages, or ImportFailed with a reason."""
    files = members(data)
    pages = notion_pages(files) if kind == "notion" else confluence_pages(files, base_url)
    if not pages:
        raise ImportFailed(f"no {kind.title()} pages found: is this a {'Markdown & CSV' if kind == 'notion' else 'HTML'} export?")
    seen, unique = set(), []
    for p in pages:  # a page in a part zip and again at the top counts once
        if p.page_id not in seen:
            seen.add(p.page_id)
            unique.append(p)
    return unique


UPSERT_SQL = """
INSERT INTO documents (org_id, source_kind, source_uri, source_version, external_key, title, content_hash,
                       classification, acl_principals, container_ref, source_path)
VALUES (%s, 'import', %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (org_id, external_key) DO UPDATE SET
  source_uri = EXCLUDED.source_uri, source_version = EXCLUDED.source_version, title = EXCLUDED.title,
  content_hash = EXCLUDED.content_hash,
  classification = kl.stricter(EXCLUDED.classification, documents.classification_floor),
  acl_principals = EXCLUDED.acl_principals, acl_synced_at = now(), source_path = EXCLUDED.source_path
WHERE documents.container_ref = EXCLUDED.container_ref  -- only this import replaces its own pages
RETURNING id
"""


def apply(conn, run: dict, pages: list[Page]) -> dict:
    """Make the import's documents match this export (as kl_ingest): pages added or changed, readers current, and
    pages no longer in the export removed with everything derived from them. Authorization follows."""
    container, org_id, readers = f"import:{run['import_id']}", run["org_id"], sorted(set(run["readers"]))
    existing = {r["external_key"]: r for r in conn.execute(
        "SELECT id, external_key, content_hash FROM documents WHERE container_ref = %s", (container,))}
    keep, changed, writes, deletes = set(), 0, set(), set()
    with conn.transaction():
        for p in pages:
            key = f"im-{str(run['import_id'])[:8]}-{hashlib.sha256(p.page_id.encode()).hexdigest()[:20]}"
            digest = hashlib.sha256(repr((p.title, p.sections)).encode()).hexdigest()
            prev = existing.get(key)
            row = conn.execute(UPSERT_SQL, (org_id, p.uri, str(run["id"]), key, p.title[:300], digest, run["classification"],
                                            readers, container, p.path[:500])).fetchone()
            if not row:
                continue
            if not prev or prev["content_hash"] != digest:
                conn.execute("DELETE FROM chunks WHERE document_id = %s", (row["id"],))
                for ordinal, (heading, body) in enumerate(p.sections):
                    conn.execute("INSERT INTO chunks (org_id, document_id, ordinal, heading_path, body, acl_principals) "
                                 "VALUES (%s, %s, %s, %s, %s, %s)", (org_id, row["id"], ordinal, heading, body, readers))
                changed += 1
            else:  # same page; the import's readers may have changed
                conn.execute("UPDATE chunks SET acl_principals = %s WHERE document_id = %s", (readers, row["id"]))
                conn.execute("UPDATE chunk_embeddings SET acl_principals = %s "
                             "WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = %s)", (readers, row["id"]))
            conn.execute("UPDATE concepts SET acl_principals = %s WHERE document_id = %s", (readers, row["id"]))
            conn.execute("INSERT INTO workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, %s) "
                         "ON CONFLICT DO NOTHING", (run["workspace_id"], row["id"], org_id))
            keep.add(key)
            want = viewer_tuples(key, readers)
            have = {(t["user"], t["relation"], t["object"]) for t in fga.read({"object": f"document:{key}"})}
            writes |= want - have
            deletes |= have - want
        gone = [(k, r["id"]) for k, r in existing.items() if k not in keep]
        if gone:
            conn.execute("DELETE FROM documents WHERE id = ANY(%s)", ([doc for _, doc in gone],))
    for key, _ in gone:
        deletes |= {(t["user"], t["relation"], t["object"]) for t in fga.read({"object": f"document:{key}"})}
    if writes or deletes:
        fga.write(writes=writes, deletes=deletes)
    return {"pages": len(pages), "changed": changed, "removed": len(gone)}
