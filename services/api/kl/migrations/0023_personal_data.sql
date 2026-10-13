-- Slice I5: personal-data detection. The worker flags documents that look like they hold personal data
-- (kl/privacy.py), recording which kinds and how many, never the values, with a suggested classification; a team's
-- admins review each flag and reclassify the document or dismiss the flag. A reclassification sets a floor: a later
-- re-index can make the document stricter, never laxer than an admin decided.
SET search_path = kl, public;

ALTER TABLE documents ADD COLUMN classification_floor text
  CHECK (classification_floor IN ('public', 'internal', 'confidential', 'restricted'));

-- The stricter of two classifications (NULL counts as no opinion).
CREATE FUNCTION kl.stricter(a text, b text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN a IS NULL THEN b WHEN b IS NULL THEN a
              WHEN array_position(ARRAY['public', 'internal', 'confidential', 'restricted'], a)
                >= array_position(ARRAY['public', 'internal', 'confidential', 'restricted'], b) THEN a
              ELSE b END
$$;

CREATE TABLE privacy_flags (                -- one row per document: its latest scan
  document_id  uuid PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
  org_id       uuid NOT NULL REFERENCES orgs(id),
  content_hash text NOT NULL,               -- the version scanned
  kinds        jsonb NOT NULL,              -- e.g. {"email": 1, "phone": 1, "resume_layout": 3}: counts, never values
  suggested    text CHECK (suggested IN ('confidential', 'restricted')),   -- NULL when nothing needs review
  status       text NOT NULL CHECK (status IN ('clear', 'open', 'reclassified', 'dismissed')),
  scanned_at   timestamptz NOT NULL DEFAULT now(),
  resolved_by  text,
  resolved_at  timestamptz
);
CREATE INDEX privacy_flags_open_idx ON privacy_flags (org_id) WHERE status = 'open';

ALTER TABLE privacy_flags ENABLE ROW LEVEL SECURITY;
CREATE POLICY ingest_in_org ON privacy_flags FOR ALL TO kl_ingest
  USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
-- A flag is visible, and resolvable, only to someone who can see its document: a review list never names a
-- document its reader can't open.
CREATE POLICY read_if_document_visible ON privacy_flags FOR SELECT TO kl_app USING (
  org_id = kl.current_org() AND EXISTS (SELECT 1 FROM documents d WHERE d.id = document_id));
CREATE POLICY resolve_if_document_visible ON privacy_flags FOR UPDATE TO kl_app
  USING (org_id = kl.current_org() AND EXISTS (SELECT 1 FROM documents d WHERE d.id = document_id))
  WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, UPDATE ON privacy_flags TO kl_ingest;
GRANT SELECT, UPDATE (status, resolved_by, resolved_at) ON privacy_flags TO kl_app;

-- Reclassify a document to at least `cls` (never laxer than it was), set that as its floor, and rebuild its bronze
-- concept so the concept's own metadata says so. Confined to the caller's org; the API checks who may call it.
CREATE FUNCTION kl.reclassify_document(doc uuid, cls text) RETURNS text
LANGUAGE sql SECURITY DEFINER SET search_path = kl, pg_temp AS $$
  UPDATE concepts SET source_hash = 'reclassified'  -- never a real hash, so the worker rebuilds it
   WHERE document_id = doc AND org_id = kl.current_org() AND tier = 'bronze';
  UPDATE documents SET classification = kl.stricter(classification, cls),
                       classification_floor = kl.stricter(classification_floor, cls)
  WHERE id = doc AND org_id = kl.current_org()
  RETURNING classification;
$$;
REVOKE ALL ON FUNCTION kl.reclassify_document(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kl.reclassify_document(uuid, text) TO kl_app;
