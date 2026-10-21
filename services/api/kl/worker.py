"""Durable job worker: a Postgres queue claimed with FOR UPDATE SKIP LOCKED.

Runs as kl_ingest inside this cell's single org. The org context is set once per session, so
row-level security scopes every query, and a bug can't reach another org's rows."""
import hashlib
import logging
import math
import os
import signal
import threading
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import alerting, capture, catalogs, config, confluence, consolidation, curator, experts, gaps, lifecycle, dataplex, datahub, directory, extraction, gdrive, glue, imports, jira, knowledge, links, manifests, microsoft365, notion, okf, originals, pipelines, privacy, purview, shutdown, slack, telemetry, unity
from .authz import fga
from .connectors import GitHubConnector
from .embeddings import CONTEXT, embed, opening_line, passage_text, vector_literal
from .media import media_for
from .github import load_app
from .sync_github import repo_key, sync_repo
from .sync_items import retire_unlisted
from .vault import vault

INGEST_DB = os.environ.get("KL_INGEST_DATABASE_URL", "postgresql://kl_ingest:ingest-dev-only@postgres:5432/kl")
POLL_SECONDS = float(os.environ.get("KL_WORKER_POLL_SECONDS", "2"))
RESYNC_MINUTES = float(os.environ.get("KL_RESYNC_MINUTES", "10"))      # freshness window per connection
READERS_MINUTES = float(os.environ.get("KL_READERS_REFRESH_MINUTES", "5"))  # how stale a connection's readers may get (S8)
OWNED_KINDS = {"jira", "confluence", "slack", "microsoft", "notion", "catalog"}  # kinds whose documents carry their connection (migration 0040)
# A data catalog's client by its provider (S7), and the source its accounts are recorded under for identity matching
# (Purview's people are Entra's, which the Entra directory records).
CATALOGS = {"dataplex": dataplex, "unity": unity, "glue": glue, "purview": purview, "datahub": datahub}
CATALOG_IDENTITIES = {"dataplex": "google", "unity": "databricks", "glue": "aws", "purview": None, "datahub": "datahub"}


def source_kind_of(c: dict) -> str:
    """What a connection's documents are: a catalog's are its provider's (dataplex, unity, ...), every other its kind."""
    return (c["config"] or {})["provider"] if c["kind"] == "catalog" else c["kind"]
SCHEDULER_SECONDS = float(os.environ.get("KL_SCHEDULER_SECONDS", "60"))  # how often to look for stale ones
FULL_SYNC_HOURS = float(os.environ.get("KL_FULL_SYNC_HOURS", "24"))  # how often a sync reads everything again (H6)

LEASE_SECONDS = float(os.environ.get("KL_JOB_LEASE_SECONDS", "120"))  # how long a job outlives a dead worker (C1)
# M7: how long each kind of job may run, in minutes; any other kind, DEFAULT_LIMIT_MINUTES. A retried sync skips what
# it already indexed (each item's content hash), so a first sync longer than its limit still gets through over its
# attempts. KL_JOB_LIMITS changes any of them ("connection.sync=480,import.apply=180").
DEFAULT_LIMIT_MINUTES = 30.0
LIMIT_MINUTES = {"connection.sync": 240.0, "connection.discover": 60.0, "embeddings.backfill": 240.0, "knowledge.seed": 60.0,
                 "workspace.purge": 120.0, "import.apply": 120.0, "retention.apply": 60.0, "units.extract": 60.0}
OVERRAN_EXIT = 3  # the worker's exit status when a job ran past its limit

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("kl.worker")

# C2d: jobs someone is waiting on run in their own lane (kl.answers_lane), as the API's role, so row-level security
# applies to them as to the API, and a long sync never holds them up. This worker never claims them: it runs as
# kl_ingest, which row-level security doesn't narrow to a person.
LANE_KINDS = ("slack.ask",)
CLAIM_SQL = """
UPDATE jobs SET status = 'running', attempts = attempts + 1, locked_at = now(),
                lease_until = now() + %s * interval '1 second', updated_at = now()
WHERE id = (SELECT id FROM jobs
            WHERE status = 'queued' AND run_after <= now() AND {kinds}
            ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING id, org_id, kind, payload, attempts, max_attempts
"""
WORKER_CLAIM_SQL = CLAIM_SQL.format(kinds="NOT (kind = ANY(%s))")
LANE_CLAIM_SQL = CLAIM_SQL.format(kinds="kind = ANY(%s)")

# C1: a running job's lease. The claim's attempt number fences each run: a worker whose lease ran out and whose job was
# taken over can neither renew it nor settle it, so a run that comes back late changes nothing.
FENCE = " AND attempts = %s AND status = 'running'"
RENEW_SQL = "UPDATE jobs SET lease_until = now() + %s * interval '1 second' WHERE id = %s" + FENCE
# Put back by a stopping worker, at once: the attempt isn't the job's failure, so it doesn't count.
REQUEUE_SQL = "UPDATE jobs SET status = 'queued', attempts = attempts - 1, run_after = now(), updated_at = now() WHERE id = %s" + FENCE
# Jobs whose worker died: the lease ran out (or, for a job claimed before leases, an hour has passed since its claim).
REAP_SQL = """
SELECT id, org_id, kind, payload, attempts, max_attempts FROM jobs
WHERE status = 'running' AND COALESCE(lease_until, locked_at + interval '1 hour') < now()
FOR UPDATE SKIP LOCKED
"""

# Reconciliation: re-sync every ready connection that hasn't synced within the window, unless a
# sync or discover for it is already pending. Webhooks make changes fast; this makes them certain.
SCHEDULE_SQL = """
INSERT INTO jobs (org_id, kind, payload)
SELECT c.org_id, 'connection.sync', jsonb_build_object('connection_id', c.id::text, 'reason', 'schedule')
FROM connections c
WHERE c.status = 'ready'
  AND (c.last_synced_at IS NULL OR c.last_synced_at < now() - (CASE WHEN c.config->>'every_minutes' ~ '^[0-9]{1,5}$'
       THEN (c.config->>'every_minutes')::int ELSE %s END) * interval '1 minute')  -- its own cadence (ADR 0011), else the default
  AND NOT EXISTS (SELECT 1 FROM jobs j
                  WHERE j.kind IN ('connection.sync', 'connection.discover')
                    AND j.status IN ('queued', 'running')
                    AND j.payload->>'connection_id' = c.id::text)
RETURNING payload->>'connection_id' AS connection_id
"""

# S8: a readers refresh for every ready connection whose readers are older than the window, between content syncs,
# unless one of its jobs is already pending. GitHub's and directories' syncs are their readers (GitHub's webhooks and a
# directory's own cadence keep them fresh), and a catalog's readers come with its daily listing.
READERS_KINDS = ("google", "jira", "confluence", "slack", "microsoft", "notion")
READERS_SQL = """
INSERT INTO jobs (org_id, kind, payload)
SELECT c.org_id, 'connection.refresh_readers', jsonb_build_object('connection_id', c.id::text, 'reason', 'schedule')
FROM connections c
WHERE c.status = 'ready' AND c.kind = ANY(%s) AND c.last_synced_at IS NOT NULL
  AND greatest(c.readers_refreshed_at, c.last_synced_at) < now() - %s * interval '1 minute'
  AND NOT EXISTS (SELECT 1 FROM jobs j
                  WHERE j.kind IN ('connection.sync', 'connection.discover', 'connection.refresh_readers')
                    AND j.status IN ('queued', 'running')
                    AND j.payload->>'connection_id' = c.id::text)
RETURNING payload->>'connection_id' AS connection_id
"""

# Open PRs are re-read from GitHub at most once a minute, unless a refresh is already pending.
PROPOSALS_SQL = """
INSERT INTO jobs (org_id, kind, payload)
SELECT p.org_id, 'proposal.refresh', jsonb_build_object('proposal_id', p.id::text)
FROM proposals p
WHERE p.status = 'pr_opened' AND (p.checked_at IS NULL OR p.checked_at < now() - interval '1 minute')
  AND NOT EXISTS (SELECT 1 FROM jobs j
                  WHERE j.kind = 'proposal.refresh' AND j.status IN ('queued', 'running')
                    AND j.payload->>'proposal_id' = p.id::text)
RETURNING payload->>'proposal_id' AS proposal_id
"""


def audit(conn, org_id, action: str, details: dict) -> None:
    conn.execute("INSERT INTO audit_events (org_id, actor, action, scope, details) VALUES (%s, %s, %s, %s, %s)",
                 (org_id, "process:worker", action, [], Jsonb(details)))


# ------------------------------------------------------------------ embeddings (M2)
# Every chunk gets a vector from each workspace's effective model: the workspace override if it has
# one, otherwise the org default. Embeddings copy the chunk's ACL so vector search pre-filters too.
MISSING_EMBEDDINGS_SQL = """
SELECT DISTINCT c.id AS chunk_id, c.org_id, c.body, c.heading_path, c.acl_principals, d.title, c.ordinal,
       (SELECT left(o.body, 600) FROM chunks o WHERE o.document_id = d.id ORDER BY o.ordinal LIMIT 1) AS opening,
       m.id AS model_id, m.provider, m.model, m.dims, m.base_url, m.credential_ref
FROM chunks c
JOIN documents d ON d.id = c.document_id
JOIN workspace_documents wd ON wd.document_id = d.id
JOIN embedding_models m ON m.status = 'active' AND m.org_id = c.org_id
  AND (m.workspace_id = wd.workspace_id
       OR (m.workspace_id IS NULL AND NOT EXISTS (
             SELECT 1 FROM embedding_models o WHERE o.workspace_id = wd.workspace_id AND o.status = 'active')))
LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id AND e.model_id = m.id
WHERE c.body IS NOT NULL  -- a pointer's chunks are embedded at its sync, from text never kept (F11a)
  AND (e.chunk_id IS NULL OR e.context < %s)  -- not embedded yet, or embedded the older way (Q2)
LIMIT 512
"""
EMBED_BATCH = 64


def embed_missing(conn) -> int:
    done = 0
    while True:
        rows = conn.execute(MISSING_EMBEDDINGS_SQL, (CONTEXT,)).fetchall()
        if not rows:
            return done
        by_model: dict = {}
        for r in rows:
            by_model.setdefault(r["model_id"], []).append(r)
        for model_id, group in by_model.items():
            m = group[0]
            api_key = vault.get(m["credential_ref"]).get("api_key") if m["credential_ref"] else None
            for i in range(0, len(group), EMBED_BATCH):
                batch = group[i:i + EMBED_BATCH]
                vectors = embed(m["provider"], m["model"],
                                [passage_text(r["title"], r["heading_path"], r["body"], opening_line(r["opening"]) if r["ordinal"] else None)
                                 for r in batch],
                                base_url=m["base_url"], api_key=api_key)
                if len(vectors) != len(batch) or any(len(v) != m["dims"] for v in vectors):
                    raise RuntimeError(f"{m['model']} returned {len(vectors)} vectors for {len(batch)} chunks, "
                                       f"or the wrong dimension (expected {m['dims']})")
                with conn.transaction():
                    for r, v in zip(batch, vectors):
                        conn.execute("INSERT INTO chunk_embeddings (chunk_id, model_id, org_id, acl_principals, embedding, context) "
                                     "VALUES (%s, %s, %s, %s, %s::vector, %s) ON CONFLICT (chunk_id, model_id) DO UPDATE SET "
                                     "embedding = EXCLUDED.embedding, acl_principals = EXCLUDED.acl_principals, context = EXCLUDED.context",
                                     (r["chunk_id"], model_id, r["org_id"], r["acl_principals"], vector_literal(v), CONTEXT))
                done += len(batch)


def handle_embeddings_backfill(conn, job) -> dict:
    return {"embedded": embed_missing(conn), "bronze": materialize_bronze(conn), **links.refresh(conn),
            "personal_data": privacy.scan(conn)}


# ------------------------------------------------------------------ bronze concepts (slice C1)
BRONZE_DUE_SQL = """
SELECT d.id, d.org_id, d.external_key, d.title, d.source_uri, d.source_version, d.classification,
       d.acl_principals, d.content_hash, c.id AS concept_id, c.source_hash
FROM documents d
LEFT JOIN concepts c ON c.document_id = d.id AND c.tier = 'bronze'
WHERE d.tier = 'evidence'  -- curated documents are concepts already
  AND d.storage = 'index'  -- a pointer keeps no text to make one from (F11a)
  AND (c.id IS NULL OR c.source_hash IS DISTINCT FROM d.content_hash
       OR c.acl_principals IS DISTINCT FROM d.acl_principals)
LIMIT 200
"""


def materialize_bronze(conn) -> int:
    """Turn every new or changed document into a bronze OKF concept. Versions are content-addressed,
    and an ACL change alone updates readers without making a new version (ACLs never go in files)."""
    built = 0
    while rows := conn.execute(BRONZE_DUE_SQL).fetchall():
        for d in rows:
            with conn.transaction():
                if d["concept_id"] and d["source_hash"] == d["content_hash"]:
                    conn.execute("UPDATE concepts SET acl_principals = %s WHERE id = %s",
                                 (d["acl_principals"], d["concept_id"]))
                    continue
                sections = [(r["heading_path"], r["body"]) for r in conn.execute(
                    "SELECT heading_path, body FROM chunks WHERE document_id = %s ORDER BY ordinal", (d["id"],))]
                text = okf.bronze(
                    key=d["external_key"], title=d["title"], resource=d["source_uri"],
                    source_version=d["source_version"], classification=d["classification"], sections=sections,
                    generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                digest = hashlib.sha256(text.encode()).hexdigest()
                concept_id = conn.execute(
                    """INSERT INTO concepts (org_id, concept_key, tier, document_id, title, acl_principals, latest_hash, source_hash)
                       VALUES (%s, %s, 'bronze', %s, %s, %s, %s, %s)
                       ON CONFLICT (org_id, concept_key) DO UPDATE SET document_id = EXCLUDED.document_id,
                         title = EXCLUDED.title, acl_principals = EXCLUDED.acl_principals,
                         latest_hash = EXCLUDED.latest_hash, source_hash = EXCLUDED.source_hash
                       RETURNING id""",
                    (d["org_id"], d["external_key"], d["id"], d["title"], d["acl_principals"], digest,
                     d["content_hash"])).fetchone()["id"]
                key = originals.key_for_document(conn, d["id"])  # stored encrypted (slice I3b); hashed as text
                if conn.execute(
                        """INSERT INTO concept_versions (concept_id, org_id, hash, content, source_version, key_ref)
                           VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING hash""",
                        (concept_id, d["org_id"], digest, originals.seal(text, key), d["source_version"], key)).fetchone():
                    conn.execute("INSERT INTO concept_events (org_id, concept_id, kind, detail) VALUES (%s, %s, 'bronze_version', %s)",
                                 (d["org_id"], concept_id, Jsonb({"hash": digest, "source_version": d["source_version"]})))
                built += 1
    if built:
        log.info("built %d bronze concept version(s)", built)
    return built


# A job's end never un-pauses its connection: a pause that lands while discovery or a sync runs stays (the API's
# enabled reads status <> 'disabled'), and the outcome is still recorded.
CONNECTION_READY_SQL = """UPDATE connections SET status = CASE WHEN status = 'disabled' THEN status ELSE 'ready' END,
  last_error = NULL, updated_at = now() WHERE id = %s"""
CONNECTION_FAILED_SQL = """UPDATE connections SET status = CASE WHEN status = 'disabled' THEN status ELSE 'error' END,
  last_error = %s, updated_at = now() WHERE id = %s"""


# ------------------------------------------------------------------ handlers
def handle_units_extract(conn, job) -> dict:
    """F2: one extraction pass; while documents still wait and this pass got through some, another is queued at once."""
    result = extraction.run(conn)
    if result.get("more") and (result.get("documents") or result.get("skipped_classification") or result.get("skipped_personal")):
        conn.execute("INSERT INTO jobs (org_id, kind) VALUES (kl.current_org(), 'units.extract')")
    return result


def handle_noop(conn, job) -> dict:
    return {"ok": True}


def handle_connection_discover(conn, job) -> dict:
    cid = job["payload"]["connection_id"]
    c = conn.execute("SELECT id, org_id, kind, status, config, credential_ref FROM connections WHERE id = %s",
                     (cid,)).fetchone()
    if not c:
        return {"skipped": "the connection was removed"}  # slice T2: its retire job cleans up
    if c["status"] == "disabled":
        return {"skipped": "connection disabled"}
    if c["kind"] == "google":  # shared drives (slice F2)
        connector = gdrive.client_for(c)
        wanted = c["config"].get("drives")  # None: every shared drive the account can read
    elif c["kind"] == "github":
        connector = GitHubConnector(load_app(c["credential_ref"]), int(c["config"]["installation_id"]))
        wanted = c["config"].get("repositories")  # None: everything the installation grants
    elif c["kind"] == "jira":  # ADR 0011: projects, as the connection's account sees them
        connector = jira.client_for(c)
        wanted = c["config"].get("projects")  # None: every project the account can browse
    elif c["kind"] == "confluence":  # S1: spaces, as the connection's account sees them
        connector = confluence.client_for(c)
        wanted = c["config"].get("spaces")  # None: every space the account can read
    elif c["kind"] == "slack":  # S4: the channels its bot is in
        connector = slack.client_for(c)
        wanted = c["config"].get("channels")  # None: every channel the bot is in
    elif c["kind"] == "notion":  # S6: what's shared with the integration, one container
        connector = notion.client_for(c)
        wanted = None
    elif c["kind"] == "microsoft":  # S5: the libraries of the sites an admin granted the app
        connector = microsoft365.client_for(c)
        wanted = c["config"].get("libraries")  # None: every library of those sites
    elif c["kind"] == "directory":  # ADR 0011: who's at the company, and who has left
        connector = directory.client_for(c)
        wanted = None
    elif c["kind"] == "catalog":  # S7: the projects, catalogs, databases, collections, or platforms it reads
        connector = CATALOGS[c["config"]["provider"]].client_for(conn, c)
        wanted = None  # each client lists only what the connection names (or everything, where it may)
    else:
        raise RuntimeError(f"no connector for kind {c['kind']!r}")
    health = connector.test()
    if not health.ok:
        raise RuntimeError(health.detail)
    found = connector.discover()
    with conn.transaction():
        captured = {r["external_id"]: r for r in conn.execute(  # a channel's capture outlives a rediscovery (C2b)
            "SELECT external_id, capture, capture_changed_at FROM connection_containers WHERE connection_id = %s", (cid,))}
        conn.execute("DELETE FROM connection_containers WHERE connection_id = %s", (cid,))
        for ct in found:
            was = captured.get(ct.external_id) or {}
            conn.execute(
                """INSERT INTO connection_containers
                     (connection_id, org_id, external_id, container_kind, visibility, default_ref, selected, display_name,
                      capture, capture_changed_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (cid, c["org_id"], ct.external_id, ct.kind, ct.visibility, ct.default_ref,
                 wanted is None or ct.external_id in wanted or ct.kind == "folder",  # a folder shared with it is chosen by that sharing
                 (ct.name or "")[:300] or None,  # its name as the source gives it, for the Documents tree
                 was.get("capture", "off"), was.get("capture_changed_at")))
        conn.execute(CONNECTION_READY_SQL, (cid,))
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.sync', %s)",  # a full read (H6)
                     (c["org_id"], Jsonb({"connection_id": str(cid), "full": True})))
        audit(conn, c["org_id"], "connection.discover", {"connection_id": str(cid), "containers": len(found)})
    return {"containers": len(found)}


def handle_connection_sync(conn, job) -> dict:
    cid = job["payload"]["connection_id"]
    c = conn.execute("SELECT id, org_id, workspace_id, kind, status, config, credential_ref FROM connections "
                     "WHERE id = %s", (cid,)).fetchone()
    if not c:
        return {"skipped": "the connection was removed"}  # slice T2: its retire job cleans up
    if c["status"] == "disabled":
        return {"skipped": "connection disabled"}
    # H6: each container from its cursor, or everything when discovery asked for it or its last full read is a day old
    containers = conn.execute("SELECT external_id, default_ref, CASE WHEN %s OR reconciled_at IS NULL "
                              "OR reconciled_at < now() - %s * interval '1 hour' THEN NULL ELSE cursor END AS since "
                              "FROM connection_containers WHERE connection_id = %s AND selected ORDER BY external_id",
                              (bool(job["payload"].get("full")), FULL_SYNC_HOURS, cid)).fetchall()
    # Q8: the team's media models, asked for by the classification each connector writes, so a model only ever gets what
    # its allowed classifications include, decided where the classification is.
    media = lambda classification, team=c["workspace_id"]: media_for(conn, team, classification)
    results = sync_containers(conn, {**c, "media": media}, containers)
    retired = retire_unlisted(conn, c, source_kind_of(c), [ct["external_id"] for ct in containers]) \
        if c["kind"] in OWNED_KINDS else 0  # a container it no longer reads keeps nothing (migration 0040)
    embedded = embed_missing(conn)
    bronze = materialize_bronze(conn)
    linked = {**links.refresh(conn), "personal_data": privacy.scan(conn)}  # map links (N), personal data (I5)
    with conn.transaction():
        conn.execute("UPDATE connections SET last_synced_at = now(), readers_refreshed_at = now(), updated_at = now() WHERE id = %s", (cid,))
        audit(conn, c["org_id"], "connection.sync", {"connection_id": str(cid), "repos": results, "retired": retired, "embedded": embedded,
                                                     "bronze": bronze, **linked})
    return {"repos": len(results), "files": sum(r["files"] for r in results), "embedded": embedded}


def handle_connection_refresh_readers(conn, job) -> dict:
    """S8: re-read only who may read a connection's items, between content syncs: each indexed item gets its current
    readers, one nobody may read now leaves, and no body is read (a changed or new item waits for the content sync).
    Queued on the readers cadence, and at once by a signed webhook."""
    cid = job["payload"]["connection_id"]
    c = conn.execute("SELECT id, org_id, workspace_id, kind, status, config, credential_ref FROM connections "
                     "WHERE id = %s", (cid,)).fetchone()
    if not c:
        return {"skipped": "the connection was removed"}
    if c["status"] == "disabled":
        return {"skipped": "connection disabled"}
    if c["kind"] not in READERS_KINDS:
        return {"skipped": "its sync reads its readers"}
    containers = conn.execute("SELECT external_id, default_ref FROM connection_containers "
                              "WHERE connection_id = %s AND selected ORDER BY external_id", (cid,)).fetchall()
    results = sync_containers(conn, {**c, "readers_only": True}, containers)
    retired = retire_unlisted(conn, c, source_kind_of(c), [ct["external_id"] for ct in containers]) if c["kind"] in OWNED_KINDS else 0
    with conn.transaction():
        conn.execute("UPDATE connections SET readers_refreshed_at = now(), updated_at = now() WHERE id = %s", (cid,))
        audit(conn, c["org_id"], "connection.refresh_readers", {"connection_id": str(cid), "reason": job["payload"].get("reason"),
                                                                "containers": results, "retired": retired})
    return {"containers": len(results), "removed": sum(r.get("removed", 0) for r in results)}


def sync_containers(conn, c: dict, containers: list) -> list[dict]:
    """Each selected container of a connection through its connector: a content sync, or (readers_only) S8's refresh."""
    containers = shutdown.each(containers)  # a worker asked to stop stops between containers (C1)
    if c["kind"] == "google":  # shared drives (slice F2)
        client = gdrive.client_for(c)
        results = [gdrive.sync_drive(conn, c, client, ct["external_id"], since=ct.get("since")) for ct in containers]
    elif c["kind"] == "github":
        connector = GitHubConnector(load_app(c["credential_ref"]), int(c["config"]["installation_id"]))
        results = [sync_repo(conn, c, connector, ct) for ct in containers]
    elif c["kind"] == "jira":
        client = jira.client_for(c)
        results = [jira.sync_project(conn, c, client, ct["external_id"], since=ct.get("since")) for ct in containers]
    elif c["kind"] == "confluence":
        client = confluence.client_for(c)
        results = [confluence.sync_space(conn, c, client, ct["external_id"], since=ct.get("since")) for ct in containers]
    elif c["kind"] == "slack":
        client = slack.client_for(c)
        results = [slack.sync_channel(conn, c, client, ct["external_id"], since=ct.get("since")) for ct in containers]
    elif c["kind"] == "notion":
        client = notion.client_for(c)
        results = [notion.sync_workspace(conn, c, client, ct["external_id"]) for ct in containers]
    elif c["kind"] == "microsoft":
        client = microsoft365.client_for(c)
        results = [microsoft365.sync_library(conn, c, client, ct["external_id"], since=ct.get("since")) for ct in containers]
    elif c["kind"] == "directory":
        results = [directory.sync_directory(conn, c, directory.client_for(c))]
    elif c["kind"] == "catalog":
        provider = c["config"]["provider"]
        client = CATALOGS[provider].client_for(conn, c)
        results = [catalogs.sync_catalog(conn, c, client, ct["external_id"], provider, CATALOG_IDENTITIES[provider]) for ct in containers]
    else:
        raise RuntimeError(f"no connector for kind {c['kind']!r}")
    return results


# ------------------------------------------------------------------ curated knowledge (slice C2)
# Dev only: the sandbox bundles stand in for <workspace>-knowledge repos, with readers like the seed's.
SEED_READERS = {"payments": ["group:payments"], "commons": [f"org:{config.ORG_SLUG}"],
                "legal": [f"org:{config.ORG_SLUG}"], "finance": ["group:finance"]}  # finance: scenarios only (slice K1)


def grant_scenario_readers(files: dict[str, str], acl: list[str]) -> int:
    """A scenario's pages get the viewer tuples the plain seed's get from authz/seed-tuples.json: the members of each
    of the bundle's readers may view each page. Indexing writes no tuples, so without these the post-check drops every
    page for everyone. Only missing tuples are written, so loading a scenario again is harmless."""
    from .okf import parse
    wanted = set()
    for path, text in files.items():
        key = (parse(text, path).meta.get("x-kl") or {}).get("id")
        if key:
            wanted |= {(f"{principal}#member", "viewer", f"document:{key}") for principal in acl}
    have = {(t["user"], t["relation"], t["object"]) for obj in sorted({w[2] for w in wanted}) for t in fga.read({"object": obj})}
    missing = wanted - have
    if missing:
        fga.write(writes=missing)
    return len(missing)


def handle_knowledge_seed(conn, job) -> dict:
    # Dev only. With no payload: the sandbox bundles the smoke suite relies on, unchanged. With {"scenario": name}
    # (make demo, make eval; slice K1): a scenario's bundles on top, each under its own container
    # (scenario/<name>/<bundle>), so smoke's fixtures never change and a scenario can be told apart from the seed.
    scenario = str((job.get("payload") or {}).get("scenario") or "").strip()
    if scenario:
        base = os.environ.get("KL_SEED_SCENARIOS_DIR")
        ok_name = len(scenario) <= 40 and scenario.replace("-", "").isalnum() and scenario.islower()
        if not base or not ok_name or not os.path.isdir(os.path.join(base, scenario)):
            return {"skipped": f"no scenario named {scenario!r}"}
        root, prefix = os.path.join(base, scenario), f"scenario/{scenario}/"
    else:
        root, prefix = os.environ.get("KL_SEED_KNOWLEDGE_DIR"), "seed/"
        if not root or not os.path.isdir(root):
            return {"skipped": "KL_SEED_KNOWLEDGE_DIR isn't a directory"}
    results = {}
    for bundle in sorted(Path(root).iterdir()):
        ws = bundle.name.removesuffix("-knowledge")
        if not bundle.is_dir() or ws == bundle.name or ws not in SEED_READERS:
            continue
        row = conn.execute("SELECT id, org_id FROM workspaces WHERE slug = %s", (ws,)).fetchone()
        if not row:
            continue
        files = knowledge.load_dir(str(bundle))
        results[bundle.name] = knowledge.index_bundle(
            conn, org_id=row["org_id"], workspace_id=row["id"], container_ref=f"{prefix}{bundle.name}",
            version="seed", files=files, acl=SEED_READERS[ws],
            uri_for=lambda path, name=bundle.name: f"https://kb.acme.test/{name}{path}")
        if scenario:  # the plain seed's readers come from authz/seed-tuples.json; a scenario's are granted here
            grant_scenario_readers(files, SEED_READERS[ws])
    return {"bundles": results, "embedded": embed_missing(conn), **links.refresh(conn), "personal_data": privacy.scan(conn)}


# ------------------------------------------------------------------ team purge (slice H1)
# Due purge requests become jobs in one statement, so a request can't be picked up twice.
DUE_PURGES_SQL = """
WITH due AS (
  UPDATE purge_requests SET status = 'running', updated_at = now()
  WHERE status = 'scheduled' AND run_after <= now()
  RETURNING id, org_id
)
INSERT INTO jobs (org_id, kind, payload)
SELECT org_id, 'workspace.purge', jsonb_build_object('request_id', id::text) FROM due
RETURNING payload->>'request_id' AS request_id
"""


def purge_tuples(plan: dict) -> int:
    """Delete every authorization tuple on the team's workspace, its documents, and its repo containers."""
    objects = ([f"workspace:{plan['slug']}"] + [f"document:{key}" for key in plan["documents"]]
               + [f"container:{repo_key(c)}" for c in plan["containers"]])
    doomed = set()
    for obj in objects:
        doomed |= {(t["user"], t["relation"], t["object"]) for t in fga.read({"object": obj})}
    if doomed:
        fga.write(deletes=doomed)
    return len(doomed)


def handle_workspace_purge(conn, job) -> dict:
    """Remove a whole team. Authorization goes first, so a purge that stops halfway leaves nothing of the
    team readable; then its secrets (every version), then its rows, then a check that nothing derived
    from its documents remains. A retry after a partial run picks up where it stopped."""
    req = conn.execute("SELECT id, org_id, workspace_id, slug, requested_by, status FROM purge_requests WHERE id = %s",
                       (job["payload"]["request_id"],)).fetchone()
    if not req or req["status"] != "running":
        return {"skipped": "the request was cancelled or already finished"}
    # A hold placed after the purge was scheduled cancels it before anything is destroyed. ponytail: a hold placed
    # between this check and purge_workspace (which checks again and refuses) can still lose tuples and secrets;
    # lock the team's row here if holds ever race purges in practice.
    if conn.execute("SELECT kl.retention_held(%s) AS held", (req["workspace_id"],)).fetchone()["held"]:
        result = {"cancelled": "a legal hold covers the team"}
        with conn.transaction():
            conn.execute("UPDATE purge_requests SET status = 'cancelled', result = %s, updated_at = now() WHERE id = %s",
                         (Jsonb(result), req["id"]))
            conn.execute("INSERT INTO audit_events (org_id, actor, action, scope, details) "
                         "VALUES (%s, %s, 'workspace.purge_cancelled', %s, %s)",
                         (req["org_id"], req["requested_by"], [req["slug"]], Jsonb({"request": str(req["id"]), **result})))
        log.info("purge of %s cancelled: a legal hold covers it", req["slug"])
        return result
    plan = conn.execute("SELECT kl.purge_plan(%s) AS plan", (req["workspace_id"],)).fetchone()["plan"]
    result = {"tuples": 0, "secrets": 0}
    if plan:
        result["tuples"] = purge_tuples(plan)
        google = [r["credential_ref"] for r in conn.execute(  # each Drive connection's own secret and token (slice F2)
            "SELECT credential_ref FROM connections WHERE workspace_id = %s AND kind = 'google'", (req["workspace_id"],))]
        for ref in plan["credentials"] + google:
            vault.destroy(ref)
        result["secrets"] = len(plan["credentials"]) + len(google)
        result["shared_documents_unlinked"] = plan["shared"]
        result["rewrapped"] = originals.rewrap_survivors(conn, req["workspace_id"])  # shared history: the org's key now
        with conn.transaction():
            counts = conn.execute("SELECT kl.purge_workspace(%s) AS counts", (req["workspace_id"],)).fetchone()["counts"]
        result.update(counts or {})
        if result.get("remaining"):
            raise RuntimeError(f"purge verification found {result['remaining']} row(s) left behind")
    else:
        result["already_removed"] = True
    vault.destroy_key(originals.team_key(req["workspace_id"]))  # every copy encrypted with it, in any backup, is unreadable
    result["key_destroyed"] = True
    with conn.transaction():
        conn.execute("UPDATE purge_requests SET status = 'done', result = %s, updated_at = now() WHERE id = %s",
                     (Jsonb(result), req["id"]))
        conn.execute("INSERT INTO audit_events (org_id, actor, action, scope, details) "
                     "VALUES (%s, %s, 'workspace.purged', %s, %s)",
                     (req["org_id"], req["requested_by"], [req["slug"]], Jsonb(result)))
    log.info("workspace %s purged: %s", req["slug"], result)
    return result


def handle_retention_apply(conn, job) -> dict:
    """Retention (slice I2): remove what its policies say has expired, unless a legal hold keeps it. Each
    expiring document's authorization tuples go first, so nothing stays readable through OpenFGA."""
    expired = [r["external_key"] for r in conn.execute("SELECT external_key FROM kl.retention_plan()").fetchall()]
    tuples = set()
    for key in expired:
        tuples |= {(t["user"], t["relation"], t["object"]) for t in fga.read({"object": f"document:{key}"})}
    if tuples:
        fga.write(deletes=tuples)
    with conn.transaction():
        counts = conn.execute("SELECT kl.apply_retention() AS c").fetchone()["c"] or {}
        pruned = conn.execute("SELECT kl.prune_changes(%s) AS n", (config.CHANGE_FEED_DAYS,)).fetchone()["n"]  # P3
        ended = conn.execute("SELECT kl.sweep_web_sessions() AS n").fetchone()["n"]  # H3: web sessions that have ended
        asked = gaps.prune(conn)  # F5a: questions past their window, and the gaps built from them
    return {**counts, "tuples": len(tuples), "changes_pruned": pruned, "web_sessions_swept": ended, "questions_pruned": asked}


def handle_answer_keep(conn, job) -> dict:
    """An expert's reply kept as knowledge (F5b): its document and proposed unit, then what's derived from them."""
    result = experts.keep(conn, job["payload"]["routed"])
    if result.get("kept"):
        result.update(embedded=embed_missing(conn), bronze=materialize_bronze(conn), **links.refresh(conn), personal_data=privacy.scan(conn))
    return result


def handle_pipeline_push(conn, job) -> dict:
    """A CI pipeline's push (slice J1): its documents, then everything derived from them, as for any connector. The
    pushed text isn't kept once it's applied."""
    push = conn.execute("SELECT id, org_id, workspace_id, pipeline_id, payload FROM pipeline_pushes "
                        "WHERE id = %s AND status = 'queued'", (job["payload"]["push_id"],)).fetchone()
    if not push:
        return {"skipped": "no queued push"}
    try:
        result = pipelines.apply_push(conn, push)
    except Exception as e:
        with conn.transaction():
            conn.execute("UPDATE pipeline_pushes SET status = 'failed', error = %s, payload = '{}', finished_at = now() "
                         "WHERE id = %s", (type(e).__name__, push["id"]))
        raise
    result.update(embedded=embed_missing(conn), bronze=materialize_bronze(conn), **links.refresh(conn),
                  personal_data=privacy.scan(conn))
    with conn.transaction():
        conn.execute("UPDATE pipeline_pushes SET status = 'done', result = %s, payload = '{}', finished_at = now() "
                     "WHERE id = %s", (Jsonb(result), push["id"]))
    return result


def handle_manifest_apply(conn, job) -> dict:
    """A CI pipeline's dbt or Dataform manifest (S7): its entries as the pipeline's documents and the team's assets,
    then everything derived from them. The manifest isn't kept once it's applied."""
    push = conn.execute("SELECT id, org_id, workspace_id, pipeline_id, payload FROM pipeline_pushes "
                        "WHERE id = %s AND status = 'queued'", (job["payload"]["push_id"],)).fetchone()
    if not push:
        return {"skipped": "no queued push"}
    try:
        result = manifests.apply_manifest(conn, push)
    except Exception as e:
        with conn.transaction():
            conn.execute("UPDATE pipeline_pushes SET status = 'failed', error = %s, payload = '{}', finished_at = now() "
                         "WHERE id = %s", (type(e).__name__, push["id"]))
        raise
    result.update(embedded=embed_missing(conn), bronze=materialize_bronze(conn), **links.refresh(conn), personal_data=privacy.scan(conn))
    with conn.transaction():
        conn.execute("UPDATE pipeline_pushes SET status = 'done', result = %s, payload = '{}', finished_at = now() "
                     "WHERE id = %s", (Jsonb(result), push["id"]))
    return result


def handle_pipeline_retire(conn, job) -> dict:
    """A removed pipeline's documents go with it (slice J1), their authorization first."""
    container = f"pipeline:{job['payload']['pipeline_id']}"
    keys = [r["external_key"] for r in conn.execute("SELECT external_key FROM documents WHERE container_ref = %s",
                                                    (container,)).fetchall()]
    tuples = set()
    for key in keys:
        tuples |= {(t["user"], t["relation"], t["object"]) for t in fga.read({"object": f"document:{key}"})}
    if tuples:
        fga.write(deletes=tuples)
    with conn.transaction():
        removed = conn.execute("DELETE FROM documents WHERE container_ref = %s", (container,)).rowcount
    return {"documents": removed, "tuples": len(tuples)}


def handle_import_apply(conn, job) -> dict:
    """An uploaded Confluence or Notion export (slice J2): its pages, then everything derived from them, as for any
    connector. The upload is cleared from staging once applied; an export that can't be read fails with the reason,
    and isn't retried: it wouldn't read any better a second time."""
    run = conn.execute("""SELECT r.id, r.org_id, r.import_id, r.archive, i.workspace_id, i.kind, i.readers, i.classification,
                                 i.base_url FROM import_runs r JOIN imports i ON i.id = r.import_id
                          WHERE r.id = %s AND r.status = 'queued'""", (job["payload"]["run_id"],)).fetchone()
    if not run:
        return {"skipped": "no queued import"}
    finish = "UPDATE import_runs SET status = %s, result = %s, error = %s, archive = NULL, finished_at = now() WHERE id = %s"
    try:
        result = imports.apply(conn, run, imports.read(bytes(run["archive"]), run["kind"], run["base_url"]))
    except imports.ImportFailed as e:
        with conn.transaction():
            conn.execute(finish, ("failed", None, str(e), run["id"]))
        return {"failed": str(e)}
    except Exception as e:
        with conn.transaction():
            conn.execute(finish, ("failed", None, type(e).__name__, run["id"]))
        raise
    result.update(embedded=embed_missing(conn), bronze=materialize_bronze(conn), **links.refresh(conn),
                  personal_data=privacy.scan(conn))
    with conn.transaction():
        conn.execute(finish, ("done", Jsonb(result), None, run["id"]))
    return result


def handle_import_retire(conn, job) -> dict:
    """A removed import's pages go with it (slice J2), their authorization first."""
    container = f"import:{job['payload']['import_id']}"
    keys = [r["external_key"] for r in conn.execute("SELECT external_key FROM documents WHERE container_ref = %s",
                                                    (container,)).fetchall()]
    tuples = set()
    for key in keys:
        tuples |= {(t["user"], t["relation"], t["object"]) for t in fga.read({"object": f"document:{key}"})}
    if tuples:
        fga.write(deletes=tuples)
    with conn.transaction():
        removed = conn.execute("DELETE FROM documents WHERE container_ref = %s", (container,)).rowcount
    return {"documents": removed, "tuples": len(tuples)}


SOURCE_KINDS = {"github": ["github", "okf"], "google": ["gdrive"], "jira": ["jira"], "confluence": ["confluence"],
                "slack": ["slack"], "microsoft": ["microsoft"], "notion": ["notion"],
                "catalog": sorted(CATALOGS)}  # what each kind of connection's documents are


def handle_connection_retire(conn, job) -> dict:
    """A removed connection's knowledge goes with it (slice T2), as a removed pipeline's does: authorization first, then
    the rows. Team membership never grants a document (its viewers come from the source), so a document another team
    still holds through its own source keeps its tuples and only loses this team's link; a repository's own tuples go
    once no connection brings it in. Its Google secret and token, if any, are destroyed. Another connection's own
    documents in a same-named container (migration 0040) are never touched: the removed connection's lost its id with
    its row (SET NULL), and theirs still carry theirs."""
    p = job["payload"]
    kinds = SOURCE_KINDS.get(p["kind"], [])
    result = {"unlinked": 0, "documents": 0, "tuples": 0, "secrets": 0}
    for ct in p["containers"]:
        rows = conn.execute(
            """SELECT d.id, d.external_key,
                      EXISTS (SELECT 1 FROM workspace_documents o WHERE o.document_id = d.id AND o.workspace_id <> %s) AS elsewhere
               FROM documents d JOIN workspace_documents wd ON wd.document_id = d.id AND wd.workspace_id = %s
               WHERE d.container_ref = %s AND d.source_kind = ANY(%s) AND d.connection_id IS NULL""",
            (ct["workspace_id"], ct["workspace_id"], ct["external_id"], kinds)).fetchall()
        only_here = [r for r in rows if not r["elsewhere"]]
        doomed = set()
        for r in only_here:
            doomed |= {(t["user"], t["relation"], t["object"]) for t in fga.read({"object": f"document:{r['external_key']}"})}
        if p["kind"] == "github" and not conn.execute("SELECT 1 FROM connection_containers WHERE external_id = %s LIMIT 1",
                                                      (ct["external_id"],)).fetchone():
            doomed |= {(t["user"], t["relation"], t["object"]) for t in fga.read({"object": f"container:{repo_key(ct['external_id'])}"})}
        if doomed:
            fga.write(deletes=doomed)
        with conn.transaction():
            result["unlinked"] += conn.execute("DELETE FROM workspace_documents WHERE workspace_id = %s AND document_id = ANY(%s)",
                                               (ct["workspace_id"], [r["id"] for r in rows])).rowcount
            result["documents"] += conn.execute("DELETE FROM documents WHERE id = ANY(%s)", ([r["id"] for r in only_here],)).rowcount
        result["tuples"] += len(doomed)
    if p["kind"] == "catalog":  # the owners it said its assets have (S7); the assets stay, as pushed lineage's do
        with conn.transaction():
            result["owners"] = conn.execute("DELETE FROM asset_owners WHERE source = %s", (p["connection_id"],)).rowcount
    if p.get("credential_ref"):
        vault.destroy(p["credential_ref"])
        result["secrets"] = 1
    if p.get("webhook_ref"):  # its webhook's secret (S8)
        vault.destroy(p["webhook_ref"])
        result["secrets"] += 1
    return result


HANDLERS = {"noop": handle_noop, "connection.retire": handle_connection_retire, "knowledge.seed": handle_knowledge_seed, "workspace.purge": handle_workspace_purge,
            "proposal.open_pr": lambda conn, job: curator.open_pr(conn, job["payload"]["proposal_id"]),
            "proposal.refresh": lambda conn, job: curator.refresh(conn, job["payload"]["proposal_id"]), "connection.discover": handle_connection_discover,
            "connection.sync": handle_connection_sync, "connection.refresh_readers": handle_connection_refresh_readers,
            "slack.capture": capture.request, "slack.notice": capture.notice, "slack.exclude": capture.exclude,  # C2b
            "slack.save": capture.save,  # C2c
            "embeddings.backfill": handle_embeddings_backfill,
            "alerts.evaluate": lambda conn, job: alerting.evaluate(conn, RESYNC_MINUTES),  # on demand (slice P)
            "retention.apply": handle_retention_apply,
            "pipeline.push": handle_pipeline_push, "pipeline.retire": handle_pipeline_retire, "manifest.apply": handle_manifest_apply,
            "import.apply": handle_import_apply, "import.retire": handle_import_retire, "units.extract": handle_units_extract,
            "units.consolidate": lambda conn, job: consolidation.run(conn),
            "units.lifecycle": lambda conn, job: lifecycle.run(conn),
            "gaps.refresh": lambda conn, job: gaps.run(conn),
            "answers.keep": lambda conn, job: handle_answer_keep(conn, job)}
RETENTION_SQL = ("INSERT INTO jobs (org_id, kind) SELECT kl.current_org(), 'retention.apply' WHERE NOT EXISTS "
                 "(SELECT 1 FROM jobs WHERE kind = 'retention.apply' AND created_at > now() - interval '1 hour')")


def job_limits(setting: str) -> dict[str, float]:
    """Each kind's time limit in minutes: LIMIT_MINUTES, changed by KL_JOB_LIMITS (kind=minutes, comma separated). A
    kind the worker doesn't run, or a limit that isn't a number above zero, is refused, stopping the worker at its start,
    so a typo never leaves a job without the limit someone meant."""
    limits = dict(LIMIT_MINUTES)
    for pair in filter(None, (p.strip() for p in setting.split(","))):
        kind, _, minutes = (part.strip() for part in pair.partition("="))
        try:
            limit = float(minutes)
        except ValueError:
            limit = math.nan
        if kind not in HANDLERS or not (math.isfinite(limit) and limit > 0):
            raise ValueError(f"KL_JOB_LIMITS: {pair!r} isn't a job kind and a number of minutes above zero")
        limits[kind] = limit
    return limits


LIMITS = job_limits(os.environ.get("KL_JOB_LIMITS", ""))
HEARTBEAT_SQL = ("INSERT INTO heartbeats (org_id, service) VALUES (kl.current_org(), 'worker') "
                 "ON CONFLICT (org_id, service) DO UPDATE SET seen_at = now()")


# ------------------------------------------------------------------ loop
def finish(conn, job, error: str | None = None) -> bool:
    """Settle a run: done, failed for good, or queued again with backoff. Only the run holding the job's current claim
    settles it (FENCE); one whose job was taken over after its lease ran out changes nothing and says so."""
    fence = (job["id"], job["attempts"])
    if error is None:
        settled = conn.execute("UPDATE jobs SET status = 'done', last_error = NULL, updated_at = now() WHERE id = %s" + FENCE,
                               fence).rowcount
    elif job["attempts"] >= job["max_attempts"]:
        settled = conn.execute("UPDATE jobs SET status = 'failed', last_error = %s, updated_at = now() WHERE id = %s" + FENCE,
                               (error, *fence)).rowcount
        if settled and job["kind"] in ("connection.discover", "connection.sync", "connection.refresh_readers"):
            conn.execute(CONNECTION_FAILED_SQL, (error, job["payload"].get("connection_id")))
    else:
        delay = min(300, 5 * 2 ** (job["attempts"] - 1))
        settled = conn.execute("UPDATE jobs SET status = 'queued', last_error = %s, run_after = now() + %s * interval '1 second', "
                               "updated_at = now() WHERE id = %s" + FENCE, (error, delay, *fence)).rowcount
    if not settled:
        log.warning("job %s %s: its lease ran out and it was taken over, so this run's outcome is dropped", job["id"], job["kind"])
    return bool(settled)


class Lease:
    """A claimed job's lease (C1), renewed every third of LEASE_SECONDS from a connection of its own while the job runs,
    up to its kind's time limit (M7). A worker that's killed stops renewing, and the reaper puts the job back once the
    lease runs out. A job still running at its limit is stuck where no safe point reaches (a call that never returns, a
    listing that never ends), and a stuck call can't be stopped from another thread: the lease settles the job as a
    failed attempt and ends the worker's process, which every deployment starts again."""

    def __init__(self, job: dict, dsn: str = INGEST_DB) -> None:
        self.job, self.dsn, self.done = job, dsn, threading.Event()
        self.limit = LIMITS.get(job["kind"], DEFAULT_LIMIT_MINUTES)
        self.thread = threading.Thread(target=self._renew, name=f"lease-{job['id']}", daemon=True)

    def __enter__(self) -> "Lease":
        self.thread.start()
        return self

    def __exit__(self, *_) -> None:
        self.done.set()
        self.thread.join(timeout=5)

    def _connect(self) -> psycopg.Connection:
        conn = psycopg.connect(self.dsn, autocommit=True)
        conn.execute("SELECT set_config('kl.org_id', %s, false)", (str(self.job["org_id"]),))
        conn.execute("SET lock_timeout = '10s'")  # never wait long on a row the stuck job holds
        return conn

    def _renew(self) -> None:  # the claim set the first lease, so a short job never opens a connection here
        job, conn = self.job, None
        deadline = time.monotonic() + self.limit * 60
        while not self.done.wait(min(LEASE_SECONDS / 3, max(0.0, deadline - time.monotonic()))):
            if time.monotonic() >= deadline:
                self._overran(conn)
                break
            try:
                if conn is None:
                    conn = self._connect()
                if not conn.execute(RENEW_SQL, (LEASE_SECONDS, job["id"], job["attempts"])).rowcount and not self.done.is_set():
                    log.info("job %s is no longer this worker's; its lease isn't renewed", job["id"])
                    break
            except psycopg.Error as e:  # the database blinked: try again next time, before the lease runs out
                log.warning("job %s: lease not renewed (%s); trying again", job["id"], type(e).__name__)
                if conn is not None:
                    conn.close()
                conn = None
        if conn is not None:
            conn.close()

    def _overran(self, conn: psycopg.Connection | None) -> None:
        """Past its limit: settle the job as a failed attempt (fenced, so one already settled or taken over is left as
        it is), then end the process. If the database won't answer, the lease runs out and the reaper settles it."""
        job = self.job
        log.error("job %s %s ran past its time limit of %g minutes; ending the worker it holds", job["id"], job["kind"], self.limit)
        try:
            finish(conn or self._connect(), job, f"ran past its time limit of {self.limit:g} minutes (KL_JOB_LIMITS changes it)")
        except psycopg.Error as e:
            log.warning("job %s: not settled (%s); its lease will run out", job["id"], type(e).__name__)
        if not self.done.is_set():  # it finished while being settled: nothing holds the worker
            logging.shutdown()
            os._exit(OVERRAN_EXIT)


def run(conn, job, handlers: dict | None = None, dsn: str = INGEST_DB) -> None:
    handler = (HANDLERS if handlers is None else handlers).get(job["kind"])
    started = time.monotonic()
    with Lease(job, dsn):
        try:
            with telemetry.span("job", kind=job["kind"], attempt=job["attempts"]):
                if handler is None:
                    raise RuntimeError(f"no handler for job kind {job['kind']!r}")
                result = handler(conn, job)
            finish(conn, job)
            telemetry.job_finished(job["kind"], "done", time.monotonic() - started)
            log.info("job %s %s done: %s", job["id"], job["kind"], result)
        except shutdown.Stopped:  # stopped at a safe point (SIGTERM): back in the queue at once, the attempt not counted
            conn.execute(REQUEUE_SQL, (job["id"], job["attempts"]))
            telemetry.job_finished(job["kind"], "stopped", time.monotonic() - started)
            log.info("job %s %s put back in the queue: the worker is stopping", job["id"], job["kind"])
        except Exception as e:  # noqa: BLE001 - every failure is recorded on the job, never lost
            error = f"{type(e).__name__}: {e}"[:500]
            log.warning("job %s %s failed (attempt %s/%s): %s",
                        job["id"], job["kind"], job["attempts"], job["max_attempts"], error)
            finish(conn, job, error)
            telemetry.job_finished(job["kind"], "failed" if job["attempts"] >= job["max_attempts"] else "retried",
                                   time.monotonic() - started)


def reap(conn) -> int:
    """Put back every job whose lease ran out, because its worker died mid-job (C1). The attempt counts, so a job that
    kills its worker every time (out of memory on one huge item) fails after max_attempts instead of looping forever."""
    with conn.transaction():
        expired = conn.execute(REAP_SQL).fetchall()
        for job in expired:
            finish(conn, job, "its worker stopped before finishing (the job's lease ran out)")
    if expired:
        log.warning("took over %s job(s) whose worker stopped: %s", len(expired), ", ".join(f"{j['id']} {j['kind']}" for j in expired))
    return len(expired)


def _stop(*_) -> None:
    shutdown.requested.set()


def main() -> None:
    telemetry.setup("kl-worker")
    telemetry.register_worker_gauges()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while not shutdown.requested.is_set():
        try:
            with psycopg.connect(INGEST_DB, row_factory=dict_row, autocommit=True) as conn:
                org_id = conn.execute("SELECT kl.resolve_org(%s) AS id", (config.ORG_SLUG,)).fetchone()["id"]
                if org_id is None:
                    raise SystemExit(f"worker: org {config.ORG_SLUG!r} not found")
                conn.execute("SELECT set_config('kl.org_id', %s, false)", (str(org_id),))
                log.info("worker ready (org %s; re-sync window %s min)", config.ORG_SLUG, RESYNC_MINUTES)
                next_schedule = 0.0
                while not shutdown.requested.is_set():
                    if time.monotonic() >= next_schedule:
                        reap(conn)  # first, so a sync taken over counts as pending and isn't scheduled twice
                        scheduled = conn.execute(SCHEDULE_SQL, (RESYNC_MINUTES,)).fetchall()
                        if scheduled:
                            log.info("scheduled re-sync for %s connection(s)", len(scheduled))
                        refreshing = conn.execute(READERS_SQL, (list(READERS_KINDS), READERS_MINUTES)).fetchall()
                        if refreshing:
                            log.info("scheduled a readers refresh for %s connection(s)", len(refreshing))
                        conn.execute(PROPOSALS_SQL).fetchall()
                        conn.execute(DUE_PURGES_SQL).fetchall()
                        try:
                            with conn.transaction():  # a savepoint: a failure here can't abort the tick's work
                                telemetry.observe_worker(conn)
                        except Exception as e:  # noqa: BLE001 - telemetry must never stop the scheduler
                            log.warning("telemetry gauges not refreshed: %s", type(e).__name__)
                        try:  # alerts in the app (slice P): the heartbeat says the worker is alive, then the pass
                            conn.execute(HEARTBEAT_SQL)
                            alerting.evaluate(conn, RESYNC_MINUTES)
                        except Exception as e:  # noqa: BLE001 - alerting must never stop the scheduler either
                            log.warning("alerts not evaluated: %s", type(e).__name__)
                        try:  # extraction (F2): a pass a minute while any team has opted in
                            conn.execute(extraction.QUEUE_SQL)
                        except Exception as e:  # noqa: BLE001
                            log.warning("extraction not scheduled: %s", type(e).__name__)
                        try:  # consolidation (F3): a pass a minute while any unit changed since it was last compared
                            conn.execute(consolidation.QUEUE_SQL)
                        except Exception as e:  # noqa: BLE001
                            log.warning("consolidation not scheduled: %s", type(e).__name__)
                        try:  # lifecycle (F6): a pass a minute over the units in use
                            conn.execute(lifecycle.QUEUE_SQL)
                        except Exception as e:  # noqa: BLE001
                            log.warning("lifecycle not scheduled: %s", type(e).__name__)
                        try:  # gaps (F5a): a pass a minute while new questions came in
                            conn.execute(gaps.QUEUE_SQL)
                        except Exception as e:  # noqa: BLE001
                            log.warning("gaps not scheduled: %s", type(e).__name__)
                        try:  # retention (slice I2): at most one run an hour, as a job like any other
                            conn.execute(RETENTION_SQL)
                        except Exception as e:  # noqa: BLE001
                            log.warning("retention not scheduled: %s", type(e).__name__)
                        try:  # stored originals from before slice I3b get encrypted, up to 2,000 a tick
                            for _ in range(10):
                                if not originals.encrypt_legacy(conn):
                                    break
                        except Exception as e:  # noqa: BLE001
                            log.warning("originals not encrypted: %s", type(e).__name__)
                        next_schedule = time.monotonic() + SCHEDULER_SECONDS
                    with conn.transaction():
                        job = conn.execute(WORKER_CLAIM_SQL, (LEASE_SECONDS, list(LANE_KINDS))).fetchone()
                    if job is None:
                        time.sleep(POLL_SECONDS)
                        continue
                    run(conn, job)
        except psycopg.OperationalError as e:
            log.warning("database unavailable (%s); retrying in 3s", type(e).__name__)
            time.sleep(3)


if __name__ == "__main__":
    main()
