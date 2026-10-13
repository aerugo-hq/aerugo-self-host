-- Slice F2 (ADR 0008): Google Drive connections. Each holds its own OAuth client secret and refresh token in OpenBao
-- (only a reference here), and syncs shared drives with each file's own readers.
SET search_path = kl, public;

ALTER TABLE connections DROP CONSTRAINT connections_kind_check;
ALTER TABLE connections ADD CONSTRAINT connections_kind_check CHECK (kind IN ('github', 'google'));
