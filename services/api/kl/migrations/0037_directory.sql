-- ADR 0011: directories (Okta first; Google Workspace and Entra next). A directory indexes no content: it says who's at
-- the company, by email, and whether they still are. Identity matching refuses an email a directory says has left, and a
-- leaver's matched identities are revoked as soon as a directory says so, without waiting for a sign-in that won't come.
SET search_path = kl, public;

CREATE TABLE directory_people (
  org_id        uuid NOT NULL REFERENCES orgs(id),
  connection_id uuid NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
  email         text NOT NULL CHECK (length(email) <= 254 AND email = lower(email)),
  display_name  text NOT NULL DEFAULT '',
  active        boolean NOT NULL,
  seen_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (connection_id, email)
);
CREATE INDEX directory_people_email_idx ON directory_people (org_id, email);

ALTER TABLE directory_people ENABLE ROW LEVEL SECURITY;
CREATE POLICY ingest_in_org ON directory_people FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY app_reads_in_org ON directory_people FOR SELECT TO kl_app USING (org_id = kl.current_org());
GRANT SELECT, INSERT, UPDATE, DELETE ON directory_people TO kl_ingest;
GRANT SELECT ON directory_people TO kl_app;

-- Revoking a leaver's matches: the worker reads who signed in with which email, and removes their sso-email aliases.
CREATE POLICY ingest_reads_in_org ON people FOR SELECT TO kl_ingest USING (org_id = kl.current_org());
GRANT SELECT ON people TO kl_ingest;
GRANT DELETE ON principal_aliases TO kl_ingest;
