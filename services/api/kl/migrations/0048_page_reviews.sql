-- E2: knowledge health's review queue. Every confirm, update request, and retirement of a curated page, by whom and
-- when, and at which version; a page whose latest review retired it is left out of search and Ask until it's confirmed.
SET search_path = kl, public;

CREATE TABLE page_reviews (
  id           bigserial PRIMARY KEY,
  org_id       uuid NOT NULL REFERENCES orgs(id),
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  concept_id   uuid NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
  action       text NOT NULL CHECK (action IN ('confirm', 'update', 'retire')),
  by_user      text NOT NULL,
  at_hash      text,
  note         text CHECK (note IS NULL OR length(note) <= 500),
  at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX page_reviews_latest ON page_reviews (concept_id, at DESC);
ALTER TABLE page_reviews ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON page_reviews USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT ON page_reviews TO kl_app;
GRANT USAGE ON SEQUENCE page_reviews_id_seq TO kl_app;
GRANT SELECT ON page_reviews TO kl_ingest;
