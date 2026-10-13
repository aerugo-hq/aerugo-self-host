-- M1 slice 2: GitHub content sync with mirrored permissions, plus identity links.
SET search_path = kl, public;

-- Where a synced document came from, so a sync can find what disappeared from its repo.
ALTER TABLE documents ADD COLUMN container_ref text;  -- e.g. owner/repo
ALTER TABLE documents ADD COLUMN source_path text;    -- path inside the container
CREATE INDEX documents_container_idx ON documents (org_id, source_kind, container_ref);

ALTER TABLE connections ADD COLUMN last_synced_at timestamptz;
ALTER TABLE connection_containers ADD COLUMN cursor text;  -- last synced commit sha

-- Links a source identity (ext:github.<login>) to a canonical user (user:<id>). ACL arrays store
-- source identities; callers' principals are expanded with their links at query time, so linking
-- or unlinking takes effect immediately, with no re-sync. An unlinked identity grants nothing.
CREATE TABLE principal_aliases (
  org_id      uuid NOT NULL REFERENCES orgs(id),
  source      text NOT NULL CHECK (source ~ '^ext:[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$'),
  canonical   text NOT NULL CHECK (canonical ~ '^user:[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$'),
  verified_by text NOT NULL,  -- how the link was made: dev-seed | idp | admin:<id>
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, source)
);
CREATE INDEX principal_aliases_canonical_idx ON principal_aliases (org_id, canonical);
ALTER TABLE principal_aliases ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON principal_aliases USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT ON principal_aliases TO kl_app, kl_ingest;

-- Cell infrastructure (not tenant data): which authorization model versions provision has written.
CREATE TABLE authz_model_versions (
  sha256     text PRIMARY KEY,
  model_id   text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now()
);
