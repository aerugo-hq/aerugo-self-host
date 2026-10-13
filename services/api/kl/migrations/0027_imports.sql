-- Slice J2: importing Confluence and Notion exports. A team's admins name an import, choose its readers and
-- classification (an export carries no permissions to trust), and upload a zip; the worker applies it through the
-- same path as connectors. A newer export under the same name updates the import, and pages no longer in it are
-- removed. An upload is cleared from staging once it's applied.
SET search_path = kl, public;

CREATE TABLE imports (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES orgs(id),
  workspace_id   uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name           text NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9-]{0,39}$'),
  kind           text NOT NULL CHECK (kind IN ('confluence', 'notion')),
  readers        text[] NOT NULL,
  classification text NOT NULL CHECK (classification IN ('public', 'internal', 'confidential', 'restricted')),
  base_url       text,                        -- Confluence: the site's address, for links to each page
  created_by     text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, name)
);

CREATE TABLE import_runs (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES orgs(id),
  import_id   uuid NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
  archive     bytea,                          -- the uploaded zip; cleared once applied
  status      text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'done', 'failed')),
  result      jsonb,
  error       text,
  created_by  text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);

ALTER TABLE imports ENABLE ROW LEVEL SECURITY;
ALTER TABLE import_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON imports USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY org_isolation ON import_runs USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, UPDATE, DELETE ON imports TO kl_app;
GRANT SELECT, INSERT ON import_runs TO kl_app;
GRANT SELECT ON imports TO kl_ingest;
GRANT SELECT, UPDATE (archive, status, result, error, finished_at) ON import_runs TO kl_ingest;
