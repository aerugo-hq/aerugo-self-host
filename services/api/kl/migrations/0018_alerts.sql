-- Slice P (ADR 0005): alerts live in the app. The API records signals (counts of things worth alerting on,
-- ids only), the worker turns them and the database's own state into alerts on every scheduler tick, and
-- admins read and acknowledge alerts in the app. An org admin can also have page and warn alerts posted to a
-- webhook; its URL is a credential, so it lives in the secrets backend.
SET search_path = kl, public;

CREATE TABLE signals (
  id      bigserial PRIMARY KEY,
  org_id  uuid NOT NULL REFERENCES orgs(id),
  kind    text NOT NULL CHECK (kind IN ('drift', 'withheld', 'server_error', 'rate_limited', 'budget_exhausted')),
  subject text NOT NULL,                  -- a surface, route, or team slug: never content
  n       int NOT NULL CHECK (n > 0),
  at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX signals_kind_at_idx ON signals (org_id, kind, at);

CREATE TABLE alerts (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          uuid NOT NULL REFERENCES orgs(id),
  rule            text NOT NULL,
  subject         text NOT NULL,          -- what it's about: a surface, connection, route, or team
  workspace       text,                   -- the team it belongs to; NULL for the whole org
  severity        text NOT NULL CHECK (severity IN ('page', 'warn', 'info')),
  status          text NOT NULL CHECK (status IN ('firing', 'resolved')),
  summary         text NOT NULL,
  details         jsonb NOT NULL DEFAULT '{}',   -- ids and counts
  first_seen      timestamptz NOT NULL DEFAULT now(),
  last_seen       timestamptz NOT NULL DEFAULT now(),
  resolved_at     timestamptz,
  acknowledged_by text,
  acknowledged_at timestamptz,
  notified        text CHECK (notified IN ('firing', 'resolved'))  -- the last state posted to the webhook
);
CREATE UNIQUE INDEX alerts_one_firing_idx ON alerts (org_id, rule, subject) WHERE status = 'firing';
CREATE INDEX alerts_recent_idx ON alerts (org_id, last_seen DESC);

CREATE TABLE alert_settings (
  org_id      uuid PRIMARY KEY REFERENCES orgs(id),
  webhook_ref text,                       -- path in the secrets backend; never the URL itself
  updated_by  text NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE heartbeats (                 -- a service that has died can't raise its own alert; the API notices
  org_id  uuid NOT NULL REFERENCES orgs(id),
  service text NOT NULL,
  seen_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, service)
);

ALTER TABLE signals        ENABLE ROW LEVEL SECURITY;
ALTER TABLE alerts         ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE heartbeats     ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON signals        USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY org_isolation ON alerts         USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY org_isolation ON alert_settings USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY org_isolation ON heartbeats     USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());

-- The API records signals and reads and acknowledges alerts; the worker evaluates, raises, and resolves.
GRANT INSERT ON signals TO kl_app;
GRANT USAGE ON SEQUENCE signals_id_seq TO kl_app;
GRANT SELECT, DELETE ON signals TO kl_ingest;
GRANT SELECT, UPDATE ON alerts TO kl_app;
GRANT SELECT, INSERT, UPDATE ON alerts TO kl_ingest;
GRANT SELECT, INSERT, UPDATE, DELETE ON alert_settings TO kl_app;
GRANT SELECT ON alert_settings TO kl_ingest;
GRANT SELECT ON heartbeats TO kl_app;
GRANT SELECT, INSERT, UPDATE ON heartbeats TO kl_ingest;
