-- ADR 0011: connectors read as a service account; people see only their own access.
--   1. Connection kinds for every connector the ADR names, so each one needs no migration of its own.
--   2. source_identities: the people a source names as readers, as the source reports them to the connection's
--      account (an id, and an email when the source shows one). Identity matching links them to people.
--   3. people: who has signed in, with the company email their SSO verified. Identity matching uses only these.
SET search_path = kl, public;

ALTER TABLE connections DROP CONSTRAINT IF EXISTS connections_kind_check;
ALTER TABLE connections ADD CONSTRAINT connections_kind_check
  CHECK (kind IN ('github', 'google', 'jira', 'confluence', 'slack', 'microsoft', 'notion', 'directory'));

CREATE TABLE source_identities (
  org_id       uuid NOT NULL REFERENCES orgs(id),
  source       text NOT NULL CHECK (source ~ '^[a-z][a-z0-9-]{1,30}$'),       -- as in ext:<source>.<id>
  external_id  text NOT NULL CHECK (length(external_id) BETWEEN 1 AND 200),
  email        text CHECK (email IS NULL OR (length(email) <= 254 AND email = lower(email))),
  display_name text NOT NULL DEFAULT '',
  seen_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, source, external_id)
);
CREATE INDEX source_identities_email_idx ON source_identities (org_id, email) WHERE email IS NOT NULL;

CREATE TABLE people (
  org_id     uuid NOT NULL REFERENCES orgs(id),
  username   text NOT NULL,
  email      text CHECK (email IS NULL OR (length(email) <= 254 AND email = lower(email))),  -- verified by SSO
  seen_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, username)
);

ALTER TABLE source_identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE people ENABLE ROW LEVEL SECURITY;
CREATE POLICY ingest_in_org ON source_identities FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY app_reads_in_org ON source_identities FOR SELECT TO kl_app USING (org_id = kl.current_org());
CREATE POLICY app_in_org ON people FOR ALL TO kl_app USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, UPDATE, DELETE ON source_identities TO kl_ingest;
GRANT SELECT ON source_identities TO kl_app;
GRANT SELECT, INSERT, UPDATE ON people TO kl_app;
