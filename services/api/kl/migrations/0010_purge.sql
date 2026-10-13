-- Slice H1: removing a whole team. A purge request waits out a delay window (and can be cancelled), then
-- the worker removes everything the team owns. The worker stays under row-level security; the deletes run
-- in two owner-privileged functions that refuse any workspace outside the caller's own org.
SET search_path = kl, public;

CREATE TABLE purge_requests (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       uuid NOT NULL REFERENCES orgs(id),
  workspace_id uuid NOT NULL,   -- no foreign key: the workspace is deleted, this record of it stays
  slug         text NOT NULL,
  requested_by text NOT NULL,
  run_after    timestamptz NOT NULL,
  status       text NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'cancelled', 'running', 'done')),
  result       jsonb,           -- counts only: what was removed, and the verification
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX purge_requests_one_open ON purge_requests (workspace_id) WHERE status IN ('scheduled', 'running');

ALTER TABLE purge_requests ENABLE ROW LEVEL SECURITY;
CREATE POLICY in_org ON purge_requests FOR ALL TO kl_app, kl_ingest
  USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, UPDATE ON purge_requests TO kl_app;
GRANT SELECT, UPDATE ON purge_requests TO kl_ingest;

-- What a purge will remove, so authorization tuples and secrets can be cleaned up first and on retry.
CREATE FUNCTION kl.purge_plan(ws uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = kl, pg_temp AS $$
DECLARE
  w record;
BEGIN
  SELECT id, slug INTO w FROM workspaces WHERE id = ws AND org_id = kl.current_org();
  IF NOT FOUND THEN
    RETURN NULL;  -- already removed, or not in this org
  END IF;
  RETURN jsonb_build_object(
    'slug', w.slug,
    -- Documents only this team holds are deleted; documents another team also holds are only unlinked.
    'documents', COALESCE((SELECT jsonb_agg(d.external_key) FROM documents d
                           JOIN workspace_documents wd ON wd.document_id = d.id AND wd.workspace_id = ws
                           WHERE NOT EXISTS (SELECT 1 FROM workspace_documents o
                                             WHERE o.document_id = d.id AND o.workspace_id <> ws)), '[]'),
    'shared', COALESCE((SELECT count(*) FROM workspace_documents wd
                        WHERE wd.workspace_id = ws AND EXISTS (SELECT 1 FROM workspace_documents o
                                                               WHERE o.document_id = wd.document_id AND o.workspace_id <> ws)), 0),
    'containers', COALESCE((SELECT jsonb_agg(DISTINCT cc.external_id) FROM connection_containers cc
                            JOIN connections c ON c.id = cc.connection_id WHERE c.workspace_id = ws), '[]'),
    'credentials', COALESCE((SELECT jsonb_agg(ref) FROM (
                               SELECT credential_ref AS ref FROM model_endpoints WHERE workspace_id = ws
                               UNION SELECT credential_ref FROM embedding_models
                                     WHERE workspace_id = ws AND credential_ref IS NOT NULL) refs), '[]'));
END $$;

-- Remove the team, then verify nothing derived from its documents remains. Returns counts only.
CREATE FUNCTION kl.purge_workspace(ws uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = kl, pg_temp AS $$
DECLARE
  doc_ids   uuid[];
  chunk_ids uuid[];
  counts    jsonb := '{}';
  n         bigint;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM workspaces WHERE id = ws AND org_id = kl.current_org()) THEN
    RETURN NULL;
  END IF;
  SELECT COALESCE(array_agg(wd.document_id), '{}') INTO doc_ids FROM workspace_documents wd
   WHERE wd.workspace_id = ws
     AND NOT EXISTS (SELECT 1 FROM workspace_documents o WHERE o.document_id = wd.document_id AND o.workspace_id <> ws);
  SELECT COALESCE(array_agg(id), '{}') INTO chunk_ids FROM chunks WHERE document_id = ANY(doc_ids);

  DELETE FROM proposals WHERE workspace_id = ws;  -- drafts hold text derived from their sources
  GET DIAGNOSTICS n = ROW_COUNT; counts := counts || jsonb_build_object('proposals', n);
  DELETE FROM model_usage WHERE workspace_id = ws;
  GET DIAGNOSTICS n = ROW_COUNT; counts := counts || jsonb_build_object('model_usage', n);
  DELETE FROM model_endpoints WHERE workspace_id = ws;
  GET DIAGNOSTICS n = ROW_COUNT; counts := counts || jsonb_build_object('model_endpoints', n);
  DELETE FROM chunk_embeddings WHERE model_id IN (SELECT id FROM embedding_models WHERE workspace_id = ws);
  DELETE FROM embedding_models WHERE workspace_id = ws;
  DELETE FROM jobs WHERE status <> 'running'
     AND payload->>'connection_id' IN (SELECT id::text FROM connections WHERE workspace_id = ws);
  DELETE FROM connections WHERE workspace_id = ws;  -- their containers cascade
  GET DIAGNOSTICS n = ROW_COUNT; counts := counts || jsonb_build_object('connections', n);
  DELETE FROM documents WHERE id = ANY(doc_ids);  -- chunks, embeddings, concepts, versions, events, edges cascade
  GET DIAGNOSTICS n = ROW_COUNT; counts := counts || jsonb_build_object('documents', n, 'chunks', cardinality(chunk_ids));
  DELETE FROM workspaces WHERE id = ws;  -- the team's remaining links to shared documents cascade
  GET DIAGNOSTICS n = ROW_COUNT; counts := counts || jsonb_build_object('workspaces', n);

  RETURN counts || jsonb_build_object('remaining',
    (SELECT count(*) FROM documents WHERE id = ANY(doc_ids))
    + (SELECT count(*) FROM chunks WHERE id = ANY(chunk_ids))
    + (SELECT count(*) FROM chunk_embeddings WHERE chunk_id = ANY(chunk_ids))
    + (SELECT count(*) FROM concepts WHERE document_id = ANY(doc_ids))
    + (SELECT count(*) FROM proposals WHERE workspace_id = ws)
    + (SELECT count(*) FROM model_endpoints WHERE workspace_id = ws));
END $$;

REVOKE ALL ON FUNCTION kl.purge_plan(uuid), kl.purge_workspace(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kl.purge_plan(uuid), kl.purge_workspace(uuid) TO kl_ingest;
