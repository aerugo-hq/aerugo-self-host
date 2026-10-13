# ADR 0007: Pipeline identity with CI workload tokens

**Status:** accepted, October 2026 (slice J1)

## Context

Teams publish knowledge from CI: generated API references, release notes, runbooks kept beside code. Pipelines need to push documents into a team with the same guarantees as connectors (every document carries its readers and classification, nothing reaches anyone it shouldn't) and without long-lived keys, which leak, outlive the people who made them, and are hard to rotate.

## Decision

- **Identity is the CI's own workload token.** GitHub Actions (and other OIDC-capable CI) issues each job a short-lived token signed by the CI provider, naming the repository and ref. A pipeline sends it as a bearer token with audience `kl-ingest`. The API checks the signature against the issuer's published keys, the audience, expiry, and issuer. **Keys are fetched only from issuers on `KL_PIPELINE_ISSUERS`** (GitHub's by default), so a token can't make the API contact a server of its choosing.
- **Registration is the grant.** A team's admins register a pipeline: an issuer, a repository, and optionally a ref (such as `refs/heads/main`). A token is accepted for a team only if its repository (and ref) matches one of that team's pipelines; a token from any other repository gets 403. Registrations and removals are audited.
- **Readers come from the team.** Each pushed document names its readers, and each must hold a role on the team: its admins, curators, members, or auditors, as users or groups. A team that admits the whole org (as commons does) can be published to the whole org; no other team can.
- **Pushes go through the connector path.** `POST /v1/workspaces/{ws}/documents` stages upserts and deletes; the worker applies them like a sync (chunks, embeddings, bronze concepts, map links, personal-data scanning, authorization tuples), and clears the pushed text from staging once applied. A pipeline's documents are namespaced to it, so it can replace or delete only its own. Removing a pipeline removes its documents.

## Alternatives

- **An OpenFGA `writer` relation and a service type,** as the build plan first sketched: it would put pipeline grants in the authorization model, but needs a model change, which the invariants ask to avoid where an equivalent guarantee exists. Registration gives the same control (a team's admins decide who writes, per repository and ref) with no model change. If pipelines later need grants beyond one team, the model is the place to revisit.
- **API keys per pipeline:** long-lived secrets to issue, store, rotate, and revoke; exactly what workload identity removes.
- **A pipeline pushing as a person:** ties automation to someone's account and permissions, and breaks when they leave.

## Consequences

- Only GitHub-shaped claims (`repository`, `ref`) are matched today. GitLab and others name these differently (`project_path`, `ref`), so supporting them is a small mapping per issuer.
- The fake model server includes a test issuer for smoke. It runs only under the smoke profile, so the default trust list naming it is harmless in dev, and production lists only real CI issuers.
- A registered repository's every workflow can push unless a ref is set; teams that want only their main branch to publish should register the ref.
