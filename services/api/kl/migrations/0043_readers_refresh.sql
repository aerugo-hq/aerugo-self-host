-- S8: faster permission changes. A readers refresh re-reads only who may read each item, on a shorter cadence than
-- content syncs (and at once when a signed webhook says something changed); this is when it last ran.
SET search_path = kl, public;

ALTER TABLE connections ADD COLUMN IF NOT EXISTS readers_refreshed_at timestamptz;
