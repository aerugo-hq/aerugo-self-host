-- Slice I4: restore drills leave evidence. `make restore-drill` writes a marker here, backs the database up,
-- restores the newest backup into a throwaway database, and records whether the copy held the marker, matched the
-- live schema, and opened its encrypted originals with the live keys. This is the cell's own operations record:
-- row-level security is on with no policies and no grants, so only the owner (the drill tooling) reads it.
SET search_path = kl, public;

CREATE TABLE backup_drills (
  id          bigserial PRIMARY KEY,
  marker      text NOT NULL UNIQUE,
  started_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  ok          boolean,
  details     jsonb
);
ALTER TABLE backup_drills ENABLE ROW LEVEL SECURITY;
