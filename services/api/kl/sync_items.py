"""One sync loop for every connector that hands over items (the connector contract of ADR 0011, in-process for now).

A connector lists a container's items: each with Aerugo's key for it, where people open it, a version that changes
whenever its content does, and its readers, already mapped from the source and failing closed. This loop does the
rest, the same way for every source: content is fetched and indexed only when its version changed; readers are
re-checked on every sync, because sharing changes without edits; the authorization store gets only the differences;
and anything the source no longer has, or no longer lets anyone read, is removed with its grants."""
from dataclasses import dataclass
from typing import Callable

from . import shutdown
from .authz import fga
from .sync_github import leave_former_teams, team_for


@dataclass
class Item:
    key: str            # unique across sources, like jira-EDP-412
    uri: str            # where people open it
    version: str        # changes whenever its content does
    title: str
    path: str           # where it lives in its source, for people
    acl: list[str]      # its readers, mapped from the source, failing closed
    sections: Callable[[], tuple[str | None, list[tuple[str | None, str]]]]  # (title, [(heading, body)]), fetched lazily


def viewer_tuples(key: str, acl: list[str]) -> set:
    """A source identity reaches a person only through the identity they've linked; a person is themselves (Notion's
    team readers, S6); everything else is membership."""
    return {(f"identity:{p[4:]}#owner" if p.startswith("ext:") else p if p.startswith("user:") else f"{p}#member", "viewer",
             f"document:{key}") for p in acl}


def sync_container(conn, connection: dict, container: str, source_kind: str, items: list[Item],
                   classification: str = "internal") -> dict:
    """A connection's documents are its own (migration 0040): container refs and keys are unique per site, not per org,
    so another connection's same-named container or same key is never read, updated, or deleted here.

    With `readers_only` on the connection (S8's readers refresh), no body is read: an item already indexed gets its
    current readers (whatever its version), one nobody may read now, or gone from the source, leaves, and a new item
    waits for the next content sync."""
    org_id, team = connection["org_id"], team_for(connection, container)
    readers_only = bool(connection.get("readers_only"))
    existing = {r["external_key"]: r for r in conn.execute(
        "SELECT id, external_key, content_hash FROM documents WHERE source_kind = %s AND container_ref = %s AND connection_id = %s",
        (source_kind, container, connection["id"]))}
    keep, changed, unreadable, writes, deletes = set(), 0, [], set(), set()
    for item in shutdown.each(items):  # a worker asked to stop stops between items (C1)
        if not item.acl:  # nobody may read it: it isn't indexed at all
            continue
        prev = existing.get(item.key)
        if readers_only and not prev:
            continue
        with conn.transaction():
            if not readers_only and (not prev or prev["content_hash"] != item.version):
                try:
                    title, sections = item.sections()
                except Exception as e:  # one unreadable item never stops the rest
                    unreadable.append({"item": item.path[:200], "reason": str(e)[:200]})
                    continue
                row = conn.execute(
                    """INSERT INTO documents (org_id, source_kind, source_uri, source_version, external_key, title,
                                              content_hash, classification, acl_principals, container_ref, source_path,
                                              connection_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (org_id, external_key) DO UPDATE SET
                         source_uri = EXCLUDED.source_uri, source_version = EXCLUDED.source_version, title = EXCLUDED.title,
                         content_hash = EXCLUDED.content_hash, acl_principals = EXCLUDED.acl_principals, acl_synced_at = now()
                       WHERE documents.connection_id = EXCLUDED.connection_id AND documents.container_ref = EXCLUDED.container_ref
                       RETURNING id""",
                    (org_id, source_kind, item.uri, item.version, item.key, (title or item.title)[:300], item.version,
                     classification, item.acl, container, item.path[:500], connection["id"])).fetchone()
                if not row:  # the key belongs to another connection, or another container: never take it over
                    continue
                doc_id = row["id"]
                conn.execute("DELETE FROM chunks WHERE document_id = %s", (doc_id,))
                for ordinal, (heading, body) in enumerate(sections):
                    conn.execute("INSERT INTO chunks (org_id, document_id, ordinal, heading_path, body, acl_principals) "
                                 "VALUES (%s, %s, %s, %s, %s, %s)", (org_id, doc_id, ordinal, heading, body, item.acl))
                changed += 1
            else:  # the same content; its readers may have changed
                doc_id = prev["id"]
                conn.execute("UPDATE documents SET acl_principals = %s, acl_synced_at = now() WHERE id = %s", (item.acl, doc_id))
                conn.execute("UPDATE chunks SET acl_principals = %s WHERE document_id = %s", (item.acl, doc_id))
                conn.execute("UPDATE chunk_embeddings SET acl_principals = %s "
                             "WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = %s)", (item.acl, doc_id))
            conn.execute("UPDATE concepts SET acl_principals = %s WHERE document_id = %s", (item.acl, doc_id))
            conn.execute("INSERT INTO workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, %s) "
                         "ON CONFLICT DO NOTHING", (team, doc_id, org_id))
        keep.add(item.key)
        want = viewer_tuples(item.key, item.acl)
        have = {(t["user"], t["relation"], t["object"]) for t in fga.read({"object": f"document:{item.key}"})}
        writes |= want - have
        deletes |= have - want
    gone = [(k, r["id"]) for k, r in existing.items() if k not in keep]  # deleted, or no longer readable by anyone
    with conn.transaction():
        if gone:
            conn.execute("DELETE FROM documents WHERE id = ANY(%s)", ([doc for _, doc in gone],))
        leave_former_teams(conn, connection, container, team)  # a container routed to another team moves there
        conn.execute("UPDATE connection_containers SET cursor = now()::text WHERE connection_id = %s AND external_id = %s",
                     (connection["id"], container))
    for key, _ in gone:
        deletes |= {(t["user"], t["relation"], t["object"]) for t in fga.read({"object": f"document:{key}"})}
    if writes or deletes:
        fga.write(writes=writes, deletes=deletes)
    return {"container": container, "files": len(items), "indexed": len(keep), "changed": changed, "removed": len(gone),
            "unreadable": unreadable[:20]}


def retire_unlisted(conn, connection: dict, source_kind: str, containers: list[str]) -> int:
    """A connection's documents in a container it no longer reads (taken out of its scope, one its account lost, a
    Slack channel its bot left) go, authorization first: a container nothing syncs would keep its readers frozen."""
    rows = conn.execute("SELECT id, external_key FROM documents WHERE connection_id = %s AND source_kind = %s "
                        "AND NOT (container_ref = ANY(%s))", (connection["id"], source_kind, containers)).fetchall()
    if not rows:
        return 0
    held = {(t["user"], t["relation"], t["object"]) for r in rows for t in fga.read({"object": f"document:{r['external_key']}"})}
    if held:
        fga.write(deletes=held)
    with conn.transaction():
        conn.execute("DELETE FROM documents WHERE id = ANY(%s)", ([r["id"] for r in rows],))
    return len(rows)


def record_identities(conn, org_id, source: str, people: dict[str, dict]) -> int:
    """The source's people as the connector saw them (ADR 0011): {id as in ext:<source>.<id>: {"email", "name"}}, so
    identity matching can link each one to whoever signs in with the same verified company email. Only what the
    source itself reports to the connection's account is recorded."""
    with conn.transaction():
        for external_id, p in people.items():
            email = (p.get("email") or "").strip().lower() or None
            conn.execute(
                """INSERT INTO source_identities (org_id, source, external_id, email, display_name)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (org_id, source, external_id) DO UPDATE
                   SET email = EXCLUDED.email, display_name = EXCLUDED.display_name, seen_at = now()""",
                (org_id, source, external_id[:200], email[:254] if email else None, (p.get("name") or "")[:200]))
    return len(people)
