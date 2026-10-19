-- F10a, the Atlas (docs/ORG_INTELLIGENCE_PLAN.md O1): one graph of systems, jobs, tables, and how data flows between
-- them, where every node and edge is only what its evidence says, and visible only to who may see that evidence.
--
-- A node (a dataset, a job, later a repo or a dashboard) is known by its kind, namespace, and name. An edge says how two
-- nodes connect (reads, writes, feeds...), in the direction data flows. Evidence says who said so: a team's pushed
-- lineage, a catalog connection, a CI pipeline; its tier, where it came from, and who may see it: the source's own
-- readers (acl_principals), or the team that reported it (team_id). A node's or edge's readers are the union of its
-- evidence's, kept by a trigger; one that loses its last evidence is removed. An edge is visible only when its own
-- evidence is and both of its ends are (invariant 15: a walk never passes through, or reveals, a hidden node).
--
-- Writes go only through kl.atlas_node and kl.atlas_edge: an upsert that met another team's hidden node would fail
-- under row-level security, and in failing say the node exists. The API may record only for the teams its transaction
-- names (kl.teams, checked in OpenFGA first) and never sets source readers; the worker records for its connection's team.
--
-- K3's tables move in: their rows become nodes, edges, and evidence (source 'k3'), and `assets` and `lineage_edges`
-- become views of the same shape (a row per node and reporting team) for the code that reads them. asset_owners points at
-- nodes.
SET search_path = kl, public;

CREATE TABLE atlas_nodes (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES orgs(id),
  kind           text NOT NULL CHECK (kind IN ('dataset', 'job', 'table', 'view', 'column', 'system', 'repo', 'service',
                                               'folder', 'topic', 'api', 'dashboard', 'team', 'person', 'document',
                                               'decision', 'incident')),
  namespace      text NOT NULL CHECK (length(namespace) BETWEEN 1 AND 200),  -- the system it lives in: edw, bigquery, dataform
  name           text NOT NULL CHECK (length(name) BETWEEN 1 AND 300),
  attrs          jsonb NOT NULL DEFAULT '{}',
  acl_principals text[] NOT NULL DEFAULT '{}',  -- the union of its evidence's source readers
  teams          uuid[] NOT NULL DEFAULT '{}',  -- the teams that reported it
  first_seen     timestamptz NOT NULL DEFAULT now(),
  last_seen      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, kind, namespace, name)
);

CREATE TABLE atlas_edges (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES orgs(id),
  src            uuid NOT NULL REFERENCES atlas_nodes(id) ON DELETE CASCADE,  -- data or control flows from here...
  dst            uuid NOT NULL REFERENCES atlas_nodes(id) ON DELETE CASCADE,  -- ...to here
  kind           text NOT NULL CHECK (kind IN ('reads', 'writes', 'feeds', 'calls', 'produces', 'consumes', 'triggers',
                                               'depends_on', 'builds', 'deploys', 'owns', 'documents', 'decided_in',
                                               'member_of', 'caused', 'joined_with', 'changes_with')),
  confidence     real NOT NULL DEFAULT 1 CHECK (confidence > 0 AND confidence <= 1),
  acl_principals text[] NOT NULL DEFAULT '{}',
  teams          uuid[] NOT NULL DEFAULT '{}',
  first_seen     timestamptz NOT NULL DEFAULT now(),
  last_seen      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (src, dst, kind),
  CHECK (src <> dst)
);
CREATE INDEX atlas_edges_dst ON atlas_edges (dst);

CREATE TABLE atlas_evidence (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES orgs(id),
  node_id        uuid REFERENCES atlas_nodes(id) ON DELETE CASCADE,
  edge_id        uuid REFERENCES atlas_edges(id) ON DELETE CASCADE,
  tier           text NOT NULL CHECK (tier IN ('confirmed', 'observed', 'declared', 'documented', 'inferred')),
  source         text NOT NULL CHECK (length(source) BETWEEN 1 AND 300),  -- what said it: 'lineage', a connection, a pipeline
  ref            text CHECK (length(ref) <= 1000),                        -- where: a file and line, a job id, a page
  detail         jsonb NOT NULL DEFAULT '{}',
  acl_principals text[] NOT NULL DEFAULT '{}',                            -- who may see the source it came from
  team_id        uuid REFERENCES workspaces(id) ON DELETE CASCADE,        -- the team that reported it, when one did
  first_seen     timestamptz NOT NULL DEFAULT now(),
  observed_at    timestamptz NOT NULL DEFAULT now(),
  CHECK ((node_id IS NULL) <> (edge_id IS NULL))
);
CREATE UNIQUE INDEX atlas_evidence_of_node ON atlas_evidence (node_id, source, team_id) NULLS NOT DISTINCT
  WHERE node_id IS NOT NULL;
CREATE UNIQUE INDEX atlas_evidence_of_edge ON atlas_evidence (edge_id, source, team_id) NULLS NOT DISTINCT
  WHERE edge_id IS NOT NULL;
CREATE INDEX atlas_evidence_team ON atlas_evidence (team_id);

-- A node's and an edge's readers follow their evidence; with none left, they go.
CREATE FUNCTION kl.atlas_follow_evidence() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = kl, pg_temp AS $$
DECLARE
  n uuid; e uuid;
BEGIN
  FOREACH n IN ARRAY ARRAY[CASE WHEN TG_OP <> 'INSERT' THEN OLD.node_id END, CASE WHEN TG_OP <> 'DELETE' THEN NEW.node_id END] LOOP
    CONTINUE WHEN n IS NULL;
    UPDATE atlas_nodes SET
      acl_principals = COALESCE((SELECT array_agg(DISTINCT p ORDER BY p) FROM atlas_evidence x, unnest(x.acl_principals) p WHERE x.node_id = n), '{}'),
      teams = COALESCE((SELECT array_agg(DISTINCT x.team_id ORDER BY x.team_id) FROM atlas_evidence x WHERE x.node_id = n AND x.team_id IS NOT NULL), '{}')
    WHERE id = n;
    DELETE FROM atlas_nodes WHERE id = n AND NOT EXISTS (SELECT 1 FROM atlas_evidence x WHERE x.node_id = n);
  END LOOP;
  FOREACH e IN ARRAY ARRAY[CASE WHEN TG_OP <> 'INSERT' THEN OLD.edge_id END, CASE WHEN TG_OP <> 'DELETE' THEN NEW.edge_id END] LOOP
    CONTINUE WHEN e IS NULL;
    UPDATE atlas_edges SET
      acl_principals = COALESCE((SELECT array_agg(DISTINCT p ORDER BY p) FROM atlas_evidence x, unnest(x.acl_principals) p WHERE x.edge_id = e), '{}'),
      teams = COALESCE((SELECT array_agg(DISTINCT x.team_id ORDER BY x.team_id) FROM atlas_evidence x WHERE x.edge_id = e AND x.team_id IS NOT NULL), '{}')
    WHERE id = e;
    DELETE FROM atlas_edges WHERE id = e AND NOT EXISTS (SELECT 1 FROM atlas_evidence x WHERE x.edge_id = e);
  END LOOP;
  RETURN NULL;
END $$;
CREATE TRIGGER atlas_follow_evidence AFTER INSERT OR UPDATE OR DELETE ON atlas_evidence
  FOR EACH ROW EXECUTE FUNCTION kl.atlas_follow_evidence();

-- Visible: a reader of a source it came from, or on a team that reported it (the teams the API checked in OpenFGA).
CREATE FUNCTION kl.atlas_visible(readers text[], teams uuid[]) RETURNS boolean
  LANGUAGE sql STABLE AS $$
  SELECT readers && COALESCE(kl.current_principals(), '{}')
      OR (cardinality(teams) > 0 AND EXISTS (SELECT 1 FROM workspaces w WHERE w.id = ANY(teams) AND w.slug = ANY(kl.current_teams())))
$$;

ALTER TABLE atlas_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE atlas_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE atlas_evidence ENABLE ROW LEVEL SECURITY;
CREATE POLICY read_if_visible ON atlas_nodes FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND kl.atlas_visible(acl_principals, teams));
CREATE POLICY read_if_visible ON atlas_edges FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND kl.atlas_visible(acl_principals, teams)
         AND EXISTS (SELECT 1 FROM atlas_nodes n WHERE n.id = src) AND EXISTS (SELECT 1 FROM atlas_nodes n WHERE n.id = dst));
CREATE POLICY read_if_visible ON atlas_evidence FOR SELECT TO kl_app
  USING (org_id = kl.current_org()
         AND kl.atlas_visible(acl_principals, CASE WHEN team_id IS NULL THEN '{}'::uuid[] ELSE ARRAY[team_id] END));
CREATE POLICY ingest_in_org ON atlas_nodes FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY ingest_in_org ON atlas_edges FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY ingest_in_org ON atlas_evidence FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
GRANT SELECT ON atlas_nodes, atlas_edges, atlas_evidence TO kl_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON atlas_nodes, atlas_edges, atlas_evidence TO kl_ingest;

-- Who may record: the API only for a team its transaction names, with no source readers of its own choosing; the
-- worker for its org. Everything else is refused.
CREATE FUNCTION kl.atlas_may_record(team uuid, readers text[]) RETURNS uuid
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = kl, pg_temp AS $$
DECLARE
  org uuid := kl.current_org();
BEGIN
  IF org IS NULL THEN
    RAISE EXCEPTION 'no org in this transaction';
  END IF;
  IF team IS NOT NULL AND NOT EXISTS (SELECT 1 FROM workspaces w WHERE w.id = team AND w.org_id = org) THEN
    RAISE EXCEPTION 'that team is not in this org';
  END IF;
  IF session_user = 'kl_ingest' THEN
    RETURN org;
  END IF;
  IF session_user <> 'kl_app' OR team IS NULL OR cardinality(COALESCE(readers, '{}')) > 0
     OR NOT EXISTS (SELECT 1 FROM workspaces w WHERE w.id = team AND w.slug = ANY(kl.current_teams())) THEN
    RAISE EXCEPTION 'not allowed to record for that team';
  END IF;
  RETURN org;
END $$;

CREATE FUNCTION kl.atlas_node(p_kind text, p_namespace text, p_name text, p_tier text, p_source text, p_team uuid,
                              p_readers text[] DEFAULT '{}', p_ref text DEFAULT NULL) RETURNS uuid
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = kl, pg_temp AS $$
DECLARE
  org uuid := kl.atlas_may_record(p_team, p_readers);
  node uuid;
BEGIN
  INSERT INTO atlas_nodes (org_id, kind, namespace, name) VALUES (org, p_kind, p_namespace, p_name)
  ON CONFLICT (org_id, kind, namespace, name) DO UPDATE SET last_seen = now() RETURNING id INTO node;
  INSERT INTO atlas_evidence (org_id, node_id, tier, source, ref, acl_principals, team_id)
  VALUES (org, node, p_tier, p_source, p_ref, COALESCE(p_readers, '{}'), p_team)
  ON CONFLICT (node_id, source, team_id) WHERE node_id IS NOT NULL
  DO UPDATE SET observed_at = now(), tier = EXCLUDED.tier, ref = EXCLUDED.ref, acl_principals = EXCLUDED.acl_principals;
  RETURN node;
END $$;

CREATE FUNCTION kl.atlas_edge(p_src uuid, p_dst uuid, p_kind text, p_tier text, p_source text, p_team uuid,
                              p_readers text[] DEFAULT '{}', p_ref text DEFAULT NULL) RETURNS uuid
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = kl, pg_temp AS $$
DECLARE
  org uuid := kl.atlas_may_record(p_team, p_readers);
  edge uuid;
BEGIN
  IF (SELECT count(*) FROM atlas_nodes WHERE id IN (p_src, p_dst) AND org_id = org) <> 2 THEN
    RAISE EXCEPTION 'an edge needs two nodes of this org';
  END IF;
  INSERT INTO atlas_edges (org_id, src, dst, kind) VALUES (org, p_src, p_dst, p_kind)
  ON CONFLICT (src, dst, kind) DO UPDATE SET last_seen = now() RETURNING id INTO edge;
  INSERT INTO atlas_evidence (org_id, edge_id, tier, source, ref, acl_principals, team_id)
  VALUES (org, edge, p_tier, p_source, p_ref, COALESCE(p_readers, '{}'), p_team)
  ON CONFLICT (edge_id, source, team_id) WHERE edge_id IS NOT NULL
  DO UPDATE SET observed_at = now(), tier = EXCLUDED.tier, ref = EXCLUDED.ref, acl_principals = EXCLUDED.acl_principals;
  RETURN edge;
END $$;
REVOKE ALL ON FUNCTION kl.atlas_may_record(uuid, text[]), kl.atlas_node(text, text, text, text, text, uuid, text[], text),
  kl.atlas_edge(uuid, uuid, text, text, text, uuid, text[], text), kl.atlas_follow_evidence() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kl.atlas_node(text, text, text, text, text, uuid, text[], text),
  kl.atlas_edge(uuid, uuid, text, text, text, uuid, text[], text) TO kl_app, kl_ingest;

-- K3's rows move in: one node per asset however many teams reported it, an evidence row per team.
INSERT INTO atlas_nodes (org_id, kind, namespace, name, first_seen, last_seen)
SELECT org_id, kind, namespace, name, min(first_seen), max(last_seen) FROM assets GROUP BY org_id, kind, namespace, name;
INSERT INTO atlas_evidence (org_id, node_id, tier, source, team_id, first_seen, observed_at)
SELECT a.org_id, n.id, 'observed', 'k3', a.workspace_id, a.first_seen, a.last_seen
FROM assets a JOIN atlas_nodes n ON n.org_id = a.org_id AND n.kind = a.kind AND n.namespace = a.namespace AND n.name = a.name;
INSERT INTO atlas_edges (org_id, src, dst, kind, last_seen)
SELECT l.org_id, ns.id, nt.id,
       CASE WHEN s.kind = 'dataset' AND t.kind = 'job' THEN 'reads' WHEN s.kind = 'job' AND t.kind = 'dataset' THEN 'writes' ELSE 'feeds' END,
       max(l.last_seen)
FROM lineage_edges l
JOIN assets s ON s.id = l.source_id JOIN assets t ON t.id = l.target_id
JOIN atlas_nodes ns ON ns.org_id = s.org_id AND ns.kind = s.kind AND ns.namespace = s.namespace AND ns.name = s.name
JOIN atlas_nodes nt ON nt.org_id = t.org_id AND nt.kind = t.kind AND nt.namespace = t.namespace AND nt.name = t.name
WHERE ns.id <> nt.id
GROUP BY l.org_id, ns.id, nt.id, s.kind, t.kind;
INSERT INTO atlas_evidence (org_id, edge_id, tier, source, team_id, observed_at)
SELECT DISTINCT ON (e.id, l.workspace_id) l.org_id, e.id, 'observed', 'k3', l.workspace_id, l.last_seen
FROM lineage_edges l
JOIN assets s ON s.id = l.source_id JOIN assets t ON t.id = l.target_id
JOIN atlas_nodes ns ON ns.org_id = s.org_id AND ns.kind = s.kind AND ns.namespace = s.namespace AND ns.name = s.name
JOIN atlas_nodes nt ON nt.org_id = t.org_id AND nt.kind = t.kind AND nt.namespace = t.namespace AND nt.name = t.name
JOIN atlas_edges e ON e.src = ns.id AND e.dst = nt.id
ORDER BY e.id, l.workspace_id, l.last_seen DESC;

-- asset_owners now name nodes.
ALTER TABLE asset_owners DROP CONSTRAINT asset_owners_asset_id_fkey;
UPDATE asset_owners o SET asset_id = n.id
FROM assets a JOIN atlas_nodes n ON n.org_id = a.org_id AND n.kind = a.kind AND n.namespace = a.namespace AND n.name = a.name
WHERE o.asset_id = a.id;
ALTER TABLE asset_owners ADD CONSTRAINT asset_owners_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES atlas_nodes(id) ON DELETE CASCADE;

DROP TABLE lineage_edges;
DROP TABLE assets;

-- The old names, for the code that reads them: a row per node and reporting team, as before, under the caller's own
-- row-level security (security_invoker), so they show only what the Atlas shows that caller.
CREATE VIEW assets WITH (security_invoker = true) AS
SELECT n.id, n.org_id, x.team_id AS workspace_id, n.kind, n.namespace, n.name, min(x.first_seen) AS first_seen,
       max(x.observed_at) AS last_seen
FROM atlas_nodes n JOIN atlas_evidence x ON x.node_id = n.id
WHERE x.team_id IS NOT NULL AND n.kind IN ('dataset', 'job')
GROUP BY n.id, n.org_id, x.team_id, n.kind, n.namespace, n.name;
CREATE VIEW lineage_edges WITH (security_invoker = true) AS
SELECT e.org_id, x.team_id AS workspace_id, e.src AS source_id, e.dst AS target_id, max(x.observed_at) AS last_seen
FROM atlas_edges e JOIN atlas_evidence x ON x.edge_id = e.id
WHERE x.team_id IS NOT NULL
GROUP BY e.org_id, x.team_id, e.src, e.dst;
GRANT SELECT ON assets, lineage_edges TO kl_app, kl_ingest;
