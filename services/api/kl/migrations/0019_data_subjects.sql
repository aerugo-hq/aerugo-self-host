-- Slice I1: data-subject requests and access reviews. Owner-privileged functions, each confined to the
-- caller's org like the purge functions, find and erase every row that names a person. Attribution columns
-- (actor, created_by, ...) are found by name at run time, so a table added later is covered without
-- touching these functions.
SET search_path = kl, public;

-- Every text column, in a kl table with an org_id, whose name says it holds a person.
CREATE FUNCTION kl.subject_columns() RETURNS TABLE (tbl text, col text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = kl, pg_temp AS $$
  SELECT c.table_name::text, c.column_name::text
  FROM information_schema.columns c
  JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
  WHERE c.table_schema = 'kl' AND t.table_type = 'BASE TABLE' AND c.data_type = 'text'
    AND c.column_name IN ('actor', 'username', 'created_by', 'updated_by', 'requested_by', 'acknowledged_by',
                          'set_by', 'opened_by', 'merged_by', 'granted_by', 'revoked_by')
    AND EXISTS (SELECT 1 FROM information_schema.columns o
                WHERE o.table_schema = 'kl' AND o.table_name = c.table_name AND o.column_name = 'org_id')
  ORDER BY 1, 2
$$;

-- What this org holds about a person: identity links, their own audit trail, and counts of attributed rows.
CREATE FUNCTION kl.subject_export(who text) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = kl, pg_temp AS $$
DECLARE
  r record;
  n bigint;
  attributed jsonb := '[]';
BEGIN
  IF kl.current_org() IS NULL THEN
    RETURN NULL;
  END IF;
  FOR r IN SELECT tbl, col FROM kl.subject_columns() WHERE NOT (tbl = 'audit_events' AND col = 'actor') LOOP
    EXECUTE format('SELECT count(*) FROM kl.%I WHERE org_id = kl.current_org() AND %I = $1', r.tbl, r.col) INTO n USING who;
    IF n > 0 THEN
      attributed := attributed || jsonb_build_object('table', r.tbl, 'column', r.col, 'rows', n);
    END IF;
  END LOOP;
  RETURN jsonb_build_object(
    'identity_links', COALESCE((SELECT jsonb_agg(jsonb_build_object('identity', source, 'linked_at', created_at,
                                                                    'verified_by', verified_by) ORDER BY created_at)
                                FROM principal_aliases WHERE org_id = kl.current_org() AND canonical = 'user:' || who), '[]'),
    'audit_events', COALESCE((SELECT jsonb_agg(jsonb_build_object('at', at, 'action', action, 'scope', scope,
                                                                  'details', details) ORDER BY at)
                              FROM audit_events WHERE org_id = kl.current_org() AND actor = who), '[]'),
    'attributed', attributed);
END $$;

-- Erase a person: every row naming them names an irreversible pseudonym instead. Their own audit events are
-- kept, as the record requires, but lose their words (queries, questions, topics); their identity links go.
-- Returns counts, and how many rows still name them, which must be zero.
CREATE FUNCTION kl.subject_erase(who text, pseudonym text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = kl, pg_temp AS $$
DECLARE
  r record;
  n bigint;
  left_over bigint := 0;
  counts jsonb := '{}';
BEGIN
  IF kl.current_org() IS NULL OR who !~ '^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$' OR pseudonym !~ '^erased-[0-9a-f]{12}$' THEN
    RETURN NULL;
  END IF;
  UPDATE audit_events SET actor = pseudonym, details = details - 'query' - 'question' - 'topic' || '{"erased": true}'
   WHERE org_id = kl.current_org() AND actor = who;
  GET DIAGNOSTICS n = ROW_COUNT; counts := counts || jsonb_build_object('audit_events', n);
  -- Their source identities, then their name, wherever another person's event mentions them.
  FOR r IN SELECT source FROM principal_aliases WHERE org_id = kl.current_org() AND canonical = 'user:' || who LOOP
    UPDATE audit_events SET details = replace(details::text, '"' || r.source || '"', '"ext:' || pseudonym || '"')::jsonb
     WHERE org_id = kl.current_org() AND strpos(details::text, '"' || r.source || '"') > 0;
  END LOOP;
  UPDATE audit_events
     SET details = replace(replace(replace(details::text,
                     '"user:' || who || '"', '"user:' || pseudonym || '"'),
                     '"admin:' || who || '"', '"admin:' || pseudonym || '"'),
                     '"' || who || '"', '"' || pseudonym || '"')::jsonb
   WHERE org_id = kl.current_org()
     AND (strpos(details::text, '"user:' || who || '"') > 0 OR strpos(details::text, '"admin:' || who || '"') > 0
          OR strpos(details::text, '"' || who || '"') > 0);
  GET DIAGNOSTICS n = ROW_COUNT; counts := counts || jsonb_build_object('mentions', n);
  FOR r IN SELECT tbl, col FROM kl.subject_columns() WHERE NOT (tbl = 'audit_events' AND col = 'actor') LOOP
    IF r.tbl = 'identity_link_states' THEN
      CONTINUE;  -- deleted below: a pending sign-in has no record to keep
    END IF;
    EXECUTE format('UPDATE kl.%I SET %I = $2 WHERE org_id = kl.current_org() AND %I = $1', r.tbl, r.col, r.col) USING who, pseudonym;
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n > 0 THEN
      counts := counts || jsonb_build_object(r.tbl || '.' || r.col, n);
    END IF;
  END LOOP;
  DELETE FROM principal_aliases WHERE org_id = kl.current_org() AND canonical = 'user:' || who;
  GET DIAGNOSTICS n = ROW_COUNT; counts := counts || jsonb_build_object('identity_links', n);
  UPDATE principal_aliases SET verified_by = 'admin:' || pseudonym WHERE org_id = kl.current_org() AND verified_by = 'admin:' || who;
  DELETE FROM identity_link_states WHERE org_id = kl.current_org() AND username = who;
  DELETE FROM rate_counters WHERE org_id = kl.current_org() AND bucket LIKE '%:user:' || who
     AND right(bucket, length(who) + 6) = ':user:' || who;
  -- Verify: nothing in this org still names them.
  FOR r IN SELECT tbl, col FROM kl.subject_columns() LOOP
    EXECUTE format('SELECT count(*) FROM kl.%I WHERE org_id = kl.current_org() AND %I = $1', r.tbl, r.col) INTO n USING who;
    left_over := left_over + n;
  END LOOP;
  left_over := left_over
    + (SELECT count(*) FROM principal_aliases WHERE org_id = kl.current_org() AND (canonical = 'user:' || who OR verified_by = 'admin:' || who))
    + (SELECT count(*) FROM audit_events WHERE org_id = kl.current_org()
         AND (strpos(details::text, '"user:' || who || '"') > 0 OR strpos(details::text, '"' || who || '"') > 0));
  RETURN counts || jsonb_build_object('remaining', left_over);
END $$;

-- Access reviews: how many documents of each classification each team holds. Counts only.
CREATE FUNCTION kl.access_counts() RETURNS TABLE (workspace text, classification text, documents bigint)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = kl, pg_temp AS $$
  SELECT w.slug, d.classification, count(*)
  FROM workspaces w
  JOIN workspace_documents wd ON wd.workspace_id = w.id
  JOIN documents d ON d.id = wd.document_id
  WHERE w.org_id = kl.current_org()
  GROUP BY w.slug, d.classification
  ORDER BY 1, 2
$$;

-- Access reviews cover every team, private ones included, whatever the reviewer's own memberships.
CREATE FUNCTION kl.access_teams() RETURNS TABLE (slug text, name text, discoverability text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = kl, pg_temp AS $$
  SELECT slug, name, discoverability FROM workspaces WHERE org_id = kl.current_org() ORDER BY slug
$$;

REVOKE ALL ON FUNCTION kl.subject_columns(), kl.subject_export(text), kl.subject_erase(text, text),
  kl.access_counts(), kl.access_teams() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kl.subject_export(text), kl.subject_erase(text, text), kl.access_counts(),
  kl.access_teams() TO kl_app;
