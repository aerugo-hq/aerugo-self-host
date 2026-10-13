-- Slice N: automatic links on the map. The worker computes them after indexing (kl/links.py).
-- Visibility follows the documents: for the app role, a semantic edge is readable only when both of its
-- documents are, a mention only when its document is, and an entity only when one of its mentions is.
-- The policies' subqueries run under the documents' own RLS, so this holds for any query, not only the map.
-- Everything here cascades from documents, so purging a team removes it with the team's content.
SET search_path = kl, public;

CREATE TABLE document_vectors (           -- the mean of a document's chunk vectors, per embedding model
  org_id       uuid NOT NULL REFERENCES orgs(id),
  document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  model_id     uuid NOT NULL REFERENCES embedding_models(id),
  content_hash text NOT NULL,             -- the document version it was computed from
  embedding    vector NOT NULL,
  computed_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (document_id, model_id)
);

CREATE TABLE semantic_edges (             -- mutual nearest neighbors, one row per pair and model
  org_id      uuid NOT NULL REFERENCES orgs(id),
  source_id   uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  target_id   uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  model_id    uuid NOT NULL REFERENCES embedding_models(id),
  score       real NOT NULL,              -- cosine similarity
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source_id, target_id, model_id),
  CHECK (source_id < target_id)
);
CREATE INDEX semantic_edges_model_idx ON semantic_edges (model_id);

CREATE TABLE entities (
  id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES orgs(id),
  kind   text NOT NULL CHECK (kind IN ('system', 'team', 'person', 'channel')),
  key    text NOT NULL,                   -- normalized
  name   text NOT NULL,                   -- as written
  UNIQUE (org_id, kind, key)
);

CREATE TABLE entity_mentions (
  entity_id   uuid NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  org_id      uuid NOT NULL REFERENCES orgs(id),
  mentions    int NOT NULL CHECK (mentions > 0),
  PRIMARY KEY (entity_id, document_id)
);
CREATE INDEX entity_mentions_document_idx ON entity_mentions (document_id);

CREATE TABLE entity_scans (               -- which document version was last scanned for entities
  document_id  uuid PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
  org_id       uuid NOT NULL REFERENCES orgs(id),
  content_hash text NOT NULL,
  scanned_at   timestamptz NOT NULL DEFAULT now()
);

-- An entity no document mentions any more (rescanned, deleted, or purged) goes with its last mention. The
-- function runs as its owner so it sees every remaining mention, not just the ones the deleting role can.
CREATE FUNCTION kl.drop_orphan_entities() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = kl, pg_temp AS $$
BEGIN
  DELETE FROM kl.entities e
  WHERE e.id IN (SELECT DISTINCT entity_id FROM gone)
    AND NOT EXISTS (SELECT 1 FROM kl.entity_mentions m WHERE m.entity_id = e.id);
  RETURN NULL;
END $$;
CREATE TRIGGER entity_mentions_drop_orphans AFTER DELETE ON entity_mentions
  REFERENCING OLD TABLE AS gone FOR EACH STATEMENT EXECUTE FUNCTION kl.drop_orphan_entities();

ALTER TABLE document_vectors ENABLE ROW LEVEL SECURITY;
ALTER TABLE semantic_edges   ENABLE ROW LEVEL SECURITY;
ALTER TABLE entities         ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_mentions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_scans     ENABLE ROW LEVEL SECURITY;

-- The worker computes everything, inside its org.
CREATE POLICY ingest_in_org ON document_vectors FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY ingest_in_org ON semantic_edges   FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY ingest_in_org ON entities         FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY ingest_in_org ON entity_mentions  FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY ingest_in_org ON entity_scans     FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());

-- The app reads only what the caller's documents allow.
CREATE POLICY read_if_both_visible ON semantic_edges FOR SELECT TO kl_app USING (
  org_id = kl.current_org()
  AND EXISTS (SELECT 1 FROM documents d WHERE d.id = source_id)
  AND EXISTS (SELECT 1 FROM documents d WHERE d.id = target_id));
CREATE POLICY read_if_document_visible ON entity_mentions FOR SELECT TO kl_app USING (
  org_id = kl.current_org() AND EXISTS (SELECT 1 FROM documents d WHERE d.id = document_id));
CREATE POLICY read_if_mentioned_visibly ON entities FOR SELECT TO kl_app USING (
  org_id = kl.current_org() AND EXISTS (SELECT 1 FROM entity_mentions m WHERE m.entity_id = id));

GRANT SELECT, INSERT, UPDATE, DELETE ON document_vectors, semantic_edges, entities, entity_mentions, entity_scans TO kl_ingest;
GRANT SELECT ON semantic_edges, entities, entity_mentions TO kl_app;
