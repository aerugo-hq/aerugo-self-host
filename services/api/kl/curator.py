"""Curator (M3, slice C3b): opens compiler proposals as pull requests in the team's knowledge repo.

A second GitHub App writes; the connector App stays read-only. GitHub shows a PR and its file to every
reader of the repo, so before a PR opens, each reader must be linked to a platform user who can read
every source the draft used. A reader who's unlinked, or linked but not entitled, refuses the PR: the
draft never reaches people its sources would hide it from. PR state is re-read from GitHub on a
schedule (webhooks would only be hints), and every change lands in proposal_events.
"""
import base64

from psycopg.types.json import Jsonb

from . import originals
from .authz import fga
from .connectors import GitHubConnector
from .github import CURATOR_REF, GitHubNotConfigured, load_app

OPENABLE = ("drafted", "refused")


class CuratorRefused(RuntimeError):
    """A PR that must not open. The message is safe to show the admin and to keep in the audit trail."""


def unverified_readers(logins: list[str], links: dict[str, list[str]], can_read) -> list[str]:
    """Repo readers the draft can't be shown to: unlinked, or linked only to users who can't read every
    source. links maps a GitHub login to platform users; can_read(user) checks one user."""
    return sorted(login for login in logins if not any(can_read(user) for user in links.get(login, [])))


def _event(conn, p: dict, kind: str, **detail) -> None:
    conn.execute("INSERT INTO proposal_events (org_id, proposal_id, kind, detail) VALUES (%s, %s, %s, %s)",
                 (p["org_id"], p["id"], kind, Jsonb(detail)))


def _proposal(conn, proposal_id: str) -> dict:
    p = conn.execute("SELECT p.*, w.slug AS ws FROM proposals p JOIN workspaces w ON w.id = p.workspace_id "
                     "WHERE p.id = %s", (proposal_id,)).fetchone()
    if not p:
        raise RuntimeError(f"proposal {proposal_id} not found")
    return p


def _knowledge_repo(conn, ws: str) -> dict | None:
    """The connected <workspace>-knowledge repo, with the read-only connection that syncs it."""
    return conn.execute(
        """SELECT cc.external_id, c.id AS connection_id, c.credential_ref, c.config
           FROM connection_containers cc JOIN connections c ON c.id = cc.connection_id
           WHERE c.kind = 'github' AND lower(split_part(cc.external_id, '/', 2)) = %s
           ORDER BY cc.external_id LIMIT 1""", (f"{ws}-knowledge",)).fetchone()


def _pr_body(p: dict) -> str:
    sources = "\n".join(f"- `{key}`" for key in p["source_keys"])
    return (f"Drafted by the knowledge-layer compiler with `{p['model']}` for the `{p['ws']}` team, at the "
            f"request of {p['created_by']}.\n\nAudience: `{p['audience']}`. Sources:\n{sources}\n\n"
            "Check every claim against its cited source before merging. Merging publishes this as a curated "
            "concept; knowledge CI must pass first.")


def open_pr(conn, proposal_id: str) -> dict:
    p = _proposal(conn, proposal_id)
    if p["status"] not in OPENABLE:
        return {"skipped": f"proposal is {p['status']}", "pr": p["pr_url"]}
    try:
        full, pr = _open(conn, p)
    except (CuratorRefused, GitHubNotConfigured) as e:
        with conn.transaction():
            conn.execute("UPDATE proposals SET status = 'refused' WHERE id = %s", (p["id"],))
            _event(conn, p, "refused", reason=str(e))
        return {"refused": str(e)}
    with conn.transaction():
        conn.execute("UPDATE proposals SET status = 'pr_opened', repo = %s, pr_number = %s, pr_url = %s, "
                     "checked_at = now() WHERE id = %s", (full, pr["number"], pr["html_url"], p["id"]))
        _event(conn, p, "pr_opened", pr=pr["html_url"], number=pr["number"], repo=full)
    return {"pr": pr["html_url"]}


def _open(conn, p: dict) -> tuple[str, dict]:
    repo = _knowledge_repo(conn, p["ws"])
    if not repo:
        raise CuratorRefused(f"no {p['ws']}-knowledge repo is connected")
    full = repo["external_id"]

    # 1. Everyone GitHub shows the PR to must already be entitled to every source.
    reader = GitHubConnector(load_app(repo["credential_ref"]), int(repo["config"]["installation_id"]))
    logins = reader.collaborators(full)
    links: dict[str, list[str]] = {}
    for row in conn.execute("SELECT source, canonical FROM principal_aliases WHERE source = ANY(%s)",
                            ([f"ext:github.{login}" for login in logins],)).fetchall():
        links.setdefault(row["source"].removeprefix("ext:github."), []).append(row["canonical"])
    blocked = unverified_readers(
        logins, links, lambda user: all(fga.check(user, "viewer", f"document:{k}") for k in p["source_keys"]))
    if blocked:
        raise CuratorRefused(f"{len(blocked)} of {len(logins)} reader(s) of {full} aren't linked to someone "
                             "who can read every source")

    # 2. Branch, file, and PR, as the curator App.
    curator = load_app(CURATOR_REF)
    inst = curator.repo_installation(full)
    base = curator.call(inst, "GET", f"/repos/{full}").json()["default_branch"]
    head = curator.call(inst, "GET", f"/repos/{full}/git/ref/heads/{base}").json()["object"]["sha"]
    branch = f"kl/proposal-{str(p['id'])[:8]}"
    curator.call(inst, "POST", f"/repos/{full}/git/refs", allow=(422,),  # 422: the branch exists (a retry)
                 json={"ref": f"refs/heads/{branch}", "sha": head})
    path = f"{p['concept_key']}.md"
    current = curator.call(inst, "GET", f"/repos/{full}/contents/{path}", allow=(404,), params={"ref": branch})
    put = {"message": f"Propose: {p['title']}", "branch": branch,
           "content": base64.b64encode(originals.open_one(p["content"], p["key_ref"]).encode()).decode()}
    if current.status_code == 200:
        put["sha"] = current.json()["sha"]
    curator.call(inst, "PUT", f"/repos/{full}/contents/{path}", json=put)
    r = curator.call(inst, "POST", f"/repos/{full}/pulls", allow=(422,), json={
        "title": f"Proposal: {p['title']}", "head": branch, "base": base, "body": _pr_body(p)})
    if r.status_code == 422:  # already open for this branch (a retry): use that one
        owner = full.split("/", 1)[0]
        found = curator.call(inst, "GET", f"/repos/{full}/pulls", params={"head": f"{owner}:{branch}"}).json()
        if not found:
            r.raise_for_status()
        return full, found[0]
    return full, r.json()


def refresh(conn, proposal_id: str) -> dict:
    """Re-read an open PR from GitHub: record new reviews, and a merge or close."""
    p = _proposal(conn, proposal_id)
    if p["status"] != "pr_opened":
        return {"skipped": f"proposal is {p['status']}"}
    curator = load_app(CURATOR_REF)
    inst = curator.repo_installation(p["repo"])
    pr = curator.call(inst, "GET", f"/repos/{p['repo']}/pulls/{p['pr_number']}").json()
    reviews = curator.call(inst, "GET", f"/repos/{p['repo']}/pulls/{p['pr_number']}/reviews").json()
    seen = {e["detail"].get("review_id") for e in conn.execute(
        "SELECT detail FROM proposal_events WHERE proposal_id = %s AND kind = 'reviewed'", (p["id"],)).fetchall()}
    status = "merged" if pr.get("merged") else "closed" if pr.get("state") == "closed" else "pr_opened"
    with conn.transaction():
        for review in reviews:
            if review["id"] not in seen and review.get("state") in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED"):
                _event(conn, p, "reviewed", review_id=review["id"], state=review["state"].lower(),
                       by=(review.get("user") or {}).get("login"))
        if status != "pr_opened":
            _event(conn, p, status, commit=pr.get("merge_commit_sha"), at=pr.get("merged_at") or pr.get("closed_at"))
        conn.execute("UPDATE proposals SET status = %s, checked_at = now() WHERE id = %s", (status, p["id"]))
        if status == "merged":  # index the merge now rather than at the next scheduled sync
            conn.execute(
                """INSERT INTO jobs (org_id, kind, payload)
                   SELECT c.org_id, 'connection.sync', jsonb_build_object('connection_id', c.id::text, 'reason', 'proposal merged')
                   FROM connection_containers cc JOIN connections c ON c.id = cc.connection_id
                   WHERE cc.external_id = %s LIMIT 1""", (p["repo"],))
    return {"status": status, "reviews": len(reviews)}
