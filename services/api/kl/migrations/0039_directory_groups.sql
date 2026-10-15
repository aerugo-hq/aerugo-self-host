-- S2: groups from a company directory (Google Workspace first; Entra writes the same table in S3). Each row is one
-- person a group reaches, nested groups already flattened, so a Drive file shared with a group reaches exactly its
-- members. A directory's sync replaces its own rows. Only the worker reads them: the API has no grant, because who's
-- in which group is the directory's to show, not Aerugo's.
SET search_path = kl, public;

CREATE TABLE directory_groups (
  org_id        uuid NOT NULL REFERENCES orgs(id),
  connection_id uuid NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
  group_email   text NOT NULL CHECK (length(group_email) <= 254 AND group_email = lower(group_email)),
  member_email  text NOT NULL CHECK (length(member_email) <= 254 AND member_email = lower(member_email)),
  seen_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (connection_id, group_email, member_email)
);
CREATE INDEX directory_groups_group_idx ON directory_groups (org_id, group_email);

ALTER TABLE directory_groups ENABLE ROW LEVEL SECURITY;
CREATE POLICY ingest_in_org ON directory_groups FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, DELETE ON directory_groups TO kl_ingest;
