-- M3, slice C3a: compiler proposals. A proposal is a drafted curated concept waiting for review.
-- Its readers are the draft's audience (a group or the org that can already read every source it
-- used) plus the admin who asked for it, who could read every source too.
SET search_path = kl, public;

CREATE TABLE proposals (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES orgs(id),
  workspace_id   uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  concept_key    text NOT NULL,
  title          text NOT NULL,
  content        text NOT NULL,   -- the OKF draft, validated in knowledge mode before it's stored
  audience       text NOT NULL,
  source_keys    text[] NOT NULL,
  model          text NOT NULL,
  status         text NOT NULL DEFAULT 'drafted' CHECK (status IN ('drafted', 'pr_opened', 'merged', 'closed')),
  pr_url         text,
  acl_principals text[] NOT NULL,
  created_by     text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX proposals_workspace_idx ON proposals (workspace_id, created_at DESC);
CREATE INDEX proposals_acl_idx ON proposals USING gin (acl_principals);

ALTER TABLE proposals ENABLE ROW LEVEL SECURITY;
CREATE POLICY read_by_acl ON proposals FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND acl_principals && kl.current_principals());
-- The API writes a proposal as the admin who asked; it can only write one that admin can read.
CREATE POLICY propose_in_org ON proposals FOR INSERT TO kl_app
  WITH CHECK (org_id = kl.current_org() AND acl_principals && kl.current_principals());
CREATE POLICY ingest_in_org ON proposals FOR ALL TO kl_ingest
  USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());

GRANT SELECT, INSERT ON proposals TO kl_app;
GRANT SELECT, UPDATE ON proposals TO kl_ingest;
