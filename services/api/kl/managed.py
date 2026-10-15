"""What tools manage (slice T2): when a change arrives with the Aerugo-Managed-By header, the API records that tool as
the resource's manager, so every read (and the app) can say "Managed by Terraform", and warn that a change made by
hand will be undone on its next apply. Shared by the API's modules (teams, models, service accounts, connections)."""
import re

from fastapi import Header, HTTPException

MANAGER = re.compile(r"^[a-z][a-z0-9-]{0,29}$")
MANAGED_WORKSPACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")


def managed_header(managed_by: str | None = Header(None, alias="Aerugo-Managed-By"),
                   managed_workspace: str | None = Header(None, alias="Aerugo-Managed-Workspace")) -> tuple | None:
    """The tool making this change, if it says so (Aerugo's Terraform provider always does). A malformed value is
    refused rather than stored."""
    if managed_by is None:
        return None
    tool = managed_by.strip().lower()
    if not MANAGER.fullmatch(tool) or (managed_workspace is not None and not MANAGED_WORKSPACE.fullmatch(managed_workspace)):
        raise HTTPException(422, "Aerugo-Managed-By names a tool, such as terraform; Aerugo-Managed-Workspace, its workspace")
    return tool, managed_workspace


def note_managed(conn, org_id: str, kind: str, key: str, managed: tuple | None, actor: str) -> None:
    """Record the tool that just changed this resource as its manager. A change made without the header leaves any
    manager in place: to the tool it's drift, which its next plan shows."""
    if managed:
        conn.execute("""INSERT INTO managed_resources (org_id, kind, key, manager, workspace, created_by)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (org_id, kind, key) DO UPDATE SET manager = EXCLUDED.manager,
                          workspace = EXCLUDED.workspace, created_by = EXCLUDED.created_by, updated_at = now()""",
                     (org_id, kind, key, managed[0], managed[1], actor))


def forget_managed(conn, kind: str, key: str) -> None:
    conn.execute("DELETE FROM managed_resources WHERE kind = %s AND key = %s", (kind, key))


def managed_info(conn, kind: str, keys) -> dict:
    """Who manages each of these resources, if a tool does: what reads return as managed_by."""
    keys = list(keys)
    if not keys:
        return {}
    rows = conn.execute("SELECT key, manager, workspace, updated_at FROM managed_resources WHERE kind = %s AND key = ANY(%s)",
                        (kind, keys)).fetchall()
    return {r["key"]: {"by": r["manager"], "workspace": r["workspace"], "since": r["updated_at"].isoformat()} for r in rows}
