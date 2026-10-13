-- Slice M2: the org's reranker. One per org, so every search, across every team, is reordered on the
-- same scale. An org admin sets it through the API; it has its own egress list, because it reads
-- passages; and only a reference to its key is stored here, never the key.
SET search_path = kl, public;

CREATE TABLE rerank_models (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                  uuid NOT NULL UNIQUE REFERENCES orgs(id),
  provider                text NOT NULL CHECK (provider IN ('openai_compatible')),
  model                   text NOT NULL,
  base_url                text NOT NULL,
  credential_ref          text,                -- path in the secrets backend; NULL for a keyless local reranker
  allowed_classifications text[] NOT NULL DEFAULT '{public,internal}',
  created_by              text NOT NULL,
  created_at              timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE rerank_models ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON rerank_models USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, UPDATE, DELETE ON rerank_models TO kl_app;
