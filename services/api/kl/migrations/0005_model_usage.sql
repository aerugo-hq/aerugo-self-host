-- Slice A: metering for model calls (tokens per workspace and endpoint). Prompts are never stored.
SET search_path = kl, public;

CREATE TABLE model_usage (
  id                bigserial PRIMARY KEY,
  org_id            uuid NOT NULL REFERENCES orgs(id),
  workspace_id      uuid NOT NULL REFERENCES workspaces(id),
  endpoint_id       uuid NOT NULL REFERENCES model_endpoints(id),
  prompt_tokens     int,
  completion_tokens int,
  at                timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX model_usage_workspace_at_idx ON model_usage (org_id, workspace_id, at DESC);
ALTER TABLE model_usage ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON model_usage USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT ON model_usage TO kl_app;
GRANT USAGE ON SEQUENCE model_usage_id_seq TO kl_app;
