-- F11b (docs/ENTERPRISE_PLAN.md, storage modes): live only, for Confluence and Jira. A live document keeps its key,
-- address, title, version, and readers: no chunks, no embeddings, nothing read out of it. Search finds it through its
-- source's own search, run as the connection's account and kept to what the asker may read; answers and the reader
-- read it from the source as a pointer's are (F11a).
SET search_path = kl, public;

ALTER TABLE documents DROP CONSTRAINT documents_storage_check;
ALTER TABLE documents ADD CONSTRAINT documents_storage_check CHECK (storage IN ('index', 'pointer', 'live'));
