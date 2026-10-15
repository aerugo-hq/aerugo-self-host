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

import httpx

from . import config
from .connectors import Container, Health
from .principals import is_valid
from .sync_items import Item, record_identities, sync_container
from .vault import vault

PROJECT = re.compile(r"^[A-Z][A-Z0-9_]{0,49}$")
ISSUE = re.compile(r"^[A-Z][A-Z0-9_]{0,49}-[0-9]{1,9}$")
SITE = re.compile(r"^https://[a-z0-9][a-z0-9-]{0,62}\.atlassian\.net$")  # Jira Cloud
FIELDS = "summary,description,comment,status,assignee,reporter,issuetype,updated,security,labels"
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
        if text:
            sections.append((f"Comment by {name(c.get('author'))} on {str(c.get('created') or '')[:10]}", text))
    return f"{issue['key']}: {f.get('summary') or ''}".strip(), sections


class JiraClient:
    """Jira Cloud's REST API, version 3, with an account's email and API token."""

    def __init__(self, api_base: str, email: str, api_token: str):
        self.base = api_base.rstrip("/")
        auth = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self.headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}

    def _get(self, path: str, **params):
        r = httpx.get(f"{self.base}/rest/api/3/{path}", params={k: v for k, v in params.items() if v is not None},
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

    def issues(self, project: str) -> list[dict]:
        out, token = [], None
        while len(out) < 20000:
            page = self._get("search/jql", jql=f"project = {project} ORDER BY updated DESC", fields=FIELDS,
                             maxResults=100, nextPageToken=token)
            out += [i for i in page.get("issues") or [] if ISSUE.fullmatch(i.get("key") or "")]
            token = page.get("nextPageToken")
            if page.get("isLast", True) or not token:
                return out
        return out

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


def sync_project(conn, connection: dict, client: JiraClient, project: str) -> dict:
    cfg = connection["config"] or {}
    seen: dict[str, dict] = {}  # the accounts Jira named, as it reports them
    everyone = bool(cfg.get("company_site")) and client.open_to_everyone(project)
    project_readers = [f"org:{config.ORG_SLUG}"] if everyone else readers_of(client.browsers(project=project), seen)
    items = []
    for issue in client.issues(project):
        restricted = bool((issue.get("fields") or {}).get("security"))
        acl = readers_of(client.browsers(issue=issue["key"]), seen) if restricted else project_readers
        items.append(Item(key=f"jira-{issue['key']}", uri=f"{client.base}/browse/{issue['key']}",
                          version=str((issue.get("fields") or {}).get("updated") or issue.get("id") or ""),
                          title=f"{issue['key']}: {(issue.get('fields') or {}).get('summary') or ''}",
                          path=issue["key"], acl=acl, sections=lambda issue=issue: issue_sections(issue)))
    if seen:
        record_identities(conn, connection["org_id"], "atlassian", seen)
    return sync_container(conn, connection, project, "jira", items)
