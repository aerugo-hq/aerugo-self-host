-- M0 schema. Invariant: every table holding tenant data has org_id and row-level security.
-- kl_owner owns everything and is not subject to RLS (it is never used by a service).
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA kl AUTHORIZATION kl_owner;
SET search_path = kl, public;

-- Request context. The API sets these per transaction with set_config(..., true).
-- Unset or empty context becomes NULL, and NULL comparisons match nothing: RLS fails closed.
CREATE FUNCTION kl.current_org() RETURNS uuid
  LANGUAGE sql STABLE
  AS $$ SELECT NULLIF(current_setting('kl.org_id', true), '')::uuid $$;

CREATE FUNCTION kl.current_principals() RETURNS text[]
  LANGUAGE sql STABLE
  AS $$ SELECT string_to_array(NULLIF(current_setting('kl.principals', true), ''), ',') $$;

-- ---------------------------------------------------------------- tenancy
CREATE TABLE orgs (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug       text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,39}$'),
  name       text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE workspaces (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          uuid NOT NULL REFERENCES orgs(id),
  slug            text NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,39}$'),
  name            text NOT NULL,
  discoverability text NOT NULL DEFAULT 'org' CHECK (discoverability IN ('org', 'private')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, slug)
);

-- Resolve an org id without granting the app role a way around RLS on orgs.
CREATE FUNCTION kl.resolve_org(p_slug text) RETURNS uuid
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = kl, pg_temp
  AS $$ SELECT id FROM kl.orgs WHERE slug = p_slug $$;
REVOKE ALL ON FUNCTION kl.resolve_org(text) FROM PUBLIC;

-- ---------------------------------------------------------------- evidence layer
-- A source document is stored once per org and linked into every workspace that reaches it.
CREATE TABLE documents (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES orgs(id),
  source_kind    text NOT NULL,                 -- github | gdrive | confluence | ...
  source_uri     text NOT NULL,                 -- deep link back to the system of record
  source_version text,                          -- commit sha, etag, or revision id
  external_key   text NOT NULL,                 -- stable id; also the OpenFGA document id
  title          text NOT NULL,
  content_hash   text NOT NULL,
  classification text NOT NULL DEFAULT 'internal'
                 CHECK (classification IN ('public', 'internal', 'confidential', 'restricted')),
  acl_principals text[] NOT NULL,               -- direct + inherited grants, as principal ids
  acl_synced_at  timestamptz NOT NULL DEFAULT now(),
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, external_key)
);
CREATE INDEX documents_acl_idx ON documents USING gin (acl_principals);

CREATE TABLE workspace_documents (
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  org_id       uuid NOT NULL REFERENCES orgs(id),
  PRIMARY KEY (workspace_id, document_id)
);

CREATE TABLE chunks (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES orgs(id),
  document_id    uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  ordinal        int  NOT NULL,
  heading_path   text,
  body           text NOT NULL,
  tsv            tsvector GENERATED ALWAYS AS
                 (to_tsvector('english'::regconfig, coalesce(heading_path, '') || ' ' || body)) STORED,
  acl_principals text[] NOT NULL,               -- denormalized from the document for pre-filtering
  UNIQUE (document_id, ordinal)
);
CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv);
CREATE INDEX chunks_acl_idx ON chunks USING gin (acl_principals);

-- Embedding model per org (workspace_id NULL) or per workspace override.
CREATE TABLE embedding_models (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES orgs(id),
  workspace_id   uuid REFERENCES workspaces(id),
  provider       text NOT NULL,
  model          text NOT NULL,
  dims           int  NOT NULL CHECK (dims > 0),
  credential_ref text,
  status         text NOT NULL DEFAULT 'building' CHECK (status IN ('building', 'active', 'retired')),
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX embedding_models_one_active_default
  ON embedding_models (org_id) WHERE workspace_id IS NULL AND status = 'active';
CREATE UNIQUE INDEX embedding_models_one_active_override
  ON embedding_models (workspace_id) WHERE workspace_id IS NOT NULL AND status = 'active';

-- One row per (chunk, model). Each model gets its own partial ANN index when it is registered:
--   CREATE INDEX ON kl.chunk_embeddings USING hnsw ((embedding::vector(768)) vector_cosine_ops)
--     WHERE model_id = '<uuid>';
CREATE TABLE chunk_embeddings (
  chunk_id       uuid NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  model_id       uuid NOT NULL REFERENCES embedding_models(id),
  org_id         uuid NOT NULL REFERENCES orgs(id),
  acl_principals text[] NOT NULL,
  embedding      vector NOT NULL,
  PRIMARY KEY (chunk_id, model_id)
);
CREATE INDEX chunk_embeddings_acl_idx ON chunk_embeddings USING gin (acl_principals);

-- ---------------------------------------------------------------- model registry
CREATE TABLE model_endpoints (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                  uuid NOT NULL REFERENCES orgs(id),
  workspace_id            uuid NOT NULL REFERENCES workspaces(id),
  name                    text NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9-]{0,39}$'),
  provider                text NOT NULL
                          CHECK (provider IN ('openai_compatible', 'anthropic', 'vertex', 'bedrock', 'azure_openai')),
  model                   text NOT NULL,
  base_url                text,
  credential_ref          text NOT NULL,        -- path in the secrets backend. Never the secret.
  allowed_classifications text[] NOT NULL DEFAULT '{public,internal}',
  baa_covered             boolean NOT NULL DEFAULT false,
  created_by              text NOT NULL,
  created_at              timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, name)
);

-- ---------------------------------------------------------------- audit (append-only)
CREATE TABLE audit_events (
  id      bigserial PRIMARY KEY,
  org_id  uuid NOT NULL REFERENCES orgs(id),
  at      timestamptz NOT NULL DEFAULT now(),
  actor   text NOT NULL,
  action  text NOT NULL,
  scope   text[] NOT NULL DEFAULT '{}',
  details jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX audit_events_org_at_idx ON audit_events (org_id, at DESC);

-- ---------------------------------------------------------------- row-level security
ALTER TABLE orgs                ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspaces          ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents           ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks              ENABLE ROW LEVEL SECURITY;
ALTER TABLE embedding_models    ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunk_embeddings    ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_endpoints     ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_events        ENABLE ROW LEVEL SECURITY;

-- Org isolation (all roles).
CREATE POLICY org_isolation ON orgs                USING (id = kl.current_org());
CREATE POLICY org_isolation ON workspaces          USING (org_id = kl.current_org());
CREATE POLICY org_isolation ON workspace_documents USING (org_id = kl.current_org());
CREATE POLICY org_isolation ON embedding_models    USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY org_isolation ON model_endpoints     USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());

-- Content: the read path sees a row only if the caller holds one of its principals.
CREATE POLICY read_by_acl ON documents FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND acl_principals && kl.current_principals());
CREATE POLICY read_by_acl ON chunks FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND acl_principals && kl.current_principals());
CREATE POLICY read_by_acl ON chunk_embeddings FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND acl_principals && kl.current_principals());

-- Content: the worker writes inside its org.
CREATE POLICY ingest_in_org ON documents        FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY ingest_in_org ON chunks           FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY ingest_in_org ON chunk_embeddings FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());

-- Audit: insert within the org; reading is gated to auditors by the API.
CREATE POLICY audit_insert ON audit_events FOR INSERT TO kl_app, kl_ingest WITH CHECK (org_id = kl.current_org());
CREATE POLICY audit_read   ON audit_events FOR SELECT TO kl_app USING (org_id = kl.current_org());

-- ---------------------------------------------------------------- grants
ALTER ROLE kl_app    SET search_path = kl, public;
ALTER ROLE kl_ingest SET search_path = kl, public;

GRANT USAGE ON SCHEMA kl TO kl_app, kl_ingest;
GRANT EXECUTE ON FUNCTION kl.resolve_org(text) TO kl_app, kl_ingest;

GRANT SELECT ON orgs, workspaces, workspace_documents, documents, chunks,
                embedding_models, chunk_embeddings TO kl_app;
GRANT SELECT, INSERT ON model_endpoints TO kl_app;
GRANT SELECT, INSERT ON audit_events TO kl_app;            -- no UPDATE or DELETE: append-only

GRANT SELECT ON orgs, workspaces, embedding_models TO kl_ingest;
GRANT SELECT, INSERT, UPDATE, DELETE ON documents, workspace_documents, chunks, chunk_embeddings TO kl_ingest;
GRANT INSERT ON audit_events TO kl_ingest;

GRANT USAGE ON SEQUENCE audit_events_id_seq TO kl_app, kl_ingest;
