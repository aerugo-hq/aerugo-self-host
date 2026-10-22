"""One sync loop for every connector that hands over items (the connector contract of ADR 0011, in-process for now).

A connector lists a container's items: each with Aerugo's key for it, where people open it, a version that changes
whenever its content does, and its readers, already mapped from the source and failing closed. This loop does the
rest, the same way for every source: content is fetched and indexed only when its version changed; readers are
re-checked on every sync, because sharing changes without edits; the authorization store gets only the differences;
and anything the source no longer has, or no longer lets anyone read, is removed with its grants."""
import logging
from dataclasses import dataclass
from typing import Callable

from . import shutdown
from .authz import fga
from .embeddings import CONTEXT, embed, opening_line, passage_text, vector_literal
from .sync_github import leave_former_teams, team_for
from .vault import vault

log = logging.getLogger(__name__)

POINTER_KINDS = ("jira", "confluence", "slack")  # F11a: the sources whose text can be read again live, for each asker
LIVE_KINDS = ("jira", "confluence")  # F11b: and those whose own search can stand in for an index (Slack's needs a person)
# A team's embedding models, as the backfill picks them: its own, else the org's default.
TEAM_MODELS_SQL = """
SELECT m.id, m.provider, m.model, m.dims, m.base_url, m.credential_ref FROM embedding_models m
WHERE m.status = 'active' AND m.org_id = %(org)s
  AND (m.workspace_id = %(team)s OR (m.workspace_id IS NULL AND NOT EXISTS (
       SELECT 1 FROM embedding_models o WHERE o.workspace_id = %(team)s AND o.status = 'active')))
"""
# A connection's documents in a container; a pointer missing an embedding of one of its team's models (one added since,
# or one that failed at its sync) is read again, since nothing else can make it without the text (F11a).
EXISTING_SQL = """
SELECT d.id, d.external_key, d.content_hash, d.storage,
       d.storage = 'pointer' AND EXISTS (
         SELECT 1 FROM chunks c CROSS JOIN unnest(%(models)s::uuid[]) AS m(id)
         WHERE c.document_id = d.id AND NOT EXISTS (
           SELECT 1 FROM chunk_embeddings e WHERE e.chunk_id = c.id AND e.model_id = m.id AND e.context >= %(context)s)) AS unembedded
FROM documents d
WHERE d.source_kind = %(kind)s AND d.container_ref = %(container)s AND d.connection_id = %(connection)s
"""
# Everything derived from a document's text, gone when it becomes a pointer (F11a): its bronze page, the entities and
# references read from it, and the proposals extracted from it (a unit someone confirmed is theirs, and stays), with the
# passes' records, so each runs again should it be indexed again.
FORGET_SQL = (
    "DELETE FROM concepts WHERE document_id = %(doc)s AND tier = 'bronze'",
    "DELETE FROM entity_mentions WHERE document_id = %(doc)s",
    "DELETE FROM entity_scans WHERE document_id = %(doc)s",
    "DELETE FROM reference_links WHERE source_id = %(doc)s",
    "DELETE FROM reference_scans WHERE document_id = %(doc)s",
    "DELETE FROM knowledge_units WHERE created_by IN ('extraction', 'slack-save', 'teams-save') AND status = 'proposed' AND evidence = ARRAY[%(doc)s]::uuid[]",
    "DELETE FROM extraction_state WHERE document_id = %(doc)s",
)
# A save whose message left its document (deleted or edited in the source, or now dropped) goes with it, unless a curator
# confirmed it (C2c, C1b); its exact section is what was saved.
UNSAVED_SQL = """DELETE FROM knowledge_units u WHERE u.created_by IN ('slack-save', 'teams-save') AND u.status = 'proposed'
AND u.evidence = ARRAY[%(doc)s]::uuid[] AND NOT EXISTS (
  SELECT 1 FROM chunks k WHERE k.document_id = %(doc)s AND k.heading_path = u.sections[1] AND k.body = u.body)"""
# What no one confirmed, made from documents the source no longer has, goes with them: a save, and a model's proposal.
ORPHANED_SQL = """DELETE FROM knowledge_units WHERE created_by IN ('extraction', 'slack-save', 'teams-save') AND status = 'proposed'
AND evidence && %s::uuid[]"""


def storage_of(connection: dict) -> str:
    """How a connection keeps what it reads: `pointer` (F11a) only for a source whose text can be read live again, `live`
    (F11b) only for one whose own search can find it."""
    wanted, kind = (connection.get("config") or {}).get("storage"), connection.get("kind")
    if (wanted == "pointer" and kind in POINTER_KINDS) or (wanted == "live" and kind in LIVE_KINDS):
        return wanted
    return "index"


def pointer_vectors(models: list[dict], title: str, sections: list[tuple[str | None, str]]) -> list[tuple[dict, list]]:
    """A pointer document's embeddings, made from its text while it's in memory, the way the backfill makes them (Q2's
    opening line included), so the text itself never has to be kept: [(model, a vector per section)]."""
    opening = opening_line(sections[0][1]) if sections else None
    texts = [passage_text(title, heading, body, opening if i else None) for i, (heading, body) in enumerate(sections)]
    out = []
    for m in models:
        api_key = vault.get(m["credential_ref"]).get("api_key") if m["credential_ref"] else None
        vectors = embed(m["provider"], m["model"], texts, base_url=m["base_url"], api_key=api_key)
        if len(vectors) != len(texts) or any(len(v) != m["dims"] for v in vectors):
            raise RuntimeError(f"{m['model']} returned {len(vectors)} vectors for {len(texts)} sections, or the wrong dimension")
        out.append((m, vectors))
    return out


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
                   classification: str = "internal", complete: bool = True, cursor: str | None = None,
                   removed: tuple[str, ...] | list[str] = ()) -> dict:
    """A connection's documents are its own (migration 0040): container refs and keys are unique per site, not per org,
    so another connection's same-named container or same key is never read, updated, or deleted here.

    With `readers_only` on the connection (S8's readers refresh), no body is read: an item already indexed gets its
    current readers (whatever its version), one nobody may read now, or gone from the source, leaves, and a new item
    waits for the next content sync.

    `complete` says the items are all the container holds (a full read): only then does what isn't listed leave, and
    the container counts as reconciled. An incremental sync (H6) lists only what changed since the container's cursor,
    so it adds and updates, and leaves deletions to the next full read, except the keys a source says are gone
    (`removed`, as Graph's delta does). `cursor` is where the next one starts, in the connector's own terms; a readers
    refresh moves neither.

    With `storage` pointer on the connection (F11a), a document keeps no text: its sections are read into memory,
    embedded there, and its chunks keep neither body nor heading; a document that changes mode is read again."""
    org_id, team = connection["org_id"], team_for(connection, container)
    readers_only, storage = bool(connection.get("readers_only")), storage_of(connection)
    models = conn.execute(TEAM_MODELS_SQL, {"org": org_id, "team": team}).fetchall() if storage == "pointer" else []
    existing = {r["external_key"]: r for r in conn.execute(EXISTING_SQL, {
        "models": [m["id"] for m in models], "context": CONTEXT, "kind": source_kind, "container": container,
        "connection": connection["id"]})}
    keep, changed, unreadable, writes, deletes = set(), 0, [], set(), set()
    for item in shutdown.each(items):  # a worker asked to stop stops between items (C1)
        if not item.acl:  # nobody may read it: it isn't indexed at all
            continue
        prev = existing.get(item.key)
        if readers_only and not prev:
            continue
        # Its content changed, or the connection now keeps it the other way (F11a): read it again, outside any transaction
        rewrite = not readers_only and (not prev or prev["content_hash"] != item.version or prev["storage"] != storage
                                        or prev["unembedded"])
        if rewrite and storage == "live":  # F11b: not even read; the source's own search finds it, and answers read it live
            title, sections, vectors = None, [], []
        elif rewrite:
            try:
                title, sections = item.sections()
            except Exception as e:  # one unreadable item never stops the rest
                unreadable.append({"item": item.path[:200], "reason": str(e)[:200]})
                continue
            try:
                vectors = pointer_vectors(models, title or item.title, sections) if storage == "pointer" else []
            except Exception as e:  # noqa: BLE001 - a model that fails is logged by its kind; the pointer waits for a later sync
                log.warning("embedding %s failed (%s): it's written without, and embedded on a later sync", item.key, type(e).__name__)
                vectors = []
        with conn.transaction():
            if rewrite:
                row = conn.execute(
                    """INSERT INTO documents (org_id, source_kind, source_uri, source_version, external_key, title,
                                              content_hash, classification, acl_principals, container_ref, source_path,
                                              connection_id, storage)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (org_id, external_key) DO UPDATE SET
                         source_uri = EXCLUDED.source_uri, source_version = EXCLUDED.source_version, title = EXCLUDED.title,
                         content_hash = EXCLUDED.content_hash, acl_principals = EXCLUDED.acl_principals, acl_synced_at = now(),
                         storage = EXCLUDED.storage
                       WHERE documents.connection_id = EXCLUDED.connection_id AND documents.container_ref = EXCLUDED.container_ref
                       RETURNING id""",
                    (org_id, source_kind, item.uri, item.version, item.key, (title or item.title)[:300], item.version,
                     classification, item.acl, container, item.path[:500], connection["id"], storage)).fetchone()
                if not row:  # the key belongs to another connection, or another container: never take it over
                    continue
                doc_id = row["id"]
                conn.execute("DELETE FROM chunks WHERE document_id = %s", (doc_id,))
                if storage != "index":  # no text kept, nor anything made from it (a pointer keeps its embeddings)
                    for sql in FORGET_SQL:
                        conn.execute(sql, {"doc": doc_id})
                for ordinal, (heading, body) in enumerate(sections):
                    kept = (heading, body) if storage == "index" else (None, None)
                    chunk = conn.execute("INSERT INTO chunks (org_id, document_id, ordinal, heading_path, body, acl_principals) "
                                         "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                                         (org_id, doc_id, ordinal, *kept, item.acl)).fetchone()["id"]
                    for m, vecs in vectors:
                        conn.execute("INSERT INTO chunk_embeddings (chunk_id, model_id, org_id, acl_principals, embedding, context) "
                                     "VALUES (%s, %s, %s, %s, %s::vector, %s)",
                                     (chunk, m["id"], org_id, item.acl, vector_literal(vecs[ordinal]), CONTEXT))
                conn.execute(UNSAVED_SQL, {"doc": doc_id})
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
    gone = [(k, r["id"]) for k, r in existing.items() if (k not in keep if complete else k in set(removed))]  # deleted, or readable by no one
    with conn.transaction():
        if gone:
            conn.execute(ORPHANED_SQL, ([doc for _, doc in gone],))
            conn.execute("DELETE FROM documents WHERE id = ANY(%s)", ([doc for _, doc in gone],))
        leave_former_teams(conn, connection, container, team)  # a container routed to another team moves there
        if not readers_only:  # a readers refresh reads no content, so it neither moves the cursor nor reconciles
            conn.execute("UPDATE connection_containers SET cursor = COALESCE(%s, now()::text), "
                         "reconciled_at = CASE WHEN %s THEN now() ELSE reconciled_at END WHERE connection_id = %s AND external_id = %s",
                         (cursor, complete, connection["id"], container))
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
        conn.execute(ORPHANED_SQL, ([r["id"] for r in rows],))
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
