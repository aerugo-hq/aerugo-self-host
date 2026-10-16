-- Q8: diagrams, audio, and video, opt-in per team: the vision and speech models a team chose from its own registry.
SET search_path = kl, public;

CREATE TABLE team_media (
  workspace_id    uuid PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
  org_id          uuid NOT NULL REFERENCES orgs(id),
  vision_model_id uuid REFERENCES model_endpoints(id) ON DELETE SET NULL,
  speech_model_id uuid REFERENCES model_endpoints(id) ON DELETE SET NULL,
  updated_by      text NOT NULL,
  updated_at      timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE team_media ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON team_media USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, UPDATE, DELETE ON team_media TO kl_app;
GRANT SELECT ON team_media TO kl_ingest;
-- The worker reads the chosen models (their addresses and credential references, never a secret) to call them.
GRANT SELECT ON model_endpoints TO kl_ingest;
