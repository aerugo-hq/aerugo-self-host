-- F4: answers from knowledge units first. Ask finds the units a question names by their words, so each unit's title and
-- text are indexed for full-text search; the column follows them on every write. Row-level security is the table's own
-- (0058): a search never sees a unit whose evidence the caller can't read.
SET search_path = kl, public;

ALTER TABLE knowledge_units
  ADD COLUMN search tsvector GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || body)) STORED;
CREATE INDEX knowledge_units_search_idx ON knowledge_units USING gin (search);
