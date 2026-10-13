-- M3, slice C2: curated concepts (silver and gold) and the entity graph.
SET search_path = kl, public;

-- Curated documents come from OKF bundles; everything synced so far is evidence. Bronze concepts are
-- built from evidence only. A curated document's x-kl.audience is checked at read time on top of its
-- readers, so the narrower rule always wins.
ALTER TABLE documents ADD COLUMN tier text NOT NULL DEFAULT 'evidence' CHECK (tier IN ('evidence', 'silver', 'gold'));
ALTER TABLE documents ADD COLUMN audience text;

ALTER TABLE concepts ADD COLUMN audience text;
ALTER TABLE concepts ADD COLUMN aliases text[] NOT NULL DEFAULT '{}';  -- lowercased, for exact lookup
CREATE INDEX concepts_aliases_idx ON concepts USING gin (aliases);

-- Typed edges from x-kl.relations. An edge is visible only when the concept it starts from is visible.
-- Its target is checked separately at read time, so an edge never reveals a hidden concept.
CREATE TABLE concept_edges (
  org_id          uuid NOT NULL REFERENCES orgs(id),
  from_concept_id uuid NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
  relation        text NOT NULL,
  to_key          text NOT NULL,
  PRIMARY KEY (from_concept_id, relation, to_key)
);
CREATE INDEX concept_edges_to_idx ON concept_edges (org_id, to_key);

ALTER TABLE concept_edges ENABLE ROW LEVEL SECURITY;
CREATE POLICY read_with_concept ON concept_edges FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND EXISTS (SELECT 1 FROM concepts c WHERE c.id = from_concept_id));
CREATE POLICY ingest_in_org ON concept_edges FOR ALL TO kl_ingest
  USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());

GRANT SELECT ON concept_edges TO kl_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON concept_edges TO kl_ingest;
