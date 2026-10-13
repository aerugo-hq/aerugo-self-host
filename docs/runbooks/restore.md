# Runbook: restore from backup

Use this when the database is lost or damaged, or a mistake has to be undone to a point in time. Backups are pgBackRest (ADR 0006). Practice first: `make restore-drill` restores the newest backup into a throwaway database and checks it, without touching anything live.

## Before you start

1. **See what you can restore to.** `make backups` lists each backup and the WAL archived since; you can restore to any moment between the oldest backup and the last archived WAL.
2. **Make sure the keys are there.** Encrypted originals (concept versions, proposal drafts) open only with the Transit keys in OpenBao, which isn't in the Postgres backup. Restore OpenBao first if it was lost too (below); without its keys, everything else restores and encrypted originals don't open.
3. **Record the incident:** who, when, why, and the target time.

## Restore Postgres

```bash
# 1. Stop everything that writes, then Postgres.
docker compose stop web mcp api worker
docker compose stop postgres

# 2. Restore into the existing data volume. Pick one:
#    the newest state:
docker compose run --rm --no-deps --entrypoint "" postgres \
  gosu postgres pgbackrest --stanza=kl --delta restore
#    or a moment in time (UTC):
docker compose run --rm --no-deps --entrypoint "" postgres \
  gosu postgres pgbackrest --stanza=kl --delta --type=time "--target=2026-10-09 14:00:00+00" --target-action=promote restore

# 3. Start Postgres (it replays WAL to the target), then the rest.
docker compose up -d postgres
make up
make smoke
```

After a point-in-time restore, take a fresh full backup (`make backup`), so later restores don't depend on the timeline you left.

## Restore OpenBao (dev)

The dev OpenBao keeps its data on the `kl-openbao` volume. Back it up whenever you take a Postgres backup you'd want to restore:

```bash
docker run --rm -v kl_kl-openbao:/data -v "$PWD":/backup alpine tar czf /backup/openbao.tgz -C /data .
```

To restore it: stop OpenBao, empty the volume, unpack the archive into it, start OpenBao (it unseals itself from the restored data).

```bash
docker compose stop openbao
docker run --rm -v kl_kl-openbao:/data -v "$PWD":/backup alpine sh -c "rm -rf /data/* && tar xzf /backup/openbao.tgz -C /data"
docker compose up -d openbao
```

The volume name is the Compose project name (`kl`) followed by the volume's. In production, OpenBao is backed up with its own snapshots or a KMS seal (slice E).

## Afterwards

- Check the Alerts page: a restore to an earlier time can leave syncs stale until their next run.
- Note what the restore lost (everything after the target time) and who needs to know.
- If the cause was a defect, add a smoke check or golden question that would have caught it.
