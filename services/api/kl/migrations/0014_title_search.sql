-- Slice M1: a document's title and file name are searchable words in their own right, so a file called
-- resume.docx is found by "resume" even when its text never says the word. Generated, so every writer
-- (seed, sync, bundles) gets it for free; indexed, so the title channel stays fast.
SET search_path = kl, public;

ALTER TABLE documents ADD COLUMN title_tsv tsvector GENERATED ALWAYS AS (
  to_tsvector('english'::regconfig,
              coalesce(title, '') || ' ' || coalesce(regexp_replace(source_path, '[/_.-]+', ' ', 'g'), ''))
) STORED;
CREATE INDEX documents_title_tsv_idx ON documents USING gin (title_tsv);

-- Answer-quality runs (make eval, and the smoke gate): the golden set's version, its scores, and each
-- question's top results. Written by the test tooling with owner rights; no app role reads or writes it.
CREATE TABLE eval_runs (
  id             bigserial PRIMARY KEY,
  ran_at         timestamptz NOT NULL DEFAULT now(),
  golden_version text NOT NULL,
  metrics        jsonb NOT NULL,
  passed         boolean NOT NULL,
  details        jsonb NOT NULL
);
