-- Slice J1 (ADR 0007): CI pipelines push documents with the workload tokens their CI issues. A team's admins
-- register which repository (and optionally which ref) may push into the team; that registration is the pipeline's
-- grant to write there. Pushes are staged here and applied by the worker, through the same path as connectors, and
-- a push's text is cleared from here once it's applied.
SET search_path = kl, public;

CREATE TABLE pipelines (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       uuid NOT NULL REFERENCES orgs(id),
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name         text NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9-]{0,39}$'),
  issuer       text NOT NULL,
  repository   text NOT NULL,
  ref          text,                          -- NULL: any ref of the repository
  created_by   text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, name)
);

CREATE TABLE pipeline_pushes (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       uuid NOT NULL REFERENCES orgs(id),
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  pipeline_id  uuid NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
  payload      jsonb NOT NULL,                -- cleared to {} once applied
  status       text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'done', 'failed')),
  result       jsonb,
  error        text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz
);

ALTER TABLE pipelines ENABLE ROW LEVEL SECURITY;
ALTER TABLE pipeline_pushes ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON pipelines USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY org_isolation ON pipeline_pushes USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, DELETE ON pipelines TO kl_app;
GRANT SELECT, INSERT ON pipeline_pushes TO kl_app;
GRANT SELECT ON pipelines TO kl_ingest;
GRANT SELECT, UPDATE (payload, status, result, error, finished_at) ON pipeline_pushes TO kl_ingest;
