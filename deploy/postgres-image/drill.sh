#!/bin/sh
# Restore drill (slice I4): restore the newest backup into an empty data directory and serve it, so kl.drill can
# check it. The backup repository is mounted read-only and the restored database archives nothing, so the drill
# can't touch the live database or its backups.
set -eu
DATA=/var/lib/postgresql/drill
mkdir -p "$DATA"
chown postgres:postgres "$DATA"
chmod 700 "$DATA"
gosu postgres pgbackrest --stanza=kl --pg1-path="$DATA" --archive-mode=off restore
exec gosu postgres postgres -D "$DATA" -c listen_addresses='*' -c archive_mode=off
