"""Jira (ADR 0010, ADR 0011): each issue, with its description and comments, becomes a document readable by exactly the
people Jira lets see it. The connection's account (a service account or a dedicated user) only sets what's indexed.
Permissions mirror Jira and fail closed:

  - A project anyone with Jira access may browse is readable by the whole org, but only on a connection whose admin
    confirmed this Jira site is the company's own; otherwise its readers are the people Jira names.
  - Every other project's readers, and every issue under a security level, come from Jira's own permission search:
    the accounts Jira says may see it, as external identities (ext:atlassian.<account id>) that reach a person only
    once identity matching or the person links them to their Aerugo account.
  - An issue nobody may read isn't indexed at all."""
import base64
import re
from datetime import datetime, timezone

import httpx

from . import config
from .connectors import Container, Health
from .principals import is_valid
from .attachments import MAX_BYTES as MAX_ATTACHMENT, MAX_PER_PAGE, attachment_item
from .decisions import heading as decision_heading, is_decision
from .sync_items import Item, record_identities, storage_of, sync_container
from .vault import vault
from . import source_http

PROJECT = re.compile(r"^[A-Z][A-Z0-9_]{0,49}$")
ISSUE = re.compile(r"^[A-Z][A-Z0-9_]{0,49}-[0-9]{1,9}$")
SITE = re.compile(r"^https://[a-z0-9][a-z0-9-]{0,62}\.atlassian\.net$")  # Jira Cloud
OVERLAP_MINUTES = 10  # an incremental sync reaches this far before its cursor: clocks drift, and edits land mid-listing (H6)
FIELDS = "summary,description,comment,status,assignee,reporter,issuetype,updated,security,labels,resolution,resolutiondate,attachment"
ATTACHMENT_ID = re.compile(r"^[0-9]{1,20}$")
WORD = re.compile(r"^[a-z0-9]{1,40}$")  # a search word (F11b): nothing else reaches a query
ANYONE = {"applicationRole", "anyone"}  # permission holders meaning "anyone with access to this Jira"
BLOCKS = {"paragraph", "heading", "listItem", "blockquote", "codeBlock", "panel", "tableRow", "rule"}


def allowed_base(api_base: str) -> bool:
    base = api_base.rstrip("/")
    return bool(SITE.fullmatch(base)) or base in config.JIRA_API_BASES


def account(account_id: str | None) -> str | None:
    """An Atlassian account as a principal. Its ids may hold ':', which principals don't allow, and never hold '.'."""
    if not account_id:
        return None
    principal = f"ext:atlassian.{str(account_id).replace(':', '.')}"
    return principal if is_valid(principal) else None


def adf_text(node) -> str:
    """Atlassian Document Format to plain text: each paragraph, heading, list item, and table row on its own line."""
    out: list[str] = []

    def walk(n):
        if isinstance(n, list):
            for x in n:
                walk(x)
            return
        if not isinstance(n, dict):
            return
        kind = n.get("type")
        if kind == "text":
            out.append(n.get("text", ""))
        elif kind in ("hardBreak", "rule"):
            out.append("\n")
        elif kind == "mention":
            out.append((n.get("attrs") or {}).get("text") or "@someone")
        elif kind in ("emoji", "inlineCard"):
            attrs = n.get("attrs") or {}
            out.append(attrs.get("shortName") or attrs.get("url") or "")
        walk(n.get("content") or [])
        if kind in BLOCKS:
            out.append("\n")

    if isinstance(node, str):  # older Jira and Server send plain text
        return node.strip()
    walk(node)
    lines = [" ".join(line.split()) for line in "".join(out).split("\n")]
    return "\n".join(line for line in lines if line)


def issue_sections(issue: dict) -> tuple[str, list[tuple[str | None, str]]]:
    """The issue's key and summary as its title; a section for its details and description, and one per comment."""
    f = issue.get("fields") or {}
    name = lambda person: (person or {}).get("displayName") or "someone"
    facts = [f"{issue['key']}: {f.get('summary') or ''}".strip(),
             " · ".join(x for x in (
                 (f.get("issuetype") or {}).get("name"), (f.get("status") or {}).get("name"),
                 f"assigned to {name(f.get('assignee'))}" if f.get("assignee") else "unassigned",
                 f"reported by {name(f.get('reporter'))}" if f.get("reporter") else None,
                 f"labels: {', '.join(f['labels'])}" if f.get("labels") else None) if x)]
    description = adf_text(f.get("description")) if f.get("description") else ""
    sections = [(None, "\n".join(facts + ([description] if description else [])))]
    for c in ((f.get("comment") or {}).get("comments") or [])[:200]:
        text = adf_text(c.get("body"))
        if text:  # a comment that records a decision is one (Q5): who decided, and when
            by, at = name(c.get("author")), str(c.get("created") or "")[:10]
            sections.append((decision_heading(by, at) if is_decision(text) else f"Comment by {by} on {at}", text))
    if f.get("resolution") and f.get("resolutiondate"):
        sections.append((decision_heading(name(f.get("assignee")) if f.get("assignee") else None, str(f["resolutiondate"])[:10]),
                         f"Resolved as {(f['resolution'] or {}).get('name') or 'resolved'}: {f.get('summary') or ''}".strip()))
    return f"{issue['key']}: {f.get('summary') or ''}".strip(), sections


class JiraClient:
    """Jira Cloud's REST API, version 3, with an account's email and API token."""

    def __init__(self, api_base: str, email: str, api_token: str):
        self.base = api_base.rstrip("/")
        auth = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self.headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}

    def download(self, attachment_id: str) -> bytes:
        """An attachment's bytes from this site, never a redirect elsewhere, up to the attachments' cap."""
        if not ATTACHMENT_ID.fullmatch(attachment_id):
            raise ValueError("unexpected attachment id")
        with httpx.stream("GET", f"{self.base}/rest/api/3/attachment/content/{attachment_id}", params={"redirect": "false"},
                          headers=self.headers, timeout=60, follow_redirects=False) as r:
            r.raise_for_status()
            data = b""
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > MAX_ATTACHMENT:
                    raise ValueError("the attachment is larger than the cap")
            return data

    def _get(self, path: str, **params):
        r = source_http.get(f"{self.base}/rest/api/3/{path}", params={k: v for k, v in params.items() if v is not None},
                      headers=self.headers, timeout=30)
        r.raise_for_status()
        return r.json()

    def test(self) -> Health:
        try:
            me = self._get("myself")
            return Health(True, f"signed in as {me.get('displayName') or me.get('accountId')}")
        except httpx.HTTPStatusError as e:
            return Health(False, f"Jira returned {e.response.status_code}: check the email and API token")
        except httpx.HTTPError as e:
            return Health(False, f"couldn't reach Jira: {type(e).__name__}")

    def discover(self) -> list[Container]:
        found, start = [], 0
        while True:
            page = self._get("project/search", startAt=start, maxResults=50)
            for p in page.get("values") or []:
                if PROJECT.fullmatch(p.get("key") or ""):
                    found.append(Container(external_id=p["key"], kind="project", visibility=None, default_ref=None,
                                           name=p.get("name")))
            if page.get("isLast", True) or not page.get("values"):
                return found
            start += len(page["values"])

    def issues(self, project: str, since: str | None = None) -> list[dict]:
        """A project's issues, or only those updated since a cursor (H6): Jira's relative minutes, so no time zone of the
        account's can shift it, with an overlap for clocks and edits in flight."""
        jql, minutes = f"project = {project} ORDER BY updated DESC", minutes_since(since)
        if minutes is not None:
            jql = f"project = {project} AND updated >= -{minutes + OVERLAP_MINUTES}m ORDER BY updated DESC"
        out, token = [], None
        while len(out) < 20000:
            page = self._get("search/jql", jql=jql, fields=FIELDS, maxResults=100, nextPageToken=token)
            out += [i for i in page.get("issues") or [] if ISSUE.fullmatch(i.get("key") or "")]
            token = page.get("nextPageToken")
            if page.get("isLast", True) or not token:
                return out
        return out

    def issue(self, key: str) -> dict:
        """One issue, with the fields a sync reads: a pointer's text, read again live (F11a)."""
        if not ISSUE.fullmatch(key):
            raise ValueError("unexpected issue key")
        return self._get(f"issue/{key}", fields=FIELDS)

    def search_text(self, words: list[str], projects: list[str] | None, limit: int = 20) -> list[str]:
        """The issues Jira's own search finds for any of these words, in its order (F11b's runner). Words are letters and
        digits alone and projects are keys, so nothing a person typed is ever JQL."""
        if not words or not all(WORD.fullmatch(w) for w in words) or not all(PROJECT.fullmatch(p) for p in projects or []):
            raise ValueError("search words are letters and digits, projects their keys")
        scope = f"project in ({', '.join(projects)}) AND " if projects else ""
        terms = " OR ".join(f'text ~ "{w}"' for w in words)
        page = self._get("search/jql", jql=f"{scope}({terms})", fields="summary", maxResults=limit)
        return [i["key"] for i in page.get("issues") or [] if ISSUE.fullmatch(i.get("key") or "")]

    def open_to_everyone(self, project: str) -> bool:
        """Whether anyone with Jira access may browse the project."""
        scheme = self._get(f"project/{project}/permissionscheme", expand="permissions")
        return any(p.get("permission") == "BROWSE_PROJECTS" and (p.get("holder") or {}).get("type") in ANYONE
                   for p in scheme.get("permissions") or [])

    def browsers(self, project: str | None = None, issue: str | None = None) -> list[dict]:
        """The active accounts Jira says may see a project, or one issue (its security level included)."""
        users, start = [], 0
        while start < 10000:
            page = self._get("user/permission/search", permissions="BROWSE_PROJECTS", projectKey=project,
                             issueKey=issue, startAt=start, maxResults=1000)
            users += [u for u in page or [] if u.get("accountId") and u.get("active", True)]
            if len(page or []) < 1000:
                break
            start += 1000
        return users


def client_for(connection: dict) -> JiraClient:
    secret = vault.get(connection["credential_ref"])
    return JiraClient(connection["config"]["api_base"], secret["email"], secret["api_token"])


def readers_of(users: list[dict], seen: dict) -> list[str]:
    """Principals for the accounts Jira names, noting each one for identity matching (ADR 0011)."""
    out = set()
    for u in users:
        principal = account(u.get("accountId"))
        if principal:
            out.add(principal)
            seen[principal[len("ext:atlassian."):]] = {"email": u.get("emailAddress"), "name": u.get("displayName")}
    return sorted(out)


def project_readers(client: JiraClient, cfg: dict, project: str, seen: dict) -> list[str]:
    """Who may read a project's issues that have no security level: the whole org for a project anyone with Jira access
    may browse, on a site its admin confirmed is the company's own; otherwise the people Jira names."""
    if cfg.get("company_site") and client.open_to_everyone(project):
        return [f"org:{config.ORG_SLUG}"]
    return readers_of(client.browsers(project=project), seen)


def restricted(issue: dict) -> bool:
    return bool((issue.get("fields") or {}).get("security"))


def live(client: JiraClient, cfg: dict, doc: dict) -> tuple[list[str], list[tuple[str | None, str]]]:
    """A pointer issue as Jira holds it now (F11a): its readers, worked out as a sync works them out, and its sections. An
    issue Jira moved (it answers under another key, perhaps in a project the connection leaves out) reaches nobody."""
    key = doc["external_key"][len("jira-"):]
    issue = client.issue(key)
    project = str(issue.get("key") or "").rsplit("-", 1)[0]
    if issue.get("key") != key or project != doc["container_ref"]:  # moved: reaches nobody until a sync says otherwise
        return [], []
    readers = readers_of(client.browsers(issue=issue["key"]), {}) if restricted(issue) else project_readers(client, cfg, project, {})
    return readers, issue_sections(issue)[1]


def search(client: JiraClient, cfg: dict, words: list[str]) -> list[str]:
    """F11b: the keys of the issues Jira's search finds, within the connection's projects."""
    return [f"jira-{key}" for key in client.search_text(words, cfg.get("projects"))]


def minutes_since(cursor: str | None) -> int | None:
    """Whole minutes since a cursor (an ISO time), or None for no cursor, or one that isn't a time."""
    try:
        then = datetime.fromisoformat(cursor) if cursor else None
    except ValueError:
        return None
    if then is None or then.tzinfo is None:
        return None
    return max(0, int((datetime.now(timezone.utc) - then).total_seconds() // 60)) + 1


def sync_project(conn, connection: dict, client: JiraClient, project: str, since: str | None = None) -> dict:
    """A project's issues: all of them, or (since a cursor, H6) only those updated since; the cursor for the next
    one is when this listing began."""
    started = datetime.now(timezone.utc).isoformat()
    cfg = connection["config"] or {}
    seen: dict[str, dict] = {}  # the accounts Jira named, as it reports them
    everyone_else = project_readers(client, cfg, project, seen)
    pointer = storage_of(connection) == "pointer"  # F11a: attachments aren't read live, so a pointer connection leaves them out
    items = []
    for issue in client.issues(project, since=since):
        acl = readers_of(client.browsers(issue=issue["key"]), seen) if restricted(issue) else everyone_else
        items.append(Item(key=f"jira-{issue['key']}", uri=f"{client.base}/browse/{issue['key']}",
                          version=str((issue.get("fields") or {}).get("updated") or issue.get("id") or ""),
                          title=f"{issue['key']}: {(issue.get('fields') or {}).get('summary') or ''}",
                          path=issue["key"], acl=acl, sections=lambda issue=issue: issue_sections(issue)))
        for a in [] if pointer else ((issue.get("fields") or {}).get("attachment") or [])[:MAX_PER_PAGE]:  # Q7: the issue's readers
            aid, content = str(a.get("id") or ""), str(a.get("content") or "")
            if not ATTACHMENT_ID.fullmatch(aid) or not content.startswith(f"{client.base}/rest/api/3/attachment/content/"):
                continue
            item = attachment_item(f"jira-att-{aid}", f"{client.base}/browse/{issue['key']}", aid, str(a.get("filename") or ""), a.get("size"),
                                   f"{issue['key']}: {(issue.get('fields') or {}).get('summary') or ''}", f"{client.base}/browse/{issue['key']}",
                                   issue["key"], acl, lambda aid=aid: client.download(aid))
            if item:
                items.append(item)
    if seen:
        record_identities(conn, connection["org_id"], "atlassian", seen)
    return sync_container(conn, connection, project, "jira", items, complete=minutes_since(since) is None, cursor=started)
