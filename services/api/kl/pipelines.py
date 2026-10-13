"""Pipeline identity and pushes (slice J1, ADR 0007).

CI pipelines push documents into a team with the workload token their CI already issues (GitHub Actions, or any OIDC
issuer listed in KL_PIPELINE_ISSUERS), so there are no long-lived keys. A token is accepted when a trusted issuer
signed it (keys are fetched only from issuers on that list, so a token can't point the API at another server), it's
for audience kl-ingest and still valid, and it names a repository (and, if one was set, a ref) that the team's admins
registered as a pipeline. That registration is the pipeline's grant to write into the team.

Every pushed document carries its readers and classification. Readers must each hold a role on the team, so a
pipeline can publish to the whole org only where the team itself admits the whole org. A pipeline's documents are
its own: their keys are namespaced to it, so it can't overwrite another pipeline's documents or a connector's.
"""
import hashlib

import httpx
import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

from . import config, jwks
from .authz import fga
from .markdown import parse as parse_markdown

ROLES = ("admin", "curator", "member", "auditor")
_jwks: dict[str, PyJWKClient] = {}


class PipelineTokenError(Exception):
    """The token isn't one a trusted CI issuer signed for kl-ingest. The message is safe to return."""


def _keys(issuer: str) -> PyJWKClient:
    if issuer not in _jwks:
        meta = httpx.get(f"{issuer}/.well-known/openid-configuration", timeout=10)
        meta.raise_for_status()
        _jwks[issuer] = jwks.client(meta.json()["jwks_uri"])
    return _jwks[issuer]


def verify(token: str) -> dict:
    """A CI workload token's claims, if a trusted issuer signed it for kl-ingest and it's still valid."""
    try:
        raw = jwt.decode(token, options={"verify_signature": False}).get("iss") or ""
    except jwt.InvalidTokenError as e:
        raise PipelineTokenError("not a token") from e
    issuer = raw.rstrip("/")
    if issuer not in config.PIPELINE_ISSUERS:
        raise PipelineTokenError("the token's issuer isn't trusted")
    try:
        return jwks.decode(_keys(issuer), token, algorithms=["RS256"], audience=config.PIPELINE_AUDIENCE, issuer=raw,
                           options={"require": ["exp", "iat", "iss", "aud"]})
    except (jwt.InvalidTokenError, PyJWKClientError, httpx.HTTPError, KeyError, ValueError) as e:
        raise PipelineTokenError("the token isn't valid") from e


def document_key(pipeline_id, key: str) -> str:
    """A pushed document's id: namespaced to its pipeline, and safe as an OpenFGA object id."""
    return f"pl-{str(pipeline_id)[:8]}-{hashlib.sha256(key.encode()).hexdigest()[:20]}"


def allowed_readers(ws: str) -> set[str]:
    """Who a pipeline may grant on a team: the principals holding a role on it (a group, or the org where the team
    admits the whole org, counts as itself)."""
    return {t["user"].split("#", 1)[0] for t in fga.read({"object": f"workspace:{ws}"}) if t["relation"] in ROLES}


def viewer_tuples(key: str, readers) -> set:
    return {(p if p.startswith("user:") else f"{p}#member", "viewer", f"document:{key}") for p in readers}


UPSERT_SQL = """
INSERT INTO documents (org_id, source_kind, source_uri, source_version, external_key, title, content_hash,
                       classification, acl_principals, container_ref, source_path)
VALUES (%s, 'pipeline', %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (org_id, external_key) DO UPDATE SET
  source_uri = EXCLUDED.source_uri, source_version = EXCLUDED.source_version, title = EXCLUDED.title,
  content_hash = EXCLUDED.content_hash,
  classification = kl.stricter(EXCLUDED.classification, documents.classification_floor),
  acl_principals = EXCLUDED.acl_principals, acl_synced_at = now(), source_path = EXCLUDED.source_path
WHERE documents.container_ref = EXCLUDED.container_ref  -- only the pipeline that pushed a document replaces it
RETURNING id
"""


def apply_push(conn, push: dict) -> dict:
    """Apply a staged push as kl_ingest: its upserts and deletes in one transaction, then authorization to match.
    Either order fails safe: until the index and OpenFGA agree, the post-check drops the document."""
    container, org_id = f"pipeline:{push['pipeline_id']}", push["org_id"]
    upserted, deleted, writes, deletes = 0, 0, set(), set()
    with conn.transaction():
        for doc in push["payload"].get("upserts", []):
            key, readers = document_key(push["pipeline_id"], doc["key"]), sorted(set(doc["readers"]))
            digest = hashlib.sha256(doc["markdown"].encode()).hexdigest()
            prev = conn.execute("SELECT content_hash FROM documents WHERE external_key = %s", (key,)).fetchone()
            row = conn.execute(UPSERT_SQL, (org_id, doc["uri"], str(push["id"]), key, doc["title"][:300], digest,
                                            doc["classification"], readers, container, doc["key"])).fetchone()
            if not row:
                continue
            if not prev or prev["content_hash"] != digest:
                conn.execute("DELETE FROM chunks WHERE document_id = %s", (row["id"],))
                _, sections = parse_markdown(doc["markdown"], fallback_title=doc["title"])
                for ordinal, (heading, body) in enumerate(sections):
                    conn.execute("INSERT INTO chunks (org_id, document_id, ordinal, heading_path, body, acl_principals) "
                                 "VALUES (%s, %s, %s, %s, %s, %s)", (org_id, row["id"], ordinal, heading, body, readers))
            else:  # same text, maybe new readers
                conn.execute("UPDATE chunks SET acl_principals = %s WHERE document_id = %s", (readers, row["id"]))
                conn.execute("UPDATE chunk_embeddings SET acl_principals = %s "
                             "WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = %s)", (readers, row["id"]))
            conn.execute("UPDATE concepts SET acl_principals = %s WHERE document_id = %s", (readers, row["id"]))
            conn.execute("INSERT INTO workspace_documents (workspace_id, document_id, org_id) VALUES (%s, %s, %s) "
                         "ON CONFLICT DO NOTHING", (push["workspace_id"], row["id"], org_id))
            want = viewer_tuples(key, readers)
            have = {(t["user"], t["relation"], t["object"]) for t in fga.read({"object": f"document:{key}"})}
            writes |= want - have
            deletes |= have - want
            upserted += 1
        for name in push["payload"].get("deletes", []):
            key = document_key(push["pipeline_id"], name)
            if conn.execute("DELETE FROM documents WHERE external_key = %s AND container_ref = %s RETURNING id",
                            (key, container)).fetchone():  # chunks, embeddings, concepts, map links cascade
                deletes |= {(t["user"], t["relation"], t["object"]) for t in fga.read({"object": f"document:{key}"})}
                deleted += 1
    if writes or deletes:
        fga.write(writes=writes, deletes=deletes)
    return {"upserted": upserted, "deleted": deleted}
