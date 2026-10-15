"""Confluence Cloud, live (ADR 0010, ADR 0011): each page and blog post becomes a document readable by exactly the
people Confluence lets read it. The connection's account (an Atlassian service account, or a dedicated user) only sets
what's indexed. Confluence has no call that says who may read a page, so its permissions are computed here, and fail
closed:

  - A space's readers are the people its permissions let read it, and the members of the groups they do (expanded).
    On a connection whose admin confirmed this site is the company's own, a space anyone with Confluence access may
    read reaches the whole org.
  - A page's readers are its space's readers who also pass the read restriction on the page and on every restricted
    page above it: restrictions are inherited down the tree, and each restricted level narrows who may read.
  - A page whose restrictions, or whose ancestors' restrictions, can't be read isn't indexed, nor is anything under
    it; nor is a page nobody may read. A space role Confluence doesn't expand grants nothing.

Pages and blog posts are read in Confluence's storage format: headings become sections, tables stay tables, and
code, Jira keys, links, images, and tasks keep their text. A page's ancestors are its path, so Documents shows the
tree. Labels and page trees narrow what's indexed. Accounts are ext:atlassian.<account id>, as Jira's are."""
import base64
import re
from html.parser import HTMLParser

import httpx

from . import config
from .authz import fga
from .connectors import Container, Health
from .jira import SITE, account
from .sync_github import team_for
from .sync_items import Item, record_identities, sync_container
from .vault import vault

SPACE_KEY = re.compile(r"^~?[A-Za-z0-9_-]{1,255}$")  # personal spaces start with ~
CONTENT_ID = re.compile(r"^[0-9]{1,20}$")
DEFAULT_GROUP = re.compile(r"^confluence-users(-.+)?$")  # the group every Confluence user is in, on a company's own site
SEPARATOR = " › "  # between a path's titles
MAX_TEXT = 400_000
EVERYONE = "everyone"  # internal: a space anyone with Confluence access may read


def allowed_base(api_base: str) -> bool:
    base = api_base.rstrip("/")
    return bool(SITE.fullmatch(base)) or base in config.CONFLUENCE_API_BASES


# ---- the storage format, to sections of text
class _Storage(HTMLParser):
    """Confluence's storage format (XHTML with ac: and ri: elements) to (heading, body) sections, Markdown-ish: pipe
    tables, fenced code, list items, and tasks. Anything unknown keeps its text."""
    HEADINGS = {"h1", "h2", "h3"}
    BLOCKS = {"p", "h4", "h5", "h6", "div", "blockquote", "pre", "br", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections: list[tuple[str | None, list[str]]] = [(None, [])]
        self.line: list[str] = []
        self.heading: list[str] | None = None
        self.table: list[list[str]] | None = None
        self.cell: list[str] | None = None
        self.macro: list[str] = []  # the names of the macros we're inside
        self.param: str | None = None
        self.params: list[dict] = []
        self.code: list[str] | None = None
        self.lists: list[str] = []
        self.task_done: bool | None = None
        self.link: list[str] | None = None  # inside an ac:link: its own text, which wins over the linked title
        self.link_title: str | None = None
        self.in_status = False  # inside a task's status: complete or incomplete
        self.skip = 0  # inside a parameter's value, or a task's status: text that isn't content

    # where text goes
    def _out(self) -> list[str]:
        if self.cell is not None:
            return self.cell
        if self.heading is not None:
            return self.heading
        return self.line

    def _flush(self):
        text = " ".join("".join(self.line).split())
        if text:
            self.sections[-1][1].append(text)
        self.line = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.HEADINGS:
            self._flush()
            self.heading = []
        elif tag in self.BLOCKS:
            self._flush()
        elif tag in ("ul", "ol"):
            self._flush()
            self.lists.append(tag)
        elif tag == "li":
            self._flush()
            self.line.append(("1. " if self.lists and self.lists[-1] == "ol" else "- ") + "  " * max(0, len(self.lists) - 1))
        elif tag == "table":
            self._flush()
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.table.append([])
        elif tag in ("td", "th") and self.table is not None:
            self.cell = []
        elif tag == "ac:structured-macro":
            self.macro.append(a.get("ac:name", ""))
            self.params.append({})
        elif tag == "ac:parameter":
            self.param = a.get("ac:name", "")
            self.skip += 1
        elif tag == "ac:plain-text-body" and self.macro and self.macro[-1] in ("code", "noformat"):
            self._flush()
            self.code = []
        elif tag == "ac:task":
            self._flush()
            self.task_done = False
        elif tag in ("ac:task-status", "ac:task-id"):
            self.skip += 1
            self.in_status = tag == "ac:task-status"
        elif tag == "ac:link":
            self.link, self.link_title = [], None
        elif tag == "ri:attachment" and self.macro == [] and a.get("ri:filename"):
            self._out().append(f"[image: {a['ri:filename']}]")
        elif tag == "ri:page" and a.get("ri:content-title"):
            if self.link is not None:
                self.link_title = a["ri:content-title"]
            else:
                self._out().append(a["ri:content-title"])
        elif tag == "ri:user":
            if self.link is not None:
                self.link_title = "@someone"
            else:
                self._out().append("@someone")

    def handle_endtag(self, tag):
        if tag in self.HEADINGS and self.heading is not None:
            title = " ".join("".join(self.heading).split())
            self.heading = None
            if title:
                self.sections.append((title, []))
        elif tag in self.BLOCKS or tag == "li":
            self._flush()
        elif tag in ("ul", "ol") and self.lists:
            self._flush()
            self.lists.pop()
        elif tag in ("td", "th") and self.table is not None and self.cell is not None:
            self.table[-1].append(" ".join("".join(self.cell).split()).replace("|", "\\|"))
            self.cell = None
        elif tag == "table" and self.table is not None:
            rows = [r for r in self.table if r]
            self.table = None
            if rows:
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width] + ["| " + " | ".join(r) + " |" for r in rows[1:]]
                self.sections[-1][1].append("\n".join(lines))
        elif tag == "ac:parameter":
            self.param = None
            self.skip = max(0, self.skip - 1)
        elif tag in ("ac:task-status", "ac:task-id"):
            self.skip = max(0, self.skip - 1)
            self.in_status = False
        elif tag == "ac:link" and self.link is not None:
            text = " ".join("".join(self.link).split()) or self.link_title or ""
            self.link, self.link_title = None, None
            if text:
                self._out().append(text)
        elif tag == "ac:plain-text-body" and self.code is not None:
            body = "".join(self.code).strip("\n")
            lang = (self.params[-1] if self.params else {}).get("language", "")
            if body:
                self.sections[-1][1].append(f"```{lang}\n{body}\n```")
            self.code = None
        elif tag == "ac:structured-macro" and self.macro:
            name, params = self.macro.pop(), self.params.pop()
            if name == "jira" and params.get("key"):
                self._out().append(params["key"])
            elif name == "status" and params.get("title"):
                self._out().append(f"[{params['title']}]")
        elif tag == "ac:task":
            text = " ".join("".join(self.line).split())
            if text:
                self.sections[-1][1].append(f"- [{'x' if self.task_done else ' '}] {text}")
            self.line = []
            self.task_done = None

    def handle_data(self, data):
        if self.param is not None and self.params:
            self.params[-1][self.param] = self.params[-1].get(self.param, "") + data
            return
        if self.in_status and self.task_done is not None:  # only the status says whether it's done
            self.task_done = data.strip() == "complete"
            return
        if self.skip:
            return
        if self.code is not None:
            self.code.append(data)
            return
        if self.link is not None:
            self.link.append(data)
            return
        self._out().append(data)

    def unknown_decl(self, data):  # <![CDATA[ ... ]]>: a code block's body, or a parameter's
        if data.startswith("CDATA["):
            self.handle_data(data[len("CDATA["):])


def storage_sections(storage: str, title: str) -> tuple[str, list[tuple[str | None, str]]]:
    """A page's storage format as (title, [(heading, body)]): a section per heading, paragraphs apart."""
    parser = _Storage()
    try:
        parser.feed((storage or "")[:MAX_TEXT])
        parser.close()
    except Exception:  # malformed markup: keep what was read
        pass
    parser._flush()
    sections = [(heading, "\n\n".join(parts)) for heading, parts in parser.sections]
    return title, [(h, b) for h, b in sections if b or h] or [(None, "")]


# ---- the client
class ConfluenceClient:
    """Confluence Cloud's REST API (version 2 for content, version 1 for restrictions, groups, and users), with an
    account's email and API token. It only ever follows a next page on its own site."""

    def __init__(self, api_base: str, email: str, api_token: str):
        self.base = api_base.rstrip("/")  # the site: Confluence lives under /wiki
        auth = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self.headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}

    def _get(self, path: str, **params):
        r = httpx.get(f"{self.base}/wiki/{path.lstrip('/')}", params={k: v for k, v in params.items() if v is not None},
                      headers=self.headers, timeout=30)
        r.raise_for_status()
        return r.json()

    def _all(self, path: str, limit: int = 20000, **params) -> list[dict]:
        """Every result of a version 2 listing, following its next links (relative, on this site only)."""
        out, page = [], self._get(path, limit=250, **params)
        while True:
            out += page.get("results") or []
            nxt = ((page.get("_links") or {}).get("next") or "")
            if not nxt or len(out) >= limit:
                return out[:limit]
            if not nxt.startswith("/wiki/api/v2/"):  # never anywhere else
                return out
            page = self._get(nxt[len("/wiki/"):])

    def _v1_all(self, path: str, limit: int = 10000, **params) -> list[dict]:
        out, start = [], 0
        while len(out) < limit:
            page = self._get(path, start=start, limit=200, **params)
            results = page.get("results") or []
            out += results
            if len(results) < 200:
                break
            start += len(results)
        return out[:limit]

    def test(self) -> Health:
        try:
            me = self._get("rest/api/user/current")
            return Health(True, f"signed in as {me.get('displayName') or me.get('publicName') or me.get('accountId')}")
        except httpx.HTTPStatusError as e:
            return Health(False, f"Confluence returned {e.response.status_code}: check the email and API token")
        except httpx.HTTPError as e:
            return Health(False, f"couldn't reach Confluence: {type(e).__name__}")

    def discover(self) -> list[Container]:
        return [Container(external_id=s["key"], kind="space", visibility=None, default_ref=None, name=s.get("name"))
                for s in self._all("api/v2/spaces", status="current") if SPACE_KEY.fullmatch(s.get("key") or "")]

    def space(self, key: str) -> dict | None:
        found = self._get("api/v2/spaces", keys=key, limit=1).get("results") or []
        return found[0] if found and found[0].get("key") == key else None

    def content(self, space_id: str, kind: str) -> list[dict]:
        """A space's current pages, or blog posts, with their bodies in the storage format."""
        return [c for c in self._all(f"api/v2/spaces/{space_id}/{kind}", status="current", **{"body-format": "storage"})
                if CONTENT_ID.fullmatch(str(c.get("id") or ""))]

    def space_grants(self, space_id: str) -> tuple[set, set, bool]:
        """Who the space's permissions let read it: accounts, group ids, and whether anyone at all may (anonymous)."""
        users, groups, anyone = set(), set(), False
        for p in self._all(f"api/v2/spaces/{space_id}/permissions"):
            op, who = p.get("operation") or {}, p.get("principal") or {}
            if op.get("key") != "read" or op.get("targetType") != "space":
                continue
            if who.get("type") == "user" and who.get("id"):
                users.add(who["id"])
            elif who.get("type") == "group" and who.get("id"):
                groups.add(who["id"])
            elif who.get("type") == "anonymous":
                anyone = True
            # a role, or anything else: grants nothing here (fail closed)
        return users, groups, anyone

    def restriction(self, content_id: str) -> tuple[set, set] | None:
        """A page's own read restriction: (accounts, group ids), or None when it has none. Raises when it can't be read."""
        r = self._get(f"rest/api/content/{content_id}/restriction/byOperation/read",
                      expand="restrictions.user,restrictions.group")
        rs = r.get("restrictions") or {}
        users = {u["accountId"] for u in (rs.get("user") or {}).get("results") or [] if u.get("accountId")}
        groups = {g.get("id") or g.get("name") for g in (rs.get("group") or {}).get("results") or [] if g.get("id") or g.get("name")}
        return (users, groups) if users or groups else None

    def group_members(self, group: str) -> list[dict]:
        return [u for u in self._v1_all(f"rest/api/group/{group}/membersByGroupId")
                if u.get("accountId") and u.get("accountType", "atlassian") == "atlassian"]

    def group_name(self, group: str) -> str:
        return (self._get("rest/api/group/by-id", id=group) or {}).get("name") or ""

    def users(self, account_ids: list[str]) -> dict[str, dict]:
        out = {}
        for i in range(0, len(account_ids), 100):
            page = self._get("rest/api/user/bulk", accountId=account_ids[i:i + 100])
            for u in page.get("results") or []:
                if u.get("accountId"):
                    out[u["accountId"]] = {"email": u.get("email"), "name": u.get("displayName") or u.get("publicName")}
        return out

    def labels(self, content_id: str, kind: str) -> set[str]:
        return {l["name"] for l in self._all(f"api/v2/{kind}/{content_id}/labels") if l.get("name")}

    def attachments(self, content_id: str, kind: str) -> list[dict]:
        return self._all(f"api/v2/{kind}/{content_id}/attachments", limit=500)

    def parent_of(self, content_id: str) -> str | None:
        """A folder's parent, for pages that sit under one."""
        f = self._get(f"api/v2/folders/{content_id}")
        return str(f.get("parentId")) if f.get("parentId") else None


def client_for(connection: dict) -> ConfluenceClient:
    secret = vault.get(connection["credential_ref"])
    return ConfluenceClient(connection["config"]["api_base"], secret["email"], secret["api_token"])


# ---- who may read what
class _Readers:
    """Expands groups once per sync, and notes each account it meets for identity matching (ADR 0011)."""

    def __init__(self, client, seen: dict):
        self.client, self.seen, self.members = client, seen, {}

    def of_group(self, group: str) -> set[str]:
        if group not in self.members:
            try:
                people = self.client.group_members(group)
            except Exception:  # can't be expanded: it grants nothing (fail closed)
                people = []
            for u in people:
                self.seen.setdefault(u["accountId"], {"email": u.get("email"), "name": u.get("displayName") or u.get("publicName")})
            self.members[group] = {u["accountId"] for u in people}
        return self.members[group]

    def of(self, users: set, groups: set) -> set[str]:
        out = set(users)
        for g in groups:
            out |= self.of_group(g)
        for u in users:
            self.seen.setdefault(u, {})
        return out


def narrowed(readers, levels: list[set]):
    """Readers after each restricted level: a page is read only by who passes every one (EVERYONE: anyone at all)."""
    for allowed in levels:
        readers = set(allowed) if readers == EVERYONE else readers & allowed
    return readers


def items_for_space(client, cfg: dict, key: str, seen: dict) -> list[Item]:
    space = client.space(key)
    if not space:
        return []
    readers = _Readers(client, seen)
    users, groups, anyone = client.space_grants(str(space["id"]))
    everyone = bool(cfg.get("company_site")) and (anyone or any(DEFAULT_GROUP.fullmatch(client.group_name(g) or "") for g in groups))
    space_readers = EVERYONE if everyone else readers.of(users, groups)
    labels = {l.strip() for l in cfg.get("labels") or [] if l and l.strip()}
    roots = {str(r) for r in cfg.get("page_trees") or []}

    pages = {str(p["id"]): {**p, "_kind": "pages"} for p in client.content(str(space["id"]), "pages")}
    posts = {str(b["id"]): {**b, "_kind": "blogposts"} for b in client.content(str(space["id"]), "blogposts")}
    everything = {**pages, **posts}
    restrictions: dict[str, tuple | None] = {}
    unreadable: set[str] = set()

    def restricted(cid: str):
        if cid not in restrictions:
            try:
                restrictions[cid] = client.restriction(cid)
            except Exception:  # can't read it: fail closed for it and everything under it
                restrictions[cid] = None
                unreadable.add(cid)
        return restrictions[cid]

    def chain(cid: str) -> list[str] | None:
        """The page and its ancestors, nearest first; None if one can't be followed (fail closed)."""
        out, seen_ids = [cid], {cid}
        node = everything.get(cid)
        parent = str(node.get("parentId")) if node and node.get("parentId") else None
        while parent:
            if parent in seen_ids or len(out) > 200:
                return None
            out.append(parent)
            seen_ids.add(parent)
            if parent in pages:
                parent = str(pages[parent].get("parentId")) if pages[parent].get("parentId") else None
            else:
                try:
                    parent = client.parent_of(parent)  # a folder
                except Exception:
                    return None
        return out

    items = []
    for cid, c in everything.items():
        ids = chain(cid)
        if ids is None:
            continue
        if roots and c["_kind"] == "pages" and not (roots & set(ids)):
            continue
        if roots and c["_kind"] == "blogposts":
            continue  # page trees choose pages
        levels = []
        for i in ids:
            r = restricted(i)
            if i in unreadable:
                levels = None
                break
            if r:
                levels.append(readers.of(*r))
        if levels is None:
            continue
        if labels and not (labels & client.labels(cid, c["_kind"])):
            continue
        allowed = narrowed(space_readers, levels)
        acl = [f"org:{config.ORG_SLUG}"] if allowed == EVERYONE else sorted(p for p in (account(a) for a in allowed) if p)
        if not acl:  # nobody may read it: it isn't indexed at all
            continue
        titles = [everything[i]["title"].replace("›", ">") for i in reversed(ids) if i in everything and everything[i].get("title")]
        webui = (c.get("_links") or {}).get("webui")
        uri = f"{client.base}/wiki{webui}" if webui and webui.startswith("/") else f"{client.base}/wiki/pages/viewpage.action?pageId={cid}"

        def sections(c=c, cid=cid):
            title, parts = storage_sections(((c.get("body") or {}).get("storage") or {}).get("value") or "", c.get("title") or "")
            files = [a.get("title") for a in client.attachments(cid, c["_kind"]) if a.get("title")]
            if files:
                parts.append(("Attachments", "\n".join(f"- {name}" for name in files)))
            return title, parts

        items.append(Item(key=f"confluence-{cid}", uri=uri, version=str((c.get("version") or {}).get("number") or ""),
                          title=c.get("title") or f"Page {cid}", path=SEPARATOR.join(titles), acl=acl, sections=sections))
    return items


def sync_space(conn, connection: dict, client: ConfluenceClient, key: str) -> dict:
    seen: dict[str, dict] = {}
    items = items_for_space(client, connection["config"] or {}, key, seen)
    missing = [a for a, v in seen.items() if not v.get("email")]
    if missing:
        try:
            found = client.users(missing)
            for a in missing:
                seen[a] = found.get(a) or seen[a]
        except Exception:
            pass  # emails are optional: identity matching then waits for one
    people = {a.replace(":", "."): v for a, v in seen.items() if v.get("email")}
    if people:
        record_identities(conn, connection["org_id"], "atlassian", people)
    result = sync_container(conn, connection, key, "confluence", items)
    result["exports_retired"] = retire_exported(conn, team_for(connection, key), {i.key[len("confluence-"):] for i in items})
    return result


def retire_exported(conn, team, page_ids: set[str]) -> int:
    """Pages a Confluence export brought into this space's team (where the sync loop puts its documents: the team it's
    routed to, else the connection's) that the live connector now reads itself are retired, so none is indexed twice:
    the live one carries Confluence's own readers. An exported page keeps its id in its address
    (viewpage.action?pageId=<id>, or confluence:<id>). Another team's exports are never touched."""
    if not page_ids:
        return 0
    ids = sorted(page_ids)
    rows = conn.execute(
        """SELECT d.id, d.external_key FROM documents d
           JOIN workspace_documents wd ON wd.document_id = d.id AND wd.workspace_id = %s
           WHERE d.source_kind = 'import' AND d.container_ref LIKE 'import:%%'
             AND (d.source_uri = ANY(%s) OR substring(d.source_uri FROM 'pageId=([0-9]+)$') = ANY(%s))""",
        (team, [f"confluence:{i}" for i in ids], ids)).fetchall()
    if not rows:
        return 0
    with conn.transaction():
        conn.execute("DELETE FROM documents WHERE id = ANY(%s)", ([r["id"] for r in rows],))
    deletes = {(t["user"], t["relation"], t["object"]) for r in rows for t in fga.read({"object": f"document:{r['external_key']}"})}
    if deletes:
        fga.write(writes=set(), deletes=deletes)
    return len(rows)
