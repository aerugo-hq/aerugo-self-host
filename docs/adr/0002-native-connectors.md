# ADR 0002: Connectors are built natively, to one contract

**Status:** accepted (slice F)

## Context

Teams need Google Drive, Confluence, Jira, and on-prem databases next. We could build each connector
ourselves, as we built GitHub, or adopt a connector framework. Onyx (formerly Danswer) is the most
complete open-source option, with dozens of connectors.

The deciding requirement is invariant 12: synced content carries its source's permissions, per
document, from day one. Onyx's Community Edition is MIT-licensed, but its permission-syncing connectors
are an Enterprise Edition feature, and that code lives under the Onyx Enterprise License, not MIT
(docs.onyx.app/admins/connectors/overview). Adopting Onyx would mean either a proprietary license we
couldn't ship in an open-source product, or writing permission sync ourselves anyway, on top of a stack
that adds a search index, task queues, Redis, and object storage to every cell.

## Decision

**Connectors are native**, each implementing the contract the GitHub connector established:

- per-document ACLs from the source, with source identities as `ext:<source>.<id>` linked to platform
  users through `principal_aliases`;
- events and webhooks as hints only; jobs re-read the source;
- delta sync with cursors, and deletions propagated to chunks, embeddings, and concepts;
- the same worker, job queue, bronze materialization, and purge path, so purge (slice H) and
  per-team keys cover every connector without per-connector work;
- leak tests in `make smoke` against a sandbox, before a connector ships.

We may study Onyx's MIT-licensed connector code for a source's API quirks (pagination, rate limits,
export formats), with attribution where anything is adapted. We don't use or read its Enterprise code.

## Alternatives considered

- **Onyx with its Enterprise license.** Its permission sync can't ship in our open-source product, and
  every cell would pay for and run its stack.
- **Onyx Community Edition for fetching, our own permission sync.** Permission sync is most of the work
  in a connector; the fetching we'd save is the easy part, and we'd inherit a second ingestion pipeline.
- **Airbyte or similar ELT connectors.** Built to move rows into warehouses; they don't model
  per-document permissions or identities at all.

## Consequences

- Each new source costs a slice of its own. The first one, Google Drive, needs a Google Cloud project
  with a service account and domain-wide delegation (or an admin OAuth client) to read permissions and
  groups, just as GitHub needed an App.
- Connector breadth grows more slowly than with a framework, but every connector we ship mirrors its
  source's permissions, which is the property customers buy.
