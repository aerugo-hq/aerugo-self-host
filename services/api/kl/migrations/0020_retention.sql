-- Slice I2: retention policies and legal holds. A policy keeps a kind of record for a number of days, for
-- the whole org or one team (and, for documents, one classification); the most specific policy wins, and no
-- policy means keep forever. A legal hold, on the org or one team, keeps everything it covers until it's
-- released. The worker applies retention hourly through owner-privileged functions confined to its org,
-- and every run is recorded with what it removed.
SET search_path = kl, public;

CREATE TABLE retention_policies (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES orgs(id),
  workspace_id   uuid REFERENCES workspaces(id) ON DELETE CASCADE,   -- NULL: the whole org
  target         text NOT NULL CHECK (target IN ('documents', 'concept_versions', 'audit_events', 'model_usage')),
  classification text CHECK (classification IN ('public', 'internal', 'confidential', 'restricted')),
  keep_days      int NOT NULL CHECK (keep_days BETWEEN 1 AND 36500),
  created_by     text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  CHECK (classification IS NULL OR target = 'documents'),                 -- only documents carry one
  CHECK (workspace_id IS NULL OR target IN ('documents', 'model_usage')),  -- the others are org-wide records
  CHECK (target <> 'audit_events' OR keep_days >= 30),                     -- the audit trail is evidence
  UNIQUE NULLS NOT DISTINCT (org_id, workspace_id, target, classification)
);

CREATE TABLE legal_holds (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       uuid NOT NULL REFERENCES orgs(id),
  workspace_id uuid REFERENCES workspaces(id) ON DELETE RESTRICT,   -- NULL: the whole org; a held team can't be removed
  reason       text NOT NULL,
  created_by   text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  released_by  text,
  released_at  timestamptz
);

CREATE TABLE retention_runs (             -- evidence: when retention ran and what it removed (counts only)
  id     bigserial PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES orgs(id),
  at     timestamptz NOT NULL DEFAULT now(),
  counts jsonb NOT NULL
);

ALTER TABLE retention_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_holds        ENABLE ROW LEVEL SECURITY;
ALTER TABLE retention_runs     ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON retention_policies USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY org_isolation ON legal_holds        USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY org_isolation ON retention_runs     USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT, INSERT, UPDATE, DELETE ON retention_policies TO kl_app;
GRANT SELECT, INSERT, UPDATE ON legal_holds TO kl_app;                   -- holds are released, never deleted
GRANT SELECT ON retention_runs TO kl_app;

-- The days to keep a kind of record, for a team and classification: the most specific policy, or NULL (forever).
CREATE FUNCTION kl.retention_keep(what text, ws uuid, cls text) RETURNS int
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = kl, pg_temp AS $$
  SELECT keep_days FROM retention_policies
  WHERE org_id = kl.current_org() AND target = what
    AND (workspace_id = ws OR workspace_id IS NULL)
    AND (classification = cls OR classification IS NULL)
  ORDER BY workspace_id IS NULL, classification IS NULL
  LIMIT 1
$$;

-- Whether a hold covers a team (or, for ws NULL, whether the whole org is held).
CREATE FUNCTION kl.retention_held(ws uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = kl, pg_temp AS $$
  SELECT EXISTS (SELECT 1 FROM legal_holds
                 WHERE org_id = kl.current_org() AND released_at IS NULL
                   AND (workspace_id IS NULL OR workspace_id = ws))
$$;

-- Documents to remove: expired for every team that holds them, measured from when they were last seen
-- (created, or re-synced: a document its source still has is never stale), and under no hold.
CREATE FUNCTION kl.retention_plan() RETURNS TABLE (document_id uuid, external_key text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = kl, pg_temp AS $$
  WITH placed AS (
    SELECT d.id, d.external_key,
           greatest(d.created_at, d.acl_synced_at) AS last_seen,
           kl.retention_keep('documents', wd.workspace_id, d.classification) AS keep_days,
           kl.retention_held(wd.workspace_id) AS held
    FROM documents d JOIN workspace_documents wd ON wd.document_id = d.id
    WHERE d.org_id = kl.current_org()
  )
  SELECT id, external_key FROM placed
  GROUP BY id, external_key
  HAVING bool_and(keep_days IS NOT NULL AND last_seen < now() - make_interval(days => keep_days))
     AND NOT bool_or(held)
$$;

-- Apply every policy, then record the run. Deleting a document removes everything derived from it
-- (chunks, embeddings, concepts and their versions, map links), which cascade. Returns counts only.
CREATE FUNCTION kl.apply_retention() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = kl, pg_temp AS $$
DECLARE
  org uuid := kl.current_org();
  n bigint;
  keep int;
  doc_ids uuid[];
  counts jsonb := '{}';
BEGIN
  IF org IS NULL THEN
    RETURN NULL;
  END IF;
  SELECT COALESCE(array_agg(document_id), '{}') INTO doc_ids FROM kl.retention_plan();
  DELETE FROM documents WHERE id = ANY(doc_ids);
  GET DIAGNOSTICS n = ROW_COUNT; counts := counts || jsonb_build_object('documents', n);

  -- Older versions of a concept, never its current one, and never one whose document a hold covers.
  keep := kl.retention_keep('concept_versions', NULL, NULL);
  IF keep IS NOT NULL AND NOT kl.retention_held(NULL) THEN
    DELETE FROM concept_versions v USING concepts k
     WHERE v.concept_id = k.id AND k.org_id = org AND v.hash IS DISTINCT FROM k.latest_hash
       AND v.created_at < now() - make_interval(days => keep)
       AND NOT EXISTS (SELECT 1 FROM workspace_documents wd
                       WHERE wd.document_id = k.document_id AND kl.retention_held(wd.workspace_id));
    GET DIAGNOSTICS n = ROW_COUNT; counts := counts || jsonb_build_object('concept_versions', n);
  END IF;

  -- The audit trail, except events in a held team's scope.
  keep := kl.retention_keep('audit_events', NULL, NULL);
  IF keep IS NOT NULL AND NOT kl.retention_held(NULL) THEN
    DELETE FROM audit_events e
     WHERE e.org_id = org AND e.at < now() - make_interval(days => keep)
       AND NOT EXISTS (SELECT 1 FROM legal_holds h JOIN workspaces w ON w.id = h.workspace_id
                       WHERE h.org_id = org AND h.released_at IS NULL AND w.slug = ANY(e.scope));
    GET DIAGNOSTICS n = ROW_COUNT; counts := counts || jsonb_build_object('audit_events', n);
  END IF;

  -- Model usage, by its team's policy (or the org's); a NULL policy keeps it.
  DELETE FROM model_usage u
   WHERE u.org_id = org AND NOT kl.retention_held(u.workspace_id)
     AND u.at < now() - make_interval(days => kl.retention_keep('model_usage', u.workspace_id, NULL));
  GET DIAGNOSTICS n = ROW_COUNT; counts := counts || jsonb_build_object('model_usage', n);

  INSERT INTO retention_runs (org_id, counts) VALUES (org, counts);
  RETURN counts;
END $$;

REVOKE ALL ON FUNCTION kl.retention_keep(text, uuid, text), kl.retention_held(uuid), kl.retention_plan(),
  kl.apply_retention() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kl.retention_plan(), kl.apply_retention() TO kl_ingest;
