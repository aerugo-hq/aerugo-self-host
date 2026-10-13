#!/usr/bin/env bash
# Runs once, when Postgres initializes an empty data directory (official image convention).
# Applies the SQL in order, passing dev passwords in as psql variables.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_password="$KL_APP_PASSWORD" \
  -v ingest_password="$KL_INGEST_PASSWORD" \
  -v fga_password="$KL_FGA_PASSWORD" \
  -f /docker-entrypoint-initdb.d/sql/01-roles.sql \
  -f /docker-entrypoint-initdb.d/sql/02-schema.sql \
  -f /docker-entrypoint-initdb.d/sql/03-seed.sql
