-- F1 (docs/ENTERPRISE_PLAN.md section 5): knowledge units, the perspectives others hold on them, and conflicts.
--
-- A unit is one piece of knowledge (a fact, a procedure, a policy, a contact, a decision, a definition, an answer, a
-- lesson) with its scope, owner, trust, status, and validity; versions of one piece share a topic, the newer naming
-- what it superseded. Its text is derived from its evidence, so nothing derived mixes permissions (invariant 16): a
-- unit is readable only by someone who can read every piece of its evidence, unlike the Atlas, where any piece shows a
-- node. Its evidence is kept on its own row, as document ids, so the rule needs no other table's rows: a document that
-- is deleted, or whose readers narrow, hides the unit at once, never widening it to the readers of what's left; and a
-- unit can't exist without evidence. kl.evidence_readable runs as the caller, so the documents' own row-level security
-- decides; the API then post-checks each document in OpenFGA (invariant 1).
--
-- A perspective (another team's, role's, or person's version) follows its own evidence and its unit; a conflict shows
-- only to someone who sees both of its sides, since naming one side reveals the other exists. The API writes only for
-- the team its transaction names (kl.teams, after it checked the caller curates that team in OpenFGA), and only what
-- the caller's own row-level security can read; the worker (F2's extraction) writes within its org.
SET search_path = kl, public;

CREATE FUNCTION kl.evidence_readable(ids uuid[]) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT cardinality(ids) > 0 AND NOT EXISTS (
    SELECT 1 FROM unnest(ids) AS e(id)
    WHERE e.id IS NULL OR NOT EXISTS (SELECT 1 FROM kl.documents d WHERE d.id = e.id))
$$;

CREATE TABLE knowledge_units (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES orgs(id),
  owner_team     uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  owner_role     text CHECK (length(owner_role) BETWEEN 1 AND 120),
  kind           text NOT NULL CHECK (kind IN ('fact', 'procedure', 'policy', 'contact', 'decision', 'definition', 'answer', 'lesson')),
  topic          text NOT NULL CHECK (topic ~ '^[a-z0-9][a-z0-9-]{1,79}$'),  -- what its versions share
  title          text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  body           text NOT NULL CHECK (length(body) BETWEEN 1 AND 20000),
  structure      jsonb NOT NULL DEFAULT '{}',   -- per kind: a procedure's steps, a contact's role, a definition's term
  scope_teams    text[] NOT NULL DEFAULT '{}',  -- who it applies to; empty: everyone (F8 matches people to it)
  scope_roles    text[] NOT NULL DEFAULT '{}',
  scope_places   text[] NOT NULL DEFAULT '{}',
  trust          text NOT NULL CHECK (trust IN ('confirmed', 'documented', 'observed', 'inferred')),
  confidence     real NOT NULL DEFAULT 0.5 CHECK (confidence >= 0 AND confidence <= 1),
  status         text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'confirmed', 'stale', 'superseded', 'retired')),
  valid_from     date,
  valid_until    date CHECK (valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from),
  superseded_by  uuid REFERENCES knowledge_units(id) ON DELETE SET NULL,
  review_days    integer NOT NULL DEFAULT 90 CHECK (review_days BETWEEN 1 AND 1825),
  reviewed_at    timestamptz,
  evidence       uuid[] NOT NULL CHECK (cardinality(evidence) BETWEEN 1 AND 50),  -- documents.id: pointers, never their text
  sections       text[] NOT NULL,               -- where in each piece (its heading), '' for all of it
  created_by     text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  CHECK (cardinality(sections) = cardinality(evidence))
);
CREATE INDEX knowledge_units_topic_idx ON knowledge_units (org_id, topic);
CREATE INDEX knowledge_units_owner_idx ON knowledge_units (org_id, owner_team, status);
CREATE INDEX knowledge_units_evidence_idx ON knowledge_units USING gin (evidence);

CREATE TABLE unit_perspectives (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES orgs(id),
  unit_id        uuid NOT NULL REFERENCES knowledge_units(id) ON DELETE CASCADE,
  holder_kind    text NOT NULL CHECK (holder_kind IN ('team', 'role', 'person')),
  holder         text NOT NULL CHECK (length(holder) BETWEEN 1 AND 120),
  context        text NOT NULL DEFAULT '' CHECK (length(context) <= 500),
  body           text NOT NULL CHECK (length(body) BETWEEN 1 AND 5000),
  evidence       uuid[] NOT NULL CHECK (cardinality(evidence) BETWEEN 1 AND 20),
  sections       text[] NOT NULL,
  created_by     text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  CHECK (cardinality(sections) = cardinality(evidence))
);
CREATE INDEX unit_perspectives_unit_idx ON unit_perspectives (unit_id);

CREATE TABLE unit_conflicts (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES orgs(id),
  unit_id        uuid NOT NULL REFERENCES knowledge_units(id) ON DELETE CASCADE,
  other_unit     uuid REFERENCES knowledge_units(id) ON DELETE CASCADE,   -- the other side: a unit...
  perspective_id uuid REFERENCES unit_perspectives(id) ON DELETE CASCADE, -- ...or a perspective on this one
  note           text NOT NULL DEFAULT '' CHECK (length(note) <= 1000),
  status         text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
  kept           text CHECK (kept IN ('unit', 'other', 'both')),
  resolution     text CHECK (length(resolution) <= 2000),
  opened_by      text NOT NULL,
  resolved_by    text,
  resolved_at    timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  CHECK ((other_unit IS NULL) <> (perspective_id IS NULL)),
  CHECK (other_unit IS DISTINCT FROM unit_id),
  CHECK ((status = 'resolved') = (resolved_at IS NOT NULL AND kept IS NOT NULL))
);
CREATE INDEX unit_conflicts_unit_idx ON unit_conflicts (unit_id);
CREATE INDEX unit_conflicts_other_idx ON unit_conflicts (other_unit);

-- Whether a unit's owner team is one the transaction names (the API names only a team it checked the caller curates).
CREATE FUNCTION kl.unit_in_my_teams(unit uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM kl.knowledge_units u JOIN kl.workspaces w ON w.id = u.owner_team
                 WHERE u.id = unit AND w.slug = ANY(kl.current_teams()))
$$;

ALTER TABLE knowledge_units ENABLE ROW LEVEL SECURITY;
ALTER TABLE unit_perspectives ENABLE ROW LEVEL SECURITY;
ALTER TABLE unit_conflicts ENABLE ROW LEVEL SECURITY;

CREATE POLICY read_if_evidence_readable ON knowledge_units FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND kl.evidence_readable(evidence));
CREATE POLICY write_in_its_team ON knowledge_units FOR INSERT TO kl_app
  WITH CHECK (org_id = kl.current_org() AND kl.evidence_readable(evidence)
              AND EXISTS (SELECT 1 FROM workspaces w WHERE w.id = owner_team AND w.slug = ANY(kl.current_teams())));
CREATE POLICY change_in_its_team ON knowledge_units FOR UPDATE TO kl_app
  USING (org_id = kl.current_org() AND kl.evidence_readable(evidence)
         AND EXISTS (SELECT 1 FROM workspaces w WHERE w.id = owner_team AND w.slug = ANY(kl.current_teams())))
  WITH CHECK (org_id = kl.current_org() AND kl.evidence_readable(evidence)
              AND EXISTS (SELECT 1 FROM workspaces w WHERE w.id = owner_team AND w.slug = ANY(kl.current_teams())));

CREATE POLICY read_if_evidence_readable ON unit_perspectives FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND kl.evidence_readable(evidence)
         AND EXISTS (SELECT 1 FROM knowledge_units u WHERE u.id = unit_id));
CREATE POLICY write_on_its_teams_unit ON unit_perspectives FOR INSERT TO kl_app
  WITH CHECK (org_id = kl.current_org() AND kl.evidence_readable(evidence)
              AND EXISTS (SELECT 1 FROM knowledge_units u WHERE u.id = unit_id) AND kl.unit_in_my_teams(unit_id));

CREATE POLICY read_if_both_sides_visible ON unit_conflicts FOR SELECT TO kl_app
  USING (org_id = kl.current_org() AND EXISTS (SELECT 1 FROM knowledge_units u WHERE u.id = unit_id)
         AND (other_unit IS NULL OR EXISTS (SELECT 1 FROM knowledge_units u WHERE u.id = other_unit))
         AND (perspective_id IS NULL OR EXISTS (SELECT 1 FROM unit_perspectives p WHERE p.id = perspective_id AND p.unit_id = unit_conflicts.unit_id)));
CREATE POLICY open_by_either_side ON unit_conflicts FOR INSERT TO kl_app
  WITH CHECK (org_id = kl.current_org() AND EXISTS (SELECT 1 FROM knowledge_units u WHERE u.id = unit_id)
              AND (other_unit IS NULL OR EXISTS (SELECT 1 FROM knowledge_units u WHERE u.id = other_unit))
              AND (perspective_id IS NULL OR EXISTS (SELECT 1 FROM unit_perspectives p WHERE p.id = perspective_id AND p.unit_id = unit_conflicts.unit_id))
              AND (kl.unit_in_my_teams(unit_id) OR (other_unit IS NOT NULL AND kl.unit_in_my_teams(other_unit))));
CREATE POLICY resolve_by_its_team ON unit_conflicts FOR UPDATE TO kl_app
  USING (org_id = kl.current_org() AND EXISTS (SELECT 1 FROM knowledge_units u WHERE u.id = unit_id)
         AND (other_unit IS NULL OR EXISTS (SELECT 1 FROM knowledge_units u WHERE u.id = other_unit))
         AND (perspective_id IS NULL OR EXISTS (SELECT 1 FROM unit_perspectives p WHERE p.id = perspective_id AND p.unit_id = unit_conflicts.unit_id))
         AND kl.unit_in_my_teams(unit_id))
  WITH CHECK (org_id = kl.current_org() AND kl.unit_in_my_teams(unit_id));

-- Who wrote a unit or perspective (created_by) and who opened a conflict (opened_by) are already columns a data-subject
-- erasure finds by name; who resolved one is added, which also reaches privacy_flags.resolved_by, missed until now.
CREATE OR REPLACE FUNCTION kl.subject_columns() RETURNS TABLE (tbl text, col text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = kl, pg_temp AS $$
  SELECT c.table_name::text, c.column_name::text
  FROM information_schema.columns c
  JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
  WHERE c.table_schema = 'kl' AND t.table_type = 'BASE TABLE' AND c.data_type = 'text'
    AND c.column_name IN ('actor', 'username', 'created_by', 'updated_by', 'requested_by', 'acknowledged_by',
                          'set_by', 'opened_by', 'merged_by', 'granted_by', 'revoked_by', 'resolved_by')
    AND EXISTS (SELECT 1 FROM information_schema.columns o
                WHERE o.table_schema = 'kl' AND o.table_name = c.table_name AND o.column_name = 'org_id')
  ORDER BY 1, 2
$$;

CREATE POLICY ingest_in_org ON knowledge_units FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY ingest_in_org ON unit_perspectives FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());
CREATE POLICY ingest_in_org ON unit_conflicts FOR ALL TO kl_ingest USING (org_id = kl.current_org()) WITH CHECK (org_id = kl.current_org());

GRANT SELECT, INSERT, UPDATE ON knowledge_units, unit_perspectives, unit_conflicts TO kl_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON knowledge_units, unit_perspectives, unit_conflicts TO kl_ingest;
GRANT EXECUTE ON FUNCTION kl.evidence_readable(uuid[]), kl.unit_in_my_teams(uuid) TO kl_app, kl_ingest;
