# Postgres for the knowledge-layer cell: pgvector plus pgBackRest for encrypted backups (slice I4, ADR 0006).
FROM pgvector/pgvector:pg17@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d
RUN apt-get update \
 && apt-get install -y --no-install-recommends pgbackrest \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /var/lib/pgbackrest /var/log/pgbackrest /var/spool/pgbackrest \
 && chown -R postgres:postgres /var/lib/pgbackrest /var/log/pgbackrest /var/spool/pgbackrest
COPY pgbackrest.conf /etc/pgbackrest/pgbackrest.conf
COPY start.sh /usr/local/bin/kl-postgres-start
COPY drill.sh /usr/local/bin/kl-restore-drill
RUN chmod 755 /usr/local/bin/kl-postgres-start /usr/local/bin/kl-restore-drill
ENTRYPOINT ["/usr/local/bin/kl-postgres-start"]
CMD ["postgres"]
