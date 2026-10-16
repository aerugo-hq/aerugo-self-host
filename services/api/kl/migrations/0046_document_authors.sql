-- Q7: Git authors. Who wrote a file a GitHub connection indexes (its commits' authors, by GitHub login), so who to ask
-- about a page can name the people who wrote it, as far as they've linked their GitHub accounts.
SET search_path = kl, public;

CREATE TABLE document_authors (
  org_id      uuid NOT NULL REFERENCES orgs(id),
  document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  login       text NOT NULL CHECK (login ~ '^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$'),
  commits     int  NOT NULL CHECK (commits > 0),
  last_at     timestamptz,
  at_commit   text NOT NULL,  -- the commit the authors were read at: read again only when the file changes
  PRIMARY KEY (document_id, login)
);
ALTER TABLE document_authors ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON document_authors USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT ON document_authors TO kl_app;
GRANT SELECT, INSERT, DELETE ON document_authors TO kl_ingest;
