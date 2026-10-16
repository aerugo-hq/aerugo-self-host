-- Q2: each chunk is embedded with where it sits: its document's title, its section path, and the document's opening
-- line. context says which way an embedding was made, so the worker re-embeds older ones as it goes.
SET search_path = kl, public;

ALTER TABLE chunk_embeddings ADD COLUMN IF NOT EXISTS context smallint NOT NULL DEFAULT 1;
