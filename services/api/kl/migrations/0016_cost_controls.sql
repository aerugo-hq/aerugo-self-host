-- Slice O: cost controls. A budget lives on the model it caps, as tokens per calendar month (UTC), summed
-- from model_usage. Rate limits count requests in one-minute windows here, in Postgres, so the API and the
-- MCP server share them; org admins can override the defaults per route.
SET search_path = kl, public;

ALTER TABLE model_endpoints ADD COLUMN monthly_token_budget bigint CHECK (monthly_token_budget > 0);
CREATE INDEX model_usage_endpoint_at_idx ON model_usage (endpoint_id, at);

CREATE TABLE rate_counters (
  org_id       uuid NOT NULL REFERENCES orgs(id),
  bucket       text NOT NULL,              -- '<route>:user:<name>', '<route>:team:<slug>', or '<route>:org'
  window_start timestamptz NOT NULL,       -- the minute being counted
  hits         int NOT NULL DEFAULT 0,
  PRIMARY KEY (org_id, bucket, window_start)
);
ALTER TABLE rate_counters ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON rate_counters USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, UPDATE, DELETE ON rate_counters TO kl_app;

CREATE TABLE rate_limits (
  org_id              uuid NOT NULL REFERENCES orgs(id),
  route               text NOT NULL CHECK (route IN ('search', 'ask', 'compile')),
  per_user_per_minute int NOT NULL CHECK (per_user_per_minute > 0),
  per_team_per_minute int NOT NULL CHECK (per_team_per_minute > 0),  -- per org, for search
  updated_by          text NOT NULL,
  updated_at          timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, route)
);
ALTER TABLE rate_limits ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON rate_limits USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, UPDATE, DELETE ON rate_limits TO kl_app;
