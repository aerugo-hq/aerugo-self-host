-- M3, slice C3b: proposals become pull requests, opened by the curator App and tracked by re-reading
-- GitHub. Their timeline joins the concept's history once the merge is indexed.
SET search_path = kl, public;

ALTER TABLE proposals ADD COLUMN repo text;              -- owner/name of the knowledge repo holding the PR
ALTER TABLE proposals ADD COLUMN pr_number integer;
ALTER TABLE proposals ADD COLUMN checked_at timestamptz; -- when the PR's state was last re-read from GitHub
ALTER TABLE proposals DROP CONSTRAINT proposals_status_check;
ALTER TABLE proposals ADD CONSTRAINT proposals_status_check
  CHECK (status IN ('drafted', 'pr_opened', 'merged', 'closed', 'refused'));

CREATE TABLE proposal_events (
  id          bigserial PRIMARY KEY,
  org_id      uuid NOT NULL REFERENCES orgs(id),
  proposal_id uuid NOT NULL REFERENCES proposals(id) ON DELETE CASCADE,
  kind        text NOT NULL,   -- pr_opened | reviewed | merged | closed | refused
  detail      jsonb NOT NULL DEFAULT '{}',
  at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX proposal_events_proposal_idx ON proposal_events (proposal_id, at);

ALTER TABLE proposal_events ENABLE ROW LEVEL SECURITY;
-- Visible exactly when the proposal is (the subquery runs under the proposals policy).
CREATE POLICY read_with_proposal ON proposal_events FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND EXISTS (SELECT 1 FROM proposals p WHERE p.id = proposal_id));
CREATE POLICY ingest_in_org ON proposal_events FOR ALL TO kl_ingest
  USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());

GRANT SELECT ON proposal_events TO kl_app;
GRANT SELECT, INSERT ON proposal_events TO kl_ingest;
GRANT USAGE ON SEQUENCE proposal_events_id_seq TO kl_ingest;
-- The curator also reads principal_aliases, which 0002 already lets kl_ingest do within its org.
