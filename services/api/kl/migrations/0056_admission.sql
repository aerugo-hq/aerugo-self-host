-- G4: who gets in. 'org' (as before): anyone the identity provider signs in. 'teams': only members of an onboarded team,
-- in any role, and org admins: a pilot, so nothing a source shares company-wide reaches people outside it. One row per
-- org, set by an org admin through the API (or Terraform); no row means 'org'.
SET search_path = kl, public;

CREATE TABLE org_admission (
  org_id     uuid PRIMARY KEY REFERENCES orgs(id),
  mode       text NOT NULL CHECK (mode IN ('org', 'teams')),
  changed_by text NOT NULL,
  changed_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE org_admission ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON org_admission TO kl_app
  USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, UPDATE ON org_admission TO kl_app;

-- The org's mode, asked while authenticating, before any request context exists, so it's owner-privileged; it answers
-- only 'org' or 'teams', for one org.
CREATE FUNCTION kl.admission_mode(org_slug text) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = kl, pg_temp AS $$
  SELECT COALESCE((SELECT a.mode FROM org_admission a JOIN orgs o ON o.id = a.org_id WHERE o.slug = org_slug), 'org')
$$;
REVOKE ALL ON FUNCTION kl.admission_mode(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kl.admission_mode(text) TO kl_app;
