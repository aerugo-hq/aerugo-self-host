-- A connection's documents are its own. Container refs and keys are unique per site, not per org (a Jira project key,
-- a Confluence space key, jira-<issue key>, confluence-<page id>), so two connections in one org, to two sites, could
-- share them: the second one's sync deleted what the first had indexed and its own listing lacked, and overwrote
-- documents whose keys collided (when the versions matched, only their readers, so one site's issue got the other's).
-- Documents the shared sync loop writes (Jira, Confluence) now name their connection, and a sync reads, updates, and
-- deletes only its own. GitHub's and Drive's stay unnamed: a repository or a drive is one thing everywhere (its ids are
-- global), and two teams' connections to it both hold it (slice T2). A removed connection's documents lose its name
-- (SET NULL) until its retire job, which then tells them from another connection's in the same container. The table's
-- grants already cover the column.
SET search_path = kl, public;

ALTER TABLE documents ADD COLUMN IF NOT EXISTS connection_id uuid REFERENCES connections(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS documents_connection_idx ON documents (connection_id);

-- Existing documents keep their connection: the one connection of their kind that lists their container.
UPDATE documents d SET connection_id = o.connection_id
FROM (SELECT d.id, (array_agg(DISTINCT c.id))[1] AS connection_id
      FROM documents d
      JOIN connection_containers cc ON cc.org_id = d.org_id AND cc.external_id = d.container_ref
      JOIN connections c ON c.id = cc.connection_id AND c.kind = d.source_kind
      WHERE d.source_kind IN ('jira', 'confluence') AND d.connection_id IS NULL
      GROUP BY d.id
      HAVING count(DISTINCT c.id) = 1) o
WHERE d.id = o.id;

-- One that no connection lists can't be kept current, and one that two list may already carry the other site's content
-- or readers: both go (with everything derived from them), failing closed, and the next sync of the connection each
-- belongs to indexes it again with its source's own readers. Their tuples stay until that sync rewrites them; with no
-- row, RLS finds nothing they could grant. Only what a connection wrote has a container: the M0 seed's Confluence page
-- has none, and stays.
DELETE FROM documents WHERE source_kind IN ('jira', 'confluence') AND connection_id IS NULL AND container_ref IS NOT NULL;
