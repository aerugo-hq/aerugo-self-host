"""GitHub content sync with mirrored permissions (M1 slice 2).

For each selected repo: read the default branch head, sync its Markdown pinned to that commit,
and mirror who GitHub lets read the repo. Content and permissions land in the same step, so a
document is never visible to anyone GitHub wouldn't show it to.

Permissions are mirrored twice, on purpose:
  - Postgres ACL arrays (ext:github.<login>) pre-filter every query via row-level security.
  - OpenFGA tuples (container viewer <- identity:github.<login>#owner, document parent <- container)
    back the post-check. If the two ever disagree, the post-check drops the document.
"""
import hashlib
import re

from .authz import fga
from .extract import BINARY_FORMATS, ExtractionError, extract, supported
from .knowledge import index_bundle
from .markdown import parse
from .principals import is_valid

MARKDOWN = (".md", ".markdown")
MAX_BYTES = 512 * 1024             # Markdown and HTML
MAX_BINARY_BYTES = 10 * 1024 * 1024  # Word, PowerPoint, PDF


def repo_key(full_name: str) -> str:
    return "gh-repo-" + hashlib.sha256(full_name.lower().encode()).hexdigest()[:24]


def doc_key(full_name: str, path: str) -> str:
    return "gh-" + hashlib.sha256(f"{full_name.lower()}/{path}".encode()).hexdigest()[:32]


def reconcile(desired: set, existing: set) -> tuple[int, int]:
    writes, deletes = desired - existing, existing - desired
    fga.write(writes=writes, deletes=deletes)
    return len(writes), len(deletes)


def _tuples(tuple_key: dict) -> set:
    return {(t["user"], t["relation"], t["object"]) for t in fga.read(tuple_key)}


def team_for(connection: dict, full_name: str):
    """The team a repo's documents belong to: the one an admin routed it to (slice F1), else the connection's own."""
    return ((connection.get("config") or {}).get("teams") or {}).get(full_name) or connection["workspace_id"]


def leave_former_teams(conn, connection: dict, full_name: str, team) -> int:
    """A routed repo moves: its documents leave the teams this connection placed them in before (its own team, and
    any team the repo was routed to earlier). Another connection's placements of the same documents stay, and another
    connection's own documents (migration 0040: a same-named container on another site) are never moved."""
    former = {str(connection["workspace_id"]),
              *map(str, ((connection.get("config") or {}).get("former_teams") or {}).get(full_name, []))}
    former.discard(str(team))
    if not former:
        return 0
    return conn.execute("DELETE FROM workspace_documents wd USING documents d WHERE wd.document_id = d.id "
                        "AND d.container_ref = %s AND (d.connection_id IS NULL OR d.connection_id = %s) "
                        "AND wd.workspace_id = ANY(%s::uuid[])", (full_name, connection["id"], sorted(former))).rowcount


LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


def authors_of(commits: list[dict]) -> list[tuple[str, int, str | None]]:
    """A file's authors from its commits: (GitHub login, commits, the latest one's date), most commits first. A commit
    whose author isn't a GitHub account names no one."""
    counts: dict[str, list] = {}
    for c in commits or []:
        login = str((c.get("author") or {}).get("login") or "")
        if LOGIN.fullmatch(login) and not login.endswith("[bot]"):
            at = ((c.get("commit") or {}).get("author") or {}).get("date")
            held = counts.setdefault(login, [0, None])
            held[0] += 1
            held[1] = max(filter(None, (held[1], at)), default=None)
    return sorted(((login, n, at) for login, (n, at) in counts.items()), key=lambda a: (-a[1], a[0]))


def record_authors(conn, org_id, doc_id, connector, full_name: str, path: str, commit: str) -> int:
    """Who wrote a file, read again only when it changed since they were last read."""
    if conn.execute("SELECT 1 FROM document_authors WHERE document_id = %s AND at_commit = %s LIMIT 1", (doc_id, commit)).fetchone():
        return 0
    authors = authors_of(connector.file_commits(full_name, path, commit))
    with conn.transaction():
        conn.execute("DELETE FROM document_authors WHERE document_id = %s", (doc_id,))
        conn.cursor().executemany("INSERT INTO document_authors (org_id, document_id, login, commits, last_at, at_commit) VALUES (%s, %s, %s, %s, %s, %s)",
                                  [(org_id, doc_id, login, n, at, commit) for login, n, at in authors])
    return len(authors)


def sync_repo(conn, connection: dict, connector, container: dict) -> dict:
    full_name, branch = container["external_id"], container["default_ref"] or "main"
    org_id, workspace_id = connection["org_id"], team_for(connection, full_name)
    commit = connector.head_commit(full_name, branch)
    files = [e for e in connector.tree(full_name, commit)
             if e.get("type") == "blob" and supported(e["path"])
             and e.get("size", 0) <= (MAX_BINARY_BYTES if e["path"].lower().endswith(BINARY_FORMATS) else MAX_BYTES)]
    logins = [l for l in connector.collaborators(full_name) if is_valid(f"ext:github.{l}")]
    acl = sorted(f"ext:github.{l}" for l in logins)
    if is_knowledge_repo(full_name):  # an OKF bundle: curated concepts, not evidence (slice C2b)
        return sync_knowledge(conn, connection, connector, full_name, commit, files, logins, acl)
    keep, changed, unreadable = set(), 0, []

    with conn.transaction():
        existing = {r["external_key"]: r for r in conn.execute(
            "SELECT id, external_key, content_hash FROM documents WHERE source_kind = 'github' AND container_ref = %s",
            (full_name,))}
        for f in files:
            key = doc_key(full_name, f["path"])
            keep.add(key)
            uri = f"https://github.com/{full_name}/blob/{commit}/{f['path']}"
            prev = existing.get(key)
            extracted = None
            if not (prev and prev["content_hash"] == f["sha"]):  # new or changed: extract it (slice L)
                try:
                    extracted = extract(f["path"], connector.blob_bytes(full_name, f["sha"]),
                                        fallback_title=f["path"].rsplit("/", 1)[-1])
                except ExtractionError as e:
                    unreadable.append({"path": f["path"], "reason": str(e)})  # reported, never indexed as empty
                    if not prev:
                        keep.discard(key)
                        continue
            if extracted is None:  # same blob, or changed but unreadable: keep what's indexed
                doc_id = prev["id"]
                if prev["content_hash"] == f["sha"]:  # same blob: the version pointer moves to this commit
                    conn.execute("UPDATE documents SET source_uri = %s, source_version = %s, acl_principals = %s, "
                                 "acl_synced_at = now() WHERE id = %s", (uri, commit, acl, doc_id))
                else:  # the last good version stays, but its readers still follow the source
                    conn.execute("UPDATE documents SET acl_principals = %s, acl_synced_at = now() WHERE id = %s",
                                 (acl, doc_id))
                conn.execute("UPDATE chunks SET acl_principals = %s WHERE document_id = %s", (acl, doc_id))
                # Embeddings carry their own copy of the ACL for pre-filtered vector search; keep it in step.
                conn.execute("UPDATE chunk_embeddings SET acl_principals = %s "
                             "WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = %s)", (acl, doc_id))
            else:
                title, sections = extracted
                doc_id = conn.execute(
                    """INSERT INTO documents (org_id, source_kind, source_uri, source_version, external_key, title,
                                              content_hash, classification, acl_principals, container_ref, source_path)
                       VALUES (%s, 'github', %s, %s, %s, %s, %s, 'internal', %s, %s, %s)
                       ON CONFLICT (org_id, external_key) DO UPDATE SET
                         source_uri = EXCLUDED.source_uri, source_version = EXCLUDED.source_version,
                         title = EXCLUDED.title, content_hash = EXCLUDED.content_hash,
                         acl_principals = EXCLUDED.acl_principals, acl_synced_at = now()
                       RETURNING id""",
                    (org_id, uri, commit, key, title[:300], f["sha"], acl, full_name, f["path"])).fetchone()["id"]
                conn.execute("DELETE FROM chunks WHERE document_id = %s", (doc_id,))
                for ordinal, (heading, body) in enumerate(sections):
                    conn.execute("INSERT INTO chunks (org_id, document_id, ordinal, heading_path, body, acl_principals) "
                                 "VALUES (%s, %s, %s, %s, %s, %s)", (org_id, doc_id, ordinal, heading, body, acl))
                record_authors(conn, org_id, doc_id, connector, full_name, f["path"], commit)  # Q7: who wrote it
                changed += 1
            # Bronze concepts mirror their source's readers; update them in the same transaction so a
            # revoked reader loses the concept the moment they lose the document.
            conn.execute("UPDATE concepts SET acl_principals = %s WHERE document_id = %s", (acl, doc_id))
            conn.execute("INSERT INTO workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, %s) "
                         "ON CONFLICT DO NOTHING", (workspace_id, doc_id, org_id))
        gone = [r["id"] for k, r in existing.items() if k not in keep]  # deleted or renamed in the repo
        if gone:
            conn.execute("DELETE FROM documents WHERE id = ANY(%s)", (gone,))
        leave_former_teams(conn, connection, full_name, workspace_id)  # a routed repo's documents move (slice F1)
        conn.execute("UPDATE connection_containers SET cursor = %s WHERE connection_id = %s AND external_id = %s",
                     (commit, connection["id"], full_name))

    # Mirror in the authorization engine after the commit. Either order fails safe: until both agree,
    # the post-check denies. Only GitHub-derived grants are managed here; others are left alone.
    rk = repo_key(full_name)
    reconcile({(f"identity:github.{l}#owner", "viewer", f"container:{rk}") for l in logins},
              {t for t in _tuples({"object": f"container:{rk}", "relation": "viewer"})
               if t[0].startswith("identity:github.")})
    reconcile({(f"container:{rk}", "parent", f"document:{key}") for key in keep},
              _tuples({"user": f"container:{rk}", "relation": "parent", "object": "document:"}))
    return {"repo": full_name, "commit": commit[:7], "files": len(files), "changed": changed,
            "removed": len(gone), "readers": len(logins), "unreadable": unreadable[:20]}


def is_knowledge_repo(full_name: str) -> bool:
    """`<workspace>-knowledge` repos hold OKF bundles. They index into their connection's workspace."""
    return full_name.rsplit("/", 1)[-1].lower().endswith("-knowledge")


def sync_knowledge(conn, connection: dict, connector, full_name: str, commit: str, files: list,
                   logins: list, acl: list) -> dict:
    """Index a knowledge repo as an OKF bundle at one commit. GitHub's readers are every concept's
    readers; each concept's x-kl.audience narrows them further at read time."""
    texts = {"/" + f["path"]: connector.blob_text(full_name, f["sha"]) for f in files
             if f["path"].lower().endswith(MARKDOWN)}  # OKF bundles are Markdown
    with conn.transaction():  # a repo synced as evidence before it was a bundle: drop those copies
        conn.execute("DELETE FROM documents WHERE source_kind = 'github' AND container_ref = %s", (full_name,))
    result = index_bundle(conn, org_id=connection["org_id"], workspace_id=team_for(connection, full_name),
                          container_ref=full_name, version=commit, files=texts, acl=acl,
                          uri_for=lambda path: f"https://github.com/{full_name}/blob/{commit}{path}")
    for path in texts:  # Q7: who wrote each curated page, read again only when its file changed
        doc = conn.execute("SELECT id FROM documents WHERE container_ref = %s AND source_uri = %s",
                           (full_name, f"https://github.com/{full_name}/blob/{commit}{path}")).fetchone()
        if doc:
            record_authors(conn, connection["org_id"], doc["id"], connector, full_name, path.lstrip("/"), commit)
    with conn.transaction():
        leave_former_teams(conn, connection, full_name, team_for(connection, full_name))  # routed repos move (F1)
        keys = [r["external_key"] for r in conn.execute(
            "SELECT external_key FROM documents WHERE container_ref = %s", (full_name,)).fetchall()]
        conn.execute("UPDATE connection_containers SET cursor = %s WHERE connection_id = %s AND external_id = %s",
                     (commit, connection["id"], full_name))
    # Same mirroring as evidence: the repo's GitHub readers view the container; each concept's parent is it.
    rk = repo_key(full_name)
    reconcile({(f"identity:github.{l}#owner", "viewer", f"container:{rk}") for l in logins},
              {t for t in _tuples({"object": f"container:{rk}", "relation": "viewer"})
               if t[0].startswith("identity:github.")})
    reconcile({(f"container:{rk}", "parent", f"document:{k}") for k in keys},
              _tuples({"user": f"container:{rk}", "relation": "parent", "object": "document:"}))
    return {"repo": full_name, "commit": commit[:7], "files": len(files), "concepts": result["concepts"],
            "indexed": result["indexed"], "problems": result["problems"][:20], "readers": len(logins)}
