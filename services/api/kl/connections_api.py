"""Connection admin API (M1 slice 1). Workspace admins connect a source; the worker discovers
what it can reach. Connecting a repo never widens access: mirrored source permissions decide
who sees its content (slice 3), not the connection."""
import re
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import config, confluence, dataplex, datahub, directory, gcp_secrets, gdrive, glue, jira, microsoft365, notion, purview, slack, unity
from .auth import Caller, authenticate
from .db import resolve_org_id, scoped
from .github import APP_REF, GitHubNotConfigured, load_app
from .managed import forget_managed, managed_header, managed_info, note_managed
from .policy import SLUG, audit, require_org_admin, require_workspace_admin
from .vault import vault

router = APIRouter(prefix="/v1/workspaces/{ws}")


class GitHubConnectionIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    installation_id: int = Field(gt=0)
    repositories: list[str] | None = Field(default=None, max_length=500)  # None: all the installation grants
    every_minutes: int | None = Field(default=None, ge=5, le=10080)  # how often it syncs (ADR 0011)


@router.get("/connectors/github/installations")
def github_installations(ws: str, caller: Caller = Depends(authenticate)):
    require_workspace_admin(caller, ws)
    try:
        app = load_app()
        return {"installations": [
            {"installation_id": inst["id"], "account": inst["account"]["login"],
             "repository_selection": inst.get("repository_selection"),
             "repositories": sorted(r["full_name"] for r in app.repositories(inst["id"]))}
            for inst in app.installations()
        ]}
    except GitHubNotConfigured as e:
        raise HTTPException(409, str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"GitHub returned {e.response.status_code}")


@router.post("/connections", status_code=201)
def create_github_connection(ws: str, body: GitHubConnectionIn, caller: Caller = Depends(authenticate),
                             managed: tuple | None = Depends(managed_header)):
    require_workspace_admin(caller, ws)
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        ws_row = conn.execute("SELECT id FROM workspaces WHERE slug = %s", (ws,)).fetchone()
        if not ws_row:
            raise HTTPException(404, "workspace not found")
        if conn.execute("SELECT 1 FROM connections WHERE workspace_id = %s AND name = %s",
                        (ws_row["id"], body.name)).fetchone():
            raise HTTPException(409, "a connection with that name already exists in this workspace")
        row = conn.execute(
            """INSERT INTO connections (org_id, workspace_id, kind, name, config, credential_ref, created_by)
               VALUES (%s, %s, 'github', %s, %s, %s, %s) RETURNING id, name, kind, status""",
            (org_id, ws_row["id"], body.name,
             Jsonb({"installation_id": body.installation_id, "repositories": body.repositories,
                    "every_minutes": body.every_minutes}),
             APP_REF, caller.username),
        ).fetchone()
        note_managed(conn, org_id, "connection", f"{ws}/{body.name}", managed, caller.username)
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.discover', %s)",
                     (org_id, Jsonb({"connection_id": str(row["id"])})))
        audit(conn, org_id, caller.username, "connection.create", [ws],
              {"name": body.name, "kind": "github", "installation_id": body.installation_id})
    return {"id": str(row["id"]), "name": row["name"], "kind": row["kind"], "status": row["status"]}


class GoogleConnectionIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    service_account_key: str | None = Field(default=None, max_length=20000, repr=False)  # its JSON key (ADR 0011)
    credential_ref: str | None = Field(default=None, max_length=300)  # or one the team keeps in OpenBao itself
    client_id: str | None = Field(default=None, min_length=3, max_length=300)  # an OAuth client and a person's refresh
    client_secret: str | None = Field(default=None, min_length=3, max_length=500, repr=False)  # token (ADR 0008): for
    refresh_token: str | None = Field(default=None, min_length=3, max_length=4000, repr=False)  # older connections only
    domain: str | None = Field(default=None, pattern=r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")  # files shared with it are org-wide
    drives: list[str] | None = Field(default=None, max_length=200)  # shared drive ids; None: every one the account reads
    api_base: str = "https://www.googleapis.com"
    every_minutes: int | None = Field(default=None, ge=5, le=10080)  # how often it syncs (ADR 0011)


@router.post("/connections/google", status_code=201)
def create_google_connection(ws: str, body: GoogleConnectionIn, caller: Caller = Depends(authenticate),
                             managed: tuple | None = Depends(managed_header)):
    """Connect Google Drive to a team (ADR 0011): a service account, by its JSON key or a reference to one the team keeps
    in OpenBao, reads the shared drives it's a member of and the folders shared with it. Each file keeps its own
    readers. An OAuth client with a person's refresh token (slice F2, ADR 0008) is still accepted, for older setups.
    Secrets go to the secrets backend; only a reference is kept here. Its admins only."""
    require_workspace_admin(caller, ws)
    if body.api_base.rstrip("/") not in config.GOOGLE_API_BASES:
        raise HTTPException(422, "that Google API address isn't allowed here")
    oauth = body.client_id and body.client_secret and body.refresh_token
    if [bool(body.service_account_key), bool(body.credential_ref), bool(oauth)].count(True) != 1:
        raise HTTPException(422, "give a service account's JSON key, or a credential reference: one, not both")
    if body.credential_ref and not team_secret(ws, body.credential_ref):
        raise HTTPException(422, ref_rule(ws))
    try:
        account = gdrive.service_account_key(body.service_account_key) if body.service_account_key else None
    except ValueError as e:
        raise HTTPException(422, str(e))
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        ws_row = conn.execute("SELECT id FROM workspaces WHERE slug = %s", (ws,)).fetchone()
        if not ws_row:
            raise HTTPException(404, "workspace not found")
        if conn.execute("SELECT 1 FROM connections WHERE workspace_id = %s AND name = %s",
                        (ws_row["id"], body.name)).fetchone():
            raise HTTPException(409, "a connection with that name already exists in this workspace")
        cid = uuid.uuid4()
        ref = (body.credential_ref if body.credential_ref
               else vault.put(f"kl/connections/{cid}", {"service_account": account}) if account  # its email and key only
               else vault.put(f"kl/connections/{cid}", {"client_secret": body.client_secret, "refresh_token": body.refresh_token}))
        row = conn.execute(
            """INSERT INTO connections (id, org_id, workspace_id, kind, name, config, credential_ref, created_by)
               VALUES (%s, %s, %s, 'google', %s, %s, %s, %s) RETURNING id, name, kind, status""",
            (cid, org_id, ws_row["id"], body.name, Jsonb({"api_base": body.api_base.rstrip("/"), "client_id": body.client_id,
                                                          "domain": body.domain, "drives": body.drives, "every_minutes": body.every_minutes,
                                                          "auth": "oauth" if oauth else "service_account"}), ref, caller.username),
        ).fetchone()
        note_managed(conn, org_id, "connection", f"{ws}/{body.name}", managed, caller.username)
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.discover', %s)",
                     (org_id, Jsonb({"connection_id": str(row["id"])})))
        audit(conn, org_id, caller.username, "connection.create", [ws],
              {"name": body.name, "kind": "google", "domain": body.domain, "drives": body.drives})
    return {"id": str(row["id"]), "name": row["name"], "kind": row["kind"], "status": row["status"]}


class JiraConnectionIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    api_base: str = Field(min_length=8, max_length=300)  # https://<site>.atlassian.net
    email: str | None = Field(default=None, min_length=3, max_length=254)  # the service account's, or a dedicated user's
    api_token: str | None = Field(default=None, min_length=8, max_length=4000, repr=False)
    credential_ref: str | None = Field(default=None, max_length=300)  # or a secret the team keeps in OpenBao itself
    projects: list[str] | None = Field(default=None, max_length=500)  # None: every project the account can browse
    company_site: bool = False  # its admin confirms this Jira site is the company's own (ADR 0011)
    every_minutes: int | None = Field(default=None, ge=5, le=10080)  # how often it syncs; None: the stack's default


LABEL = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,99}$")  # Confluence's labels: lowercase, no spaces


class ConfluenceConnectionIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    api_base: str = Field(min_length=8, max_length=300)  # https://<site>.atlassian.net
    email: str | None = Field(default=None, min_length=3, max_length=254)  # the service account's, or a dedicated user's
    api_token: str | None = Field(default=None, min_length=8, max_length=4000, repr=False)
    credential_ref: str | None = Field(default=None, max_length=300)  # or a secret the team keeps itself
    spaces: list[str] | None = Field(default=None, max_length=500)  # None: every space the account can read
    labels: list[str] | None = Field(default=None, max_length=50)  # only pages with one of these labels
    page_trees: list[str] | None = Field(default=None, max_length=100)  # only these pages, and everything under them
    company_site: bool = False  # its admin confirms this Confluence site is the company's own (ADR 0011)
    every_minutes: int | None = Field(default=None, ge=5, le=10080)


def confluence_scope(spaces, labels, page_trees) -> str | None:
    """What's wrong with a Confluence connection's scope, if anything."""
    if spaces and not all(confluence.SPACE_KEY.fullmatch(s) for s in spaces):
        return "space keys are letters, digits, and underscores, like PAY (a personal space's starts with ~)"
    if labels and not all(LABEL.fullmatch(l) for l in labels):
        return "labels are lowercase, with no spaces, like runbook"
    if page_trees and not all(confluence.CONTENT_ID.fullmatch(p) for p in page_trees):
        return "a page tree is named by its root page's id, the number in its address"
    return None


def team_secret(ws: str, ref: str) -> bool:
    """A credential reference must name a secret under the team's own prefix, so no team's admin can point a connection
    at another team's secrets (which the worker would then send to a source of their choosing)."""
    return bool(re.fullmatch(rf"openbao://secret/kl/{re.escape(config.ORG_SLUG)}/{re.escape(ws)}/connections/[a-z0-9][a-z0-9/_-]{{0,150}}", ref)) \
        or gcp_secrets.team_owns(config.ORG_SLUG, ws, ref)  # or one it keeps in GCP Secret Manager


def ref_rule(ws: str) -> str:
    return (f"a credential reference must be under openbao://secret/kl/{config.ORG_SLUG}/{ws}/connections/, or be a GCP Secret "
            f"Manager secret named kl_{config.ORG_SLUG}_{ws}_<name> in a project this deployment allows")


@router.post("/connections/jira", status_code=201)
def create_jira_connection(ws: str, body: JiraConnectionIn, caller: Caller = Depends(authenticate),
                           managed: tuple | None = Depends(managed_header)):
    """Connect a Jira Cloud site to a team (ADR 0011): a service account, or a dedicated user, and its API token. The
    token is either handed over once and kept in the secrets backend, or referenced in the team's own part of it.
    Issues carry Jira's own permissions; the account only sets what's indexed. Its admins only."""
    require_workspace_admin(caller, ws)
    if not jira.allowed_base(body.api_base):
        raise HTTPException(422, "that isn't a Jira Cloud site (https://<site>.atlassian.net)")
    if bool(body.credential_ref) == bool(body.email or body.api_token) or (not body.credential_ref and not (body.email and body.api_token)):
        raise HTTPException(422, "give the account's email and API token, or a credential reference: one, not both")
    if body.credential_ref and not team_secret(ws, body.credential_ref):
        raise HTTPException(422, ref_rule(ws))
    if body.projects and not all(jira.PROJECT.fullmatch(p) for p in body.projects):
        raise HTTPException(422, "project keys are letters and digits, starting with a letter, like EDP")
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        ws_row = conn.execute("SELECT id FROM workspaces WHERE slug = %s", (ws,)).fetchone()
        if not ws_row:
            raise HTTPException(404, "workspace not found")
        if conn.execute("SELECT 1 FROM connections WHERE workspace_id = %s AND name = %s",
                        (ws_row["id"], body.name)).fetchone():
            raise HTTPException(409, "a connection with that name already exists in this workspace")
        cid = uuid.uuid4()
        ref = body.credential_ref or vault.put(f"kl/connections/{cid}", {"email": body.email, "api_token": body.api_token})
        row = conn.execute(
            """INSERT INTO connections (id, org_id, workspace_id, kind, name, config, credential_ref, created_by)
               VALUES (%s, %s, %s, 'jira', %s, %s, %s, %s) RETURNING id, name, kind, status""",
            (cid, org_id, ws_row["id"], body.name, Jsonb({"api_base": body.api_base.rstrip("/"), "projects": body.projects,
                                                          "company_site": body.company_site, "every_minutes": body.every_minutes}),
             ref, caller.username),
        ).fetchone()
        note_managed(conn, org_id, "connection", f"{ws}/{body.name}", managed, caller.username)
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.discover', %s)",
                     (org_id, Jsonb({"connection_id": str(row["id"])})))
        audit(conn, org_id, caller.username, "connection.create", [ws],
              {"name": body.name, "kind": "jira", "site": body.api_base.rstrip("/"), "projects": body.projects,
               "company_site": body.company_site, "credential": "reference" if body.credential_ref else "stored"})
    return {"id": str(row["id"]), "name": row["name"], "kind": row["kind"], "status": row["status"]}


class DirectoryConnectionIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    provider: str = Field(pattern=r"^(okta|google|entra)$")
    api_base: str | None = Field(default=None, min_length=8, max_length=300)  # Okta: https://<org>.okta.com; others: their own
    api_token: str | None = Field(default=None, min_length=8, max_length=4000, repr=False)  # Okta: a read-only admin's
    service_account_key: str | None = Field(default=None, max_length=20000, repr=False)  # Google: the account's JSON key
    admin_email: str | None = Field(default=None, min_length=3, max_length=254)  # Google: the admin it acts as (S2)
    tenant_id: str | None = Field(default=None, max_length=36)  # Entra: the tenant's id (S3)
    client_id: str | None = Field(default=None, max_length=36)  # Entra: the app registration's (client) id
    certificate: str | None = Field(default=None, max_length=20000, repr=False)  # Entra: its private key and certificate (PEM)
    credential_ref: str | None = Field(default=None, max_length=300)
    every_minutes: int | None = Field(default=None, ge=5, le=10080)


def company_admin(email: str | None) -> str | None:
    """The admin a Google directory acts as: a well formed address in one of the company's own domains, lower case."""
    email = (email or "").strip().lower()
    if not directory.EMAIL.fullmatch(email) or email.rsplit("@", 1)[1] not in config.ORG_EMAIL_DOMAINS:
        return None
    return email


@router.post("/connections/confluence", status_code=201)
def create_confluence_connection(ws: str, body: ConfluenceConnectionIn, caller: Caller = Depends(authenticate),
                                 managed: tuple | None = Depends(managed_header)):
    """Connect a Confluence Cloud site to a team (ADR 0011), as Jira is: a service account, or a dedicated user, and its
    API token, handed over once or referenced. Pages carry Confluence's own permissions, space grants narrowed by every
    restriction down the tree; the account only sets what's indexed. Its admins only."""
    require_workspace_admin(caller, ws)
    if not confluence.allowed_base(body.api_base):
        raise HTTPException(422, "that isn't a Confluence Cloud site (https://<site>.atlassian.net)")
    if bool(body.credential_ref) == bool(body.email or body.api_token) or (not body.credential_ref and not (body.email and body.api_token)):
        raise HTTPException(422, "give the account's email and API token, or a credential reference: one, not both")
    if body.credential_ref and not team_secret(ws, body.credential_ref):
        raise HTTPException(422, ref_rule(ws))
    problem = confluence_scope(body.spaces, body.labels, body.page_trees)
    if problem:
        raise HTTPException(422, problem)
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        ws_row = conn.execute("SELECT id FROM workspaces WHERE slug = %s", (ws,)).fetchone()
        if not ws_row:
            raise HTTPException(404, "workspace not found")
        if conn.execute("SELECT 1 FROM connections WHERE workspace_id = %s AND name = %s",
                        (ws_row["id"], body.name)).fetchone():
            raise HTTPException(409, "a connection with that name already exists in this workspace")
        cid = uuid.uuid4()
        ref = body.credential_ref or vault.put(f"kl/connections/{cid}", {"email": body.email, "api_token": body.api_token})
        row = conn.execute(
            """INSERT INTO connections (id, org_id, workspace_id, kind, name, config, credential_ref, created_by)
               VALUES (%s, %s, %s, 'confluence', %s, %s, %s, %s) RETURNING id, name, kind, status""",
            (cid, org_id, ws_row["id"], body.name,
             Jsonb({"api_base": body.api_base.rstrip("/"), "spaces": body.spaces, "labels": body.labels,
                    "page_trees": body.page_trees, "company_site": body.company_site, "every_minutes": body.every_minutes}),
             ref, caller.username),
        ).fetchone()
        note_managed(conn, org_id, "connection", f"{ws}/{body.name}", managed, caller.username)
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.discover', %s)",
                     (org_id, Jsonb({"connection_id": str(row["id"])})))
        audit(conn, org_id, caller.username, "connection.create", [ws],
              {"name": body.name, "kind": "confluence", "site": body.api_base.rstrip("/"), "spaces": body.spaces,
               "labels": body.labels, "page_trees": body.page_trees, "company_site": body.company_site,
               "credential": "reference" if body.credential_ref else "stored"})
    return {"id": str(row["id"]), "name": row["name"], "kind": row["kind"], "status": row["status"]}


class SlackConnectionIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    bot_token: str | None = Field(default=None, min_length=10, max_length=300, repr=False)  # an internal app's xoxb- token
    credential_ref: str | None = Field(default=None, max_length=300)
    channels: list[str] | None = Field(default=None, max_length=500)  # channel ids; None: every channel the bot is in
    company_workspace: bool = False  # a public channel reaches the whole org, on the company's own workspace only
    history_days: int | None = Field(default=None, ge=1, le=3650)  # how far back it reads; a year unless set
    api_base: str = slack.SLACK_API
    every_minutes: int | None = Field(default=None, ge=5, le=10080)


@router.post("/connections/slack", status_code=201)
def create_slack_connection(ws: str, body: SlackConnectionIn, caller: Caller = Depends(authenticate),
                            managed: tuple | None = Depends(managed_header)):
    """Connect a Slack workspace to a team (S4, ADR 0011): an internal Slack app's bot token, handed over once or
    referenced. The channels the bot is in are indexed, each message readable by the channel's own members (the whole
    org for a public channel only on a workspace its admin confirms is the company's own); a channel shared with
    another organization reaches only this workspace's members. Its admins only."""
    require_workspace_admin(caller, ws)
    if not slack.allowed_base(body.api_base):
        raise HTTPException(422, "that Slack API address isn't allowed here")
    if bool(body.bot_token) == bool(body.credential_ref):
        raise HTTPException(422, "give the bot token, or a credential reference: one, not both")
    if body.bot_token and not body.bot_token.startswith("xoxb-"):
        raise HTTPException(422, "that isn't a bot token (xoxb-...): a user token would read as a person")
    if body.channels is not None and not all(slack.CHANNEL.fullmatch(c) for c in body.channels):
        raise HTTPException(422, "channels are named by their ids, like C0123ABCD")
    if body.credential_ref and not team_secret(ws, body.credential_ref):
        raise HTTPException(422, ref_rule(ws))
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        ws_row = conn.execute("SELECT id FROM workspaces WHERE slug = %s", (ws,)).fetchone()
        if not ws_row:
            raise HTTPException(404, "workspace not found")
        if conn.execute("SELECT 1 FROM connections WHERE workspace_id = %s AND name = %s", (ws_row["id"], body.name)).fetchone():
            raise HTTPException(409, "a connection with that name already exists in this workspace")
        cid = uuid.uuid4()
        ref = body.credential_ref or vault.put(f"kl/connections/{cid}", {"bot_token": body.bot_token})
        row = conn.execute(
            """INSERT INTO connections (id, org_id, workspace_id, kind, name, config, credential_ref, created_by)
               VALUES (%s, %s, %s, 'slack', %s, %s, %s, %s) RETURNING id, name, kind, status""",
            (cid, org_id, ws_row["id"], body.name,
             Jsonb({"api_base": body.api_base.rstrip("/"), "channels": body.channels, "company_workspace": body.company_workspace,
                    "history_days": body.history_days, "every_minutes": body.every_minutes}), ref, caller.username),
        ).fetchone()
        note_managed(conn, org_id, "connection", f"{ws}/{body.name}", managed, caller.username)
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.discover', %s)",
                     (org_id, Jsonb({"connection_id": str(row["id"])})))
        audit(conn, org_id, caller.username, "connection.create", [ws],
              {"name": body.name, "kind": "slack", "channels": body.channels, "company_workspace": body.company_workspace,
               "history_days": body.history_days, "credential": "reference" if body.credential_ref else "stored"})
    return {"id": str(row["id"]), "name": row["name"], "kind": row["kind"], "status": row["status"]}


class NotionConnectionIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    token: str | None = Field(default=None, min_length=20, max_length=300, repr=False)  # an internal integration's secret
    credential_ref: str | None = Field(default=None, max_length=300)
    api_base: str = notion.NOTION_API
    every_minutes: int | None = Field(default=None, ge=5, le=10080)


@router.post("/connections/notion", status_code=201)
def create_notion_connection(ws: str, body: NotionConnectionIn, caller: Caller = Depends(authenticate),
                             managed: tuple | None = Depends(managed_header)):
    """Connect Notion live to a team (S6, ADR 0011): an internal integration's token, handed over once or referenced.
    Notion doesn't say who can read a page, so what's shared with the integration is readable by this team's members
    only, as the team holds them, never wider (the whole org never counts). Its admins only."""
    require_workspace_admin(caller, ws)
    if not notion.allowed_base(body.api_base):
        raise HTTPException(422, "that Notion API address isn't allowed here")
    if bool(body.token) == bool(body.credential_ref):
        raise HTTPException(422, "give the integration's token, or a credential reference: one, not both")
    if body.token and not body.token.startswith(("ntn_", "secret_")):
        raise HTTPException(422, "that isn't an internal integration's token (ntn_... or secret_...)")
    if body.credential_ref and not team_secret(ws, body.credential_ref):
        raise HTTPException(422, ref_rule(ws))
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        ws_row = conn.execute("SELECT id FROM workspaces WHERE slug = %s", (ws,)).fetchone()
        if not ws_row:
            raise HTTPException(404, "workspace not found")
        if conn.execute("SELECT 1 FROM connections WHERE workspace_id = %s AND name = %s", (ws_row["id"], body.name)).fetchone():
            raise HTTPException(409, "a connection with that name already exists in this workspace")
        cid = uuid.uuid4()
        ref = body.credential_ref or vault.put(f"kl/connections/{cid}", {"token": body.token})
        row = conn.execute(
            """INSERT INTO connections (id, org_id, workspace_id, kind, name, config, credential_ref, created_by)
               VALUES (%s, %s, %s, 'notion', %s, %s, %s, %s) RETURNING id, name, kind, status""",
            (cid, org_id, ws_row["id"], body.name, Jsonb({"api_base": body.api_base.rstrip("/"), "every_minutes": body.every_minutes}),
             ref, caller.username),
        ).fetchone()
        note_managed(conn, org_id, "connection", f"{ws}/{body.name}", managed, caller.username)
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.discover', %s)",
                     (org_id, Jsonb({"connection_id": str(row["id"])})))
        audit(conn, org_id, caller.username, "connection.create", [ws],
              {"name": body.name, "kind": "notion", "readers": "the team's members",
               "credential": "reference" if body.credential_ref else "stored"})
    return {"id": str(row["id"]), "name": row["name"], "kind": row["kind"], "status": row["status"]}


class MicrosoftConnectionIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    tenant_id: str = Field(max_length=36)
    client_id: str = Field(max_length=36)  # its app registration, holding Sites.Selected
    certificate: str | None = Field(default=None, max_length=20000, repr=False)  # its private key and certificate (PEM)
    credential_ref: str | None = Field(default=None, max_length=300)
    sites: list[str] = Field(min_length=1, max_length=200)  # the sites an admin granted the app, by Graph's site id
    api_base: str = directory.GRAPH
    every_minutes: int | None = Field(default=None, ge=5, le=10080)


@router.post("/connections/microsoft", status_code=201)
def create_microsoft_connection(ws: str, body: MicrosoftConnectionIn, caller: Caller = Depends(authenticate),
                                managed: tuple | None = Depends(managed_header)):
    """Connect Microsoft 365 to a team (S5, ADR 0011): an Entra app registration holding `Sites.Selected`, granted read
    on each site to index by an admin, never tenant-wide, signing in with its certificate. Each site's document
    libraries are indexed; each file keeps its own sharing (people, and Entra groups through an Entra directory). Its
    admins only."""
    require_workspace_admin(caller, ws)
    if not directory.allowed_graph(body.api_base):
        raise HTTPException(422, "that Microsoft Graph address isn't allowed here")
    if not directory.GUID.fullmatch(body.tenant_id) or not directory.GUID.fullmatch(body.client_id):
        raise HTTPException(422, "name the tenant and the app registration by their ids")
    if not all(microsoft365.SITE.fullmatch(s) for s in body.sites):
        raise HTTPException(422, "sites are named by Graph's site id: <tenant>.sharepoint.com,<site collection id>,<web id>")
    if bool(body.certificate) == bool(body.credential_ref):
        raise HTTPException(422, "give the app's certificate (its private key and certificate, PEM), or a credential reference: one, not both")
    try:
        secret = {"certificate": directory.entra_certificate(body.certificate)} if body.certificate else None
    except ValueError as e:
        raise HTTPException(422, str(e))
    if body.credential_ref and not team_secret(ws, body.credential_ref):
        raise HTTPException(422, ref_rule(ws))
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        ws_row = conn.execute("SELECT id FROM workspaces WHERE slug = %s", (ws,)).fetchone()
        if not ws_row:
            raise HTTPException(404, "workspace not found")
        if conn.execute("SELECT 1 FROM connections WHERE workspace_id = %s AND name = %s", (ws_row["id"], body.name)).fetchone():
            raise HTTPException(409, "a connection with that name already exists in this workspace")
        cid = uuid.uuid4()
        ref = body.credential_ref or vault.put(f"kl/connections/{cid}", secret)
        row = conn.execute(
            """INSERT INTO connections (id, org_id, workspace_id, kind, name, config, credential_ref, created_by)
               VALUES (%s, %s, %s, 'microsoft', %s, %s, %s, %s) RETURNING id, name, kind, status""",
            (cid, org_id, ws_row["id"], body.name,
             Jsonb({"api_base": body.api_base.rstrip("/"), "tenant_id": body.tenant_id.lower(), "client_id": body.client_id.lower(),
                    "sites": body.sites, "every_minutes": body.every_minutes}), ref, caller.username),
        ).fetchone()
        note_managed(conn, org_id, "connection", f"{ws}/{body.name}", managed, caller.username)
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.discover', %s)",
                     (org_id, Jsonb({"connection_id": str(row["id"])})))
        audit(conn, org_id, caller.username, "connection.create", [ws],
              {"name": body.name, "kind": "microsoft", "tenant_id": body.tenant_id.lower(), "sites": body.sites,
               "credential": "reference" if body.credential_ref else "stored"})
    return {"id": str(row["id"]), "name": row["name"], "kind": row["kind"], "status": row["status"]}


class CatalogConnectionIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    provider: str = Field(pattern=r"^(dataplex|unity|glue|purview|datahub)$")
    api_base: str | None = Field(default=None, max_length=300)  # the catalog's address; Dataplex's and Glue's are their own
    projects: list[str] | None = Field(default=None, max_length=100)     # Dataplex: the projects it reads
    location: str | None = Field(default=None, max_length=40)            # Dataplex: where lineage is kept (global)
    domain: str | None = Field(default=None, max_length=253)             # Dataplex: the company's domain, as the org
    catalogs: list[str] | None = Field(default=None, max_length=200)     # Unity Catalog: the catalogs it reads (all)
    region: str | None = Field(default=None, max_length=30)              # Glue
    role_arn: str | None = Field(default=None, max_length=600)           # Glue: the role the runner assumes
    databases: list[str] | None = Field(default=None, max_length=500)    # Glue: the databases it reads (all)
    tenant_id: str | None = Field(default=None, max_length=36)           # Purview
    client_id: str | None = Field(default=None, max_length=36)           # Unity's service principal, Purview's app
    collections: list[str] | None = Field(default=None, max_length=200)  # Purview: the collections it reads
    platforms: list[str] | None = Field(default=None, max_length=50)     # DataHub: the platforms it reads
    client_secret: str | None = Field(default=None, min_length=8, max_length=500, repr=False)  # Unity's service principal
    certificate: str | None = Field(default=None, max_length=20000, repr=False)              # Purview's app (PEM)
    token: str | None = Field(default=None, min_length=20, max_length=4000, repr=False)       # DataHub's access token
    credential_ref: str | None = Field(default=None, max_length=300)
    every_minutes: int | None = Field(default=None, ge=5, le=10080)


def catalog_settings(ws: str, body: CatalogConnectionIn) -> tuple[dict, dict | None]:
    """A catalog connection's config and the secret to store (None: none, or the team's own reference), validated for
    its provider. Raises HTTPException(422) on anything that doesn't fit."""
    def fail(why: str):
        raise HTTPException(422, why)
    keyless = body.provider in ("dataplex", "glue")  # read as the runner itself: no secret at all
    secrets = [s for s in (body.client_secret, body.certificate, body.token) if s]
    if keyless and (secrets or body.credential_ref):
        fail("Dataplex and Glue are read as the runner's own identity: give no secret")
    if not keyless and len(secrets) + bool(body.credential_ref) != 1:
        fail("give the catalog's credential, or a credential reference: one, not both")
    if body.credential_ref and not team_secret(ws, body.credential_ref):
        fail(ref_rule(ws))
    cfg: dict = {"provider": body.provider, "every_minutes": body.every_minutes}
    secret = None
    if body.provider == "dataplex":
        if not body.projects or not all(dataplex.PROJECT.fullmatch(p) for p in body.projects):
            fail("name the Google Cloud projects to read, by their ids")
        if body.location and not dataplex.LOCATION.fullmatch(body.location):
            fail("that isn't a Google Cloud location")
        domain = (body.domain or "").strip().lower() or None
        if domain and domain not in config.ORG_EMAIL_DOMAINS:
            fail("the domain must be one of the company's own (KL_ORG_EMAIL_DOMAINS)")
        cfg.update(api_base=(body.api_base or dataplex.GOOGLE).rstrip("/"), projects=body.projects, location=body.location or "global",
                   domain=domain)
        if not dataplex.allowed_base(cfg["api_base"]):
            fail("that Google API address isn't allowed here")
    elif body.provider == "glue":
        if not glue.REGION.fullmatch(body.region or "") or not glue.ROLE.fullmatch(body.role_arn or ""):
            fail("name the AWS region and the IAM role's ARN the runner assumes")
        if body.databases is not None and not all(glue.NAME.fullmatch(d) for d in body.databases):
            fail("Glue's databases are named in lower case: letters, digits, _ and -")
        cfg.update(api_base=(body.api_base or "").rstrip("/"), region=body.region, role_arn=body.role_arn, databases=body.databases)
        if not glue.allowed_base(cfg["api_base"]):
            fail("that AWS address isn't allowed here")
    elif body.provider == "unity":
        if not unity.allowed_base(body.api_base or "") or not directory.GUID.fullmatch(body.client_id or ""):
            fail("name the Databricks workspace's address and the service principal's application id")
        if body.catalogs is not None and not all(unity.NAME.fullmatch(c) for c in body.catalogs):
            fail("catalogs are named by letters, digits, _ and -")
        if body.certificate or body.token:
            fail("Unity Catalog takes the service principal's OAuth secret")
        cfg.update(api_base=body.api_base.rstrip("/"), client_id=body.client_id.lower(), catalogs=body.catalogs)
        secret = {"client_secret": body.client_secret} if body.client_secret else None
    elif body.provider == "purview":
        if not purview.allowed_base(body.api_base or "") or not directory.GUID.fullmatch(body.tenant_id or "") \
                or not directory.GUID.fullmatch(body.client_id or ""):
            fail("name the Purview account's address, the tenant, and the app registration by their ids")
        if not body.collections or not all(purview.COLLECTION.fullmatch(c) for c in body.collections):
            fail("name the collections to read, by their ids")
        if body.client_secret or body.token:
            fail("Purview's app signs in with its certificate, never a secret")
        cfg.update(api_base=body.api_base.rstrip("/"), tenant_id=body.tenant_id.lower(), client_id=body.client_id.lower(),
                   collections=body.collections)
        try:
            secret = {"certificate": directory.entra_certificate(body.certificate)} if body.certificate else None
        except ValueError as e:
            fail(str(e))
    else:  # datahub
        if not datahub.allowed_base(body.api_base or ""):
            fail("that DataHub address isn't allowed here (KL_DATAHUB_API_BASES)")
        if not body.platforms or not all(datahub.PLATFORM.fullmatch(p) for p in body.platforms):
            fail("name the platforms to read, as DataHub names them (bigquery, snowflake)")
        if body.client_secret or body.certificate:
            fail("DataHub takes an access token")
        cfg.update(api_base=body.api_base.rstrip("/"), platforms=body.platforms)
        secret = {"token": body.token} if body.token else None
    return cfg, secret


@router.post("/connections/catalog", status_code=201)
def create_catalog_connection(ws: str, body: CatalogConnectionIn, caller: Caller = Depends(authenticate),
                              managed: tuple | None = Depends(managed_header)):
    """Connect a data catalog to a team (S7): Dataplex, Unity Catalog, Glue, Purview, or DataHub. Metadata only, never
    data: each entry becomes a document only the people the catalog lets read it find, and an asset with its lineage
    and owners for this team. Dataplex and Glue are read as the runner's own identity, which every team shares, so
    only an org admin connects them; any other is the team's admins', with its own credential."""
    require_workspace_admin(caller, ws)
    if body.provider in ("dataplex", "glue"):
        require_org_admin(caller)
    cfg, secret = catalog_settings(ws, body)
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        ws_row = conn.execute("SELECT id FROM workspaces WHERE slug = %s", (ws,)).fetchone()
        if not ws_row:
            raise HTTPException(404, "workspace not found")
        if conn.execute("SELECT 1 FROM connections WHERE workspace_id = %s AND name = %s", (ws_row["id"], body.name)).fetchone():
            raise HTTPException(409, "a connection with that name already exists in this workspace")
        cid = uuid.uuid4()
        ref = vault.put(f"kl/connections/{cid}", secret) if secret else body.credential_ref
        row = conn.execute(
            """INSERT INTO connections (id, org_id, workspace_id, kind, name, config, credential_ref, created_by)
               VALUES (%s, %s, %s, 'catalog', %s, %s, %s, %s) RETURNING id, name, kind, status""",
            (cid, org_id, ws_row["id"], body.name, Jsonb(cfg), ref, caller.username),
        ).fetchone()
        note_managed(conn, org_id, "connection", f"{ws}/{body.name}", managed, caller.username)
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.discover', %s)",
                     (org_id, Jsonb({"connection_id": str(row["id"])})))
        audit(conn, org_id, caller.username, "connection.create", [ws],
              {"name": body.name, "kind": "catalog", "provider": body.provider,
               "credential": "runner" if not ref else "reference" if body.credential_ref else "stored"})
    return {"id": str(row["id"]), "name": row["name"], "kind": row["kind"], "status": row["status"]}


@router.post("/connections/directory", status_code=201)
def create_directory_connection(ws: str, body: DirectoryConnectionIn, caller: Caller = Depends(authenticate),
                                managed: tuple | None = Depends(managed_header)):
    """Connect the company's directory (ADR 0011): who's at the company, by email, and who has left. It indexes no content;
    identity matching refuses an email it says has left, and a leaver's matched identities are revoked as soon as it
    says so. Okta reads with a read-only API token; Google Workspace (S2) with a service account acting as a read-only
    admin through domain-wide delegation; Entra (S3) with an app registration's certificate. Google's and Entra's groups
    reach their members through the files shared with them. Its credential is kept in the secrets backend, or referenced
    in the team's own part of it. A directory decides who has left and who a group reaches, company-wide, so only an
    org admin connects one (S2)."""
    require_workspace_admin(caller, ws)
    require_org_admin(caller)
    extra = {}  # what else its config holds: Google's admin, Entra's tenant and app
    if body.provider == "okta":
        if not body.api_base or not directory.allowed_okta(body.api_base):
            raise HTTPException(422, "that isn't an Okta org (https://<org>.okta.com)")
        if body.service_account_key or body.admin_email or body.certificate or bool(body.api_token) == bool(body.credential_ref):
            raise HTTPException(422, "give a read-only API token, or a credential reference: one, not both")
        api_base, admin, secret = body.api_base.rstrip("/"), None, {"api_token": body.api_token}
    elif body.provider == "entra":
        api_base, admin = (body.api_base or directory.GRAPH).rstrip("/"), None
        if not directory.allowed_graph(api_base):
            raise HTTPException(422, "that Microsoft Graph address isn't allowed here")
        if not directory.GUID.fullmatch(body.tenant_id or "") or not directory.GUID.fullmatch(body.client_id or ""):
            raise HTTPException(422, "name the tenant and the app registration by their ids")
        if body.api_token or body.service_account_key or bool(body.certificate) == bool(body.credential_ref):
            raise HTTPException(422, "give the app's certificate (its private key and certificate, PEM), or a credential reference: one, not both")
        try:
            secret = {"certificate": directory.entra_certificate(body.certificate)} if body.certificate else None
        except ValueError as e:
            raise HTTPException(422, str(e))
        extra = {"tenant_id": body.tenant_id.lower(), "client_id": body.client_id.lower()}
    else:
        api_base = (body.api_base or directory.GOOGLE_ADMIN).rstrip("/")
        if not directory.allowed_google(api_base):
            raise HTTPException(422, "that Google Admin API address isn't allowed here")
        if body.api_token or body.certificate or bool(body.service_account_key) == bool(body.credential_ref):
            raise HTTPException(422, "give the service account's JSON key, or a credential reference: one, not both")
        admin = company_admin(body.admin_email)
        if not admin:
            raise HTTPException(422, "admin_email must be an admin's address in one of the company's domains (KL_ORG_EMAIL_DOMAINS)")
        try:
            secret = {"service_account": gdrive.service_account_key(body.service_account_key)} if body.service_account_key else None
        except ValueError as e:
            raise HTTPException(422, str(e))
    if body.credential_ref and not team_secret(ws, body.credential_ref):
        raise HTTPException(422, ref_rule(ws))
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        ws_row = conn.execute("SELECT id FROM workspaces WHERE slug = %s", (ws,)).fetchone()
        if not ws_row:
            raise HTTPException(404, "workspace not found")
        if conn.execute("SELECT 1 FROM connections WHERE workspace_id = %s AND name = %s", (ws_row["id"], body.name)).fetchone():
            raise HTTPException(409, "a connection with that name already exists in this workspace")
        cid = uuid.uuid4()
        ref = body.credential_ref or vault.put(f"kl/connections/{cid}", secret)
        row = conn.execute(
            """INSERT INTO connections (id, org_id, workspace_id, kind, name, config, credential_ref, created_by)
               VALUES (%s, %s, %s, 'directory', %s, %s, %s, %s) RETURNING id, name, kind, status""",
            (cid, org_id, ws_row["id"], body.name,
             Jsonb({"provider": body.provider, "api_base": api_base, "every_minutes": body.every_minutes,
                    **({"admin_email": admin} if admin else {}), **extra}), ref, caller.username),
        ).fetchone()
        note_managed(conn, org_id, "connection", f"{ws}/{body.name}", managed, caller.username)
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.discover', %s)",
                     (org_id, Jsonb({"connection_id": str(row["id"])})))
        audit(conn, org_id, caller.username, "connection.create", [ws],
              {"name": body.name, "kind": "directory", "provider": body.provider, "org": api_base, "admin_email": admin, **extra,
               "credential": "reference" if body.credential_ref else "stored"})
    return {"id": str(row["id"]), "name": row["name"], "kind": row["kind"], "status": row["status"]}


@router.get("/connections")
def list_connections(ws: str, caller: Caller = Depends(authenticate)):
    require_workspace_admin(caller, ws)
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        rows = conn.execute(
            """SELECT c.name, c.kind, c.status, c.updated_at, c.last_synced_at, c.config->>'every_minutes' AS every_minutes
               FROM connections c JOIN workspaces w ON w.id = c.workspace_id WHERE w.slug = %s ORDER BY c.name""", (ws,)).fetchall()
        managers = managed_info(conn, "connection", [f"{ws}/{r['name']}" for r in rows])
    # last_synced_at is also when its readers were last read: every sync re-reads them (ADR 0011).
    return {"connections": [{**r, "updated_at": r["updated_at"].isoformat(),
                             "last_synced_at": r["last_synced_at"].isoformat() if r["last_synced_at"] else None,
                             "every_minutes": int(r["every_minutes"]) if r["every_minutes"] else None,
                             "managed_by": managers.get(f"{ws}/{r['name']}")} for r in rows]}


@router.get("/connections/{name}")
def get_connection(ws: str, name: str, caller: Caller = Depends(authenticate)):
    require_workspace_admin(caller, ws)
    if not SLUG.fullmatch(name):
        raise HTTPException(404, "connection not found")
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        c = conn.execute(
            """SELECT c.id, c.name, c.kind, c.status, c.last_error, c.config, c.credential_ref, c.last_synced_at FROM connections c
               JOIN workspaces w ON w.id = c.workspace_id WHERE w.slug = %s AND c.name = %s""", (ws, name)).fetchone()
        if not c:
            raise HTTPException(404, "connection not found")
        containers = conn.execute(
            """SELECT external_id, container_kind, visibility, default_ref, selected, cursor
               FROM connection_containers WHERE connection_id = %s ORDER BY external_id""", (c["id"],)).fetchall()
        routed = (c["config"] or {}).get("teams") or {}  # repos sent to other teams (slice F1)
        slugs = {str(r["id"]): r["slug"] for r in conn.execute("SELECT id, slug FROM workspaces").fetchall()}
        manager = managed_info(conn, "connection", [f"{ws}/{name}"]).get(f"{ws}/{name}")
    containers = [{**ct, "team": slugs.get(routed.get(ct["external_id"]), ws)} for ct in containers]
    return {"name": c["name"], "kind": c["kind"], "status": c["status"], "last_error": c["last_error"],
            "installation_id": c["config"].get("installation_id"),
            "enabled": c["status"] != "disabled", "managed_by": manager,
            "settings": settings_of(ws, c["config"] or {}, c["credential_ref"]),
            "last_synced_at": c["last_synced_at"].isoformat() if c["last_synced_at"] else None,
            "containers": containers}


# What a connection was declared with, as Terraform reads it back. A credential reference is returned only when it's
# one the team gave (its own secret); a secret Aerugo stored itself is never named.
SETTINGS = ("api_base", "projects", "company_site", "every_minutes", "domain", "drives", "repositories", "provider",
            "spaces", "labels", "page_trees", "admin_email", "tenant_id", "client_id", "channels", "company_workspace",
            "history_days", "sites", "location", "catalogs", "region", "role_arn", "databases", "collections", "platforms")


def settings_of(ws: str, cfg: dict, credential_ref: str | None) -> dict:
    return {**{k: cfg.get(k) for k in SETTINGS},
            "credential_ref": credential_ref if credential_ref and team_secret(ws, credential_ref) else None}


def stored_secret(connection_id, ref: str | None) -> bool:
    """Whether this is the secret Aerugo stored for this very connection (so it may replace or destroy it), known by its
    own path, kl/connections/<id>, and nothing else: never a secret a team keeps, another connection's, or GitHub's app."""
    return bool(ref) and ref.startswith("openbao://") and ref.endswith(f"/kl/connections/{connection_id}")


class RouteIn(BaseModel):
    repository: str = Field(min_length=3, max_length=200)
    workspace: str | None = Field(default=None, max_length=64)  # None: back to the connection's own team


def routed_config(cfg: dict, own: str, repository: str, target: str) -> dict:
    """A connection's config with one repo routed to `target` (its own team means no route), remembering the team
    the repo leaves, so its next sync takes the repo's documents out of there."""
    teams = dict(cfg.get("teams") or {})
    former = {repo: list(t) for repo, t in (cfg.get("former_teams") or {}).items()}
    previous = teams.get(repository, own)
    if previous != target:
        former[repository] = sorted(set(former.get(repository, [])) | {previous})
    if target == own:
        teams.pop(repository, None)
    else:
        teams[repository] = target
    return {**cfg, "teams": teams, "former_teams": former}


@router.put("/connections/{name}/routes")
def route_repository(ws: str, name: str, body: RouteIn, caller: Caller = Depends(authenticate)):
    """Send one repo's documents to another team instead of the connection's (slice F1), or back with no team. Putting
    content in a team takes that team's say, so the caller must administer both. The documents move on the next sync,
    which this queues; who may read them still comes from GitHub."""
    require_workspace_admin(caller, ws)
    if body.workspace:
        require_workspace_admin(caller, body.workspace)
    if not SLUG.fullmatch(name):
        raise HTTPException(404, "connection not found")
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        c = conn.execute("""SELECT c.id, c.workspace_id, c.config FROM connections c JOIN workspaces w ON w.id = c.workspace_id
                            WHERE w.slug = %s AND c.name = %s""", (ws, name)).fetchone()
        if not c:
            raise HTTPException(404, "connection not found")
        if not conn.execute("SELECT 1 FROM connection_containers WHERE connection_id = %s AND external_id = %s",
                            (c["id"], body.repository)).fetchone():
            raise HTTPException(404, "that repository isn't in this connection")
        target = str(c["workspace_id"])
        if body.workspace:
            row = conn.execute("SELECT id FROM workspaces WHERE slug = %s", (body.workspace,)).fetchone()
            if not row:
                raise HTTPException(404, "team not found")
            target = str(row["id"])
        cfg = routed_config(dict(c["config"] or {}), str(c["workspace_id"]), body.repository, target)
        conn.execute("UPDATE connections SET config = %s, updated_at = now() WHERE id = %s", (Jsonb(cfg), c["id"]))
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.sync', %s)",
                     (org_id, Jsonb({"connection_id": str(c["id"])})))
        audit(conn, org_id, caller.username, "connection.route", [ws] + ([body.workspace] if body.workspace else []),
              {"connection": name, "repository": body.repository, "team": body.workspace or ws})
    return {"repository": body.repository, "team": body.workspace or ws, "queued": True}


@router.post("/connections/{name}/refresh", status_code=202)
def refresh_connection(ws: str, name: str, caller: Caller = Depends(authenticate)):
    """Re-discover the connection's repos, then re-sync their content and permissions."""
    require_workspace_admin(caller, ws)
    if not SLUG.fullmatch(name):
        raise HTTPException(404, "connection not found")
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        c = conn.execute(
            """SELECT c.id, c.kind FROM connections c JOIN workspaces w ON w.id = c.workspace_id
               WHERE w.slug = %s AND c.name = %s""", (ws, name)).fetchone()
        if not c:
            raise HTTPException(404, "connection not found")
        if c["kind"] == "directory":  # a directory decides who has left, company-wide (S2)
            require_org_admin(caller)
        now = conn.execute("SELECT now() AS now").fetchone()["now"]
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.discover', %s)",
                     (org_id, Jsonb({"connection_id": str(c["id"])})))
        audit(conn, org_id, caller.username, "connection.refresh", [ws], {"name": name})
    return {"requested_at": now.isoformat()}


# ------------------------------------------------------------------ changing and removing a connection (slice T2)
class ConnectionPatch(BaseModel):
    enabled: bool | None = None  # pause or resume its syncing
    repositories: list[str] | None = Field(default=None, max_length=500)  # GitHub: the repositories it brings in
    drives: list[str] | None = Field(default=None, max_length=200)        # Google: the shared drives it brings in
    client_secret: str | None = Field(default=None, min_length=3, max_length=500, repr=False)    # Google: a rotation,
    refresh_token: str | None = Field(default=None, min_length=3, max_length=4000, repr=False)  # always both together
    projects: list[str] | None = Field(default=None, max_length=500)  # Jira: the projects it brings in
    spaces: list[str] | None = Field(default=None, max_length=500)    # Confluence: the spaces it brings in
    labels: list[str] | None = Field(default=None, max_length=50)     # Confluence: only pages with one of these
    page_trees: list[str] | None = Field(default=None, max_length=100)  # Confluence: only these trees
    channels: list[str] | None = Field(default=None, max_length=500)  # Slack: the channels it brings in, by id
    sites: list[str] | None = Field(default=None, min_length=1, max_length=200)  # Microsoft 365: the sites it reads
    history_days: int | None = Field(default=None, ge=1, le=3650)     # Slack: how far back it reads
    catalogs: list[str] | None = Field(default=None, max_length=200)    # Unity Catalog: the catalogs it reads
    databases: list[str] | None = Field(default=None, max_length=500)   # Glue: the databases it reads
    collections: list[str] | None = Field(default=None, min_length=1, max_length=200)  # Purview: its collections
    platforms: list[str] | None = Field(default=None, min_length=1, max_length=50)     # DataHub: its platforms
    bot_token: str | None = Field(default=None, min_length=10, max_length=300, repr=False)  # Slack: a rotation
    token: str | None = Field(default=None, min_length=20, max_length=300, repr=False)  # Notion: a rotation
    every_minutes: int | None = Field(default=None, ge=5, le=10080)   # how often it syncs (ADR 0011)
    api_token: str | None = Field(default=None, min_length=8, max_length=4000, repr=False)  # Jira, Okta: a rotation
    email: str | None = Field(default=None, min_length=3, max_length=254)  # Jira: the account, with a new token
    service_account_key: str | None = Field(default=None, max_length=20000, repr=False)  # Google: a rotation
    admin_email: str | None = Field(default=None, min_length=3, max_length=254)  # a Google directory: the admin it acts as
    certificate: str | None = Field(default=None, max_length=20000, repr=False)  # an Entra directory: a rotation


def owned_connection(conn, ws: str, name: str):
    c = conn.execute(
        """SELECT c.id, c.workspace_id, c.kind, c.status, c.config, c.credential_ref FROM connections c
           JOIN workspaces w ON w.id = c.workspace_id WHERE w.slug = %s AND c.name = %s""", (ws, name)).fetchone() \
        if SLUG.fullmatch(name) else None
    if not c:
        raise HTTPException(404, "connection not found")
    return c


@router.patch("/connections/{name}")
def update_connection(ws: str, name: str, body: ConnectionPatch, caller: Caller = Depends(authenticate),
                      managed: tuple | None = Depends(managed_header)):
    """Change a connection in place: pause or resume it, choose what it brings in, or rotate a Google account's secret
    and token. Anything that changes what it brings in is discovered and synced again. Secrets are never echoed, and
    the audit records which fields changed, never their values."""
    require_workspace_admin(caller, ws)
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        c = owned_connection(conn, ws, name)
        provider = (c["config"] or {}).get("provider")
        unity_secret = body.client_secret if c["kind"] == "catalog" else None  # Unity Catalog's service principal
        rotating = c["kind"] != "catalog" and (body.client_secret is not None or body.refresh_token is not None)
        if rotating and (body.client_secret is None or body.refresh_token is None):
            raise HTTPException(422, "rotating Google credentials takes both the client secret and the refresh token")
        google_directory = c["kind"] == "directory" and provider == "google"
        if c["kind"] == "directory" or (c["kind"] == "catalog" and provider in ("dataplex", "glue")):
            require_org_admin(caller)  # a directory decides who has left (S2); these catalogs read as the runner (S7)
        if body.admin_email is not None and not google_directory:
            raise HTTPException(422, "only a Google Workspace directory acts as an admin")
        if body.repositories is not None and c["kind"] != "github":
            raise HTTPException(422, "only a GitHub connection has repositories")
        if (body.drives is not None or rotating) and c["kind"] != "google":
            raise HTTPException(422, "only a Google connection has drives and a refresh token")
        if body.projects is not None and provider == "dataplex" and c["kind"] == "catalog":
            if not body.projects or not all(dataplex.PROJECT.fullmatch(p) for p in body.projects):
                raise HTTPException(422, "name the Google Cloud projects to read, by their ids")
        elif body.projects is not None and (c["kind"] != "jira" or not all(jira.PROJECT.fullmatch(p) for p in body.projects)):
            raise HTTPException(422, "only a Jira connection has projects, keyed like EDP")
        catalog_lists = {"catalogs": ("unity", unity.NAME), "databases": ("glue", glue.NAME), "collections": ("purview", purview.COLLECTION),
                         "platforms": ("datahub", datahub.PLATFORM)}  # S7: each catalog's own list, validated as at creation
        for field, (owner, rule) in catalog_lists.items():
            values = getattr(body, field)
            if values is not None and (c["kind"] != "catalog" or provider != owner or not all(rule.fullmatch(v) for v in values)):
                raise HTTPException(422, f"only a {owner} catalog has {field}, named as it names them")
        if (body.channels is not None or body.history_days is not None or body.bot_token) and c["kind"] != "slack":
            raise HTTPException(422, "only a Slack connection has channels, a history window, and a bot token")
        if body.sites is not None and (c["kind"] != "microsoft" or not all(microsoft365.SITE.fullmatch(s) for s in body.sites)):
            raise HTTPException(422, "only a Microsoft 365 connection has sites, by Graph's site id")
        if body.channels is not None and not all(slack.CHANNEL.fullmatch(ch) for ch in body.channels):
            raise HTTPException(422, "channels are named by their ids, like C0123ABCD")
        if body.bot_token and not body.bot_token.startswith("xoxb-"):
            raise HTTPException(422, "that isn't a bot token (xoxb-...): a user token would read as a person")
        scope = {"spaces": body.spaces, "labels": body.labels, "page_trees": body.page_trees}
        if any(v is not None for v in scope.values()):
            if c["kind"] != "confluence":
                raise HTTPException(422, "only a Confluence connection has spaces, labels, and page trees")
            problem = confluence_scope(body.spaces, body.labels, body.page_trees)
            if problem:
                raise HTTPException(422, problem)
        cfg, ref, status, changed = dict(c["config"] or {}), c["credential_ref"], c["status"], []
        if body.projects is not None:
            cfg["projects"] = body.projects
            changed.append("projects")
        for field in ("channels", "history_days", "sites", "catalogs", "databases", "collections", "platforms"):
            if getattr(body, field) is not None:
                cfg[field] = getattr(body, field)
                changed.append(field)
        for field, value in scope.items():
            if value is not None:
                cfg[field] = value
                changed.append(field)
        if body.every_minutes is not None:
            cfg["every_minutes"] = body.every_minutes
            changed.append("schedule")
        if body.repositories is not None:
            cfg["repositories"] = body.repositories
            changed.append("repositories")
        if body.drives is not None:
            cfg["drives"] = body.drives
            changed.append("drives")
        if body.admin_email is not None:
            cfg["admin_email"] = company_admin(body.admin_email)
            if not cfg["admin_email"]:
                raise HTTPException(422, "admin_email must be an admin's address in one of the company's domains (KL_ORG_EMAIL_DOMAINS)")
            changed.append("admin_email")
        if rotating:  # a new version of its secret; the connection reads the new one from now on
            ref = vault.put(f"kl/connections/{c['id']}", {"client_secret": body.client_secret, "refresh_token": body.refresh_token})
            changed.append("credentials")
        if body.api_token or body.email or body.service_account_key or body.certificate or body.bot_token or body.token \
                or unity_secret:  # a new secret for one Aerugo stored
            if not stored_secret(c["id"], ref):
                raise HTTPException(422, "this connection reads a secret the team keeps (or GitHub's app): rotate it there")
            if unity_secret:
                if provider != "unity":
                    raise HTTPException(422, "only a Unity Catalog connection takes a service principal's secret")
                secret = {"client_secret": unity_secret}
            elif body.token:
                if c["kind"] == "catalog" and provider == "datahub":
                    secret = {"token": body.token}
                elif c["kind"] != "notion" or not body.token.startswith(("ntn_", "secret_")):
                    raise HTTPException(422, "only Notion (ntn_... or secret_...) and DataHub take a token")
                else:
                    secret = {"token": body.token}
            elif body.bot_token:
                secret = {"bot_token": body.bot_token}
            elif body.certificate:
                if not (c["kind"] == "microsoft" or (c["kind"], provider) in (("directory", "entra"), ("catalog", "purview"))):
                    raise HTTPException(422, "only an Entra directory, Microsoft 365, and Purview have a certificate")
                try:
                    secret = {"certificate": directory.entra_certificate(body.certificate)}
                except ValueError as e:
                    raise HTTPException(422, str(e))
            elif body.service_account_key:
                if c["kind"] != "google" and not google_directory:
                    raise HTTPException(422, "only a Google connection has a service account key")
                try:
                    secret = {"service_account": gdrive.service_account_key(body.service_account_key)}
                except ValueError as e:
                    raise HTTPException(422, str(e))
            elif c["kind"] in ("jira", "confluence"):  # an Atlassian account: its email and API token
                held = vault.get(ref)
                secret = {"email": body.email or held.get("email"), "api_token": body.api_token or held.get("api_token")}
            elif c["kind"] == "directory" and body.api_token and not body.email:
                secret = {"api_token": body.api_token}
            else:
                raise HTTPException(422, "a Jira connection takes an email and API token; Okta, an API token")
            ref = vault.put(f"kl/connections/{c['id']}", secret)
            changed.append("credentials")
        if body.enabled is False and status != "disabled":
            status = "disabled"
            changed.append("enabled")
        elif body.enabled is True and status == "disabled":
            status = "pending"
            changed.append("enabled")
        conn.execute("UPDATE connections SET config = %s, credential_ref = %s, status = %s, updated_at = now() WHERE id = %s",
                     (Jsonb(cfg), ref, status, c["id"]))
        if status != "disabled" and set(changed) - {"schedule"}:  # a new cadence alone needs no rediscovery
            conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.discover', %s)",
                         (org_id, Jsonb({"connection_id": str(c["id"])})))
        note_managed(conn, org_id, "connection", f"{ws}/{name}", managed, caller.username)
        audit(conn, org_id, caller.username, "connection.update", [ws], {"name": name, "changed": changed})
    return {"name": name, "kind": c["kind"], "status": status, "changed": changed}


@router.delete("/connections/{name}", status_code=202)
def remove_connection(ws: str, name: str, caller: Caller = Depends(authenticate)):
    """Remove a connection. It stops at once; the worker then retires what it brought in (connection.retire): a document
    only this team held goes, its authorization first, and one another team still holds through its own source only
    loses this team's link. The secret Aerugo stored for it is destroyed; one the team keeps itself is left alone."""
    require_workspace_admin(caller, ws)
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        c = owned_connection(conn, ws, name)
        if c["kind"] == "directory" or (c["kind"] == "catalog" and (c["config"] or {}).get("provider") in ("dataplex", "glue")):
            require_org_admin(caller)  # a directory decides who has left (S2); these catalogs read as the runner (S7)
        routed = (c["config"] or {}).get("teams") or {}  # repositories it sends to other teams (slice F1)
        containers = [{"external_id": r["external_id"], "workspace_id": routed.get(r["external_id"]) or str(c["workspace_id"])}
                      for r in conn.execute("SELECT external_id FROM connection_containers WHERE connection_id = %s",
                                            (c["id"],)).fetchall()]
        conn.execute("DELETE FROM connections WHERE id = %s", (c["id"],))  # its containers cascade
        forget_managed(conn, "connection", f"{ws}/{name}")
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.retire', %s)", (org_id, Jsonb({
            "connection_id": str(c["id"]), "kind": c["kind"], "containers": containers,
            # the secret Aerugo stored for it is destroyed; a team's own secret, and GitHub's shared app, are not
            "credential_ref": c["credential_ref"] if stored_secret(c["id"], c["credential_ref"]) else None})))
        audit(conn, org_id, caller.username, "connection.remove", [ws], {"name": name, "kind": c["kind"], "containers": len(containers)})
    return {"removed": name, "containers": len(containers)}
