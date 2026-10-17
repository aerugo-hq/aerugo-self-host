-- H6: syncs read what changed since their container's cursor, and everything again once a day (KL_FULL_SYNC_HOURS), when
-- deletions and the permissions of what didn't change are caught. This is when a container was last read in full;
-- NULL (never, or rediscovered since) means its next sync reads everything.
SET search_path = kl, public;

ALTER TABLE connection_containers ADD COLUMN reconciled_at timestamptz;
