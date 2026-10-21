-- C2b (docs/ENTERPRISE_PLAN.md section 6, principle 3): Slack channel capture, opt-in and visible.
--
-- connection_containers.capture: off; requested, once the channel's creator or a workspace admin ran `/aerugo capture
-- on` in the channel (the first key, checked against Slack by the worker); on, once the connection's team admin
-- confirmed in Aerugo (the second). Only `on` lets extraction propose from the channel's threads (F2). Who did each is
-- in the audit, not here.
-- capture_exclusions: the messages someone marked "don't capture" (a :no_entry_sign: reaction the worker read back from
-- Slack), by the source's own id for them: never kept, searched, or extracted from again. Who marked one isn't kept.
SET search_path = kl, public;

ALTER TABLE connection_containers
  ADD COLUMN capture text NOT NULL DEFAULT 'off' CHECK (capture IN ('off', 'requested', 'on')),
  ADD COLUMN capture_changed_at timestamptz;
GRANT UPDATE (capture, capture_changed_at) ON connection_containers TO kl_app;  -- the team admin's key (org_isolation applies)

CREATE TABLE capture_exclusions (
  org_id        uuid NOT NULL REFERENCES orgs(id),
  connection_id uuid NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
  container     text NOT NULL CHECK (length(container) BETWEEN 1 AND 300),
  message       text NOT NULL CHECK (length(message) BETWEEN 1 AND 100),
  excluded_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (connection_id, container, message)
);
ALTER TABLE capture_exclusions ENABLE ROW LEVEL SECURITY;
CREATE POLICY ingest_in_org ON capture_exclusions FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY read_in_org ON capture_exclusions FOR SELECT TO kl_app USING (org_id = kl.current_org());  -- live reads leave them out
GRANT SELECT, INSERT, DELETE ON capture_exclusions TO kl_ingest;
GRANT SELECT ON capture_exclusions TO kl_app;
