-- Slice K2: identity links proven by signing in to the source (GitHub first). A sign-in state is random,
-- bound to one user, single-use, and short-lived. The API adds and removes links only after that proof
-- (or for an org admin, removal); org isolation keeps both inside the caller's org.
SET search_path = kl, public;

CREATE TABLE identity_link_states (
  state      text PRIMARY KEY,
  org_id     uuid NOT NULL REFERENCES orgs(id),
  username   text NOT NULL,
  provider   text NOT NULL CHECK (provider IN ('github')),
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE identity_link_states ENABLE ROW LEVEL SECURITY;
CREATE POLICY in_org ON identity_link_states FOR ALL TO kl_app
  USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, DELETE ON identity_link_states TO kl_app;

GRANT INSERT, DELETE ON principal_aliases TO kl_app;
