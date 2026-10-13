-- Slice T2: what a tool such as Terraform manages. When a change arrives with the Aerugo-Managed-By header, the API
-- records that tool as the resource's manager, so every read (and the app) can say "Managed by Terraform", and warn
-- that a change made by hand will be undone on its next apply. A change made by hand doesn't clear the record: to the
-- tool it's drift, which its next plan shows. Removing the resource does. created_by is named so that erasing a person
-- pseudonymizes it, like every other record of who did what (slice I1).
SET search_path = kl, public;

CREATE TABLE managed_resources (
  org_id     uuid NOT NULL REFERENCES orgs(id),
  kind       text NOT NULL CHECK (kind ~ '^[a-z][a-z_]{0,39}$'),
  key        text NOT NULL CHECK (length(key) BETWEEN 1 AND 300),
  manager    text NOT NULL CHECK (manager ~ '^[a-z][a-z0-9-]{0,29}$'),
  workspace  text CHECK (workspace ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$'),
  created_by text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, kind, key)
);
ALTER TABLE managed_resources ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON managed_resources TO kl_app
  USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, UPDATE, DELETE ON managed_resources TO kl_app;
