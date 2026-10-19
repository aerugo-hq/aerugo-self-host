-- F2 (docs/ENTERPRISE_PLAN.md section 5): extraction, opt-in per team. A team's admin chooses one model from the team's
-- own registry (team_extraction); the worker then sends it the team's pages and tickets, only those whose
-- classification the model may receive, and stores what it proposes as knowledge units (proposed, inferred), each held
-- to the claim checker and the sensitive gate. Chat threads are never sent until their channels opt in (C1, C2).
-- extraction_state records each document's last pass per team (its version, the outcome, and how many proposals were
-- kept or dropped and why, never the text), so only what changed is sent again.
SET search_path = kl, public;

CREATE TABLE team_extraction (
  workspace_id   uuid PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
  org_id         uuid NOT NULL REFERENCES orgs(id),
  model_id       uuid NOT NULL REFERENCES model_endpoints(id) ON DELETE CASCADE,  -- its model gone, it's off
  updated_by     text NOT NULL,
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE extraction_state (
  workspace_id   uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  document_id    uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  org_id         uuid NOT NULL REFERENCES orgs(id),
  content_hash   text,           -- the version this pass read; NULL after a failure, so the next pass tries again
  model_id       uuid REFERENCES model_endpoints(id) ON DELETE SET NULL,
  outcome        text NOT NULL CHECK (outcome IN ('done', 'skipped_classification', 'skipped_personal', 'failed')),
  proposed       integer NOT NULL DEFAULT 0,
  unsupported    integer NOT NULL DEFAULT 0,  -- dropped: a sentence its section doesn't support
  sensitive      integer NOT NULL DEFAULT 0,  -- dropped: about one person's health, performance, discipline, or pay
  invalid        integer NOT NULL DEFAULT 0,  -- dropped: not a unit F1 accepts (kind, topic, structure, length)
  tokens         integer NOT NULL DEFAULT 0,
  extracted_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, document_id)
);

ALTER TABLE team_extraction ENABLE ROW LEVEL SECURITY;
ALTER TABLE extraction_state ENABLE ROW LEVEL SECURITY;
-- A team's own rows, in its own transactions (kl.teams, after the API checked the caller administers it), and only with
-- its own models, as team_media's (P2).
CREATE POLICY in_its_team ON team_extraction FOR ALL TO kl_app
  USING (org_id = kl.current_org()
         AND EXISTS (SELECT 1 FROM workspaces w WHERE w.id = workspace_id AND w.slug = ANY(kl.current_teams())))
  WITH CHECK (org_id = kl.current_org()
              AND EXISTS (SELECT 1 FROM workspaces w WHERE w.id = workspace_id AND w.slug = ANY(kl.current_teams()))
              AND EXISTS (SELECT 1 FROM model_endpoints m WHERE m.id = model_id AND m.workspace_id = team_extraction.workspace_id));
CREATE POLICY ingest_reads_in_org ON team_extraction FOR SELECT TO kl_ingest USING (org_id = kl.current_org());
CREATE POLICY read_in_its_team ON extraction_state FOR SELECT TO kl_app
  USING (org_id = kl.current_org()
         AND EXISTS (SELECT 1 FROM workspaces w WHERE w.id = workspace_id AND w.slug = ANY(kl.current_teams())));
CREATE POLICY ingest_in_org ON extraction_state FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());

GRANT SELECT, INSERT, UPDATE, DELETE ON team_extraction TO kl_app;
GRANT SELECT ON team_extraction TO kl_ingest;
GRANT SELECT ON extraction_state TO kl_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON extraction_state TO kl_ingest;
-- The worker meters its own model calls against the team's monthly budget, as the API does for Ask.
GRANT SELECT, INSERT ON model_usage TO kl_ingest;
GRANT USAGE ON SEQUENCE model_usage_id_seq TO kl_ingest;
-- ...and says so when a budget is spent, as the API does: the team's admins see it in their alerts.
GRANT INSERT ON signals TO kl_ingest;
GRANT USAGE ON SEQUENCE signals_id_seq TO kl_ingest;
