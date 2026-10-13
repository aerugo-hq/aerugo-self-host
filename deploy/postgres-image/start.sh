#!/bin/sh
# Postgres with WAL archiving to pgBackRest (slice I4). Runs the image's own entrypoint, then makes sure the backup
# stanza exists, so archiving works from the first segment on; creating a stanza that exists changes nothing.
# Until it exists, Postgres keeps its WAL rather than lose it.
set -eu
docker-entrypoint.sh "$@" &
PG=$!
trap 'kill -TERM "$PG" 2>/dev/null; wait "$PG"; exit $?' TERM INT

tries=0
until gosu postgres pgbackrest --stanza=kl stanza-create >/dev/null 2>&1; do
  if ! kill -0 "$PG" 2>/dev/null; then wait "$PG"; exit $?; fi  # the server stopped (a bad config, say): stop with it
  tries=$((tries + 1))
  if [ "$tries" -ge 120 ]; then
    echo "pgbackrest: no stanza after two minutes; WAL is kept until one exists" >&2
    break
  fi
  sleep 1
done
wait "$PG"
