"""Policy helpers shared by every API module: workspace membership and admin checks, and audit."""
import re

from fastapi import HTTPException
from psycopg.types.json import Jsonb

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
