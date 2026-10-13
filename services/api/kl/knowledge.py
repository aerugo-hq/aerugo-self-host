"""Curated knowledge (M3, slice C2): index an OKF bundle as silver and gold concepts, with the entity graph.

A curated concept is stored as a document, so search, ask, RLS, and the OpenFGA check apply to it exactly
as to evidence, plus a concept row. Its readers are the bundle's readers; its x-kl.audience is checked on
top at read time, so the narrower rule wins. Its x-kl.relations become edges. Used for the dev seed
bundles now, and for `*-knowledge` repos in C2b."""
import hashlib
import logging
from pathlib import Path

from psycopg.types.json import Jsonb

from . import okf, originals
from .markdown import parse as chunk_markdown

log = logging.getLogger("kl.knowledge")

UPSERT_DOCUMENT = """
INSERT INTO documents (org_id, source_kind, source_uri, source_version, external_key, title, content_hash,
                       classification, acl_principals, container_ref, source_path, tier, audience)
VALUES (%s, 'okf', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (org_id, external_key) DO UPDATE SET
  source_uri = EXCLUDED.source_uri, source_version = EXCLUDED.source_version, title = EXCLUDED.title,
  content_hash = EXCLUDED.content_hash,
  classification = kl.stricter(EXCLUDED.classification, documents.classification_floor),  -- never laxer than an admin set (I5)
  acl_principals = EXCLUDED.acl_principals, acl_synced_at = now(), source_path = EXCLUDED.source_path,
  tier = EXCLUDED.tier, audience = EXCLUDED.audience
-- Only a document from this same bundle may be replaced: a bundle can't take over another team's id.
WHERE documents.container_ref = EXCLUDED.container_ref
RETURNING id
"""

UPSERT_CONCEPT = """
INSERT INTO concepts (org_id, concept_key, tier, document_id, title, acl_principals, latest_hash, source_hash,
                      audience, aliases)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (org_id, concept_key) DO UPDATE SET
  tier = EXCLUDED.tier, title = EXCLUDED.title, acl_principals = EXCLUDED.acl_principals,
  latest_hash = EXCLUDED.latest_hash, source_hash = EXCLUDED.source_hash, audience = EXCLUDED.audience,
  aliases = EXCLUDED.aliases
WHERE concepts.document_id = EXCLUDED.document_id
RETURNING id
"""


def load_dir(root: str) -> dict[str, str]:
    base = Path(root)
    return {"/" + p.relative_to(base).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(base.rglob("*.md")) if ".git" not in p.parts}


def index_bundle(conn, *, org_id, workspace_id, container_ref: str, version: str, files: dict[str, str],
                 acl: list[str], uri_for) -> dict:
    """Index one bundle at one version. An invalid concept keeps its last good version and is reported;
    a concept whose file left the bundle is deleted with everything derived from it."""
    problems, parsed = okf.check_files(files, knowledge=True)
    invalid = {p.split(":", 1)[0] for p in problems}
    good = {path: c for path, c in parsed.items() if path not in invalid}
    key_of = {path: c.meta["x-kl"]["id"] for path, c in good.items()}
    keep, indexed = set(), 0
    with conn.transaction():
        existing = conn.execute("SELECT id, external_key, content_hash, source_path FROM documents "
                                "WHERE container_ref = %s", (container_ref,)).fetchall()
        hashes = {r["external_key"]: r["content_hash"] for r in existing}
        for path, concept in good.items():
            xkl, key = concept.meta["x-kl"], key_of[path]
            text = okf.render(concept)
            digest = hashlib.sha256(text.encode()).hexdigest()
            title = str(concept.meta.get("title") or key)[:300]
            tier = "gold" if concept.meta.get("verified") else "silver"
            audience = xkl.get("audience")
            row = conn.execute(UPSERT_DOCUMENT, (
                org_id, uri_for(path), version, key, title, digest, xkl.get("classification", "internal"), acl,
                container_ref, path, tier, audience)).fetchone()
            if not row:
                problems.append(f"{path}: `x-kl.id` {key} already belongs to another source; pick another id")
                continue
            doc_id = row["id"]
            if hashes.get(key) != digest:  # new or changed: re-chunk (embeddings follow via the worker)
                conn.execute("DELETE FROM chunks WHERE document_id = %s", (doc_id,))
                for ordinal, (heading, body) in enumerate(chunk_markdown(concept.body, fallback_title=title)[1]):
                    conn.execute("INSERT INTO chunks (org_id, document_id, ordinal, heading_path, body, acl_principals) "
                                 "VALUES (%s, %s, %s, %s, %s, %s)", (org_id, doc_id, ordinal, heading, body, acl))
            else:  # unchanged content: readers may still have changed
                conn.execute("UPDATE chunks SET acl_principals = %s WHERE document_id = %s", (acl, doc_id))
                conn.execute("UPDATE chunk_embeddings SET acl_principals = %s "
                             "WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = %s)", (acl, doc_id))
            aliases = sorted({a.strip().lower() for a in xkl.get("aliases") or []})
            concept_row = conn.execute(UPSERT_CONCEPT, (org_id, key, tier, doc_id, title, acl, digest, digest,
                                                        audience, aliases)).fetchone()
            if not concept_row:
                problems.append(f"{path}: concept {key} already exists for another document")
                continue
            concept_id = concept_row["id"]
            key = originals.team_key(workspace_id)  # stored encrypted under its team's key (slice I3b)
            if conn.execute("INSERT INTO concept_versions (concept_id, org_id, hash, content, source_version, key_ref) "
                            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING hash",
                            (concept_id, org_id, digest, originals.seal(text, key), version, key)).fetchone():
                conn.execute("INSERT INTO concept_events (org_id, concept_id, kind, detail) VALUES (%s, %s, 'indexed', %s)",
                             (org_id, concept_id, Jsonb({"hash": digest, "version": version, "path": path, "tier": tier})))
                indexed += 1
            conn.execute("DELETE FROM concept_edges WHERE from_concept_id = %s", (concept_id,))
            for relation in xkl.get("relations") or []:
                target = str(relation["target"])
                to_key = key_of.get(target) if target.startswith("/") else target[3:]
                if to_key and to_key != key:
                    conn.execute("INSERT INTO concept_edges (org_id, from_concept_id, relation, to_key) "
                                 "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                                 (org_id, concept_id, relation["type"], to_key))
            conn.execute("INSERT INTO workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, %s) "
                         "ON CONFLICT DO NOTHING", (workspace_id, doc_id, org_id))
            keep.add(key)
        # Files that are gone from the bundle take their concepts with them; invalid files keep theirs.
        gone = [r["id"] for r in existing if r["external_key"] not in keep and r["source_path"] not in files]
        if gone:
            conn.execute("DELETE FROM documents WHERE id = ANY(%s)", (gone,))
    for problem in problems:
        log.warning("knowledge bundle %s: %s", container_ref, problem)
    return {"concepts": len(keep), "indexed": indexed, "removed": len(gone), "problems": problems}
