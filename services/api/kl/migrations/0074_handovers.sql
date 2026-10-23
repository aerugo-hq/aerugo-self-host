-- C4a (docs/ENTERPRISE_PLAN.md section 6): handover interviews. Someone leaving or changing roles answers prompts built
-- from what they own, and the answers they choose to share become knowledge their successor and lead confirm.
--
-- Consent (decided 2026-10-23): the person starts their own handover, or their lead or a team admin requests one and it
-- begins only when the person accepts (`handovers`). Their drafts are theirs alone (`handover_answers`, readable and
-- writable only by them, by the database's own rule); a shared answer's audience is theirs to choose, and they can
-- withdraw it until they leave; when a directory says they've left, every unshared draft is deleted (the worker).
-- A shared answer becomes a document of kind `handover` with exactly its audience and a proposed unit; the named
-- successor or lead may settle a unit that cites that handover alone (kl.handover_confirmer, beside 0072's meeting rule),
-- the team's curators as for any unit. The person, successor, lead, and requester are names an erasure finds.
SET search_path = kl, public;

CREATE TABLE handovers (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       uuid NOT NULL REFERENCES orgs(id),
  person       text NOT NULL CHECK (length(person) BETWEEN 1 AND 200),        -- whose handover it is
  team_id      uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,    -- the team it hands over to
  successor    text CHECK (successor IS NULL OR length(successor) BETWEEN 1 AND 200),
  lead         text CHECK (lead IS NULL OR length(lead) BETWEEN 1 AND 200),
  reason       text NOT NULL CHECK (reason IN ('leaving', 'changing roles')),
  status       text NOT NULL CHECK (status IN ('requested', 'open', 'declined', 'closed')),
  requested_by text NOT NULL CHECK (length(requested_by) BETWEEN 1 AND 200),
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CHECK (successor IS DISTINCT FROM person AND lead IS DISTINCT FROM person)
);
CREATE INDEX handovers_person_idx ON handovers (org_id, person, created_at);

CREATE TABLE handover_answers (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES orgs(id),
  handover_id uuid NOT NULL REFERENCES handovers(id) ON DELETE CASCADE,
  kind        text NOT NULL CHECK (kind IN ('owned', 'asked', 'routine', 'contact', 'breaks', 'in_flight', 'where')),
  prompt      text NOT NULL CHECK (length(prompt) BETWEEN 1 AND 600),   -- shown to the person alone
  subject     text CHECK (subject IS NULL OR length(subject) <= 300),   -- what it's about, for the person alone
  answer      text CHECK (answer IS NULL OR length(answer) <= 4000),
  audience    text NOT NULL DEFAULT 'named' CHECK (audience IN ('named', 'org')),
  shared_at   timestamptz,
  document_id uuid REFERENCES documents(id) ON DELETE SET NULL,
  unit_id     uuid REFERENCES knowledge_units(id) ON DELETE SET NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX handover_answers_handover_idx ON handover_answers (handover_id);
CREATE INDEX handover_answers_document_idx ON handover_answers (document_id);

ALTER TABLE handovers ENABLE ROW LEVEL SECURITY;
ALTER TABLE handover_answers ENABLE ROW LEVEL SECURITY;

CREATE POLICY its_people_read ON handovers FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND (('user:' || person) = ANY(kl.current_principals())
         OR ('user:' || successor) = ANY(kl.current_principals()) OR ('user:' || lead) = ANY(kl.current_principals())
         OR ('user:' || requested_by) = ANY(kl.current_principals())));
CREATE POLICY someone_asks ON handovers FOR INSERT TO kl_app
  WITH CHECK (org_id = kl.current_org() AND ('user:' || requested_by) = ANY(kl.current_principals()));
CREATE POLICY the_person_decides ON handovers FOR UPDATE TO kl_app
  USING (org_id = kl.current_org() AND ('user:' || person) = ANY(kl.current_principals()))
  WITH CHECK (org_id = kl.current_org() AND ('user:' || person) = ANY(kl.current_principals()));
CREATE POLICY ingest_in_org ON handovers FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT ON handovers TO kl_app;
GRANT UPDATE (status, updated_at) ON handovers TO kl_app;
GRANT SELECT, UPDATE ON handovers TO kl_ingest;

CREATE POLICY the_person_alone ON handover_answers FOR ALL TO kl_app
  USING (org_id = kl.current_org() AND EXISTS (
    SELECT 1 FROM handovers h WHERE h.id = handover_id AND ('user:' || h.person) = ANY(kl.current_principals())))
  WITH CHECK (org_id = kl.current_org() AND EXISTS (
    SELECT 1 FROM handovers h WHERE h.id = handover_id AND ('user:' || h.person) = ANY(kl.current_principals())));
CREATE POLICY ingest_in_org ON handover_answers FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT ON handover_answers TO kl_app;
GRANT UPDATE (answer, audience, updated_at) ON handover_answers TO kl_app;
GRANT SELECT, UPDATE, DELETE ON handover_answers TO kl_ingest;

-- How far a handover has come, for the people who may see it (its lead, successor, and requester too): counts only,
-- never a draft. A security definer, since its answers are the person's alone.
CREATE FUNCTION kl.handover_progress(handover uuid) RETURNS TABLE (prompts int, shared int)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = kl, pg_temp AS $$
  SELECT count(*)::int, count(*) FILTER (WHERE a.shared_at IS NOT NULL)::int
  FROM kl.handover_answers a JOIN kl.handovers h ON h.id = a.handover_id
  WHERE h.id = handover AND h.org_id = kl.current_org()
    AND ('user:' || h.person = ANY(kl.current_principals()) OR 'user:' || h.successor = ANY(kl.current_principals())
         OR 'user:' || h.lead = ANY(kl.current_principals()) OR 'user:' || h.requested_by = ANY(kl.current_principals()))
$$;

-- Whether the caller is the named successor or lead of the handover every one of these documents came from: true only
-- for documents of kind `handover`, each a shared answer of a handover that names the caller. A security definer, since
-- the answers are the person's alone; it says only that, about the caller.
CREATE FUNCTION kl.handover_confirmer(evidence uuid[]) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = kl, pg_temp AS $$
  SELECT cardinality(evidence) > 0 AND NOT EXISTS (
    SELECT 1 FROM unnest(evidence) e WHERE NOT EXISTS (
      SELECT 1 FROM kl.documents d JOIN kl.handover_answers a ON a.document_id = d.id JOIN kl.handovers h ON h.id = a.handover_id
      WHERE d.id = e AND d.source_kind = 'handover' AND d.org_id = kl.current_org()
        AND ('user:' || h.successor = ANY(kl.current_principals()) OR 'user:' || h.lead = ANY(kl.current_principals()))))
$$;
GRANT EXECUTE ON FUNCTION kl.handover_progress(uuid), kl.handover_confirmer(uuid[]) TO kl_app, kl_ingest;

CREATE POLICY settle_from_its_handover ON knowledge_units FOR UPDATE TO kl_app
  USING (org_id = kl.current_org() AND kl.evidence_readable(evidence) AND kl.handover_confirmer(evidence))
  WITH CHECK (org_id = kl.current_org() AND kl.evidence_readable(evidence) AND kl.handover_confirmer(evidence));
CREATE POLICY record_from_its_handover ON unit_events FOR INSERT TO kl_app
  WITH CHECK (org_id = kl.current_org()
              AND EXISTS (SELECT 1 FROM knowledge_units u WHERE u.id = unit_id AND kl.handover_confirmer(u.evidence)));

-- The person, successor, and lead are people a data-subject erasure must find, beside the requester (requested_by).
CREATE OR REPLACE FUNCTION kl.subject_columns() RETURNS TABLE (tbl text, col text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = kl, pg_temp AS $$
  SELECT c.table_name::text, c.column_name::text
  FROM information_schema.columns c
  JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
  WHERE c.table_schema = 'kl' AND t.table_type = 'BASE TABLE' AND c.data_type = 'text'
    AND c.column_name IN ('actor', 'username', 'created_by', 'updated_by', 'requested_by', 'acknowledged_by',
                          'set_by', 'opened_by', 'merged_by', 'granted_by', 'revoked_by', 'resolved_by', 'expert',
                          'person', 'successor', 'lead')
    AND EXISTS (SELECT 1 FROM information_schema.columns o
                WHERE o.table_schema = 'kl' AND o.table_name = c.table_name AND o.column_name = 'org_id')
  ORDER BY 1, 2
$$;
