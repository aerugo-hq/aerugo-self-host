-- S7: data catalogs. A catalog connection (Dataplex, Glue, Purview, Unity Catalog, DataHub) reads entries, metadata
-- only, never data: each entry's description, columns, and owners become a document readable by whom the catalog
-- lets read it, and its name and lineage become assets and edges for the connecting team (K3's model), so the Map and
-- impact walks reach them. CI pushes dbt and Dataform manifests the same way, through a pipeline.
SET search_path = kl, public;

ALTER TABLE connections DROP CONSTRAINT IF EXISTS connections_kind_check;
ALTER TABLE connections ADD CONSTRAINT connections_kind_check
  CHECK (kind IN ('github', 'google', 'jira', 'confluence', 'slack', 'microsoft', 'notion', 'directory', 'catalog'));

-- Dataplex and Glue are read as the runner's own identity, so they have no credential at all; every other connection
-- still names one.
ALTER TABLE connections ALTER COLUMN credential_ref DROP NOT NULL;
ALTER TABLE connections ADD CONSTRAINT connections_credential_check
  CHECK (credential_ref IS NOT NULL OR (kind = 'catalog' AND config->>'provider' IN ('dataplex', 'glue')));

-- The worker writes a catalog's assets and lineage, as the API writes pushed lineage.
GRANT SELECT, INSERT, UPDATE ON assets, lineage_edges TO kl_ingest;

-- Who owns an asset, as its catalog names them (by email): who to ask about a page that describes it (K6). Shown
-- only to members of the team the asset belongs to, as the asset is. Replaced each sync of the connection it came from.
CREATE TABLE asset_owners (
  org_id        uuid NOT NULL REFERENCES orgs(id),
  workspace_id  uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  asset_id      uuid NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  email         text NOT NULL CHECK (length(email) <= 254 AND email = lower(email)),
  source        text NOT NULL CHECK (length(source) BETWEEN 1 AND 100),  -- a connection's id, or a pipeline's
  PRIMARY KEY (asset_id, email, source)
);
ALTER TABLE asset_owners ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON asset_owners USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, DELETE ON asset_owners TO kl_app, kl_ingest;

-- A directory person's own id where the provider has one (Entra's object id), so a catalog that grants by id (Purview)
-- can be read as people.
ALTER TABLE directory_people ADD COLUMN IF NOT EXISTS external_id text CHECK (external_id IS NULL OR length(external_id) <= 200);
CREATE INDEX IF NOT EXISTS directory_people_external_idx ON directory_people (org_id, external_id) WHERE external_id IS NOT NULL;
