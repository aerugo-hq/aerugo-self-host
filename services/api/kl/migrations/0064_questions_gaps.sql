-- F5a (docs/ENTERPRISE_PLAN.md section 5, the learning loop; principle 7: no monitoring of people): what people ask,
-- what isn't answered well, and corrections sent to a unit's owners.
--
-- questions: one row per question Ask answered for a person (never a service account), kept KL_QUESTION_DAYS (30) and
-- then pruned by retention. Its text is NULL when the sensitive gate or personal-data detection flagged it (counted,
-- never kept). Its username is there only to count different askers and for a data-subject request: the API can read
-- and change only the caller's own rows (their feedback on their own answer), and nothing shows who asked.
-- knowledge_gaps: what several different people asked and didn't get answered well, grouped by the worker; never who.
-- unit_feedback: "this unit is wrong", with a note, from someone who reads the unit; read only by the unit's owner team
-- (who sent it isn't shown, but stays findable for erasure).
SET search_path = kl, public;

CREATE TABLE questions (
  id          bigserial PRIMARY KEY,
  org_id      uuid NOT NULL REFERENCES orgs(id),
  username    text NOT NULL CHECK (length(username) BETWEEN 1 AND 200),
  text        text CHECK (text IS NULL OR length(text) <= 2000),
  words       text[] NOT NULL DEFAULT '{}',
  teams       text[] NOT NULL DEFAULT '{}',
  found       boolean NOT NULL,
  confidence  text CHECK (confidence IS NULL OR confidence IN ('high', 'medium', 'low')),
  unit_led    boolean NOT NULL DEFAULT false,
  helpful     boolean,
  asked_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX questions_asked_idx ON questions (org_id, asked_at);

CREATE TABLE knowledge_gaps (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES orgs(id),
  key         text NOT NULL CHECK (length(key) BETWEEN 1 AND 200),   -- its wording, normalized: the same gap across passes
  question    text NOT NULL CHECK (length(question) <= 2000),          -- the wording asked most often
  words       text[] NOT NULL,
  asked       int NOT NULL CHECK (asked >= 1),
  askers      int NOT NULL CHECK (askers >= 1),
  teams       text[] NOT NULL DEFAULT '{}',                           -- the teams whose people asked: who sees it
  state       text NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'answered', 'dismissed')),
  first_asked timestamptz NOT NULL,
  last_asked  timestamptz NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, key)
);

CREATE TABLE unit_feedback (
  id          bigserial PRIMARY KEY,
  org_id      uuid NOT NULL REFERENCES orgs(id),
  unit_id     uuid NOT NULL REFERENCES knowledge_units(id) ON DELETE CASCADE,
  note        text NOT NULL CHECK (length(note) BETWEEN 1 AND 1000),
  created_by  text NOT NULL CHECK (length(created_by) BETWEEN 1 AND 200),
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX unit_feedback_unit_idx ON unit_feedback (unit_id, created_at);

ALTER TABLE questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_gaps ENABLE ROW LEVEL SECURITY;
ALTER TABLE unit_feedback ENABLE ROW LEVEL SECURITY;

-- The API records the caller's own question, and reads or rates only their own; never anyone else's.
CREATE POLICY record_own ON questions FOR INSERT TO kl_app
  WITH CHECK (org_id = kl.current_org() AND ('user:' || username) = ANY(kl.current_principals()));
CREATE POLICY read_own ON questions FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND ('user:' || username) = ANY(kl.current_principals()));
CREATE POLICY rate_own ON questions FOR UPDATE TO kl_app
  USING (org_id = kl.current_org() AND ('user:' || username) = ANY(kl.current_principals()))
  WITH CHECK (org_id = kl.current_org() AND ('user:' || username) = ANY(kl.current_principals()));
CREATE POLICY ingest_in_org ON questions FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT INSERT ON questions TO kl_app;
GRANT SELECT (id, org_id, username) ON questions TO kl_app;
GRANT UPDATE (helpful) ON questions TO kl_app;
GRANT USAGE ON SEQUENCE questions_id_seq TO kl_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON questions TO kl_ingest;

-- Gaps: the API reads those of the teams it names (their curators, checked in OpenFGA), and dismisses them; the
-- worker writes them.
CREATE POLICY read_my_teams ON knowledge_gaps FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND teams && kl.current_teams());
CREATE POLICY dismiss_my_teams ON knowledge_gaps FOR UPDATE TO kl_app
  USING (org_id = kl.current_org() AND teams && kl.current_teams())
  WITH CHECK (org_id = kl.current_org() AND teams && kl.current_teams());
CREATE POLICY ingest_in_org ON knowledge_gaps FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT ON knowledge_gaps TO kl_app;
GRANT UPDATE (state, updated_at) ON knowledge_gaps TO kl_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON knowledge_gaps TO kl_ingest;

-- Corrections: anyone who reads the unit may send one; only its owner team reads them.
CREATE POLICY send_on_readable_unit ON unit_feedback FOR INSERT TO kl_app
  WITH CHECK (org_id = kl.current_org() AND EXISTS (SELECT 1 FROM knowledge_units u WHERE u.id = unit_id));
CREATE POLICY owners_read ON unit_feedback FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND EXISTS (SELECT 1 FROM knowledge_units u WHERE u.id = unit_id) AND kl.unit_in_my_teams(unit_id));
CREATE POLICY ingest_in_org ON unit_feedback FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT ON unit_feedback TO kl_app;
GRANT USAGE ON SEQUENCE unit_feedback_id_seq TO kl_app;
GRANT SELECT, INSERT, DELETE ON unit_feedback TO kl_ingest;
