# ADR 0006: Backups with pgBackRest

**Status:** accepted, October 2026 (slice I4)

## Context

A cell's Postgres holds everything that isn't in a source system: curated concepts and their history, proposals, the audit trail, permissions mirrors, settings. It needs backups that are encrypted, that recover to a point in time, that cost nothing on a laptop and move to object storage in production unchanged, and that are proven by restoring them, not assumed.

## Decision

**pgBackRest**, installed from the Postgres package repository into the cell's Postgres image (`deploy/postgres-image/`).

- **Continuous archiving:** every finished WAL segment is pushed to the repository (`archive_command`), and `archive_timeout` ships a segment at least every five minutes when anything changed, so a restore loses at most a few minutes of writes.
- **Backups:** `make backup` takes an encrypted backup (full first, incremental after), keeping the last two full backups and everything that depends on them. `make backups` shows what the repository holds.
- **Encryption:** the repository is encrypted (AES-256) with a passphrase from the environment (`KL_BACKUP_CIPHER_PASS`). On top of that, stored originals are already ciphertext under per-team keys (I3b), so a backup of a purged team's content is unreadable even to whoever holds the passphrase.
- **Storage:** a Docker volume (`kl-pgbackrest`) in dev; production points `repo1` at S3, GCS, or Azure Blob with the same configuration.
- **Proof:** `make restore-drill` restores the newest backup into a throwaway Postgres that reads the repository read-only and archives nothing, then checks the copy against the live cell: a marker written just before the backup is present, migrations, tables, row-level security and policies match, the core tables hold rows, and encrypted originals open with the live keys. Each drill's result is kept in `kl.backup_drills`. Smoke separately proves archiving works on every run, by forcing a segment switch and watching it arrive.

## Alternatives

- **WAL-G:** a single binary with similar features, but it's fetched from release downloads rather than a package repository, and its encryption setup is less uniform across storage backends.
- **`pg_basebackup` plus a copy command:** no encryption, retention, or verification of its own.
- **Volume snapshots:** depend on the host, aren't point-in-time, and say nothing about whether the database inside is consistent.

## Consequences

- The cell's Postgres image is built locally (pgvector plus one package) instead of pulled.
- The backup passphrase becomes a secret to keep: lose it and the backups can't be restored. Changing it means starting a new repository.
- The drill restores into a separate database rather than a whole second cell. A full-cell drill (a second stack restored from object storage, then smoke and the leak suites run against it) comes with deployment (slice E).
- Retention in the database (I2) and in the repository are separate: deleted rows stay in backups until the last full backup holding them expires, and per-team keys (I3b) cover what must become unreadable sooner.
