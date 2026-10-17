-- The permissions audit (docs/PRODUCTION_REVIEW.md, P1 to P3). Four tables were readable org-wide although each row is
-- about something narrower; a query that read one without joining through what it's about would have leaked, and the
-- database wouldn't have stopped it. Now each policy checks what the row is about.
--
-- A team's own rows (asset owners, review history, media choices) need the team, which the database can't see: who is
-- on a team lives in OpenFGA. So a transaction names the teams it may touch (db.scoped's teams, set as kl.teams after
-- the API checked them in OpenFGA), and a transaction that names none sees none of these rows.
SET search_path = kl, public;

CREATE FUNCTION kl.current_teams() RETURNS text[]
  LANGUAGE sql STABLE
  AS $$ SELECT COALESCE(string_to_array(NULLIF(current_setting('kl.teams', true), ''), ','), '{}') $$;

-- P1: asset owners, by email, only to their asset's team. Only the worker writes them.
DROP POLICY org_isolation ON asset_owners;
REVOKE INSERT, DELETE ON asset_owners FROM kl_app;
CREATE POLICY read_in_its_team ON asset_owners FOR SELECT TO kl_app USING (
  org_id = kl.current_org()
  AND EXISTS (SELECT 1 FROM workspaces w WHERE w.id = workspace_id AND w.slug = ANY(kl.current_teams())));
CREATE POLICY ingest_in_org ON asset_owners FOR ALL TO kl_ingest
  USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());

-- P1: who wrote a document, only to those who may read it (its own row-level security decides).
DROP POLICY org_isolation ON document_authors;
CREATE POLICY read_if_document_visible ON document_authors FOR SELECT TO kl_app USING (
  org_id = kl.current_org() AND EXISTS (SELECT 1 FROM documents d WHERE d.id = document_id));
CREATE POLICY ingest_in_org ON document_authors FOR ALL TO kl_ingest
  USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());

-- P1: a page's review history, notes included, only to its team, and only for a page the reader may see.
DROP POLICY org_isolation ON page_reviews;
CREATE POLICY read_in_its_team ON page_reviews FOR SELECT TO kl_app USING (
  org_id = kl.current_org()
  AND EXISTS (SELECT 1 FROM concepts k WHERE k.id = concept_id)
  AND EXISTS (SELECT 1 FROM workspaces w WHERE w.id = workspace_id AND w.slug = ANY(kl.current_teams())));
CREATE POLICY review_in_its_team ON page_reviews FOR INSERT TO kl_app WITH CHECK (
  org_id = kl.current_org()
  AND EXISTS (SELECT 1 FROM concepts k WHERE k.id = concept_id)
  AND EXISTS (SELECT 1 FROM workspaces w WHERE w.id = workspace_id AND w.slug = ANY(kl.current_teams())));
CREATE POLICY ingest_reads_in_org ON page_reviews FOR SELECT TO kl_ingest USING (org_id = kl.current_org());

-- A retired page leaves everyone's search and Ask, not only its team's: which pages are retired (their ids, nothing
-- of the review) comes from here. The caller joins concepts under its own row-level security, so it learns only
-- about pages it may read anyway.
CREATE FUNCTION kl.retired_concepts() RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = kl, pg_temp AS $$
  SELECT concept_id FROM (SELECT DISTINCT ON (concept_id) concept_id, action FROM page_reviews
                          WHERE org_id = kl.current_org() ORDER BY concept_id, at DESC, id DESC) latest
  WHERE action = 'retire'
$$;
REVOKE ALL ON FUNCTION kl.retired_concepts() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kl.retired_concepts() TO kl_app;

-- P2: a team's media choices are read and written only in its own transactions, and only with its own models, so a
-- bug elsewhere can't choose the models another team's documents are sent to.
DROP POLICY org_isolation ON team_media;
CREATE POLICY in_its_team ON team_media FOR ALL TO kl_app
  USING (org_id = kl.current_org()
         AND EXISTS (SELECT 1 FROM workspaces w WHERE w.id = workspace_id AND w.slug = ANY(kl.current_teams())))
  WITH CHECK (org_id = kl.current_org()
              AND EXISTS (SELECT 1 FROM workspaces w WHERE w.id = workspace_id AND w.slug = ANY(kl.current_teams()))
              AND (vision_model_id IS NULL OR EXISTS (SELECT 1 FROM model_endpoints m
                                                      WHERE m.id = vision_model_id AND m.workspace_id = team_media.workspace_id))
              AND (speech_model_id IS NULL OR EXISTS (SELECT 1 FROM model_endpoints m
                                                      WHERE m.id = speech_model_id AND m.workspace_id = team_media.workspace_id)));
CREATE POLICY ingest_reads_in_org ON team_media FOR SELECT TO kl_ingest USING (org_id = kl.current_org());

-- P3: the change feed keeps a window (90 days by default, KL_CHANGE_FEED_DAYS); retention prunes what's older and
-- records how far it pruned, so the API can tell a client asking from before the window that it missed changes.
CREATE TABLE change_feed_floor (
  org_id    uuid PRIMARY KEY REFERENCES orgs(id),
  before_id bigint NOT NULL,           -- every change with a smaller id is gone
  pruned_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE change_feed_floor ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_isolation ON change_feed_floor USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT ON change_feed_floor TO kl_app;

CREATE FUNCTION kl.prune_changes(keep_days int) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = kl, pg_temp AS $$
DECLARE
  org uuid := kl.current_org();
  last_gone bigint;
  n bigint;
BEGIN
  IF org IS NULL OR keep_days < 1 THEN
    RETURN 0;
  END IF;
  SELECT max(id) INTO last_gone FROM document_changes WHERE org_id = org AND at < now() - make_interval(days => keep_days);
  IF last_gone IS NULL THEN
    RETURN 0;
  END IF;
  DELETE FROM document_changes WHERE org_id = org AND id <= last_gone;
  GET DIAGNOSTICS n = ROW_COUNT;
  INSERT INTO change_feed_floor (org_id, before_id) VALUES (org, last_gone + 1)
    ON CONFLICT (org_id) DO UPDATE SET before_id = greatest(change_feed_floor.before_id, EXCLUDED.before_id), pruned_at = now();
  RETURN n;
END $$;
REVOKE ALL ON FUNCTION kl.prune_changes(int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kl.prune_changes(int) TO kl_ingest;
