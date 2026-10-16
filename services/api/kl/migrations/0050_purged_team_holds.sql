-- A released legal hold is evidence, so it outlives the team it held. legal_holds' foreign key refuses to lose its
-- team (ON DELETE RESTRICT, meant for an active hold), so purging any team that was ever held, and released, failed
-- for good. The purge now refuses a team an active hold covers, then detaches the team's released holds, keeping the
-- team's name on each; an active hold still can never lose its team.
SET search_path = kl, public;

ALTER TABLE legal_holds ADD COLUMN team_slug text;  -- the team a released hold covered, once that team is purged
ALTER TABLE legal_holds ADD CONSTRAINT legal_holds_purged_team_check
  CHECK (team_slug IS NULL OR (workspace_id IS NULL AND released_at IS NOT NULL));

CREATE OR REPLACE FUNCTION kl.purge_workspace(ws uuid) RETURNS jsonb
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
  IF kl.retention_held(ws) THEN
    RAISE EXCEPTION 'this team is under a legal hold, so it can''t be purged';
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
  UPDATE legal_holds h SET team_slug = w.slug, workspace_id = NULL  -- released ones only: an active one stopped us above
    FROM workspaces w WHERE w.id = ws AND h.workspace_id = ws AND h.released_at IS NOT NULL;
  GET DIAGNOSTICS n = ROW_COUNT; counts := counts || jsonb_build_object('released_holds_kept', n);
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

-- The worker asks before it destroys anything outside Postgres (authorization, secrets, the team's key).
GRANT EXECUTE ON FUNCTION kl.retention_held(uuid) TO kl_ingest;
