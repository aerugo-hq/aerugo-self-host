-- F5b (docs/ENTERPRISE_PLAN.md sections 5 and 6, ask-time capture): a question the asker routes to a likely expert,
-- and the expert's reply. Only the two of them see it: the asker chose to send it, and who-to-ask suggested the expert.
-- When replying, the expert may keep the reply as knowledge for an audience they choose (the asker only, their own team,
-- or the whole company: decided 2026-10-20); the worker then writes it as a document with exactly that audience and a
-- proposed unit for the team's curators (kept_document, kept_unit). Both names are found by a data-subject erasure.
SET search_path = kl, public;

CREATE TABLE routed_questions (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid NOT NULL REFERENCES orgs(id),
  created_by    text NOT NULL CHECK (length(created_by) BETWEEN 1 AND 200),   -- the asker
  expert        text NOT NULL CHECK (length(expert) BETWEEN 1 AND 200),
  page          text NOT NULL CHECK (length(page) BETWEEN 1 AND 128),         -- whose who-to-ask suggested the expert
  question      text NOT NULL CHECK (length(question) BETWEEN 3 AND 2000),
  reply         text CHECK (reply IS NULL OR length(reply) BETWEEN 1 AND 10000),
  audience      text CHECK (audience IS NULL OR audience IN ('asker', 'team', 'org')),
  keep_team     text CHECK (keep_team IS NULL OR length(keep_team) <= 40),
  replied_at    timestamptz,
  kept_document uuid REFERENCES documents(id) ON DELETE SET NULL,
  kept_unit     uuid REFERENCES knowledge_units(id) ON DELETE SET NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  CHECK (created_by <> expert)
);
CREATE INDEX routed_questions_expert_idx ON routed_questions (org_id, expert, created_at);
CREATE INDEX routed_questions_asker_idx ON routed_questions (org_id, created_by, created_at);

ALTER TABLE routed_questions ENABLE ROW LEVEL SECURITY;
CREATE POLICY asker_or_expert ON routed_questions FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND (('user:' || created_by) = ANY(kl.current_principals()) OR ('user:' || expert) = ANY(kl.current_principals())));
CREATE POLICY asker_sends ON routed_questions FOR INSERT TO kl_app
  WITH CHECK (org_id = kl.current_org() AND ('user:' || created_by) = ANY(kl.current_principals()));
CREATE POLICY expert_replies ON routed_questions FOR UPDATE TO kl_app
  USING (org_id = kl.current_org() AND ('user:' || expert) = ANY(kl.current_principals()))
  WITH CHECK (org_id = kl.current_org() AND ('user:' || expert) = ANY(kl.current_principals()));
CREATE POLICY ingest_in_org ON routed_questions FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT ON routed_questions TO kl_app;
GRANT UPDATE (reply, audience, keep_team, replied_at) ON routed_questions TO kl_app;
GRANT SELECT, UPDATE, DELETE ON routed_questions TO kl_ingest;

-- The expert is a person a data-subject erasure must find, beside the asker (created_by).
CREATE OR REPLACE FUNCTION kl.subject_columns() RETURNS TABLE (tbl text, col text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = kl, pg_temp AS $$
  SELECT c.table_name::text, c.column_name::text
  FROM information_schema.columns c
  JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
  WHERE c.table_schema = 'kl' AND t.table_type = 'BASE TABLE' AND c.data_type = 'text'
    AND c.column_name IN ('actor', 'username', 'created_by', 'updated_by', 'requested_by', 'acknowledged_by',
                          'set_by', 'opened_by', 'merged_by', 'granted_by', 'revoked_by', 'resolved_by', 'expert')
    AND EXISTS (SELECT 1 FROM information_schema.columns o
                WHERE o.table_schema = 'kl' AND o.table_name = c.table_name AND o.column_name = 'org_id')
  ORDER BY 1, 2
$$;
