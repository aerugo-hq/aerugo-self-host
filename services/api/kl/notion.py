"""Notion, live (S6, ADR 0011): the pages and databases shared with an internal integration, their blocks as text, and
each database's rows as pages of their own, under the page hierarchy they sit in.

Notion's API doesn't say who can read a page, so Aerugo decides, as exports do, and never wider: what's shared with the
integration is readable by the connecting team's members, as the team holds them (its groups and people), recomputed on
every sync. The whole org never counts, even for a team the whole org may join. Connecting Notion this way is a choice
a team's admin makes for what they share with the integration, and the form says so plainly.

Each sync reads everything shared with the integration now, so a page shared later arrives and one unshared or moved
to the trash leaves. The export copies of the pages it reads, in the same team, are retired, so none is indexed twice."""
import random
import re
import time

import httpx

from . import config
from .authz import fga
from .connectors import Container, Health
from .principals import is_valid
from .sync_github import team_for
from .sync_items import Item, sync_container
from .vault import vault

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"
ID = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$")
ROLES = {"member", "curator", "admin"}  # a role on the team that reads its documents
MAX_PAGES = 1000
MAX_BLOCKS = 5000  # per page: past this its text is cut, and says so
MAX_DEPTH = 8
RETRIES = 5
TEXT_BLOCKS = {"paragraph", "quote", "callout", "toggle", "bulleted_list_item", "numbered_list_item", "template"}


class NotionError(RuntimeError):
    """Notion refused a call: its code says why (a revoked token, a page no longer shared)."""


def allowed_base(api_base: str) -> bool:
    return api_base.rstrip("/") in config.NOTION_API_BASES


def plain(rich: list | None) -> str:
    return "".join(str(r.get("plain_text") or "") for r in rich or []).strip()


def page_title(page: dict) -> str:
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return plain(prop.get("title")) or "Untitled"
    return plain(page.get("title")) or "Untitled"  # a data source carries its title at the top


def compact(notion_id: str) -> str:
    return notion_id.replace("-", "").lower()


class NotionClient:
    """Notion's API with an internal integration's token, read only, at the version this connector was written for."""

    def __init__(self, api_base: str, token: str):
        if not allowed_base(api_base):
            raise ValueError("that Notion API address isn't allowed here")
        self.base = api_base.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION}

    def _call(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict:
        attempt = 0
        while True:
            r = httpx.request(method, f"{self.base}/{path}", json=body, params=params, headers=self.headers, timeout=30)
            if r.status_code == 429 and attempt < RETRIES:  # about three requests a second; Notion says how long to wait
                wait = r.headers.get("Retry-After")
                time.sleep(float(wait) if wait and wait.replace(".", "", 1).isdigit() else 2 ** attempt + random.random())
                attempt += 1
                continue
            if r.status_code >= 400:
                code = (r.json() if r.headers.get("content-type", "").startswith("application/json") else {}).get("code")
                raise NotionError(f"Notion answered {r.status_code} {str(code or '')[:60]}".strip())
            return r.json()

    def _pages(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> list[dict]:
        out, cursor = [], None
        for _ in range(MAX_PAGES):
            page = self._call(method, path, {**(body or {}), **({"start_cursor": cursor} if cursor else {})} if method == "POST" else None,
                              {**(params or {}), **({"start_cursor": cursor} if cursor else {})} if method == "GET" else None)
            out += page.get("results") or []
            cursor = page.get("next_cursor") if page.get("has_more") else None
            if not cursor:
                return out
        raise NotionError(f"Notion's listing of {path} ran past {MAX_PAGES} pages")

    def test(self) -> Health:
        try:
            me = self._call("GET", "users/me")
            return Health(True, f"connected to {(me.get('bot') or {}).get('workspace_name') or 'Notion'}")
        except NotionError as e:
            return Health(False, f"{e}: check the integration's token")
        except httpx.HTTPError as e:
            return Health(False, f"couldn't reach Notion: {type(e).__name__}")

    def discover(self) -> list[Container]:
        me = self._call("GET", "users/me")
        return [Container(external_id="workspace", kind="workspace", visibility=None, default_ref=None,
                          name=(me.get("bot") or {}).get("workspace_name") or "Notion")]

    def shared(self) -> tuple[dict[str, dict], dict[str, str]]:
        """Every page shared with the integration, a data source's rows included, by id; and each data source's title."""
        pages = {compact(p["id"]): p for p in self._pages("POST", "search", {"filter": {"property": "object", "value": "page"}, "page_size": 100})
                 if ID.fullmatch(p.get("id") or "") and not p.get("in_trash")}
        sources = {}
        for d in self._pages("POST", "search", {"filter": {"property": "object", "value": "data_source"}, "page_size": 100}):
            if not ID.fullmatch(d.get("id") or "") or d.get("in_trash"):
                continue
            sources[compact(d["id"])] = page_title(d)
            for row in self._pages("POST", f"data_sources/{d['id']}/query", {"page_size": 100}):
                if ID.fullmatch(row.get("id") or "") and not row.get("in_trash"):
                    pages.setdefault(compact(row["id"]), row)
        return pages, sources

    def blocks(self, block_id: str, depth: int = 0, budget: list | None = None) -> list[dict]:
        """A page's blocks, children nested under their parents, up to MAX_BLOCKS and MAX_DEPTH."""
        budget = budget if budget is not None else [MAX_BLOCKS]
        out = []
        for b in self._pages("GET", f"blocks/{block_id}/children", params={"page_size": 100}):
            if budget[0] <= 0:
                break
            budget[0] -= 1
            if b.get("has_children") and depth < MAX_DEPTH and b.get("type") not in ("child_page", "child_database") \
                    and ID.fullmatch(b.get("id") or ""):
                b = {**b, "_children": self.blocks(b["id"], depth + 1, budget)}
            out.append(b)
        return out


def sections_of(title: str, blocks: list[dict]) -> list[tuple[str | None, str]]:
    """Blocks as text: a section per heading; lists, to-dos with their state, quotes, code fenced, tables as pipe rows."""
    sections, heading, lines = [], None, []

    def flush():
        if lines:
            sections.append((heading, "\n".join(lines).strip()))

    def walk(items: list[dict], indent: str = ""):
        nonlocal heading, lines
        for b in items:
            kind, data = b.get("type"), b.get(b.get("type") or "") or {}
            text = plain(data.get("rich_text"))
            if kind in ("heading_1", "heading_2", "heading_3"):
                flush()
                heading, lines = text or None, []
            elif kind == "to_do":
                lines.append(f"{indent}- [{'x' if data.get('checked') else ' '}] {text}")
            elif kind in ("bulleted_list_item", "numbered_list_item"):
                lines.append(f"{indent}- {text}")
            elif kind == "code":
                lines.append(f"```{data.get('language') or ''}\n{text}\n```")
            elif kind == "table_row":
                lines.append("| " + " | ".join(plain(cell) for cell in data.get("cells") or []) + " |")
            elif kind == "child_page":
                lines.append(f"{indent}{data.get('title') or ''}")
            elif kind in TEXT_BLOCKS and text:
                lines.append(f"{indent}{text}")
            if b.get("_children"):
                walk(b["_children"], indent + ("  " if kind in ("bulleted_list_item", "numbered_list_item", "to_do") else ""))
    walk(blocks)
    flush()
    return sections or [(None, title)]


def client_for(connection: dict) -> NotionClient:
    return NotionClient(connection["config"]["api_base"], vault.get(connection["credential_ref"])["token"])


def team_readers(team_slug: str) -> list[str]:
    """The connecting team's members as it holds them, its groups and people: never the org, even for a team the whole
    org may join, so connecting Notion never makes a page org-wide."""
    held = {t["user"].split("#", 1)[0] for t in fga.read({"object": f"workspace:{team_slug}"}) if t["relation"] in ROLES}
    return sorted(p for p in held if p.startswith(("group:", "user:")) and is_valid(p))


def path_of(page: dict, pages: dict[str, dict], sources: dict[str, str]) -> str:
    """Where a page sits: its parents' titles from the top, as far as they're shared with the integration."""
    names, parent, seen = [], page.get("parent") or {}, set()
    while parent and len(names) < 20:
        kind = parent.get("type")
        pid = compact(str(parent.get(kind) or "")) if kind in ("page_id", "data_source_id", "database_id") else ""
        if not pid or pid in seen:
            break
        seen.add(pid)
        if kind == "page_id" and pid in pages:
            names.insert(0, page_title(pages[pid]))
            parent = pages[pid].get("parent") or {}
        elif kind in ("data_source_id", "database_id") and pid in sources:
            names.insert(0, sources[pid])
            break
        else:
            break
    return " › ".join(names)


def retire_exported(conn, team, page_ids: set[str]) -> int:
    """Pages a Notion export brought into this team that the live connector now holds are retired, with their tuples,
    so none is indexed twice: the export kept each page's id in its address (https://www.notion.so/<id>)."""
    if not page_ids:
        return 0
    rows = conn.execute(
        """SELECT d.id, d.external_key FROM documents d
           JOIN workspace_documents wd ON wd.document_id = d.id AND wd.workspace_id = %s
           WHERE d.source_kind = 'import' AND d.container_ref LIKE 'import:%%' AND d.source_uri = ANY(%s)""",
        (team, [f"https://www.notion.so/{i}" for i in sorted(page_ids)])).fetchall()
    if not rows:
        return 0
    held = {(t["user"], t["relation"], t["object"]) for r in rows for t in fga.read({"object": f"document:{r['external_key']}"})}
    if held:
        fga.write(deletes=held)
    with conn.transaction():
        conn.execute("DELETE FROM documents WHERE id = ANY(%s)", ([r["id"] for r in rows],))
    return len(rows)


def sync_workspace(conn, connection: dict, client: NotionClient, container: str) -> dict:
    team = team_for(connection, container)
    slug = conn.execute("SELECT slug FROM workspaces WHERE id = %s", (team,)).fetchone()["slug"]
    acl = team_readers(slug)
    pages, sources = client.shared()
    items = []
    for pid, page in pages.items():
        title = page_title(page)
        url = str(page.get("url") or "")
        items.append(Item(key=f"notion-{pid}", uri=url if url.startswith("https://") else f"https://www.notion.so/{pid}",
                          version=str(page.get("last_edited_time") or ""), title=title, path=path_of(page, pages, sources), acl=acl,
                          sections=lambda pid=pid, title=title: (title, sections_of(title, client.blocks(pid)))))
    result = sync_container(conn, connection, container, "notion", items)
    held = conn.execute("SELECT external_key FROM documents WHERE connection_id = %s AND container_ref = %s AND source_kind = 'notion'",
                        (connection["id"], container)).fetchall()
    result["exports_retired"] = retire_exported(conn, team, {r["external_key"][len("notion-"):] for r in held})
    return result
