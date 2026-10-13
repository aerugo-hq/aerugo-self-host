-- M1 slice 1: connections, the containers each one can see, and the durable job queue.
-- Invariant: tenant tables carry org_id and row-level security from their first migration.
SET search_path = kl, public;

CREATE TABLE connections (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES orgs(id),
  workspace_id   uuid NOT NULL REFERENCES workspaces(id),
  kind           text NOT NULL CHECK (kind IN ('github')),
  name           text NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9-]{0,39}$'),
  config         jsonb NOT NULL DEFAULT '{}',   -- non-secret settings: installation id, chosen repos
  credential_ref text NOT NULL,                 -- reference into the secrets backend, never the secret
  status         text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'ready', 'error', 'disabled')),
  last_error     text,
  created_by     text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workspace_id, name)
);

-- What a connection can reach (repos, drives, spaces), refreshed by connector.discover().
CREATE TABLE connection_containers (
  connection_id  uuid NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
  org_id         uuid NOT NULL REFERENCES orgs(id),
  external_id    text NOT NULL,                 -- e.g. owner/repo
  container_kind text NOT NULL,                 -- repo | drive | space
  visibility     text,
  default_ref    text,                          -- default branch
  selected       boolean NOT NULL DEFAULT false,
  discovered_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (connection_id, external_id)
);

-- Durable jobs, claimed with FOR UPDATE SKIP LOCKED. Retries back off exponentially.
CREATE TABLE jobs (
  id           bigserial PRIMARY KEY,
  org_id       uuid NOT NULL REFERENCES orgs(id),
  kind         text NOT NULL,
  payload      jsonb NOT NULL DEFAULT '{}',
  status       text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
  attempts     int  NOT NULL DEFAULT 0,
  max_attempts int  NOT NULL DEFAULT 5,
  run_after    timestamptz NOT NULL DEFAULT now(),
  locked_at    timestamptz,
  last_error   text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_ready_idx ON jobs (org_id, run_after) WHERE status = 'queued';

ALTER TABLE connections           ENABLE ROW LEVEL SECURITY;
ALTER TABLE connection_containers ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs                  ENABLE ROW LEVEL SECURITY;

CREATE POLICY org_isolation ON connections           USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY org_isolation ON connection_containers USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY org_isolation ON jobs                  USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());

-- API: admins create connections and enqueue work. Worker: runs the work.
GRANT SELECT, INSERT, UPDATE ON connections TO kl_app;
GRANT SELECT ON connection_containers TO kl_app;
GRANT SELECT, INSERT ON jobs TO kl_app;
GRANT SELECT, UPDATE ON connections TO kl_ingest;
GRANT SELECT, INSERT, UPDATE, DELETE ON connection_containers TO kl_ingest;
GRANT SELECT, INSERT, UPDATE ON jobs TO kl_ingest;
GRANT USAGE ON SEQUENCE jobs_id_seq TO kl_app, kl_ingest;
