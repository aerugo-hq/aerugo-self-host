# Build plan: from M2 to v1

**Done:**

- M0: stack, security baseline, permission tests
- M1: GitHub connector with mirrored permissions and freshness
- M2: semantic search with embeddings and rank fusion, same permission path
- Slice A: ask, with cited answers, workspace models, and egress policy v0
- Slice B: MCP server with audience-bound tokens and the same permission path
- Slice C1: OKF engine (`kl okf check`), bronze concepts, and the progression timeline
- Slice C2: knowledge repos as curated concepts, the entity graph, curated-first ranking, and knowledge CI
- Slice C3a: the compiler drafts curated concepts as proposals, under egress and the audience rule
- Slice C3b: the curator App opens proposals as PRs when every repo reader may see the sources; merges are indexed with their PR history
- Slices D1–D3: the knowledge map's API, the web app with the map, proposal review and admin
- Slice G: observability under a content rule (ADR 0003)
- Slice H1: team purge with a delay window, verified deletion, and a deletion record
- ADR 0002: connectors are native, to one contract

**Order for the rest:**

| Step | Slice | Why it goes here |
|---|---|---|
| 1 | C2 · Knowledge repos and entity graph (done) | Curated knowledge and the graph are the core of the product |
| 2 | C3 · Compiler (done) | Closes the curated loop: bronze, PR, review, merge |
| 3 | D · Web app (D1–D3 done; D4 later) | People can use the platform without curl |
| 4 | G · Observability (done) | Cheapest to add before more code exists; later slices report into it |
| 5 | H · Team purge and per-team keys (H1 done; keys move to I, beside backups) | Every later connector plugs into purge and keys from its first line |
| 6 | F · Connectors | More sources, each built against the purge and key model |
| 7 | E · Hardening and packaging | Required before any deployment beyond a laptop |
| 8 | I · Compliance operations | Backups, retention, and data-subject requests run on the packaged cell |
| 9 | J · Pipeline ingestion and asset migration | Integrations for teams already on the platform |

D can move ahead of C3 if a clickable demo is needed sooner.

**First, from the audit after H1 (in this order):** S (small fixes), K (teams, members, identity links), L (document formats), M (answer-quality evaluation), N (automatic links on the map), O (cost controls), P (alerts). Then the table resumes at F, with D4 whenever it's wanted, and Q (SaaS control plane) last: it provisions cells with E's Terraform, and it matters only for the managed offering. Each is specified at the end of this file.

**The shortest path to a pilot:** L, M, O, then E's core (an HTTPS deployment on GCP with real secrets auth), I's backups, and the one connector the pilot customer uses most.

**Decided, October 2026:** everything that runs on one machine comes first, at no cost: M2b, O, N, P, I, F, L2, D4, and J, in that order, each verified by `make smoke` before the next starts. Deployment (E) and the SaaS control plane (Q) wait until there's funding for hosting.

**Rules for every slice:**

- All existing `make smoke` checks stay green, and each slice adds its own.
- The invariants in `CLAUDE.md` bind every line.
- Anything that adds a dependency gets an ADR.
- Slices F to J start with a design section in `docs/design/v1.md`, written before any code.

## Slice A · Ask (M4 core)

**Status:** done.

Goal: `POST /v1/ask` returns an answer with numbered citations, using the caller's workspace model.

**Retrieval and context:**

- Reuse the search path, including RLS, the OpenFGA post-check, and audit, to pick the top chunks.
- Build the context with dedup and a token budget.
- Each chunk is a delimited, numbered block marked as untrusted data.

**Model gateway (`kl/gateway.py`):**

- Generalize `providers.py` into chat providers: OpenAI-compatible, Anthropic, and later Vertex and Bedrock.
- Add timeouts and retries, plus a `model_usage` table recording tokens per workspace and model.
- Logs are redacted and never contain prompts.

**Egress policy v0:** a chunk is dropped before the prompt unless its classification is in the endpoint's `allowed_classifications`. Drops are audited.

**Answer contract:**

- Fields: `answer`, `citations[]` (id, title, source, section, commit), and a corpus manifest (source versions used).
- If nothing relevant survives, answer "I couldn't find that in sources you can access". Never answer from the model alone.

**Acceptance:**

- With any model configured (Ollama is fine), alice asks "who do I page when payments are down?" and the answer cites the payments runbook.
- bob gets no citations.
- A test-only echo model, added to Compose under a test profile, records prompts, and a confidential chunk never reaches an endpoint that doesn't allow confidential content.

## Slice B · MCP server (M5, part 1)

**Status:** done.

Goal: Claude Desktop and other MCP clients can search and ask with the user's own permissions.

**Service:** an `mcp` service using the MCP Python SDK over streamable HTTP. Tools are `search` and `ask`; `get_concept` comes in C2, and `kl://<workspace>/<id>` resources come later.

**Auth:**

- The MCP server is an OAuth resource server and publishes protected-resource metadata.
- Keycloak is the dev authorization server, with tokens audience-bound to `kl-mcp`.
- The server never passes a client's token on, as the MCP spec requires. It validates its own `kl-mcp` tokens and runs the API's permission path in-process. There is no shared service key.
- Tools are read-only.

**Acceptance:**

- An MCP client with alice's token gets her results; with bob's, none of hers.
- No token gets a 401 whose `WWW-Authenticate` points to the metadata.

## Slice C1 · OKF engine and bronze (M3, part 1)

**Status:** done.

- `kl/okf.py` parses, renders, and validates OKF v0.2 plus the `x-kl` profile: links, relations, footnotes keyed to sources, and the audience rule. Unknown keys survive a round-trip.
- `python -m kl.okf check <bundle>` runs the validator over a bundle.
- Each document becomes a bronze concept in `concept_versions`, hash-addressed and pinned to its source version. Readers mirror the source and change in the same transaction as the document.
- `GET /v1/concepts/{key}` and `/history` go through scope, RLS, and the OpenFGA check.

## Slice C2 · Knowledge repos and entity graph (M3, part 2)

**Status:** done. Knowledge CI runs the validator inside the bundle repo. Looking up source audiences from the cell over GitHub Actions OIDC needs a public cell endpoint, so that part moved to slice E.

Goal: curated, reviewed knowledge in Git that ranks first, cites a commit, and links to related concepts.

**Indexing:**

- `<workspace>-knowledge` repos are read through the existing read-only App and indexed as OKF bundles (silver and gold).
- A concept's readers are its `x-kl.audience` intersected with the repo's GitHub readers, so the narrower rule always wins.
- A merge to the default branch triggers re-indexing: the webhook is a hint, and the job re-reads the repo.

**Entity graph:**

- `x-kl.relations` become typed edges in a `concept_edges` table, with org isolation and RLS from its first migration.
- Search and ask follow edges one hop from the top hits, and only to concepts the caller can see. A hidden concept is never traversed through, so an edge can't reveal that it exists.
- `GET /v1/concepts/{key}/related` returns the visible neighbors.

**Ranking:** a canonical lookup strategy (concept id or alias, exact match) ranks first in search and ask. Graph neighbors join rank fusion as a third channel.

**Knowledge CI:**

- A GitHub Action runs `kl okf check` on every PR, with the audience floor computed from each concept's sources, plus checks for secrets and PII.
- It authenticates to the cell with GitHub Actions OIDC to look up source audiences.

**MCP:** a `get_concept` tool.

**Acceptance:**

- A merged concept is answerable with a SHA-pinned citation and ranks above the bronze documents it summarizes.
- A PR that widens an audience fails CI.
- A cross-team question reaches a related concept through an edge. A user who can't see that concept never gets it or learns it exists.

## Slice C3 · Compiler (M3, part 3)

**Status:** done. C3a: `POST /v1/workspaces/{ws}/compile` has the workspace's own model draft a concept, validated in knowledge mode and stored as a proposal readable by its audience. The model writes only type, title, and body; the platform writes sources, audience, classification, and provenance. A draft's audience is the org or a group on every source's reader list. C3b: `POST /v1/proposals/{id}/pr` has the curator App open the proposal as a PR, but only if every reader of the knowledge repo is linked to someone who can read every source, because GitHub shows them the PR. PR state is re-read from GitHub every minute, and a merge triggers a sync, so the concept's history runs from PR to review to merge to indexed.

Goal: the workspace model drafts silver concepts from bronze, and people approve them as PRs.

**Write access:**

- A second GitHub App ("curator") with `contents:write` and `pull_requests:write`, installed only on `*-knowledge` repos.
- The read-only connector App stays read-only.

**Compiler job:**

- The workspace model drafts silver hubs from bronze concepts, under the same egress policy as ask, and opens a PR to `<workspace>-knowledge`.
- `x-kl.audience` is the narrowest audience among the sources, and `generated` records the model.
- PR events (opened, reviewed, merged, closed) are logged in `concept_events`.

**Acceptance:**

- Compiling opens a PR that passes knowledge CI.
- After merge, `GET /v1/concepts/{id}/history` shows source → bronze → PR → review → merge → indexed.
- Confidential bronze never reaches a compiler model that may not receive it.

## Slice D · Web app (M5, part 2)

**Status:** in three parts. D1 (built): `GET /v1/graph`, the knowledge map's data, through the same permission path as search, with leak tests in `make smoke`. D2 (built): the web app (`make web`, http://localhost:3000), a backend-for-frontend that signs users in with PKCE and keeps tokens server-side (ADR 0001), with search and ask, the knowledge map drawn from D1 with replay, and concept pages. D3 (built): proposal review (draft from a topic, read the draft beside its sources, open the PR, follow its timeline), admin pages for models, connections, and the audit trail, and two read APIs behind them: `GET /v1/workspaces/{ws}/models` for members (no credentials, not even references) and `GET /v1/audit` for a workspace's admins (only that workspace's events; 404 for private teams you're not in). D4 (in part, done): a light design system (Geist and JetBrains Mono, borders and tint instead of shadows, indigo only for primary actions and focus) and a redesign of every view, with a Linear-style shell, dense tables, status dots, skeletons, one-line empty states, and a mobile reflow down to 320px. D4b (built): screens for everything that had only an API. A Compliance page for org admins (retention policies with recent runs and "run now", legal holds, exporting or erasing a person's data with the username typed twice, the access review with a download, rate limits, and the reranker); on the Admin page, personal-data flags to reclassify or dismiss, each repo's team in Connections (only teams you administer are offered, as the API requires), and each model's tokens this month against its budget, which org admins can change. D4 (later): a richer, more featureful UI pass. **D5 (built):** the Playwright suite (`make e2e`, in `services/e2e`), meeting the acceptance criteria below: Chromium signs in through Keycloak's own login form, as people do, and checks that alice finds her team's runbook while bob never sees it (found by document id, since a synced copy can share its title), that non-admins get no Compliance page, that alice imports a Notion export through the Admin page and then finds it, that olivia sets up and removes Google sign-in, and that Connected accounts offers linking. The tests run in a container that forwards localhost:3000 and :8081 to the web app and Keycloak, so sign-in redirects match the configured addresses. D5 also added the last API-only screens (Imports on the Admin page; Google sign-in on the Compliance page) and taught the web proxy to pass file uploads through byte for byte. Carried into D4 from the map's design: gap overlays (stale concepts, evidence no curated concept covers yet, concepts with no relations), ownership edges from `x-kl.owners`, a focused neighborhood view for one concept, and `cited` and `stale` events in each concept's history.

Goal: people use the platform without curl.

**App:** Next.js with Keycloak login (authorization code + PKCE). It covers:

- chat (ask) and search;
- the knowledge map (below), as the home for exploring the org's knowledge;
- concept pages with the progression timeline and related concepts;
- proposals to review, with their sources side by side;
- connections (installations, connect, refresh, status);
- the model registry;
- the audit viewer, for auditors only.

Navigation is rendered by role. The browser never holds secrets.

**Knowledge map:** the org's knowledge as one living graph.

- **Nodes:** documents and concepts, colored by tier (evidence, bronze, silver, gold) and grouped into team clusters, with the viewer at the center of their own teams.
- **Edges:** relations, derivations (a concept to the sources it was built from), and ownership. Cross-team edges stand out, because that's where shared knowledge lives.
- **Views:** the whole org, one team, or one concept's neighborhood, filtered by team, tier, type, and source. Clicking a node opens its concept page.
- **Replay:** a time slider replays growth from `concept_events` and sync history, beside a live feed of what was synced, indexed, proposed, and merged, with running totals.
- **Gaps:** overlays for stale concepts, evidence no curated concept covers yet, and concepts with no relations.
- **Permissions:** served by `GET /v1/graph` through the same path as search: scope, RLS, the OpenFGA check, and the audience rule. Hidden nodes aren't drawn at all, not even as anonymous dots; edges to them are dropped; private workspaces appear only to their members; and every total counts visible nodes only, so the map never reveals how much a viewer can't see. Access lists aren't graph data, so the map never shows who can read what.
- **Scale:** WebGL rendering for thousands of nodes. Large orgs load team clusters first and expand on demand. The rendering library gets an ADR.

**Acceptance:** a Playwright smoke test where alice logs in, asks, and sees citations; bob sees fewer results; admin pages are hidden from non-admins. For the map: alice's graph holds exactly the nodes her search can reach and its totals match; bob's has no payments nodes and no edges into them; only dave sees the legal cluster; a replay at a past date shows only what existed then.

## Slice E · Hardening and packaging (M6)

**Credentials:**

- OpenBao moves to AppRole or Kubernetes auth.
- The Keycloak password grant is turned off; tests use a test-only token path.
- TLS everywhere, including a public HTTPS endpoint and a registered OAuth client, so Claude Desktop can connect to the MCP server.
- Knowledge CI looks up each concept's source audiences from the cell over GitHub Actions OIDC (moved from C2), so it can fail a PR that widens an audience beyond its sources.
- A shared session store for the web app (Redis or Postgres), so it can run as more than one replica (ADR 0001).
- Sizing, retention, and access control for the observability stack in production cells (ADR 0003).

**Audit:** a hash-chained audit log, plus SIEM export over OTel, syslog, or webhook.

**Identity and packaging:**

- SCIM 2.0 provisioning.
- A Helm chart.
- A Terraform GCP reference cell: Cloud Run or GKE Autopilot, Cloud SQL, Secret Manager, Vertex.
- A cell-provisioning script for the SaaS.
- A hardening guide and a shared-responsibility matrix.

**Freshness:** close the known gap in `docs/security/threat-model.md`: ACL freshness enforcement for restricted content. Retiring connections moved to slice H, and the Google Drive connector to slice F.

**Acceptance:** a fresh GCP cell provisions from Terraform and passes the full smoke and leak suites.

## Slice F · Connectors

**Status:** ADR 0002 accepted: connectors are native, to one contract, because Onyx's permission sync is Enterprise-licensed. **F1 built:** each repo of a connection can belong to its own team. `PUT /v1/workspaces/{ws}/connections/{name}/routes` sends one repo's documents to another team, or back; the caller must administer both teams, since putting content in a team takes that team's say. The route lives in the connection's settings, so it survives re-discovery, and the next sync (queued at once) moves the repo's documents, plain or curated, out of the teams this connection placed them in before, leaving any other connection's placement of the same documents alone. Who may read them still comes from GitHub. **F2 built** (ADR 0008): Google Drive. A team's admins connect an account that can read the shared drives to index (an OAuth client and refresh token, kept in OpenBao and destroyed with the team); each drive is a container that can be routed to its own team, Docs, Sheets, and Slides are exported as Word, Excel, and PowerPoint for the existing extractors, and other files are downloaded as they are. Each file's own permissions decide its readers and fail closed: a person it's shared with is `ext:google.<email>` until they link their Google identity, the org's own domain is the whole org, and groups and "anyone with the link" grant nothing; a file with no reader isn't indexed, and deleted or unshared files are removed with everything derived from them. Connections may use only the addresses on `KL_GOOGLE_API_BASES`. **F2b built** (the linking part): people link their Google identity by signing in to Google from Connected accounts, once an org admin sets the org's sign-in client (`PUT /v1/identities/google/client`, kept in OpenBao). Google's ID token is verified against Google's published keys, for this org's client, and only a verified email links; sign-in states are single-use and bound to their user and to their provider (GitHub's completion now checks its provider too). Still to do: group members through the Directory API, and Drive's changes API for incremental sync.

Goal: the sources teams actually use, each with the same permission guarantees as GitHub.

**Decide first (ADR 0002):** build connectors natively, as GitHub was built, or adopt a framework such as Onyx. The ADR checks whether the framework's permission sync meets invariants 4, 12, and 13, under what license, and what it adds to every cell.

**Connector contract,** however it's built:

- per-document ACLs from the source, with source identities as `ext:<source>.<id>` linked through `principal_aliases`;
- events and webhooks as hints only; jobs re-read the source;
- delta sync with cursors, and deletions propagated to chunks, embeddings, and concepts;
- wired into purge and per-team keys (slice H) from its first line;
- its own leak tests in `make smoke`, against a sandbox like `sandbox/github`.

**In order:**

1. Google Drive (moved from E): per-file permissions, groups through the Admin SDK, shared drives.
2. Confluence: space permissions and page restrictions.
3. Jira: project permissions and issue-level security.
4. On-prem databases: schemas, tables, columns, comments, and view definitions (catalog metadata, not row data), readable by holders of the matching grants. For SaaS cells, a relay agent runs inside the customer's network and connects outbound only, so no inbound firewall rule is needed and database credentials stay on the customer's side.

**Acceptance, per connector:** a synced document is visible only to its source readers; revoking access in the source removes it after the next sync; deleting it in the source removes everything derived from it.

## Slice G · Observability

**Status:** built (ADR 0003). `make obs` turns telemetry on and starts the collector, Prometheus, Jaeger, and Grafana with a provisioned dashboard. Instrumentation is by hand, under a content rule: route templates, ids, counts, and durations only. Three smoke checks, run while `make obs` is on, prove that one ask is one trace with every stage, that drift and egress decisions are exported as metrics, and that no document text, question, answer, or token reaches the export.

Goal: operators see latency, freshness, and policy decisions as they happen, and no content leaves the cell.

**Instrumentation:** OpenTelemetry traces and metrics from `api`, `worker`, and `mcp`. An ask traces retrieval, the post-check, egress, and the model call as separate spans.

**Stack:** an OTel Collector, plus metrics, traces, and dashboards in Compose under an `observability` profile. An ADR picks the backends. Dashboards are provisioned as code.

**Signals:**

- request latency per endpoint and stage;
- sync lag per connection, job queue depth, and job failures;
- ACL drift drops, egress withholds, and rejected webhooks;
- model tokens per workspace and endpoint.

**Content rule:** telemetry holds ids and counts only, never document text, prompts, or tokens.

**Acceptance:** after a smoke run, the drift counter shows the seeded drift; an ask's trace has all its stages; a canary string from the seed never appears in exported telemetry.

## Slice H · Team purge and per-team keys

**Status:** H1 built. `POST /v1/workspaces/{ws}/purge` (admins, slug typed again) schedules a purge after a delay window (`KL_PURGE_DELAY_SECONDS`, an hour by default); `DELETE` cancels it; `GET /v1/purges/{id}` reports it to the requester. The worker deletes authorization first, then secrets with every version, then rows, through two owner-privileged functions confined to its org, verifies nothing derived remains, and writes a deletion record. Per-team keys move to slice I: they protect copies outside the live database, which is what backups create, and encryption happens where content is stored, so connectors need nothing extra for it.

Goal: a team can have all its information removed, verifiably.

**Purge:** an org admin can remove a workspace, after a confirmation and a delay window, or retire a single connection (moved from E). The job removes:

- its connections and their documents, chunks, embeddings, concepts, versions, events, and proposals (drafts hold text derived from their sources); a document shared with another workspace is unlinked, not deleted;
- its model endpoints and every secret it owns in OpenBao;
- its OpenFGA tuples;
- its knowledge repo, archived or deleted through the curator App, as the admin chooses.

**Per-team keys:** stored originals (bronze content, and raw content once there's an object store) are encrypted with a per-workspace data key from OpenBao Transit. Destroying the key makes every copy unreadable, including copies in backups.

**What keys can't cover:** search indexes (chunk text, full-text vectors, embeddings) can't be encrypted per team without losing search. Purge hard-deletes them and verifies none remain. Backup copies expire with the backup retention window from slice I, and the docs say so plainly.

**Proof:** a deletion record in the audit log with counts per table and the destroyed key's id. The audit log keeps ids and actions, never content.

**Acceptance:** purging a test workspace leaves zero rows of its content in any table, removes its tuples and secrets, keeps shared documents visible elsewhere, and makes its bronze content unreadable from a backup taken before the purge.

## Slice I · Compliance operations

**Status:** built, in five parts. **I1 built:** data-subject requests and access reviews (migration 0019). `GET /v1/me/export` (anyone, for themselves) and `GET /v1/users/{name}/export` (org admins) return a person's teams and roles, identity links, model usage, audit trail, and counts of the records attributed to them, with everyone else redacted; `POST /v1/users/{name}/erase` (org admins, confirmed by repeating the name) removes their authorization and identity links and replaces their name everywhere with an irreversible pseudonym, keeping their audit events but without their own words, and refuses to commit unless nothing still names them. Attribution columns are found by name at run time, so new tables are covered. `GET /v1/access-review` lists each team's admins and members (groups expanded), documents by classification, org admins, and linked identities. **I2 built:** retention and legal holds (migration 0020). Org admins set how many days to keep documents (per team, and per classification), concept versions, audit events (at least 30), and model usage; no policy keeps records forever, and the most specific policy wins. A document is removed only when every team holding it agrees it has expired, measured from when it was last seen (created or re-synced, so a document its source still has never expires), and its authorization tuples go first; a concept's current version is never removed. Legal holds on the org or a team keep everything they cover, and a held team can't be purged. The worker runs retention hourly and records each run's counts as evidence. **I3a built:** OpenBao runs as a persistent dev server (`deploy/openbao/`): file storage on the `kl-openbao` volume, initialized once and unsealed on every start, with the services' fixed dev token and the KV and Transit engines set up by its start script, and a healthcheck the API and provision wait on. Secrets and encryption keys now survive restarts, and the vault client can create, use, and destroy Transit keys. **I3b built:** stored originals, concept versions and proposal drafts, are encrypted with OpenBao Transit (`kl/originals.py`, migration 0021): under their team's key, or the org's key for a document several teams hold, with the key's name beside each ciphertext. Every write seals and every read opens (the map in one batch per key); rows from before I3b are encrypted by the worker, up to 2,000 a tick. Purging a team re-wraps its surviving originals (a shared document's history) under the org key, then destroys the team's key, so a copy taken before the purge can't be read after it. Search indexes stay plaintext so search works; backups holding them expire with retention. **I4 built:** backups with pgBackRest (ADR 0006): the cell's Postgres image (`deploy/postgres-image/`) archives every WAL segment to an encrypted repository (the `kl-pgbackrest` volume in dev, object storage in production), `make backup` and `make backups` take and list backups, and `make restore-drill` restores the newest backup into a throwaway database and checks it against the live cell (a marker from just before the backup, migrations, tables, row-level security, policies, core rows, and encrypted originals opening with the live keys), keeping each result in `kl.backup_drills`. Smoke proves archiving works on every run. **I5 built:** personal-data detection (`kl/privacy.py`, migration 0023): the worker flags documents that look like they hold personal data (a résumé, an ID or card number, a date of birth, HR material) with a suggested classification, recording which kinds and how many, never the values; a team's admins review flags they can see (`GET /v1/personal-data`) and reclassify (stricter at once, and held as a floor through later re-indexing) or dismiss. The control map (`docs/compliance/control-map.md`) maps SOC 2, ISO 27001, GDPR, and HIPAA controls to features, evidence, and smoke checks, with its gaps; runbooks (`docs/runbooks/`) cover restores (OpenBao included), data-subject requests, retention and legal holds, and alerts and incidents.

Goal: the evidence SOC 2, HIPAA, GDPR, and ISO 27001 reviewers ask for, running in every cell.

**Backups:** Postgres base backups plus WAL archiving to object storage, encrypted. An ADR picks the tool. A restore drill runs in CI: restore into a fresh cell, then run the full smoke and leak suites.

**Per-team keys (moved from H):** stored originals (concept versions and proposal drafts) are encrypted with a per-workspace key from OpenBao Transit, so a backup holds ciphertext, and a purge that destroys the team's key makes every backed-up copy unreadable. Search indexes stay plaintext so search works; backups holding them expire with the retention window.

**Retention:** policies per workspace and classification for documents, concept versions, audit events, and model usage, with legal holds as the exception.

**Data-subject requests:**

- export of everything tied to a user: identity links, their audit trail, and model usage;
- erasure of user-scoped data, with audit entries pseudonymized rather than deleted.

**Evidence:** access reviews (who can see what, per workspace), a control map in `docs/compliance/`, and runbooks for incidents and key rotation.

**Acceptance:** a restored cell passes smoke; an expired test document disappears from every table; alice's export contains her data and nobody else's.

## Slice J · Pipeline ingestion and asset migration

**Status:** J1 built (ADR 0007): CI pipelines push documents with their workload tokens. A team's admins register a pipeline (an issuer from `KL_PIPELINE_ISSUERS`, a repository, and optionally a ref); `POST /v1/workspaces/{ws}/documents` accepts upserts and deletes from a token that trusted issuer signed for audience `kl-ingest` and whose repository (and ref) matches, and refuses any other repository with 403. Every document names readers who must each hold a role on the team, so only a team that admits the whole org can be published to it, and a classification. The worker applies a push through the connector path and clears its text from staging; documents are namespaced to their pipeline, a pushed delete removes everything derived, and removing a pipeline removes its documents. Registration takes the place of the `writer` relation sketched below, with the same guarantee and no change to the authorization model. **J2 built:** importing Confluence and Notion exports. A team's admins upload a Confluence HTML space export or a Notion Markdown & CSV export (a zip, up to 50 MB) to `POST /v1/workspaces/{ws}/imports`, naming the import, its readers (each must hold a role on the team: an export carries no permissions to trust), and its classification. The worker reads the archive in memory within limits on size and file count (a zip bomb is refused before anything decompresses), turns each page into a document through the connector path (Confluence pages keep only their main content; Notion databases become tables), and clears the upload from staging. Pages are identified by the ids in the export's file names, so a newer export under the same name updates pages in place and removes those no longer in it; removing an import removes its pages. Moving knowledge between installations, or a team between orgs, is still to scope.

Goal: CI/CD pipelines push content into workspaces with the same guarantees as connectors.

**Push API:** `POST /v1/workspaces/{ws}/documents` for batch upserts and deletes, idempotent by external key and content hash.

**Pipeline identity:** CI workload tokens (GitHub Actions or GitLab OIDC) with audience `kl-ingest`, mapped to a `svc:` principal that holds `writer` on the workspace in OpenFGA. There are no long-lived keys.

**Permissions:** every pushed document carries an ACL and a classification. A pipeline can grant only the principals its workspace allows, so it can't publish to the whole org unless the workspace permits that. Pushed content goes through the same bronze and embedding path.

**Asset migration:** not defined yet. To scope with Rama before design: moving OKF bundles between cells (self-hosted and SaaS), moving a workspace between orgs, and importing existing knowledge bases.

**Acceptance:** a sandbox workflow pushes a document with its OIDC token, and only the document's readers see it; a token from another repo is refused; a pushed delete removes the document and everything derived from it.

## Slice S · Small fixes

**Status:** built. `make lock` writes the lockfiles; commit them.

- The model registry accepts only providers the gateway speaks (OpenAI-compatible and Anthropic). Vertex, Bedrock, and Azure work today through an OpenAI-compatible gateway such as LiteLLM.
- Models can be updated, have their key rotated (old secret versions destroyed), and be removed (secret destroyed, usage history kept).
- `make lock` writes hash-pinned lockfiles for Python and npm, and images install from them when present.
- The production image carries no test tooling: smoke and the fake model build from a separate `test` stage.
- The map stays complete past OpenFGA's list limit: above it, visibility comes from batched checks.
- The threat model covers MCP, the web app, the curator's write access, purge, and telemetry.

## Slice K · Teams, members, and identity links

**Status:** K1 built: `POST /v1/workspaces` (org admins), `PATCH /v1/workspaces/{ws}` (rename, private or org-wide), and `/members` (`GET`, `PUT`, `DELETE`; users or IdP groups as member, curator, admin, or auditor), with a Team card and New team form in Admin. The dev org admin is `olivia`. K2 built: identity links proven by signing in to GitHub (`/v1/identities/github/start` and `/complete`; a single-use, user-bound, 10-minute state checked before any call to GitHub; GitHub's token used once and revoked), listed and revoked by their owner or an org admin (`GET` and `DELETE /v1/identities`), with a Connected accounts page. Email mapping from the IdP comes with SCIM in E.

Goal: an org runs itself, without SQL.

- **Teams:** org admins create teams, set them private or org-wide, and rename them; team admins add and remove members and admins (users or IdP groups). All of it goes through OpenFGA and is audited.
- **Identity links:** people link a source identity by proving it (GitHub OAuth first), or the IdP maps it from a verified email. Admins see and revoke links. Links decide what synced content someone sees, so each is audited and revocable, and a revoked link stops working on the next request.
- **UI:** team settings, and a connected-accounts page.
- **Acceptance:** a team created through the API works end to end; an added member sees the team's content and a removed one stops seeing it on the next request; a user who links GitHub sees synced docs; nobody can claim an account without proving it.

## Slice L · Document formats

**Status:** built. `kl/extract.py` turns Markdown, HTML, Word, PowerPoint (with speaker notes), and PDF (a section per page) into titled sections; Google Docs come through as Word exports. GitHub sync indexes every supported format (512 KB for text, 10 MB for Office and PDF); a changed file that's become unreadable keeps its last good version with fresh readers, a never-readable one is skipped, and both are reported in the sync result. Knowledge repos stay Markdown-only. OCR for scanned PDFs is later.

Goal: content beyond Markdown, with its structure.

- PDF, Word, PowerPoint, HTML, and Google Docs exports become sections with headings, tables kept as text. Scanned PDFs are flagged (OCR later).
- One extraction path shared by every connector; a file that can't be read is reported, never silently indexed as empty.
- **Acceptance:** a fixture of each format indexes with its headings and is searchable; a malformed file fails visibly.

## Slice L2 · More document types

**Status:** built (L2a and L2b). Spreadsheets (XLSX and CSV, with the delimiter detected) become sections of 50 rows that each repeat the header row, so a match deep in a sheet still says what its columns mean; past 5,000 rows a sheet is indexed in part and says so. OpenDocument text, spreadsheets, and presentations keep their headings (nested as Word's are), tables (inline in a document, in sections of their own in a spreadsheet), and slides. Email (EML) keeps its subject, sender, recipients, date, and text or HTML body, and names its attachments without opening them. Plain text is read as it is. All of it uses the standard library alone (`kl/extract.py`), so no new dependencies; zip bombs, damaged archives, and empty files are refused with a reason like every other format, and GitHub sync picks the new types up from repos. **L2b built:** scanned PDFs (up to 50 pages, with the rest noted) and pictures of text (PNG, JPEG, TIFF) are read with OCR: Poppler renders pages and Tesseract reads them, both in the API image, each call with a timeout. An image with fewer than five words of text (a logo, a diagram) isn't indexed, one over 50 megapixels isn't opened, and a scan OCR finds nothing in is still refused with a reason. RTF becomes plain text with a small converter. Outlook MSG, code files, and audio and video transcripts stay out of scope until someone needs them.

Goal: the rest of what companies keep. Candidates: spreadsheets (XLSX, CSV) with sheets and headers kept; OpenDocument (ODT, ODS, ODP); RTF and plain text; email (EML, MSG) with threads; code and config files; images and scanned PDFs through OCR; audio and video through transcripts. Each plugs into `kl/extract.py`, with the same rule: unreadable files fail loudly.

## Slice M · Answer-quality evaluation

**Status:** M1 built. Search gains a title channel (a document's title and file name, generated and indexed as `documents.title_tsv`), fused at 1.5× weight, so a file named for the query beats a passing mention elsewhere. `kl/golden.yaml` holds questions per user with their right answers and must-not documents; `make eval` and a smoke gate score hit@1, MRR, and found@5 against its thresholds and store each run in `kl.eval_runs`. M2a built: one reranker per org over the `/rerank` API (ADR 0004), reading only post-checked passages its egress list allows, with withheld passages keeping their place and fusion's order standing if it fails. `make eval` scores retrieval with and without it, and, given `KL_EVAL_ASK_MODEL` and a judge (`KL_EVAL_JUDGE_*`), grades answers on citing a right source, citing nothing invented or forbidden, faithfulness to the cited passages, and key facts. A reversed ranking fails the gate. M2b built: feedback in the app (a search result marked right or wrong, "none of these answered it", an answer rated helpful or not) goes to the audit log through `POST /v1/feedback`, which names only documents the caller can see and answers 404 for anything else; `make eval-mine` turns it into candidate golden cases for a person to review, and lists questions nothing answered as content gaps. Slice M is complete.

Goal: measure whether answers are right, not just permitted.

- A golden set: questions with expected sources and key facts, versioned with the code.
- `make eval`: retrieval recall and rank of the right source, citation correctness, and, with a real model, answer faithfulness graded against the cited sources.
- Results are stored per run, and a regression past a threshold fails CI.
- **Acceptance:** the seed golden set passes its thresholds, and deliberately breaking ranking fails the run.

## Slice N · Automatic links on the map

**Status:** built (`kl/links.py`, migration 0017). After every indexing pass (backfill, sync, seed) the worker computes two kinds of links. Semantic neighbors: each document's vector is the mean of its chunks' vectors per embedding model, and two documents are linked as mutual nearest neighbors (each in the other's top 3) at cosine similarity 0.6 or more; a curated relation between the same pair wins on the map. Entities: systems, teams, channels, and people found in the text by conservative patterns (code spans, service-style names, "<name> API", "<name> team", #channels, @handles, "Owner: <name>"), with no model reading anything. RLS follows the documents: an edge is readable only when both documents are, a mention only when its document is, an entity only when one of its mentions is, and an orphaned entity is deleted with its last mention. The map shows an entity once `min_mentions` visible documents name it (2 by default). Neighbors are computed by brute force, fine to a few thousand documents; past that, an approximate index per model is the next step.

Goal: the map shows how knowledge connects without anyone curating it.

- Semantic-neighbor edges between documents whose embeddings are close, stored with their score.
- Entities (people, systems, services, teams) extracted from content as nodes, with mention edges.
- Visibility rules hold: an edge appears only when both ends are visible, and an entity mentioned only in hidden documents doesn't appear at all.
- **Acceptance:** the seed map gains semantic edges and entities; bob's map never shows an entity that only payments documents mention.

## Slice O · Cost controls

**Status:** built. Budgets: an org admin caps a team's model at tokens per calendar month (UTC) with `PUT /v1/workspaces/{ws}/models/{name}/budget`; a team admin can't raise it. Usage is summed from `model_usage`; past 80% the team's admins get a `budget.warning` in their audit trail, and a spent model refuses ask and compile with 429 and `Retry-After` (until the 1st), auditing `budget.exhausted`. The models list shows each model's budget and this month's use. Rate limits: one-minute windows counted in Postgres (`rate_counters`, one atomic upsert per request, in its own transaction so no lock is held through a model call), per person on every route, per team for ask and compile, and per org for search. The MCP server calls the same handlers, so it shares them. Defaults (search 120/3,000, ask 30/300, compile 10/60 a minute) are overridden by org admins through `/v1/limits/{route}`; anyone signed in can read them at `GET /v1/limits`. Refusals are counted as `kl.ratelimit.rejected` and `kl.budget.refused`. Budget and limit settings in the Admin screens come with D4.

Goal: no team can run up a bill or exhaust the cell.

- Per-team monthly token budgets per model, with a warning and a hard stop, shown in admin.
- Rate limits per user and per team on search, ask, compile, and the MCP tools, answered with 429 and Retry-After.
- **Acceptance:** a team over budget gets a clear refusal and its admins see why; a burst past the limit gets 429.

## Slice P · Alerts

**Status:** built, as a feature of the app (ADR 0005). The API records signals where things happen (post-check drift, egress withholds, server errors, rate-limit refusals, spent budgets: ids and counts only), and on every scheduler tick the worker turns them, plus connection syncs and job outcomes, into alerts that fire and resolve on their own: permissions drift and server errors (page), failing jobs, a failing or stale connection, and surging withholds (warning), and a spent budget (notice). The app's Alerts page shows org admins every alert and a team's admins their team's, with a badge in the sidebar; admins acknowledge alerts there, and an org admin can post page and warning alerts to Slack or any webhook (its URL in the secrets backend). A stopped worker is reported by the API from the worker's heartbeat. The first version, Prometheus rules with Alertmanager and a mail catcher, was retired: it needed the optional observability stack and kept alerts outside the product.

Goal: problems page someone before users notice.

- Alert rules as code: ACL drift spikes, sync lag past the window, job failure rate, egress withholds surging, 5xx rate, and the telemetry pipeline going quiet.
- Routed through Alertmanager to email or a webhook (Slack, PagerDuty), chosen at deploy time.
- **Acceptance:** forcing a drift spike fires the drift alert to a test webhook.

## Slice Q · SaaS control plane

Goal: for the managed offering, signing up provisions a dedicated cell.

- Signup and org creation, then a cell per customer (Terraform from E), its IdP federation, and its first admin.
- Billing from usage and seats; cell upgrades; teardown with purge and key destruction.
- **Acceptance:** a test signup produces a working cell that passes smoke, and tearing it down leaves nothing behind.

## Slices R1–R4 · Drill-down: answers linked to the knowledge behind them

Goal: every answer opens onto the knowledge it used, and every piece of that knowledge onto the next question, so people drill down instead of starting over. Built in order:

- **R1 · Ask workspace.** *Backend built:* conversations (`/v1/conversations`), private to their owner by row-level security (org admins included); follow-ups that carry earlier turns only as far as egress allows (the person's own questions always; an earlier answer only to the model that wrote it, and only while every source it drew on is one that model may still receive); answers re-checked against the reader's current access on every read, so an answer whose source they've lost is hidden rather than served from storage; conversation search over titles and the person's own questions, never answers; data-subject export and erasure covering conversations (erasure deletes them: they're the person's own words); and `GET /v1/graph?focus=` for an answer's sources and their neighbors through the map's permission path. *UI built:* the Ask workspace (`#/ask`, `G Q`, "Ask" in the palette): your history grouped by day and searchable over your own questions; the thread, answers in the reading face with citation numbers, hidden answers shown as hidden; a knowledge pane per answer with its Sources (cited first) and a Graph of those sources and their neighbors (capped at 24 nodes, "N more on the map"), where a node opens a panel with "Ask about this" and "Open"; the composer with model and scope; deleting a conversation behind a confirmation. Search's Ask tab now leads here. *Focus and modes built:* typing @ in the composer picks documents (from search, so only ones you can open) to answer from, shown as removable chips that carry over to follow-ups; "Answer from" chooses Smart (every channel), Keywords, Meaning, or Curated only. Both live in the shared retrieval path, so search's every gate still applies: a focus on a document you can't open yields nothing, and the model isn't called. **R1 is complete.**
- **R2 · Documents and Pipeline.** *Built:* Documents (`#/documents`, `G D`) lists everything you can open, newest first, through search's gates (a team you can reach, the index's readers, the authorization engine with drift audited, the audience rule), filtered by title, team, source, classification, and tier, every filter only narrowing; a row opens a panel with where it came from and the ways on, Open and Ask about this, which starts a conversation focused on that document (`#/ask/focus:<key>`). Source links render only for web addresses, since some come from admin settings. Pipeline (Admin, then Pipeline) shows a team's sync, import, and push runs to its admins, with status, attempts, and why one failed; a failed sync or removal can run again (audited), while a failed import or push says to upload or push again, because its upload is cleared once applied. Migration 0029 lets the app reset only the columns a retry touches.
- **Acceptance (R2):** each person's document list holds exactly what they can open, never the seeded drift; filters only narrow; asking for a hidden document by key reveals nothing; a team's runs reach only its admins (others get 403 or 404); only retryable failures retry, once, audited (smoke checks each).
- **R3 · Home and Costs.** *Built:* Home (`#/home`, now where signing in lands, `G H`) shows each of your teams with how many documents you can open there (counted through search's gates, so the count equals what Documents lists and never includes the seeded drift) and when it last brought knowledge in; your recent conversations; and, for a team's admins only, its failed runs and firing alerts under "Needs you". Costs (Admin, then Costs) shows each model's tokens this month against its monthly budget, counted over exactly the UTC calendar month the budget gate counts, with meters that warn at 80% and turn red at 100%, and a 30-day chart; org admins also see every team's month. How a run finds its team is now one shared query (`RUN_FROM`) used by Pipeline and Home.
- **Acceptance (R3):** Home's document count for a team equals Documents' list for it; failed runs reach only a team's admins; Costs' month total equals the budget gate's own count; a team's costs reach only its admins and every team's only org admins (smoke checks each).
- **R4 · Developer page and header.** *Built:* Developer (`#/developer`, `G V`) shows how to connect an AI assistant over MCP and call the API, and lists every endpoint from the API's own description (`GET /v1/openapi.json`, signed-in only); reads under `/v1` with no path parameters run there with your session, so nothing on the page can change anything. The sidebar's team switcher ("Working in") sets where Ask and Documents start, remembered in the browser, falling back to all your teams if you leave one; the health indicator shows the API's version and "All systems working" or which part isn't answering, from `GET /v1/status`, which says up or down only, never addresses or errors. **R1–R4 are complete.**
- **Acceptance (R4):** status needs a token and exposes no address, component version, or error; the endpoint list comes from the API and needs a token (smoke checks each); the developer page runs a read as the signed-in person (browser test).
- **Acceptance (R1):** conversations stay private to their owner; a follow-up never carries an answer to a model whose egress forbids its sources; an answer whose source the reader lost is hidden; export includes conversations and erasure removes them; a focused question answers only from the named documents, and from none the asker can't open; Keywords and Meaning modes differ on a paraphrase, and Curated returns only gold and silver (smoke checks each).
