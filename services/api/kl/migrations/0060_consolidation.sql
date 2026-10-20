-- F3 (docs/ENTERPRISE_PLAN.md section 5): consolidation. A unit that says what another of its team's says is linked to
-- it (duplicate_of), never combined: each keeps its own evidence, so each keeps its own readers, and lists hide a copy
-- only from someone who can see its original. A contradiction opens a conflict (F1's unit_conflicts, opened by
-- 'consolidation'), shown only to someone who reads both sides. consolidated_at says when the worker last compared a
-- unit; one changed since is compared again. Nothing here widens who reads anything: no unit's evidence changes.
SET search_path = kl, public;

ALTER TABLE knowledge_units ADD COLUMN duplicate_of uuid REFERENCES knowledge_units(id) ON DELETE SET NULL;
ALTER TABLE knowledge_units ADD COLUMN consolidated_at timestamptz;
ALTER TABLE knowledge_units ADD CONSTRAINT knowledge_units_not_its_own_duplicate CHECK (duplicate_of IS DISTINCT FROM id);
CREATE INDEX knowledge_units_duplicate_idx ON knowledge_units (duplicate_of) WHERE duplicate_of IS NOT NULL;
