-- Slice T1: service accounts. A machine identity (an OAuth client using the client-credentials flow, such as the
-- Terraform provider, ADR 0009) is a caller only once an org admin registers it here, and only while it's enabled. It
-- holds roles like a person, but nothing org-wide: it reads no documents unless granted.
SET search_path = kl, public;

CREATE TABLE service_accounts (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     uuid NOT NULL REFERENCES orgs(id),
  client_id  text NOT NULL CHECK (client_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$'),
  name       text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
  enabled    boolean NOT NULL DEFAULT true,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, client_id)
);
ALTER TABLE service_accounts ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON service_accounts TO kl_app
  USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, DELETE ON service_accounts TO kl_app;
GRANT UPDATE (name, enabled) ON service_accounts TO kl_app;

-- Whether a client may act as a caller. Asked while authenticating, before any request context exists, so it's
-- owner-privileged; it answers only yes or no, for one org and one client.
CREATE FUNCTION kl.service_account_enabled(org_slug text, client text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = kl, pg_temp AS $$
  SELECT EXISTS (SELECT 1 FROM service_accounts s JOIN orgs o ON o.id = s.org_id
                 WHERE o.slug = org_slug AND s.client_id = client AND s.enabled)
$$;
REVOKE ALL ON FUNCTION kl.service_account_enabled(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kl.service_account_enabled(text, text) TO kl_app;
