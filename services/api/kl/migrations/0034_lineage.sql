-- Slice K3 (ADR 0010): systems and data. Lineage arrives in OpenLineage's shape (each event: a job, the datasets it
-- read, the datasets it wrote) and becomes assets (datasets and jobs) and lineage edges (a dataset into the job that
-- read it, a job into the dataset it wrote), so data can be followed from a source table to a dashboard. Metadata
-- only: names, never the data. Each row belongs to the team whose pipeline reported it; the API shows lineage only to
-- members of that team, so another team's pipelines stay theirs. Rows cascade from the team.
SET search_path = kl, public;

CREATE TABLE assets (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES orgs(id),
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,  -- the team whose lineage reported it
  kind        text NOT NULL CHECK (kind IN ('dataset', 'job')),
  namespace   text NOT NULL CHECK (length(namespace) BETWEEN 1 AND 200),   -- e.g. edw, bigquery, dataform
  name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 300),        -- e.g. ORDER_HIST, edp.orders, ORD_LOAD
  first_seen  timestamptz NOT NULL DEFAULT now(),
  last_seen   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, kind, namespace, name)
);

CREATE TABLE lineage_edges (
  org_id      uuid NOT NULL REFERENCES orgs(id),
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  source_id   uuid NOT NULL REFERENCES assets(id) ON DELETE CASCADE,  -- data flows from here...
  target_id   uuid NOT NULL REFERENCES assets(id) ON DELETE CASCADE,  -- ...to here
  last_seen   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source_id, target_id),
  CHECK (source_id <> target_id)
);
CREATE INDEX lineage_edges_target_idx ON lineage_edges (target_id);

ALTER TABLE assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE lineage_edges ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON assets USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY org_isolation ON lineage_edges USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, UPDATE ON assets, lineage_edges TO kl_app;
