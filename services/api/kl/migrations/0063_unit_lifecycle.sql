-- F6 (docs/ENTERPRISE_PLAN.md section 5): lifecycle. A confirmed unit keeps each evidence document's content hash as
-- its owner last confirmed it (evidence_hashes), so the worker can tell when the evidence changed underneath it; and
-- every change of a unit's status is recorded (unit_events): what it was, what it became, why, and who (a person, or
-- 'lifecycle' for the worker), readable only with its unit, as a perspective is. Nothing here widens who reads anything.
SET search_path = kl, public;

ALTER TABLE knowledge_units ADD COLUMN evidence_hashes text[];

CREATE TABLE unit_events (
  id          bigserial PRIMARY KEY,
  org_id      uuid NOT NULL REFERENCES orgs(id),
  unit_id     uuid NOT NULL REFERENCES knowledge_units(id) ON DELETE CASCADE,
  from_status text NOT NULL CHECK (from_status IN ('proposed', 'confirmed', 'stale', 'superseded', 'retired')),
  to_status   text NOT NULL CHECK (to_status IN ('proposed', 'confirmed', 'stale', 'superseded', 'retired')),
  reason      text NOT NULL CHECK (length(reason) BETWEEN 1 AND 300),
  created_by  text NOT NULL CHECK (length(created_by) BETWEEN 1 AND 200),
  at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX unit_events_unit_idx ON unit_events (unit_id, at);

ALTER TABLE unit_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY read_with_its_unit ON unit_events FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND EXISTS (SELECT 1 FROM knowledge_units u WHERE u.id = unit_id));
CREATE POLICY record_on_its_teams_unit ON unit_events FOR INSERT TO kl_app
  WITH CHECK (org_id = kl.current_org() AND EXISTS (SELECT 1 FROM knowledge_units u WHERE u.id = unit_id) AND kl.unit_in_my_teams(unit_id));
CREATE POLICY ingest_in_org ON unit_events FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());

GRANT SELECT, INSERT ON unit_events TO kl_app;
GRANT SELECT, INSERT, DELETE ON unit_events TO kl_ingest;
GRANT USAGE ON SEQUENCE unit_events_id_seq TO kl_app, kl_ingest;
