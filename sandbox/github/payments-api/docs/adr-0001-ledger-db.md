# ADR 0001: Postgres for the ledger

Status: accepted, 2026-03-14

## Context

Settlement needs strict consistency and point-in-time recovery.

## Decision

Use Postgres with synchronous replication for ledger-db.

## Consequences

Failover is manual and gated on replication lag. See the incident runbook.
