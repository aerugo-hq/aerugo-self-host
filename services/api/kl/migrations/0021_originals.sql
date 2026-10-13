-- Slice I3b: stored originals (concept versions and proposal drafts) are encrypted per team with OpenBao
-- Transit (kl/originals.py). key_ref names the key a row is encrypted with; a row without one is from before
-- this slice, and the worker encrypts those in batches. A version's hash stays the hash of its plaintext.
SET search_path = kl, public;

ALTER TABLE concept_versions ADD COLUMN key_ref text;
ALTER TABLE proposals ADD COLUMN key_ref text;
CREATE INDEX concept_versions_key_ref_idx ON concept_versions (key_ref);                  -- a purge re-wraps by key
CREATE INDEX concept_versions_plaintext_idx ON concept_versions (concept_id) WHERE key_ref IS NULL;
CREATE INDEX proposals_plaintext_idx ON proposals (id) WHERE key_ref IS NULL;
GRANT UPDATE (content, key_ref) ON concept_versions TO kl_ingest;  -- encrypting older rows, re-wrapping on purge
GRANT UPDATE (content, key_ref) ON proposals TO kl_ingest;
