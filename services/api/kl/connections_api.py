"""Connection admin API (M1 slice 1). Workspace admins connect a source; the worker discovers
what it can reach. Connecting a repo never widens access: mirrored source permissions decide
who sees its content (slice 3), not the connection."""
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import config
from .auth import Caller, authenticate
from .db import resolve_org_id, scoped
from .github import APP_REF, GitHubNotConfigured, load_app
from .policy import SLUG, audit, require_workspace_admin
from .vault import vault

router = APIRouter(prefix="/v1/workspaces/{ws}")


class GitHubConnectionIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    installation_id: int = Field(gt=0)
    repositories: list[str] | None = Field(default=None, max_length=500)  # None: all the installation grants


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
def create_github_connection(ws: str, body: GitHubConnectionIn, caller: Caller = Depends(authenticate)):
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
             Jsonb({"installation_id": body.installation_id, "repositories": body.repositories}),
             APP_REF, caller.username),
        ).fetchone()
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.discover', %s)",
                     (org_id, Jsonb({"connection_id": str(row["id"])})))
        audit(conn, org_id, caller.username, "connection.create", [ws],
              {"name": body.name, "kind": "github", "installation_id": body.installation_id})
    return {"id": str(row["id"]), "name": row["name"], "kind": row["kind"], "status": row["status"]}


class GoogleConnectionIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    client_id: str = Field(min_length=3, max_length=300)
    client_secret: str = Field(min_length=3, max_length=500, repr=False)
    refresh_token: str = Field(min_length=3, max_length=4000, repr=False)
    domain: str | None = Field(default=None, pattern=r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")  # files shared with it are org-wide
    drives: list[str] | None = Field(default=None, max_length=200)  # shared drive ids; None: every one the account reads
    api_base: str = "https://www.googleapis.com"


@router.post("/connections/google", status_code=201)
def create_google_connection(ws: str, body: GoogleConnectionIn, caller: Caller = Depends(authenticate)):
    """Connect a Google account's shared drives to a team (slice F2, ADR 0008): an OAuth client and a refresh token for an
    account that can read them. The secret and token go to the secrets backend; only a reference is kept here. Its
    admins only."""
    require_workspace_admin(caller, ws)
    if body.api_base.rstrip("/") not in config.GOOGLE_API_BASES:
        raise HTTPException(422, "that Google API address isn't allowed here")
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        ws_row = conn.execute("SELECT id FROM workspaces WHERE slug = %s", (ws,)).fetchone()
        if not ws_row:
            raise HTTPException(404, "workspace not found")
        if conn.execute("SELECT 1 FROM connections WHERE workspace_id = %s AND name = %s",
                        (ws_row["id"], body.name)).fetchone():
            raise HTTPException(409, "a connection with that name already exists in this workspace")
        cid = uuid.uuid4()
        ref = vault.put(f"kl/connections/{cid}", {"client_secret": body.client_secret, "refresh_token": body.refresh_token})
        row = conn.execute(
            """INSERT INTO connections (id, org_id, workspace_id, kind, name, config, credential_ref, created_by)
               VALUES (%s, %s, %s, 'google', %s, %s, %s, %s) RETURNING id, name, kind, status""",
            (cid, org_id, ws_row["id"], body.name, Jsonb({"api_base": body.api_base.rstrip("/"), "client_id": body.client_id,
                                                          "domain": body.domain, "drives": body.drives}), ref, caller.username),
        ).fetchone()
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.discover', %s)",
                     (org_id, Jsonb({"connection_id": str(row["id"])})))
        audit(conn, org_id, caller.username, "connection.create", [ws],
              {"name": body.name, "kind": "google", "domain": body.domain, "drives": body.drives})
    return {"id": str(row["id"]), "name": row["name"], "kind": row["kind"], "status": row["status"]}


@router.get("/connections")
def list_connections(ws: str, caller: Caller = Depends(authenticate)):
    require_workspace_admin(caller, ws)
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        rows = conn.execute(
            """SELECT c.name, c.kind, c.status, c.updated_at FROM connections c
               JOIN workspaces w ON w.id = c.workspace_id WHERE w.slug = %s ORDER BY c.name""", (ws,)).fetchall()
    return {"connections": [{**r, "updated_at": r["updated_at"].isoformat()} for r in rows]}


@router.get("/connections/{name}")
def get_connection(ws: str, name: str, caller: Caller = Depends(authenticate)):
    require_workspace_admin(caller, ws)
    if not SLUG.fullmatch(name):
        raise HTTPException(404, "connection not found")
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        c = conn.execute(
            """SELECT c.id, c.name, c.kind, c.status, c.last_error, c.config, c.last_synced_at FROM connections c
               JOIN workspaces w ON w.id = c.workspace_id WHERE w.slug = %s AND c.name = %s""", (ws, name)).fetchone()
        if not c:
            raise HTTPException(404, "connection not found")
        containers = conn.execute(
            """SELECT external_id, container_kind, visibility, default_ref, selected, cursor
               FROM connection_containers WHERE connection_id = %s ORDER BY external_id""", (c["id"],)).fetchall()
        routed = (c["config"] or {}).get("teams") or {}  # repos sent to other teams (slice F1)
        slugs = {str(r["id"]): r["slug"] for r in conn.execute("SELECT id, slug FROM workspaces").fetchall()}
    containers = [{**ct, "team": slugs.get(routed.get(ct["external_id"]), ws)} for ct in containers]
    return {"name": c["name"], "kind": c["kind"], "status": c["status"], "last_error": c["last_error"],
            "installation_id": c["config"].get("installation_id"),
            "last_synced_at": c["last_synced_at"].isoformat() if c["last_synced_at"] else None,
            "containers": containers}


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
            """SELECT c.id FROM connections c JOIN workspaces w ON w.id = c.workspace_id
               WHERE w.slug = %s AND c.name = %s""", (ws, name)).fetchone()
        if not c:
            raise HTTPException(404, "connection not found")
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


def owned_connection(conn, ws: str, name: str):
    c = conn.execute(
        """SELECT c.id, c.workspace_id, c.kind, c.status, c.config, c.credential_ref FROM connections c
           JOIN workspaces w ON w.id = c.workspace_id WHERE w.slug = %s AND c.name = %s""", (ws, name)).fetchone() \
        if SLUG.fullmatch(name) else None
    if not c:
        raise HTTPException(404, "connection not found")
    return c


@router.patch("/connections/{name}")
def update_connection(ws: str, name: str, body: ConnectionPatch, caller: Caller = Depends(authenticate)):
    """Change a connection in place: pause or resume it, choose what it brings in, or rotate a Google account's secret
    and token. Anything that changes what it brings in is discovered and synced again. Secrets are never echoed, and
    the audit records which fields changed, never their values."""
    require_workspace_admin(caller, ws)
    rotating = body.client_secret is not None or body.refresh_token is not None
    if rotating and (body.client_secret is None or body.refresh_token is None):
        raise HTTPException(422, "rotating Google credentials takes both the client secret and the refresh token")
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        c = owned_connection(conn, ws, name)
        if body.repositories is not None and c["kind"] != "github":
            raise HTTPException(422, "only a GitHub connection has repositories")
        if (body.drives is not None or rotating) and c["kind"] != "google":
            raise HTTPException(422, "only a Google connection has drives and a refresh token")
        cfg, ref, status, changed = dict(c["config"] or {}), c["credential_ref"], c["status"], []
        if body.repositories is not None:
            cfg["repositories"] = body.repositories
            changed.append("repositories")
        if body.drives is not None:
            cfg["drives"] = body.drives
            changed.append("drives")
        if rotating:  # a new version of its secret; the connection reads the new one from now on
            ref = vault.put(f"kl/connections/{c['id']}", {"client_secret": body.client_secret, "refresh_token": body.refresh_token})
            changed.append("credentials")
        if body.enabled is False and status != "disabled":
            status = "disabled"
            changed.append("enabled")
        elif body.enabled is True and status == "disabled":
            status = "pending"
            changed.append("enabled")
        conn.execute("UPDATE connections SET config = %s, credential_ref = %s, status = %s, updated_at = now() WHERE id = %s",
                     (Jsonb(cfg), ref, status, c["id"]))
        if status != "disabled" and changed:
            conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.discover', %s)",
                         (org_id, Jsonb({"connection_id": str(c["id"])})))
        audit(conn, org_id, caller.username, "connection.update", [ws], {"name": name, "changed": changed})
    return {"name": name, "kind": c["kind"], "status": status, "changed": changed}


@router.delete("/connections/{name}", status_code=202)
def remove_connection(ws: str, name: str, caller: Caller = Depends(authenticate)):
    """Remove a connection. It stops at once; the worker then retires what it brought in (connection.retire): a document
    only this team held goes, its authorization first, and one another team still holds through its own source only
    loses this team's link. A Google account's secret and token are destroyed."""
    require_workspace_admin(caller, ws)
    org_id = resolve_org_id(config.ORG_SLUG)
    with scoped(org_id, caller.principals) as conn:
        c = owned_connection(conn, ws, name)
        routed = (c["config"] or {}).get("teams") or {}  # repositories it sends to other teams (slice F1)
        containers = [{"external_id": r["external_id"], "workspace_id": routed.get(r["external_id"]) or str(c["workspace_id"])}
                      for r in conn.execute("SELECT external_id FROM connection_containers WHERE connection_id = %s",
                                            (c["id"],)).fetchall()]
        conn.execute("DELETE FROM connections WHERE id = %s", (c["id"],))  # its containers cascade
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.retire', %s)", (org_id, Jsonb({
            "connection_id": str(c["id"]), "kind": c["kind"], "containers": containers,
            "credential_ref": c["credential_ref"] if c["kind"] == "google" else None})))  # GitHub's app is shared
        audit(conn, org_id, caller.username, "connection.remove", [ws], {"name": name, "kind": c["kind"], "containers": len(containers)})
    return {"removed": name, "containers": len(containers)}
