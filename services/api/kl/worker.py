"""Durable job worker: a Postgres queue claimed with FOR UPDATE SKIP LOCKED.

Runs as kl_ingest inside this cell's single org. The org context is set once per session, so
row-level security scopes every query, and a bug can't reach another org's rows."""
import hashlib
import logging
import os
import signal
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import alerting, config, confluence, curator, directory, gdrive, imports, jira, knowledge, links, okf, originals, pipelines, privacy, slack, telemetry
from .authz import fga
from .connectors import GitHubConnector
from .embeddings import embed, passage_text, vector_literal
from .github import load_app
from .sync_github import repo_key, sync_repo
from .sync_items import retire_unlisted
from .vault import vault

INGEST_DB = os.environ.get("KL_INGEST_DATABASE_URL", "postgresql://kl_ingest:ingest-dev-only@postgres:5432/kl")
POLL_SECONDS = float(os.environ.get("KL_WORKER_POLL_SECONDS", "2"))
RESYNC_MINUTES = float(os.environ.get("KL_RESYNC_MINUTES", "10"))      # freshness window per connection
OWNED_KINDS = {"jira", "confluence", "slack"}  # kinds whose documents carry their connection (migration 0040)
SCHEDULER_SECONDS = float(os.environ.get("KL_SCHEDULER_SECONDS", "60"))  # how often to look for stale ones

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("kl.worker")
_stopping = False

CLAIM_SQL = """
UPDATE jobs SET status = 'running', attempts = attempts + 1, locked_at = now(), updated_at = now()
WHERE id = (SELECT id FROM jobs
            WHERE status = 'queued' AND run_after <= now()
            ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING id, kind, payload, attempts, max_attempts
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
SELECT DISTINCT c.id AS chunk_id, c.org_id, c.body, c.heading_path, c.acl_principals, d.title,
       m.id AS model_id, m.provider, m.model, m.dims, m.base_url, m.credential_ref
FROM chunks c
JOIN documents d ON d.id = c.document_id
JOIN workspace_documents wd ON wd.document_id = d.id
JOIN embedding_models m ON m.status = 'active' AND m.org_id = c.org_id
  AND (m.workspace_id = wd.workspace_id
       OR (m.workspace_id IS NULL AND NOT EXISTS (
             SELECT 1 FROM embedding_models o WHERE o.workspace_id = wd.workspace_id AND o.status = 'active')))
LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id AND e.model_id = m.id
WHERE e.chunk_id IS NULL
LIMIT 512
"""
EMBED_BATCH = 64


def embed_missing(conn) -> int:
    done = 0
    while True:
        rows = conn.execute(MISSING_EMBEDDINGS_SQL).fetchall()
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
                                [passage_text(r["title"], r["heading_path"], r["body"]) for r in batch],
                                base_url=m["base_url"], api_key=api_key)
                if len(vectors) != len(batch) or any(len(v) != m["dims"] for v in vectors):
                    raise RuntimeError(f"{m['model']} returned {len(vectors)} vectors for {len(batch)} chunks, "
                                       f"or the wrong dimension (expected {m['dims']})")
                with conn.transaction():
                    for r, v in zip(batch, vectors):
                        conn.execute("INSERT INTO chunk_embeddings (chunk_id, model_id, org_id, acl_principals, embedding) "
                                     "VALUES (%s, %s, %s, %s, %s::vector) ON CONFLICT (chunk_id, model_id) DO NOTHING",
                                     (r["chunk_id"], model_id, r["org_id"], r["acl_principals"], vector_literal(v)))
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


# ------------------------------------------------------------------ handlers
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
    elif c["kind"] == "directory":  # ADR 0011: who's at the company, and who has left
        connector = directory.client_for(c)
        wanted = None
    else:
        raise RuntimeError(f"no connector for kind {c['kind']!r}")
    health = connector.test()
    if not health.ok:
        raise RuntimeError(health.detail)
    found = connector.discover()
    with conn.transaction():
        conn.execute("DELETE FROM connection_containers WHERE connection_id = %s", (cid,))
        for ct in found:
            conn.execute(
                """INSERT INTO connection_containers
                     (connection_id, org_id, external_id, container_kind, visibility, default_ref, selected, display_name)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (cid, c["org_id"], ct.external_id, ct.kind, ct.visibility, ct.default_ref,
                 wanted is None or ct.external_id in wanted or ct.kind == "folder",  # a folder shared with it is chosen by that sharing
                 (ct.name or "")[:300] or None))  # its name as the source gives it, for the Documents tree
        conn.execute("UPDATE connections SET status = 'ready', last_error = NULL, updated_at = now() WHERE id = %s",
                     (cid,))
        conn.execute("INSERT INTO jobs (org_id, kind, payload) VALUES (%s, 'connection.sync', %s)",
                     (c["org_id"], Jsonb({"connection_id": str(cid)})))
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
    containers = conn.execute("SELECT external_id, default_ref FROM connection_containers "
                              "WHERE connection_id = %s AND selected ORDER BY external_id", (cid,)).fetchall()
    if c["kind"] == "google":  # shared drives (slice F2)
        client = gdrive.client_for(c)
        results = [gdrive.sync_drive(conn, c, client, ct["external_id"]) for ct in containers]
    elif c["kind"] == "github":
        connector = GitHubConnector(load_app(c["credential_ref"]), int(c["config"]["installation_id"]))
        results = [sync_repo(conn, c, connector, ct) for ct in containers]
    elif c["kind"] == "jira":
        client = jira.client_for(c)
        results = [jira.sync_project(conn, c, client, ct["external_id"]) for ct in containers]
    elif c["kind"] == "confluence":
        client = confluence.client_for(c)
        results = [confluence.sync_space(conn, c, client, ct["external_id"]) for ct in containers]
    elif c["kind"] == "slack":
        client = slack.client_for(c)
        results = [slack.sync_channel(conn, c, client, ct["external_id"]) for ct in containers]
    elif c["kind"] == "directory":
        results = [directory.sync_directory(conn, c, directory.client_for(c))]
    else:
        raise RuntimeError(f"no connector for kind {c['kind']!r}")
    retired = retire_unlisted(conn, c, c["kind"], [ct["external_id"] for ct in containers]) \
        if c["kind"] in OWNED_KINDS else 0  # a container it no longer reads keeps nothing (migration 0040)
    embedded = embed_missing(conn)
    bronze = materialize_bronze(conn)
    linked = {**links.refresh(conn), "personal_data": privacy.scan(conn)}  # map links (N), personal data (I5)
    with conn.transaction():
        conn.execute("UPDATE connections SET last_synced_at = now(), updated_at = now() WHERE id = %s", (cid,))
        audit(conn, c["org_id"], "connection.sync", {"connection_id": str(cid), "repos": results, "retired": retired, "embedded": embedded,
                                                     "bronze": bronze, **linked})
    return {"repos": len(results), "files": sum(r["files"] for r in results), "embedded": embedded}


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
    return {**counts, "tuples": len(tuples)}


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
                "slack": ["slack"]}  # what each kind of connection's documents are


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
    if p.get("credential_ref"):
        vault.destroy(p["credential_ref"])
        result["secrets"] = 1
    return result


HANDLERS = {"noop": handle_noop, "connection.retire": handle_connection_retire, "knowledge.seed": handle_knowledge_seed, "workspace.purge": handle_workspace_purge,
            "proposal.open_pr": lambda conn, job: curator.open_pr(conn, job["payload"]["proposal_id"]),
            "proposal.refresh": lambda conn, job: curator.refresh(conn, job["payload"]["proposal_id"]), "connection.discover": handle_connection_discover,
            "connection.sync": handle_connection_sync, "embeddings.backfill": handle_embeddings_backfill,
            "alerts.evaluate": lambda conn, job: alerting.evaluate(conn, RESYNC_MINUTES),  # on demand (slice P)
            "retention.apply": handle_retention_apply,
            "pipeline.push": handle_pipeline_push, "pipeline.retire": handle_pipeline_retire,
            "import.apply": handle_import_apply, "import.retire": handle_import_retire}
RETENTION_SQL = ("INSERT INTO jobs (org_id, kind) SELECT kl.current_org(), 'retention.apply' WHERE NOT EXISTS "
                 "(SELECT 1 FROM jobs WHERE kind = 'retention.apply' AND created_at > now() - interval '1 hour')")
HEARTBEAT_SQL = ("INSERT INTO heartbeats (org_id, service) VALUES (kl.current_org(), 'worker') "
                 "ON CONFLICT (org_id, service) DO UPDATE SET seen_at = now()")


# ------------------------------------------------------------------ loop
def finish(conn, job, error: str | None = None) -> None:
    if error is None:
        conn.execute("UPDATE jobs SET status = 'done', last_error = NULL, updated_at = now() WHERE id = %s",
                     (job["id"],))
    elif job["attempts"] >= job["max_attempts"]:
        conn.execute("UPDATE jobs SET status = 'failed', last_error = %s, updated_at = now() WHERE id = %s",
                     (error, job["id"]))
        if job["kind"] in ("connection.discover", "connection.sync"):
            conn.execute("UPDATE connections SET status = 'error', last_error = %s, updated_at = now() WHERE id = %s",
                         (error, job["payload"].get("connection_id")))
    else:
        delay = min(300, 5 * 2 ** (job["attempts"] - 1))
        conn.execute("UPDATE jobs SET status = 'queued', last_error = %s, "
                     "run_after = now() + %s * interval '1 second', updated_at = now() WHERE id = %s",
                     (error, delay, job["id"]))


def run(conn, job) -> None:
    handler = HANDLERS.get(job["kind"])
    started = time.monotonic()
    try:
        with telemetry.span("job", kind=job["kind"], attempt=job["attempts"]):
            if handler is None:
                raise RuntimeError(f"no handler for job kind {job['kind']!r}")
            result = handler(conn, job)
        finish(conn, job)
        telemetry.job_finished(job["kind"], "done", time.monotonic() - started)
        log.info("job %s %s done: %s", job["id"], job["kind"], result)
    except Exception as e:  # noqa: BLE001 - every failure is recorded on the job, never lost
        error = f"{type(e).__name__}: {e}"[:500]
        log.warning("job %s %s failed (attempt %s/%s): %s",
                    job["id"], job["kind"], job["attempts"], job["max_attempts"], error)
        finish(conn, job, error)
        telemetry.job_finished(job["kind"], "failed" if job["attempts"] >= job["max_attempts"] else "retried",
                               time.monotonic() - started)


def _stop(*_):
    global _stopping
    _stopping = True


def main() -> None:
    telemetry.setup("kl-worker")
    telemetry.register_worker_gauges()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while not _stopping:
        try:
            with psycopg.connect(INGEST_DB, row_factory=dict_row, autocommit=True) as conn:
                org_id = conn.execute("SELECT kl.resolve_org(%s) AS id", (config.ORG_SLUG,)).fetchone()["id"]
                if org_id is None:
                    raise SystemExit(f"worker: org {config.ORG_SLUG!r} not found")
                conn.execute("SELECT set_config('kl.org_id', %s, false)", (str(org_id),))
                log.info("worker ready (org %s; re-sync window %s min)", config.ORG_SLUG, RESYNC_MINUTES)
                next_schedule = 0.0
                while not _stopping:
                    if time.monotonic() >= next_schedule:
                        scheduled = conn.execute(SCHEDULE_SQL, (RESYNC_MINUTES,)).fetchall()
                        if scheduled:
                            log.info("scheduled re-sync for %s connection(s)", len(scheduled))
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
                        job = conn.execute(CLAIM_SQL).fetchone()
                    if job is None:
                        time.sleep(POLL_SECONDS)
                        continue
                    run(conn, job)
        except psycopg.OperationalError as e:
            log.warning("database unavailable (%s); retrying in 3s", type(e).__name__)
            time.sleep(3)


if __name__ == "__main__":
    main()
