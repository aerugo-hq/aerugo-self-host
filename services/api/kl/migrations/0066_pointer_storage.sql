-- F11a (docs/ENTERPRISE_PLAN.md, storage modes): pointer mode, for a company that doesn't want a source's text kept.
-- A connection's `storage` (in its config) is `index`, as before, or `pointer`, for Confluence, Jira, and Slack only.
-- A pointer document keeps its key, address, title, version, readers, and embeddings, never its text: its chunks hold
-- no body and no heading (so no full-text index over them), and its passages are read from the source live, for an
-- asker the source still lists among their readers, and dropped after the answer.
SET search_path = kl, public;

ALTER TABLE chunks ALTER COLUMN body DROP NOT NULL;
ALTER TABLE documents ADD COLUMN storage text NOT NULL DEFAULT 'index' CHECK (storage IN ('index', 'pointer'));
