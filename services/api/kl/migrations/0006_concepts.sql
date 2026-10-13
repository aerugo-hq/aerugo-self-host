-- M3, slice C1: OKF concepts, their versions, and the progression timeline.
-- Bronze concepts are stored here, not in Git (regulatory deletes work, and reviewers aren't flooded).
-- A concept's readers mirror its source; live ACLs never go into OKF files.
SET search_path = kl, public;

CREATE TABLE concepts (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES orgs(id),
  concept_key    text NOT NULL,   -- x-kl.id; for bronze, the source document's external key
  tier           text NOT NULL CHECK (tier IN ('bronze', 'silver', 'gold')),
  document_id    uuid REFERENCES documents(id) ON DELETE CASCADE,  -- bronze: deleted with its source
  title          text NOT NULL,
  acl_principals text[] NOT NULL,
  latest_hash    text,
  source_hash    text,            -- the source content hash the latest version was built from
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, concept_key)
);
CREATE INDEX concepts_document_idx ON concepts (document_id);
CREATE INDEX concepts_acl_idx ON concepts USING gin (acl_principals);

CREATE TABLE concept_versions (
  concept_id     uuid NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
  org_id         uuid NOT NULL REFERENCES orgs(id),
  hash           text NOT NULL,   -- sha256 of the OKF text: versions are content-addressed
  content        text NOT NULL,   -- the OKF document, frontmatter and body
  source_version text,            -- commit or revision it was derived from
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (concept_id, hash)
);

CREATE TABLE concept_events (
  id         bigserial PRIMARY KEY,
  org_id     uuid NOT NULL REFERENCES orgs(id),
  concept_id uuid NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
  kind       text NOT NULL,       -- bronze_version | compile_proposed | reviewed | merged | indexed | cited | stale | deprecated
  detail     jsonb NOT NULL DEFAULT '{}',
  at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX concept_events_concept_idx ON concept_events (concept_id, at);

ALTER TABLE concepts         ENABLE ROW LEVEL SECURITY;
ALTER TABLE concept_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE concept_events   ENABLE ROW LEVEL SECURITY;

-- Readers: a concept is visible only to holders of one of its principals. Versions and events are
-- visible only when their concept is (the subquery runs under the concepts policy).
CREATE POLICY read_by_acl ON concepts FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND acl_principals && kl.current_principals());
CREATE POLICY read_with_concept ON concept_versions FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND EXISTS (SELECT 1 FROM concepts c WHERE c.id = concept_id));
CREATE POLICY read_with_concept ON concept_events FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND EXISTS (SELECT 1 FROM concepts c WHERE c.id = concept_id));
-- Worker: writes inside its org.
CREATE POLICY ingest_in_org ON concepts         FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY ingest_in_org ON concept_versions FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY ingest_in_org ON concept_events   FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());

GRANT SELECT ON concepts, concept_versions, concept_events TO kl_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON concepts, concept_versions, concept_events TO kl_ingest;
GRANT USAGE ON SEQUENCE concept_events_id_seq TO kl_ingest;
