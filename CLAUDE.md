# Aerugo (self-hosted): instructions for Claude Code

## What Aerugo is

Aerugo is **the knowledge layer for an enterprise**. It brings in what every team knows, from every source, connects it
across team boundaries, and serves it to people, AI agents, RAG systems, and decision tools, always within each
person's permissions. **The knowledge layer is the product**; connectors, the web app, and the hosted service are built
around it. Its promise is *measurably accurate, and honest when unsure*: precision over completeness.

This repository is the open product: everything a company needs to run Aerugo in its own environment. The hosted
service's control plane is private (`aerugo-SaaS`); it deploys pinned releases of this product and never copies it.
The Terraform provider lives in its own repository (`terraform-provider-aerugo`).

Where documents disagree, **ADR 0010 and this file win**. `docs/design/v1.md` and `docs/design/build-plan.md` record
how the project started; read them for history, not for direction.

## The knowledge layer (ADR 0010)

| Component | What it does |
|---|---|
| **Extract** | Turns any format into text and structure: documents, slides, sheets, PDFs, email, scans (OCR). |
| **Evidence** | Everything from every source: chunks, embeddings, full text, metadata, permissions, classification. |
| **Graph** | Entities (people, systems, tables, projects, customers) and typed links between everything. |
| **Curated** | A small set of reviewed pages in OKF (definitions, decisions, system pages, runbooks), citing evidence. |
| **Quality** | Evaluation sets, the review queue for unsure links and merges, and knowledge health. |
| **Policy** | Permissions, egress, audit, and retention, on every read path. |
| **Interfaces** | The REST API, MCP, an SDK, a change feed, and OKF export, so anyone can build on the layer. |

**Where data lives.** Original documents stay in their sources, the system of record; documents never move. Aerugo's
store (inside the company's environment when self-hosted, in its region on the hosted service) keeps what it extracts:
text, chunks, embeddings, metadata, the graph, and permission mirrors; optionally an original's copy, encrypted with
its team's key. Large media is a pointer plus derived text (transcripts, descriptions), never a copy. Curated pages are
small: they live in Git (one repo per team, or one repo with a folder per team) or in Aerugo's own versioned store for
teams that don't use Git. OKF is the curated layer's format and the portable export, never a requirement that every
document become OKF.

**Three kinds of links**, never mixed up: *recorded* (facts a source wrote down: references, attachments, lineage,
owners; near-certain), *inferred* (Aerugo's judgment: the same entity, a similar meaning; always shown with its
confidence and reason, drawn dashed), and *confirmed* (a person agreed; becomes a rule for that company).

## Identities and connections

Aerugo owns no credentials to anyone else's systems. The **runner** (self-hosted: the worker) runs as the company's
own service account; each **connector** uses either the runner's identity (no key at all) or a reference to a secret
in the company's own secret manager, never the value. **API access** is separate and is the only identity Aerugo
checks: people sign in through SSO; pipelines show their own identity (a GitHub Actions token, a cloud service
account), with a token as the fallback. Today's service accounts (slice T1) become these trusted workloads. The
decision is in `aerugo-SaaS/docs/adr/0001`; the product side will be ADR 0011.

## Invariants (IMPORTANT: never violate; ask before any change that touches one)

1. Every content read goes through the policy path in `services/api/kl/main.py`:
   1. Resolve scope.
   2. Query inside `db.scoped()`, where RLS pre-filters by ACL.
   3. Post-check every document in OpenFGA.
   4. Write an audit event.

   Never read content tables outside `scoped()`, and never skip the post-check.
2. Scope is not permission. Workspace or org scope only chooses where to look.
3. Never weaken RLS. Every new table with tenant data gets `org_id`, `ENABLE ROW LEVEL SECURITY`, and policies in the same migration. Services never connect as `kl_owner`.
4. Principals entering RLS context must pass `principals.is_valid`. Never build principal strings from unvalidated input.
5. Secrets never go into Postgres, logs, exceptions, traces, or Git. Store a `credential_ref` and read the value from `kl.vault` at the moment of use. Pydantic fields holding secrets use `repr=False`.
6. Authenticated calls act as the end user. Never add a shared or service key that bypasses document ACLs.
7. Content may go to a model (generation or embeddings) only if its classification is allowed for that endpoint (egress policy).
8. Deny by default. Treat an unknown principal, missing context, or unresolved identity as no access, and fail closed on errors in authorization code.
9. Every new read surface (REST, MCP, webhook payload, export) adds permission-leak checks to `kl/smoke.py` in the same PR.
10. No new infrastructure dependency without an ADR in `docs/adr/`.
11. Don't return counts of hidden items or distinguish "forbidden" from "not found" for content or private workspaces. Use 404.
12. Synced content carries only the source's permissions. ACL arrays hold source identities (`ext:github.<login>`), and a caller gets them only through a `principal_aliases` link, expanded inside `db.scoped()`. Never put workspace, group, or org principals on synced content: connecting a source must never widen who can read it.
13. Webhooks are hints, never truth. A delivery must pass HMAC verification and replay protection, and it may only queue jobs. Never read permissions or content from a webhook payload; the job re-reads the source.
14. Every link carries its provenance: the signal that made it, its strength, and the evidence behind it. Never store a bare edge.
15. Graph expansion is authorized at every hop. A walk never passes through, or reveals, a node the asker can't read.
16. Nothing derived mixes permissions. A summary, cluster label, or entity description shown to someone is built only from what that person can read; never precompute one across documents with different readers.

## Conventions

- Python 3.12, FastAPI, psycopg 3 with plain SQL (no ORM), httpx. Keep modules small and typed.
- Schema changes are numbered migrations in `services/api/kl/migrations/NNNN_name.sql`; `provision` applies pending ones on every `make up`. Never edit an applied migration. `deploy/postgres/sql/` is the frozen M0 baseline; dev seed data never goes into migrations.
- Background work goes through the `jobs` table and `kl/worker.py` handlers. The worker runs as `kl_ingest` with its org context set, so RLS still applies.
- Embeddings carry their own copy of each chunk's ACL. Code that changes a chunk's ACL updates `chunk_embeddings` in the same transaction, or the vector path leaks.
- Authorization model: `authz/model.json`, loaded by `kl.provision`. A document's readers come from its source (direct grants, or its container); team membership never grants a document.
- OKF: follow OKF v0.2 exactly; extensions live only under `x-kl`. Relation types: `owns`, `owned-by`, `depends-on`, `approves`, `derived-from`, `supersedes`; targets are bundle paths or `kl:<id>`. A page never names a page its readers can't see.
- The web app follows `docs/design/DESIGN.md`: Datavine's system with Verdigris as the one accent; light, dark, and system themes; every color is a token, and every pairing meets WCAG AA.
- Tests describe behavior in plain sentences (see `kl/smoke.py` docstrings). Write permission tests first.
- Never use dev credentials outside the local stack.

## Commands (run from this folder)

- `make up`: build and start the stack. `make web`: the web app on http://localhost:3000.
- `make smoke`: the smoke suite; it must pass before a task is done. It expects the plain seed.
- `make e2e`: the browser tests. `make eval`: the golden set (slice M2); `make eval-mine` grows it from feedback.
- `make demo`: load the fictional migration company (`sandbox/scenarios/migration`). `make eval-scenario`: load it and score its questions; it fails only on a permission leak. Run `make reset && make up` before `make smoke` afterwards.
- `make github-app ORG=<org>`: the dev GitHub App; credentials land in `.secrets/`.
- `make reset && make up`: re-run init SQL and the realm import after schema or seed changes.
- `docker compose logs <service>`: debug a service.

## What's built

- **Retrieval and knowledge:** hybrid search (pgvector and full text) behind RLS and OpenFGA post-checks, with audit; curated OKF bundles with tiers (gold, silver, evidence); proposals compiled from evidence and reviewed as pull requests; Ask with conversations, focus, and citations; the Map, with entities extracted from text.
- **Sources:** GitHub (routing each repo to its own team), Google Drive (with Google sign-in linking), CI pipelines pushing with workload tokens, Confluence and Notion export imports. Formats: PDFs, Office files, spreadsheets, OpenDocument, email, plain text, RTF, and OCR for scans and images.
- **Connections:** create, refresh, change in place, and remove; removal retires what only that team held, its authorization first.
- **Compliance:** data-subject requests, access reviews, retention, legal holds, persistent OpenBao, per-team encryption, pgBackRest backups and the restore drill, personal-data detection, the control map, runbooks, and team purge.
- **Administration:** teams, members, models and budgets, service accounts, marks for what Terraform manages, alerts, and observability (Grafana, Jaeger, Prometheus).
- **The web app:** team scopes with documentation-style spaces and an in-place reader; Datavine's design system; light, dark, and system themes.
- **The Terraform provider:** teams, members, models, budgets, and service accounts.
- **Tests:** the smoke suite, 21 browser tests, the golden set, and the evaluation-scenario runner (`kl/evals.py`) with its opt-in scenario loader.

## What's next, in order

The knowledge layer comes first; the platform is built around it afterwards.

1. **K1, the evaluation set.** Built: the runner (`kl/evals.py`, 13 cases with permission traps), the scenario mode of
   the seed job, `make demo` / `make eval-scenario`, and the migration company's ten pages in
   `sandbox/scenarios/migration/<team>-knowledge/` (Commons: the program, the platform standards, and data owners;
   Payments: ORDER_HIST, edp.orders, the EDP-412 migration, and the Q3 2025 cutover decision superseding Q2; Finance:
   the Sales dashboard; Legal, private: order data retention). Payments pages never name Legal's page. **Next:**
   record the first baseline, then merge `kl.evals` into `kl.eval`'s scorecard.
2. **K2, recorded links.** Built: references (migration 0033: `reference_links`, with the signal, its strength, and the
   text that made each one; `links.refresh_references`). A document that writes another document's address, or an
   identifier a curated page declares among its aliases (ORDER_HIST, EDP-412, edp.orders), links to it; plain words and
   identifiers two pages share never link. The map serves them as `reference` edges, between visible documents only,
   after curated links and before semantic ones. **Next, K2b:** attachments ("attached to"), then owners and authors
   from every connector.
3. **K3, systems and data.** Built: lineage in OpenLineage's shape (`POST /v1/workspaces/{ws}/lineage`, team
   admins; migration 0034: `assets` and `lineage_edges`, metadata only) becomes datasets and jobs on the map, joined by
   `lineage` edges and a `describes` link from the page that declares an asset's name; one node per asset however many
   teams report it; shown only to members of a reporting team. The scenario loads Payments' and Finance's lineage.
   **Next:** connectors that emit it for you (an OpenLineage HTTP endpoint for Airflow and Marquez, dbt and Dataform
   manifests, BigQuery's lineage API, warehouse catalogs).
4. **K4, understanding.** Built: entity resolution on the map (an entity whose name a visible page declares among its
   aliases is that page, so its mentions link to the page; an alias two pages declare resolves to neither). **Next:**
   chunk context written at index time, and the same resolution in search.
5. **K5, decisions.** Built: current knowledge first (`ranking.current_first`, applied at the end of retrieval):
   among the results someone may see, a page another result `supersedes` ranks just after it, so the eval's "When is
   the order history cutover?" now counts. **Next:** decisions as their own items, extracted from Jira comments and
   Confluence decision pages: what was decided, who decided, when, and where it's recorded.
6. **K6, people.** Built: who to ask about a page (`kl/people.py`; `GET /v1/concepts/{key}/people`; the reader's "Who
   to ask"): who verified it, who owns it, its owning team's members (listed only for teams the asker is on, so a
   private team's membership never leaks), and the people it names, each with reasons; a hidden page is a 404.
   **Next:** the company directory as a connector (display names, who's still active), then assignees and commenters
   from Jira.
7. **K7, the Map redesign.** Built, after EdgeQuake's look: one packed disc (`graph-physics.js`), clusters found from
   the links by Louvain local moving at resolution 1.5, links weighted by shared neighbors (`graph-clusters.js`; plain
   label propagation let one hub page paint everything one color), a pastel per cluster from the `--cluster-0` to
   `--cluster-9` tokens (light and dark), no lines and no names at rest (a lit node names itself and its links, with a
   halo, and draws its links in blue, itself purple), fitting that keeps the typical bubble at about 14 px on screen and refits when
   the panel resizes, and the selected node as a bottom card that says how each link was made. A lit node's links are
   drawn by kind (`LINK_STYLE`): curated bold, recorded (references, lineage, mentions) solid, inferred dashed.
   **Next:** stable cluster IDs across reloads as the graph grows; cluster names built only from what the viewer can
   read.
8. **K8, the permission-checked walk.** Built: for lineage, `GET /v1/lineage/impact` (what depends on an asset,
   nearest first, loading only lineage the caller's own teams reported); in search and Ask, a hop along recorded
   references (`REFERENCE_NEIGHBORS_SQL`) after the curated one, each page found authorized on its own.
   **Next:** lineage hops inside search and Ask (a dashboard's question reaching the tables it reads).
9. **K9, confidence.** Built: every answer carries High, Medium, or Low with reasons, from signals (the tier of what it
   cites, how many sources, whether one is superseded), never the model's opinion of itself (`kl/confidence.py`);
   saved with the message (migration 0035) and hidden with it; Ask shows the badge and reasons. **Next:** claim-level
   checking; thresholds calibrated against each company's evaluation set; abstaining when unsure.
10. **K10, review and health:** the review queue for unsure links and merges, confirmed decisions as rules, and a
    knowledge-health page that shows accuracy.
11. **K11, extraction gaps:** attachments as their own documents, descriptions of diagrams, transcripts for audio and
    video, a richer document model (sections, tables), and pointers for large media.
12. **K12, interfaces.** Built: MCP graph tools `related`, `who_to_ask`, and `impact` (same permission path as the
    app). **Next:** neighbors, path, and why-linked tools; an SDK; a change feed; OKF export; curated review inside
    Aerugo for teams without Git.

**Connectors (ADR 0011: a service account reads; people see only their own access).** Built: the ADR; one sync loop
for every connector (`kl/sync_items.py`: items in, documents, grants, deletions, and routing out); connections with
their own cadence (`every_minutes`), freshness (`last_synced_at` is when readers were last read), and a credential
either stored once or referenced under the team's own `openbao://secret/kl/<org>/<team>/connections/` (migration 0036);
Jira Cloud (`kl/jira.py`: projects as containers, issues with comments, Jira's own permissions including security
levels, `company_site` for company-wide projects, the accounts Jira names recorded in `source_identities`), with a fake
and a smoke leak check. Identity matching (`kl/identity_match.py`, run at `/v1/whoami`): a person is linked to the source
accounts that connectors reported with their SSO-verified email, in a domain the company declared (`KL_ORG_EMAIL_DOMAINS`),
recorded as `sso-email`; never an account someone else holds; removed, grant and all, once it stops holding; proven
links untouched. The Connect-a-source gallery (Admin, Sources, Connections: `CONNECTORS` in `admin-view.js`): every
source, the built ones with a form (Jira) or steps (GitHub, with its installations found and connected in a click;
imports, pipelines, lineage), the rest marked coming; each connection's row shows its cadence and when its permissions
were last read, with re-sync, pause, resume, and remove (confirmed). Google Drive on a service account (`kl/gdrive.py`):
its JSON key signs its own token request (RS256, read-only Drive scope), sent only to the allowed Google address, never
a key's own `token_uri`; shared drives it's a member of, plus folders shared with it (`folder-<id>` containers, walked
breadth first, always selected); the accounts Drive names recorded for identity matching; the person-token mode kept
only for older connections. Directories (`kl/directory.py`, migration 0037), Okta first: who's at the company and who
has left, by email, in `directory_people`; an email listed twice is active if any listing is; deprovisioned people
are asked for explicitly; Okta's next-page links are followed only on the same org; a leaver's `sso-email` matches
are revoked by the worker as soon as the directory says so, grants and all, and matching refuses them at sign-in;
links a person proved stay. Documents, as in Datavine's documentation (`documents-view.js`), at the org and in a team
alike: a 288px sidebar with a Documentation header, a page search, and every space you're in (at the org, each
unfolding on click, its pages loaded then, a search unfolding them all; in a team, that one unfolded), each tree its
own navigation, "Documents in <space>"; the main pane a sticky bar (the space, where the page sits in its source, and
its actions) over an 800px reading column, or a home with what was added recently. A page opened at the org keeps
its own address (`#/documents/<id>`), its space unfolded. Every document opens: a curated page through its page, any
other through
`GET /v1/documents/{key}/content` (its sections, through the documents list's own gates, a 404 otherwise). Text is
rendered by `markdown.js` in documents mode: links (http and https), images (https; an http one is only a link),
and video players for YouTube, Vimeo, and Loom built from a validated id (YouTube's privacy-enhanced domain,
sandboxed, lazy, sending only the site's origin), and video files over https; Ask's answers never embed anything.
Each source shows as its own brand mark (`brand.js`: `SourceIcon`, and `sourceName` for its name), the same wherever a
source shows, so people can tell at a glance where something came from: search results and Ask's sources (both open
any document in the reader, in scope), the Documents tree's groups, the breadcrumb, Open in, the space's recent table,
the gallery, connection rows, and imports; the label loses its kind's name (`sourceOf`) since the mark says it. Search
results and Ask's citations carry `source_kind` (every retrieval query selects it; stored citations keep it). Marks
are files in `services/web/public/brand/`, fetched as the web image builds (`scripts/brand-icons.mjs`, in the
Dockerfile): full color from Iconify's open logos set first, one color from Simple Icons otherwise, each recorded in
`sources.json` so a one-color mark upgrades later and nothing downgrades; any holding a script, an outside reference,
or an embedded image is refused. Never committed (trademarks), never drawn in code; a missing one is a neutral glyph;
in dark mode each sits on a light backing (`--mark-bg`), never inverted.
The tree remembers what's open (`folds`, keyed by each folder's path): a source starts unfolded and its folders folded,
opening a page unfolds its way there (`foldsTo`), nothing folds unless you fold it, and a search unfolds its matches
without changing what's remembered. Sources order curated pages first, then Confluence, Jira, GitHub, Drive, Notion,
Slack, each named by its container (spaces by name: `connection_containers.display_name`, migration 0038, set at
discovery; imports and pipelines by their names; curated bundles by theirs), returned as each document's `container`.
The reader isn't remounted per page: the current page stays under a thin progress line until the next is ready,
pages read are cached for the session (`READ`, at most 50), and pressing a link starts the read (never hover: reads
are audited). `markdown.js` renders pipe tables (alignment, cell formatting, wide ones scrolling inside the page).
Connections are Terraform-ready (the API half of `aerugo_connection`): every create and update records its manager
from the provider's `Aerugo-Managed-By` header (`kl/managed.py`, moved out of `main.py` so the connections API can
share it), and reads return `managed_by`; a connection's read returns its `settings` (site, projects, company_site,
cadence, domain, drives, repositories, provider) and `enabled`, so a plan sees no false drift, naming a
`credential_ref` only when it's the team's own; updates rotate a secret Aerugo stored (an API token, or a service
account key), refusing a team's own reference (rotate it where it lives); removing a connection destroys only the
secret Aerugo stored for it, recognized by its own path (`kl/connections/<id>`), never a team's or GitHub's app.
The provider's half (terraform-provider-aerugo): `aerugo_connection` (`internal/provider/connection_resource.go`), one
resource for every kind (github, google, jira, directory), its credential a `credential_ref` or a write-only
`api_token` / `service_account_key` (never in state; `credential_version` rotates it); cadence, `enabled`, the scope
lists, and the Jira email change in place, removing a list replaces it; imports as `team/name`; an acceptance test
against the fake Jira (`connection_test.go`). `examples/onboarding` is a whole onboarding repository: a YAML file per
team (`teams/<team>/sources.yaml`, found by `fileset`), the `aerugo-team` module (which refuses a source without a
`credential_ref`), `CODEOWNERS`, a GCS state backend, a workflow that plans on pull requests and applies on merge, and
`terraformrc.example` for the private provider mirror. In the app, a connection Terraform manages shows its tag (the
workspace managing it, and that a change made here is undone on its next apply) and only Re-sync (and GitHub's
routing, which Terraform doesn't manage); any other can be edited in place: its cadence (one set elsewhere kept as a
choice) and its scope list, prefilled from its settings. Credential references can also name a GCP Secret Manager
secret (`kl/gcp_secrets.py`): `gcpsm://projects/<project>/secrets/kl_<org>_<team>_<name>[/versions/<n>]`, in a
project the deployment allows (`KL_GCP_SECRET_PROJECTS`, none unless set), named for its team (underscores, which
teams' names never hold, so no prefix is another team's); the runner reads it as its own workload identity (the
metadata server) or a key file (`GOOGLE_APPLICATION_CREDENTIALS`); `vault.get` hands it such references; smoke proves
it with a fake Secret Manager and metadata server. Phase 0 and Phase 1 of the plan are built; next, the deployment.
The deployment's first stage (`deploy/gcp`, README there): one VM, deployed by `.github/workflows/deploy-gcp.yml`, run
by hand (`workflow_dispatch`; merging to main deploys nothing). GitHub signs in keyless (Workload Identity Federation, this repository's main branch only),
builds the api, web, and postgres images into Artifact Registry by commit, and runs `deploy/gcp/vm/deploy.sh` on the
VM through IAP: it reads the VM's settings (metadata) and every credential (Secret Manager, generated by
`deploy/gcp/terraform`) into a root-only .env, builds the production realm and seed tuples from the dev ones
(`vm/prepare.py`: no people, SSL required, no password grant, a generated kl-terraform secret, the domain's
addresses; the org's structure and the first admin), initializes OpenBao once (Cloud KMS unseals it; the root token
and recovery key go to Secret Manager), gives Keycloak its own Postgres database, and starts `compose.gcp.yml` over
the dev file (registry images, restart policies, Keycloak in production mode, every dev fake gone, Caddy's TLS; the
Keycloak admin console only through a tunnel). Provisioning makes no dev identity when `KL_DEV_IDENTITIES=0`, and
removes any that exist.
Local Kubernetes (`deploy/k8s`, README there; the default way to try it beyond Compose, free): kind or Docker
Desktop's cluster, `make kind-up` then `make kind-open`. Plain manifests applied with `kubectl apply -k` (no Helm
yet: YAML can be checked without a cluster), mirroring Compose service for service: Postgres, OpenBao, and Keycloak
as StatefulSets with volumes (Keycloak's users kept), OpenFGA after its migrate Job, the provision Job, and the API,
worker, and MCP server each waiting for provisioning (`aerugo-wait`: the authorization model is written); shared
settings in `aerugo-app-env`; no demo content and no fakes. `up.ps1` refuses a non-local context, builds and loads
the images (kind's CLI, or straight into the nodes), turns the repository's files into ConfigMaps on each run,
re-creates the Jobs, and waits; `open.ps1` forwards Compose's ports. The GCP VM deploy is by hand only.
What's left is `docs/ROADMAP.md`: five sections of checklists (sources, quality, experience, agents, governance),
item IDs, one definition of done, and the build order; tick items there as they land. Q1 (the quality baseline):
`kl/evals.py` turns its results into a scorecard (`scorecard`), held to `kl/evals_baseline.json` (`compare`): a case
that finds less, or MRR falling more than `MRR_TOLERANCE`, fails the run; what improved is said, to record next.
`make eval-baseline` records it deliberately (`--record`: stdout holds only the JSON, everything else goes to stderr;
written to a temporary file and moved, so a failed or leaking run never replaces the baseline); CI runs
`make eval-scenario` after smoke.
S1 (Confluence, live), its core: `kl/confluence.py`. Confluence has no "who may read this page" call, so readers are
computed: a space's read grants (people, and groups' members, expanded once per sync; a space role grants nothing),
narrowed by the read restriction on every restricted page from the root down (`narrowed`); the whole org only on a
confirmed company site, when anyone may read the space (anonymous, or the `confluence-users` group). Fails closed: an
unreadable restriction or folder drops that subtree; a group that won't expand grants nothing; a page nobody may read
isn't an item. The storage format becomes sections per heading, tables as pipe tables, fenced code, Jira keys, link
text (over the linked title), tasks with their state (from the status alone), images by file name. A page's path is
its ancestors' titles (` › `), for the tree. Labels and page trees filter; attachments are listed. Paging follows
only next links on the site itself. Keys are `confluence-<id>`; accounts `ext:atlassian.<id>`, shared with Jira.
`KL_CONFLUENCE_API_BASES` adds sites beyond Confluence Cloud's (the dev fake).
S1 wired in: `POST .../connections/confluence` (Confluence sites only, one credential, the team fence; space keys,
labels, and page ids validated by `confluence_scope`), its scope lists in updates and in Terraform's `settings`, the
worker's discovery and sync, `SOURCE_KINDS` for retiring. The documents list returns each document's `path`, and
Documents nests a live page under its ancestors. A live sync retires the export copies of the pages it read, in the
team the space syncs into (`team_for`), with their tuples (`retire_exported`); another team's are never touched. The
fake Confluence (`kl/fake_llm.py`) answers the client's every call at Confluence's paths; smoke's
`confluence_pages_carry_confluences_own_permissions` proves inherited restrictions, a page nobody passes, groups, the
space's name and tree, a label filter, an export retired, and three refusals. The gallery's form, editable spaces,
`kind = "confluence"` in the provider (spaces, labels, page_trees; an acceptance test) and its onboarding module, and
`docs/connectors/confluence.md` (the account, and exactly the read-only access it needs). S1 is done.
Step V (everything built, proven in the stack): `make smoke` (123 passed; the optional checks skip, as in CI),
`make eval-scenario`, `make e2e` (21 browser tests; the gallery's is new), the provider's build, vet, and acceptance
tests (`TestAccConnection`, `TestAccConnectionConfluence`, and the rest), and `make kind-up` then `make kind-open` on
a kind cluster, signed in. What failed: `retire_exported` looked for exported pages as `source_kind = 'confluence'`,
but imports store `'import'`, so a page read live stayed indexed twice; T2's Drive fixtures were title-only Docs,
which the extractor rightly refuses; and the imports check's cleanup is a job, so the conversations check after it
cited a page that went mid-conversation (`earlier_turns` then rightly dropped that answer). The first baseline is
recorded (`kl/evals_baseline.json`: Recall@10 100%, MRR 0.92 over 12 cases), after smoke, as CI orders them. Locally,
a GitHub App in `.secrets/` turns smoke's optional checks on: they connect your own sandbox org, and the curator check
opens and merges a real pull request in its `payments-knowledge`, adding a curated page each run, which in time
crowds the seed's runbook out of Ask's sources; `KL_OTEL_ENDPOINT` left in `.env` by `make obs` fails the trace check
unless the collector runs. To prove an item as CI does, run with `.env.example` and no `.secrets/` (BUILD_PLAN's traps).
P3 (local smoke and your GitHub): the curator check, the only one that writes to GitHub, now runs only with
`KL_SMOKE_GITHUB_WRITES=1`; and the GitHub checks end by removing their connection
(`github_checks_leave_the_seed_alone`), waiting until what it brought in is gone, so the checks after them see the
seed alone, whatever curated pages your sandbox already holds.
Connections own their documents (migration 0040). Container refs and keys are unique per site, not per org (a Jira
project key, a Confluence space key, `jira-<issue key>`, `confluence-<page id>`), so a team admin's connection to
another site with the same project key used to delete what the first connection had indexed and its own listing
lacked, and overwrite the keys that collided (with matching versions, only their readers: one site's issue read by the
other site's people). `documents.connection_id` now names the connection that wrote a Jira or Confluence document:
`sync_container` reads only its own (`existing`), its upsert updates only its own (`documents.connection_id =
EXCLUDED.connection_id`), and a key another connection holds is skipped, never taken over, so a second connection to
the same project indexes none of the first's issues. `leave_former_teams`, the retire job (a removed connection's
documents lose its id, `ON DELETE SET NULL`, so `d.connection_id IS NULL` tells them from another's), the Documents
tree's container names, and `retire_exported` (only pages the connection now holds) follow it. GitHub and Drive
documents stay unnamed: their ids are global, and two teams' connections to one repository or drive both hold it (T2).
The migration gives existing documents the one connection of their kind that lists their container and deletes the
rest a connection wrote, which their own connection's next sync indexes again (the M0 seed's Confluence page has no
container, and stays). Smoke's `a_connection_never_touches_another_connections_documents`
proves it against a second fake Jira site (`/jira-b`, `FAKE_JIRA_B`).
G1 (a least-privilege OpenBao token): the API, the worker, the MCP server, provisioning, and smoke hold the services'
token (`OPENBAO_SERVICES_TOKEN`; `openbao-services-token` in kind), never root. Its policy is
`deploy/openbao/services.hcl`: secrets under `secret/kl/` (written, read, and the delete and destroy that purges and
rotations need), the transit keys named `kl-...`, and reading the mount list; nothing else. A token that isn't root
expires with the token mount's default (32 days) and nothing renews it, so the mount's ceiling rises and the token is
minted for ten years. `deploy/openbao/start.sh` writes the policy and mints the token in dev and kind, before Transit
is mounted, so the healthcheck (the services' token listing Transit) means it works; it refuses a services' token equal
to the root one, and the dev root token is the operator's alone. On a VM, `deploy.sh` checks it on every deploy with
the root token read from Secret Manager, replacing a token that still holds root. Smoke's
`the_services_openbao_token_holds_only_their_paths` proves the policy, six refusals, and the lifetime. Provisioning can
check the KV mount but no longer create it; every OpenBao start creates it.
S2 (Google Workspace as a directory, and Drive's group shares; the plan is `docs/plans/2026-10-15-s2-google-workspace-directory.md`):
`provider = "google"` beside `"okta"` in `kl/directory.py` (`GoogleDirectoryClient`, `client_for`). The Directory
API on its own host (`KL_GOOGLE_ADMIN_API_BASES`, `admin.googleapis.com`; the dev fake added, production overrides
keep only Google's) through a service account acting as an admin by domain-wide delegation: `gdrive.service_account_token`,
Drive's token request made a function, with exactly three read-only scopes (`GOOGLE_SCOPES`) and a `sub`, sent only
to Google's token address, never a key's own `token_uri`. People are users of `my_customer`, then the deleted ones
asked for on their own; suspended or archived means left. In every directory, Okta's too, someone a previous sync
listed and this one doesn't has left; a listing cut short raises `ListingCutShort` (Okta's `_pages` no longer
returns what it had at `MAX_PAGES`), so a failed sync changes nothing rather than revoking everyone. Rate limits
(429, and a 403 whose reason is a rate or quota: `rate_limited`) back off exponentially with jitter, honoring
`Retry-After`; pages follow `pageToken`, never a URL. Groups (migration 0039: `directory_groups`, one row per person a
group reaches, nested groups flattened through Google's derived membership; `kl_ingest` only, no grant to `kl_app`)
are replaced per directory in one transaction; only active `USER` members count, a whole-domain or outside member
reaches no one, and a group over `MAX_GROUP_MEMBERS` (2,000) grants nothing and is named in the sync's result. Active
Workspace users are recorded as `ext:google.<email>` identities, so sign-in matching links them. A membership change
re-queues every Drive connection's sync (`REQUEUE_DRIVE_SQL`), so someone taken out of a group loses the file on
the next Drive sync. In `kl/gdrive.py`, `readers` turns a `group` permission into its members' `ext:google.<email>`
from Google directories only (`directory_groups`, `GROUPS_SQL`; a paused one counts for nothing), compared lower
case; with no directory a group still grants nothing. A directory decides who has left and who a group reaches,
company-wide, so only an org admin connects, changes, re-syncs, or removes one (`require_org_admin`, moved to
`kl/policy.py`); its team's admins still read it. A Google directory takes the key once or a team's
`credential_ref`, and `admin_email`, which must be in a company domain (`KL_ORG_EMAIL_DOMAINS`, `company_admin`);
`settings` read it back, and an update can change it or rotate the stored key. The fake Admin SDK in `kl/fake_llm.py`
(`ADMIN`, `_admin`, small pages on purpose) answers only a token minted for exactly the three scopes and an allowed
`sub`, and keeps every token request (`/google/admin/requests`). Smoke: `a_google_directory_brings_groups_to_drive`
(a group and a nested group reach their members and no one else; a suspended member, someone taken out of the group,
and someone deleted between syncs lose access; a failed listing changes nothing; the token's address, scopes, and
`sub`; five refusals), and `a_directory_revokes_leavers_at_once` now connects as an org admin, refuses a team admin,
and proves "listed before, not now". The gallery's Directory card chooses Okta or Google Workspace and is offered
only to org admins (`orgAdmin`; a team admin is told who can); two browser tests. Terraform: `directory = "google"`
with `admin_email` (in place) and the write-only `service_account_key`, an acceptance test against the fake, and the
onboarding module's type. `docs/connectors/google-workspace.md` says what the identity team sets up. Not built:
keyless delegation (IAM `signJwt`), matching a user's alias addresses, and group members in the app.
S3 (Entra as a directory): `provider = "entra"` in `kl/directory.py` (`EntraClient`). Microsoft Graph
(`KL_MICROSOFT_GRAPH_BASES`, `graph.microsoft.com`) with an app registration's application permissions
`User.Read.All` and `GroupMember.Read.All`, signing in with a certificate, never a client secret: the client
credentials grant with a client assertion signed PS256, naming the certificate by its SHA-256 thumbprint
(`x5t#S256`), its audience the tenant's own token address on `login.microsoftonline.com` (the dev fake's otherwise),
`iss` and `sub` the app, a fresh `jti`, five minutes long. `entra_certificate` turns a PEM bundle into the key, the
certificate, and its thumbprint, refusing a key that isn't the certificate's (RSA only; an encrypted key isn't
supported). Tenants and apps are named by id (`GUID`). People are users, then Graph's deleted items; a disabled or
deleted account has left; Graph not saying whether an account is enabled fails the sync, and in every directory
a listing of nobody does too, so an empty answer is never read as everyone leaving. Groups are each group's
transitive members (a cast to users, which Graph only serves with `ConsistencyLevel: eventual` and `$count`), only
enabled users, keyed by the group's email or, for a security group without one, its id; migration 0041 adds
`directory_groups.group_id`, set for Entra (Microsoft 365 shares by id, S5). Pages follow `@odata.nextLink` only on
Graph itself (one anywhere else raises `ListingCutShort`); 429, 503, and 504 back off, honoring `Retry-After`. The
directory clients now say what they feed: `identities` (the source recorded for matching, `ext:microsoft.<email>` for
Entra) and `feeds` (the connection kind re-queued on a membership change, `microsoft` for Entra), so the sync loop
treats Google and Entra alike. The route takes `tenant_id`, `client_id`, and `certificate` (`repr=False`) or a team's
`credential_ref`; `settings` read back the two ids; an update rotates the stored certificate. The fake Graph in
`kl/fake_llm.py` (`GRAPH`, `_graph`, `entra_token`, `new_entra_app`) mints an app with its own certificate and checks
every assertion as Entra does; its next links keep the request's query, as Graph's do (the client, run against the
fake over HTTP before smoke, caught the fake dropping it). Smoke: `an_entra_directory_revokes_leavers_and_lists_groups`.
The gallery's Directory card offers Microsoft Entra (tenant, app, certificate; no secret field); a browser test.
Terraform: `directory = "entra"` with `tenant_id`, `client_id`, and a write-only `certificate`, an acceptance test with
a throwaway certificate, and the onboarding module's ids. `docs/connectors/entra.md`. Not built: a federated
credential (the runner's own workload identity).
S4 (Slack): `kl/slack.py`. A bot token (`xoxb-`, never a person's; `KL_SLACK_API_BASES`) of an app the company
creates in its own workspace and never distributes, so Slack's history calls keep their usual rate. Containers are
the channels the bot is in (`conversations.list`, `is_member`), named `#<channel>`; direct messages are never read.
Readers (`readers_of`): a channel's members from this workspace (a shared channel's other side, someone who has left,
and bots reach no one), or the whole org for a public, unshared channel on a workspace its admin confirmed
(`company_workspace`). Members are `ext:slack.<user id>`, recorded with their emails for matching. A thread is one
document (`slack-<channel>-<ts>`); other messages are one per channel per day (`slack-<channel>-<date>`); joins and
topic changes are skipped (`SKIPPED`); text is plain (`plain`: people and channels by name, links as label and
address); the version changes on any added, edited, or removed message. Each sync re-reads the history window
(`history_days`, a year by default) in full, so edits, deletions, retention, and a removed member carry over on that
sync, and a day past the window leaves; a channel whose bot left is synced as empty. Paging follows Slack's cursor;
a 429 is waited out (`Retry-After`); `ok: false` raises `SlackError`; more than `MAX_MESSAGES` fails the sync rather
than reading the rest as deleted. New for every kind that owns its documents (`OWNED_KINDS`: Jira, Confluence, Slack):
after a sync, `sync_items.retire_unlisted` removes the connection's documents in containers it no longer reads (taken
out of its scope, lost by its account, a channel its bot left), authorization first, so no container keeps its readers
frozen. The route `POST .../connections/slack` (the bot token or a team's reference, channels by id, a history
window); updates change channels and the window and rotate the token; `settings` read them back. The fake Slack
(`SLACK`, `_slack`) answers each method as Slack does, including one 429 when asked. Smoke:
`slack_messages_carry_each_channels_own_readers`. The gallery's Slack form (and `SCOPES`); a browser test. Terraform:
`kind = "slack"` (the bot token as the write-only `api_token`, `channels`, `history_days`, `company_workspace`), an
acceptance test, the onboarding module's fields. `docs/connectors/slack.md`. Not built: incremental reads from each
channel's cursor (a full window is simpler and follows deletions exactly; it costs more calls on busy channels).
S5 (Microsoft 365): `kl/microsoft365.py`, `M365Client` on `directory.GraphClient` (Entra's token and paging, now
shared). An app registration holding only `Sites.Selected`, granted read site by site, never tenant-wide; the
connection names those sites by Graph's site id (`SITE`), and each one's document libraries are its containers
(`<site> / <library>`); a site the app wasn't granted answers 403 and is skipped. Files are a delta listing from the
start (the only complete one), facets read by presence (Graph sends `"deleted": {}`), paths rebuilt from parent ids
(delta gives none), content through the extractors, over `MAX_BYTES` indexed by name only; Graph's content redirect is
followed only to the tenant's own SharePoint host (`SHAREPOINT`) or the dev fake, without the Graph token. Readers
(`readers`, failing closed): the people a file is shared with (`ext:microsoft.<email>`, from the user, the site user,
or its login name), an Entra group's members by the group's id (`entra_groups`: Entra directories' `directory_groups`
with `group_id`), and a sharing link's named people; a SharePoint site group, an organization or anonymous link, and
an identity without an email grant nothing. An Entra membership change re-queues these connections (`feeds`). Keys
are `m365-<library hash>-<item id>`; the version is the item's `cTag`. The route `POST .../connections/microsoft`
(tenant, app, sites, a certificate or a team's reference); updates change the sites and rotate the certificate. The
fake SharePoint in `kl/fake_llm.py` (sites with the apps each is granted to, per-app tokens, delta, permissions, a
download redirect smoke can divert). Smoke: `microsoft_365_files_carry_their_own_sharing`. The gallery's Microsoft
365 form; a browser test. Terraform: `kind = "microsoft"` with `tenant_id`, `client_id`, `sites`, and the write-only
`certificate`; an acceptance test. `docs/connectors/microsoft-365.md`. Not built: OneDrive (people's own drives),
expanding SharePoint site groups, and incremental delta tokens.
S6 (Notion, live): `kl/notion.py`. An internal integration's token (`ntn_` or `secret_`; `KL_NOTION_API_BASES`), at
`Notion-Version` 2026-03-11 (`archived` is `in_trash`; databases' rows come from data sources: search for
`data_source`, then `data_sources/{id}/query`). One container, the workspace (by its name, from `users/me`); every
page shared with the integration, each data source's rows as pages, trash and unshared pages left out; blocks as text
(`sections_of`: a section per heading, lists, to-dos with their state, quotes, fenced code, table rows), up to
`MAX_BLOCKS`; a page's path its shared parents' titles, a row's its data source's. Notion's API doesn't say who can
read a page, so readers are the connecting team's members as the team holds them (`team_readers`: its `group:` and
`user:` principals with a role on it, recomputed every sync), never `org:`, even for a team the whole org may join;
the form and `docs/connectors/notion.md` say so plainly. This is the one connector whose readers aren't the source's
own (invariant 12): the roadmap's decision, as exports do, flagged in `docs/plans/decisions.md`. `viewer_tuples` now
hands OpenFGA a `user:` principal as itself. The pages it holds retire the same team's export copies
(`retire_exported`, by `https://www.notion.so/<id>`). The route `POST .../connections/notion`; updates rotate the
token. The fake Notion (`NOTION`, `_notion`) insists on the token and the version header, as Notion does. Smoke:
`notion_pages_reach_only_the_connecting_team`. The gallery's Notion form, saying who reads it; a browser test; every
gallery source is now built. Terraform: `kind = "notion"` (the token as the write-only `api_token`); an acceptance
test. Not built: reading people's own Notion permissions (the API has none), and comments.
**Next, in order:** `docs/BUILD_PLAN.md` says how to build what's left (the rules, a map of the code, the verification
loop, the recipe every source follows, and each item's design), and `docs/ROADMAP.md` is the checklist: step V is
done, and so are G1 and S2 to S6; next S7 (data catalogs) and S8; then Q1 (its golden-set part) to Q8;
E1 to E3; A1 to A4; G2 and G3.

Then the platform: the runner (ADR 0011, hosted: in the company's cluster, calling out only), GCP first (Vertex AI
models, Secret Manager references), then Vault, AWS, and Azure; a build-time provider address for private mirrors;
the Helm chart with its volumes; and continued polish of the web app's spacing and components.
