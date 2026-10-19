"""Policy helpers shared by every API module: workspace membership and admin checks, where to look, the audience
rule, and audit."""
import re

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from . import config
from .auth import Caller
from .authz import fga

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def audit(conn, org_id: str, actor: str, action: str, scope: list[str], details: dict) -> None:
    conn.execute(
        "INSERT INTO audit_events (org_id, actor, action, scope, details) VALUES (%s, %s, %s, %s, %s)",
        (org_id, actor, action, scope, Jsonb(details)),
    )


def memberships(caller: Caller, relation: str = "member") -> set[str]:
    return {obj.split(":", 1)[1] for obj in fga.list_objects(caller.fga_user, relation, "workspace")}


def require_workspace_admin(caller: Caller, ws: str) -> None:
    if not SLUG.fullmatch(ws):
        raise HTTPException(404, "workspace not found")
    if not fga.check(caller.fga_user, "admin", f"workspace:{ws}"):
        raise HTTPException(403, "workspace admin required")


def require_org_admin(caller: Caller) -> None:
    if not fga.check(caller.fga_user, "admin", f"org:{config.ORG_SLUG}"):
        raise HTTPException(403, "org admin required")


def resolve_targets(conn, caller: Caller, scope: str) -> list[str]:
    """Workspaces to look in. Scope is not permission: RLS and the post-check still decide visibility."""
    member_of = memberships(caller)
    rows = conn.execute("SELECT slug, discoverability FROM workspaces").fetchall()
    searchable = {r["slug"] for r in rows if r["discoverability"] == "org" or r["slug"] in member_of}
    if scope == "org":
        return sorted(searchable)
    targets = sorted({s.strip() for s in scope.split(",") if s.strip()})
    if not targets or any(not SLUG.fullmatch(s) or s not in searchable for s in targets):
        raise HTTPException(404, "workspace not found")  # 404, not 403: existence isn't disclosed
    return targets


def audience_allows(caller: Caller, audience: str | None, member_of) -> bool:
    """A curated concept's x-kl.audience, checked on top of its readers (RLS and OpenFGA), so the
    narrower rule wins. Deny by default: an audience this code doesn't understand admits nobody."""
    if audience is None:
        return True
    kind, _, name = audience.partition("/")
    if kind == "org":
        return not name
    if kind == "workspace":
        return name in member_of
    if kind == "group":
        return name in caller.groups
    return False
