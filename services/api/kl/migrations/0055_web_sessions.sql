-- H3 (docs/PRODUCTION_REVIEW.md): the web app's sessions and sign-ins in progress leave its process's memory, so a
-- restart signs no one out and a second web server sees the first one's sessions. M2: a session's row is locked while
-- its tokens are refreshed, so requests that arrive together refresh it once.
--
-- The web server connects as kl_web, which reaches these two tables and nothing else; its password is set by
-- provisioning from KL_WEB_PASSWORD. A row never holds the session cookie, only its SHA-256 (a stolen row or backup
-- signs no one in), and holds tokens only encrypted (AES-256-GCM, with a key the web server alone has).
SET search_path = kl, public;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kl_web') THEN
    CREATE ROLE kl_web LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO kl_web', current_database());
END $$;
GRANT USAGE ON SCHEMA kl TO kl_web;
GRANT EXECUTE ON FUNCTION kl.resolve_org(text) TO kl_web;  -- its own org's id, by the deployment's slug
-- As H1 gave the other roles: a session's refresh holds its row while Keycloak answers, never longer than this.
ALTER ROLE kl_web SET statement_timeout = '10s';
ALTER ROLE kl_web SET idle_in_transaction_session_timeout = '30s';

CREATE TABLE web_sessions (
  id_hash           bytea PRIMARY KEY CHECK (octet_length(id_hash) = 32),  -- SHA-256 of the cookie, never the cookie
  org_id            uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  username          text NOT NULL,                                        -- for sign-out everywhere, and leavers
  access_token      bytea NOT NULL,                                       -- encrypted
  refresh_token     bytea NOT NULL,                                       -- encrypted
  access_expires_at timestamptz NOT NULL,
  idle_expires_at   timestamptz NOT NULL,                                 -- pushed out as the session is used
  expires_at        timestamptz NOT NULL,                                 -- absolute: never pushed out
  created_at        timestamptz NOT NULL DEFAULT now(),
  last_seen_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX web_sessions_person ON web_sessions (org_id, username);
CREATE INDEX web_sessions_ending ON web_sessions (org_id, least(idle_expires_at, expires_at));

-- A sign-in between the redirect to Keycloak and its callback: ten minutes, used once.
CREATE TABLE web_logins (
  state_hash bytea PRIMARY KEY CHECK (octet_length(state_hash) = 32),    -- SHA-256 of the OAuth state
  org_id     uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  verifier   bytea NOT NULL,                                              -- the PKCE verifier, encrypted
  nonce      text NOT NULL,                                               -- sent in the clear to Keycloak already
  expires_at timestamptz NOT NULL
);

ALTER TABLE web_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE web_logins ENABLE ROW LEVEL SECURITY;
CREATE POLICY in_org ON web_sessions FOR ALL TO kl_web USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY in_org ON web_logins FOR ALL TO kl_web USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, UPDATE, DELETE ON web_sessions, web_logins TO kl_web;

-- The worker's retention job removes what has ended, in its org, whether or not a web server is running. Nothing else
-- of these tables is the worker's: it gets this function, not the tables.
CREATE FUNCTION kl.sweep_web_sessions() RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = kl, pg_temp AS $$
DECLARE
  org uuid := kl.current_org();
  n bigint;
  m bigint;
BEGIN
  IF org IS NULL THEN
    RETURN 0;
  END IF;
  DELETE FROM web_sessions WHERE org_id = org AND least(idle_expires_at, expires_at) <= now();
  GET DIAGNOSTICS n = ROW_COUNT;
  DELETE FROM web_logins WHERE org_id = org AND expires_at <= now();
  GET DIAGNOSTICS m = ROW_COUNT;
  RETURN n + m;
END $$;
REVOKE ALL ON FUNCTION kl.sweep_web_sessions() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kl.sweep_web_sessions() TO kl_ingest;
