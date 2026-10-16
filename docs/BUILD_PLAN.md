# Build plan: from S2 to G3

For Claude Code, picking up the build. Read `CLAUDE.md` first (the project's memory: every slice so far, and why it
is the way it is), then this file (how to build what's left), then `docs/ROADMAP.md` (the checklist you tick). Build
the roadmap's items in its order, one at a time. Deployment comes after all of it.

What's built and what isn't: the roadmap's ticks are the truth. S1 (Confluence, live) is the newest finished item,
and the best template for every source that follows: `kl/confluence.py`, its route in `kl/connections_api.py`, its
fake in `kl/fake_llm.py`, its smoke check `confluence_pages_carry_confluences_own_permissions`, its gallery form in
`services/web/app/admin-view.js`, its Terraform kind, and `docs/connectors/confluence.md`.

## 1. How to work

- **One item at a time, in the roadmap's order:** S2 to S8, then Q1 (its golden-set part) to Q8, E1 to E3, A1 to A4,
  G1 to G3. A branch and a pull request per item (`s2-google-workspace-directory`), CI green before merging.
- **Read before you write.** Each item names the existing code it extends. Mirror that code's structure, naming, and
  comment voice exactly. Don't invent a second way to do something the codebase already does once.
- **Confirm every vendor API against its current documentation** before coding a client: endpoints, parameters,
  pagination, scopes, rate limits. The endpoints in this plan are a starting point written from memory, not a spec.
- **Prove it before you tick it.** The loop in section 4, for every item, every time.
- **Record it.** When an item is done: tick its boxes in `docs/ROADMAP.md`, add a paragraph to `CLAUDE.md` in the
  style of the ones before it (what was built, the decisions, the names a future reader greps for), and update the
  `**Next, in order:**` line there.

## 2. The rules that never bend

1. **Permissions fail closed.** When a reader can't be determined (an unreadable restriction, a group that won't
   expand, an unknown principal type, an API error), the item gets fewer readers or isn't indexed. Never more. Every
   source's smoke check proves a stranger can't find what they shouldn't, and that a revoked reader loses it.
2. **The source's own permissions decide who reads what** (ADR 0010, ADR 0011). The connection's account only sets the
   ceiling of what's indexed. Each item carries readers mapped from the source: `ext:<source>.<id>` identities that
   reach a person through identity matching (`kl/identity_match.py`) or a link they made, `org:<slug>` only on a
   connection whose admin confirmed the site or workspace is the company's own (`company_site`), and teams or groups
   where the model has them.
3. **Secrets never land in a repository, a log, an audit row, or Terraform state.** A credential is either handed
   over once (stored in OpenBao at `kl/connections/<connection id>`) or referenced (`credential_ref`), and references
   are fenced per team (`team_secret` in `kl/connections_api.py`). Terraform takes secrets only as write-only
   arguments. Pydantic fields holding secrets are `repr=False`.
4. **Outbound calls go only where they're allowed.** A connector's base address must be the vendor's own (a pattern,
   like `SITE` in `kl/jira.py`) or listed in its `KL_<SOURCE>_API_BASES` setting (dev fakes only, in
   `docker-compose.yml`; `deploy/gcp/compose.gcp.yml` clears each list). Pagination follows next links only on the
   same host and path prefix (see `_all` in `kl/confluence.py`). Never follow a URL a response hands you to anywhere else.
5. **Smoke never calls a real service.** Every source has a fake in `kl/fake_llm.py`, answering the exact calls the
   real client makes at the vendor's paths, with authentication checked. Test the real client against the real fake
   before relying on smoke (S1 did this over HTTP; do the same).
6. **No silent widening in the app either.** The UI never offers an option that broadens readers without saying so
   plainly (the company-site checkbox's wording is the model).
7. **Migrations are additive and numbered** (`kl/migrations/00NN_<name>.sql`, next is 0048). Grant what the roles
   need (`kl_app` for the API, `kl_ingest` for the worker), as earlier migrations do.

## 3. Where things are

Repositories (Windows, PowerShell, Docker Desktop; GNU make with a POSIX shell, recipes prefixed `>`):

- **Self-hosted, public:** `C:\Users\rkneu\Downloads\knowledge-layer-m0\knowledge-layer\self-hosted\`. Everything below.
- **The Terraform provider:** `C:\Users\rkneu\OneDrive\Desktop\terraform-provider-aerugo\`. It's inside OneDrive, which
  can lock a file while syncing (an edit then fails with EPERM on rename): retry, or move the repository out of
  OneDrive. Its README's Tests section runs the acceptance tests against the dev stack.
- **SaaS, private:** `...\knowledge-layer-m0\knowledge-layer\saas\`. Not in this plan.

The API and worker, `services/api/kl/`:

| File | What it holds |
|---|---|
| `main.py` | The API: search, Ask, documents (`DOCUMENTS_SQL`, `list_documents`), concepts, imports, models |
| `connections_api.py` | Every connection route, `team_secret`, `ref_rule`, `settings_of` (Terraform's read-back), the update and its secret rotation, removal |
| `worker.py` | Jobs: `connection.discover` and `.sync` (a branch per kind), `SOURCE_KINDS` (for retiring), imports, pipelines |
| `sync_items.py` | The shared sync loop: `Item`, `sync_container` (content re-indexed only on a new version; readers refreshed every sync), `record_identities` |
| `connectors.py` | `Container` (with `name`), `Health`, the GitHub connector |
| `jira.py`, `confluence.py`, `gdrive.py`, `directory.py` | The connectors; `directory.py` is Okta today (`client_for` dispatches by provider) |
| `identity_match.py` | Links people to source identities by verified email at sign-in; respects the directory's leavers |
| `fake_llm.py` | Every fake: the model, Jira, Confluence, Drive and Google's token endpoint, Okta, GCP Secret Manager and metadata |
| `smoke.py` | The leak suite (`@check` functions); constants `FAKE_JIRA`, `FAKE_CONFLUENCE`, and the rest |
| `evals.py` | The scenario evaluation and its baseline (`evals_baseline.json`, `make eval-baseline`) |
| `eval.py` | The golden set (slice M2), to merge into the same scorecard (Q1) |
| `people.py` | Who to ask; `links.py`: references between documents; `privacy.py`: personal data |
| `config.py` | Every setting, from the environment |
| `vault.py`, `gcp_secrets.py` | Credential references: OpenBao, and GCP Secret Manager (`gcpsm://`) |
| `managed.py` | What tools manage (Terraform's `Aerugo-Managed-By`) |
| `provision.py` | Migrations, the authorization model and seed tuples, OpenBao's mounts; `KL_DEV_IDENTITIES` |
| `migrations/` | Numbered SQL; `0036` allows connection kinds github, google, jira, confluence, slack, microsoft, notion, directory |

The web app, `services/web/app/`: `admin-view.js` (the Connect-a-source gallery `CONNECTORS`, each form, connection
rows, the edit form and `SCOPES`), `documents-view.js` (spaces, the tree via `placeOf`, the reader), `markdown.js`,
`brand.js` (source marks and names), `terraform.js`, `ui.js`. Browser tests: `services/e2e/test_web.py`.

Also: `authz/` (the authorization model and seed tuples), `deploy/` (Compose's mounted config, `k8s/` for kind,
`gcp/` for a VM), `docs/adr/`, `docs/connectors/` (one page per source), `.github/workflows/` (`smoke.yml` gates PRs;
`deploy-gcp.yml` runs only by hand).

## 4. The verification loop

For every item, before it's done:

```powershell
make up                # migrations, the stack (rebuilds what changed)
make smoke             # every leak check, the new one included
make eval-scenario     # no permission leak; no regression against kl/evals_baseline.json
make web; make e2e     # when it touches the app: the browser tests (extend test_web.py for new screens)

cd C:\Users\rkneu\OneDrive\Desktop\terraform-provider-aerugo   # when it touches the provider
go build ./... ; go vet ./...                                    # and the acceptance tests, per its README
```

If `make eval-scenario` says something got better, record it (`make eval-baseline`) and commit the file. If it says
something got worse, fix it, or explain in the pull request why the baseline should move.

## 5. Step V: prove what's built (do this first)

Much of the last session's code was checked statically and against fakes, but never run in the stack: identity
matching, the Okta directory, Jira, Drive on a service account, the Documents redesign, connections in Terraform,
GCP Secret Manager references, the local Kubernetes manifests, and all of S1.

1. Run the whole loop above. Fix what fails, smallest change first; don't weaken a check to make it pass.
2. `make kind-up` and `make kind-open` (`deploy/k8s/README.md`): the stack on a kind cluster. Fix what fails.
3. Once everything is green: `make eval-baseline`, and commit `services/api/kl/evals_baseline.json`. From then on CI
   holds every pull request to it.
4. Tick section V of the roadmap.

## 6. The recipe for every source (S2 to S7)

S1 is the worked example; follow its shape.

1. **The client** (`kl/<source>.py`): a class with `test() -> Health`, `discover() -> list[Container]` (each with its
   `name`), and the listings sync needs; `allowed_base`; `client_for(connection)` reading the credential through
   `vault.get` (which also resolves `gcpsm://`). Validate every identifier from the source with a regex before using
   it in a path or query. Handle rate limits (respect `Retry-After`).
2. **Readers:** a function from the source's permission model to principals, failing closed (section 2). Note each
   account it meets for `record_identities` (with an email when the source shows one).
3. **Items:** `Item(key="<source>-<id>", uri=..., version=..., title=..., path=..., acl=..., sections=lambda: ...)`,
   bodies fetched lazily, then `sync_container`. Drop items nobody may read. `path` carries the source's hierarchy
   (S1 joins ancestors' titles with ` › `; extend `placeOf` in `documents-view.js` when a source has a tree).
4. **Wiring:** a `POST .../connections/<kind>` route in `connections_api.py` (mirror `create_confluence_connection`:
   the allowlist, one credential, the team fence, scope validation, `note_managed`, the discover job, the audit
   row); the update's scope fields; `SETTINGS`; the worker's discover and sync branches; `SOURCE_KINDS`.
5. **The fake** in `fake_llm.py` (state, a setup route smoke posts to, auth checked, the vendor's paths and shapes),
   and its `KL_<SOURCE>_API_BASES` default in `docker-compose.yml` (cleared in `deploy/gcp/compose.gcp.yml`).
6. **The smoke check:** who reads what (including the source's trickiest permission case), a stranger who can't, a
   revoked reader who loses it on the next sync, the container's name, the API refusing another team's secret, an
   internal address, and a malformed identifier.
7. **The gallery:** its `CONNECTORS` entry `ready: true`, its form (own state and setter), `SCOPES` if it has a scope
   list. **Terraform:** a new `kind` in `connection_resource.go` (schema, create's route and body, `refresh`, update's
   lists), an acceptance test against the fake, and the onboarding module's type. **The provider README** row.
8. **`docs/connectors/<source>.md`:** what the identity team creates, and exactly which read-only permissions it needs.

## 7. The items

### S2. Google Workspace as a directory, and Drive's group shares

**Why:** a Drive file shared with a Google group reaches no one today, and Google leavers aren't revoked.

- **Design:** a second provider for the directory connection (`provider = "google"` beside `"okta"` in
  `directory.py`'s `client_for`). The Admin SDK Directory API, through the Drive service account's key with
  **domain-wide delegation**: the JWT names an admin to act as (`sub`), and asks only for the read-only scopes
  `admin.directory.user.readonly`, `admin.directory.group.readonly`, and `admin.directory.group.member.readonly`.
  Reuse `gdrive.py`'s token minting (its token URL is fixed to Google's; keep it so).
- **People:** users of the customer (`customer=my_customer`); suspended or archived means left; ask for recently
  deleted users too. Someone in the previous sync who is missing now counts as left (fail closed), so
  `revoke_leavers` takes their matched access at once, as Okta's leavers do.
- **Groups:** groups and their members, nested membership flattened (the API can include derived membership).
  Migration 0039: a `directory_groups` table (org, connection, group email, member email, seen), replaced each sync.
- **Drive:** in `gdrive.py`, a permission of type `group` resolves through `directory_groups` to its members'
  `ext:google.<email>` identities; with no directory connection, a group still grants nothing. `domain` permissions
  keep their rule (the company domain reaches the org). Re-resolved every sync, so a member removed from a group
  loses the file on the next one.
- **Fake:** Admin SDK users, groups, and members in `fake_llm.py`, behind the same service-account token check.
- **Smoke:** a file shared with a group: its member finds it, a stranger doesn't; the member suspended, then the
  directory synced: they lose it; a user deleted between syncs loses matched access.
- **App and Terraform:** the directory form chooses Okta or Google Workspace (the key or a reference, the admin to act
  as); `directory` in the provider accepts `"google"`, with an `admin_email` attribute.
- **Watch for:** delegation needs a super admin to authorize the service account's client id for exactly these
  scopes; write that down in `docs/connectors/google-workspace.md`. Large groups: paginate, and cap with a clear error.

### S3. Entra (Microsoft) as a directory

- **Design:** `provider = "entra"` in `directory.py`. Microsoft Graph with an app registration, application
  permissions `User.Read.All` and `GroupMember.Read.All`, signed in with a **certificate** (a client assertion JWT)
  in preference to a client secret. Token from `login.microsoftonline.com/<tenant>/oauth2/v2.0/token`, scope
  `https://graph.microsoft.com/.default`; both hosts on the allowlist.
- **People:** `accountEnabled` false means left; users missing since the last sync, too. **Groups:** transitive
  members into `directory_groups` (from S2), for Microsoft 365 (S5) to use.
- **Fake, smoke, form, Terraform** (`directory = "entra"`, `tenant_id`, the certificate as a write-only argument or a
  reference), `docs/connectors/entra.md`.
- **Watch for:** paging through `@odata.nextLink` (same host only); delta queries can come later.

### S4. Slack

- **Design:** a Slack app's bot token. Read-only scopes for the channels the bot is in: public and private channel
  history and lists, users and their emails. Only channels where the bot is a member are containers.
- **Documents:** a thread is one document; messages outside threads are grouped per channel per day, so a busy
  channel doesn't become a million documents. The version changes when a message is edited, added, or removed;
  `sync_container` drops what's gone, which is how deletions and retention are followed.
- **Readers:** a private channel, its members; a public channel, its members by name, or the whole org only on a
  workspace confirmed as the company's own (`company_workspace`, like `company_site`); a shared channel (Slack
  Connect), only members whose team is this workspace's, never another organization's.
- **Watch for:** rate limits are tight on history calls (honor `Retry-After`, and sync incrementally from each
  channel's last cursor); never index a channel the bot was removed from.
- **The rest of the recipe**, with `docs/connectors/slack.md` naming each scope and why.

### S5. Microsoft 365 (SharePoint and OneDrive libraries)

- **Design:** Graph with `Sites.Selected`: the app is granted read on each site, by an admin, site by site; never
  tenant-wide. The connection lists the sites it's granted; each site's document libraries are containers. Files go
  through the existing extractors (Word, PowerPoint, Excel, PDF; check `kl/extract.py` for what it handles).
- **Readers:** each item's permissions: people (`ext:microsoft.<email>`), Entra groups through `directory_groups`
  (S3), organization-wide sharing links reach the org only on a confirmed company tenant, anonymous links likewise,
  and SharePoint site groups grant nothing until they can be expanded (document it).
- **Watch for:** use delta listings for changes; large files: a size cap, with the item listed but not extracted.

### S6. Notion, live

- **Design:** an internal integration's token; the pages and databases shared with it (search, then block children,
  recursively; database rows as documents); the page hierarchy as `path`. Send the current `Notion-Version` header.
- **Readers:** Notion's API doesn't say who can read a page. Decide it as exports do today: the connecting team only,
  never wider, and say so plainly in the form and in `docs/connectors/notion.md`.
- **Watch for:** a page shared with the integration later, or unshared: the next sync adds or removes it. Exports of
  the same workspace: retire their copies, as S1's `retire_exported` does.

### S7. Data catalogs

- **Order:** Dataplex Universal Catalog first (entries, aspects, glossary terms, owners; lineage from the Data Lineage
  API), keyless through workload identity on GCP or a service account key; then the Glue Data Catalog and SageMaker
  Catalog (an IAM role assumed through OIDC), Purview, Unity Catalog; then dbt and Dataform manifests from a
  repository; then DataHub or OpenMetadata.
- **Design:** metadata only, never data. Entries become assets (the K3 tables; read the lineage migrations for their
  names) and readable entries linked to the documents that mention them; owners feed who to ask; lineage feeds
  impact walks. Readers mirror the catalog's access control (IAM members to principals; groups through
  `directory_groups`; a domain to the org on a confirmed company domain), failing closed.

### S8. Faster permission changes

- **Permission-only refreshes:** a job kind (`connection.refresh_readers`) on a shorter cadence than content syncs,
  re-reading only readers and updating them as `sync_container` does for unchanged items.
- **Webhooks**, where sources offer them: GitHub, Jira, Confluence, Slack's Events API, Microsoft Graph change
  notifications, Drive's push notifications. One route per source under `/v1/hooks/...`, each **verifying the
  source's signature** before anything else, then enqueueing a targeted refresh. No webhook body is ever trusted as
  data: it only says what to re-read.
- **Smoke:** a revoked share stops being found within the refresh window, with the webhook simulated.

### Q1. The baseline's last part: the golden set

Fold `kl/eval.py`'s golden set (slice M2) into the same scorecard and baseline as `kl/evals.py`, one gate for both:
each case's result, per set, in `evals_baseline.json`; `make eval` and `make eval-scenario` both held to it.

### Q2. Context at index time, and entity resolution (K4)

- **Chunks:** embed each chunk with where it sits (its document's title, its section path, and a one-line summary of
  the document: its opening, or a model's summary when the team has a model), so a chunk that says "it" knows what.
- **Entities:** tables, jobs, tickets, services, with aliases (`ORDER_HIST`, `edp.orders`, "the EDW orders table");
  start from `links.py`'s identifier names. A query naming one alias also searches the others.
- **Prove it:** the eval's lookup and cross-team cases improve; record the new baseline.

### Q3. Claim-level checking (K9)

Each sentence of an answer is checked against the passages it cites (similarity, then the team's model judging
entailment when there is one). An unsupported claim is marked, or removed. Confidence thresholds are calibrated on
the evaluation set and stored, not guessed; the answer's confidence (`answer_confidence`) uses them.

### Q4. Lineage in search and Ask (K8)

"What breaks if…" walks lineage edges from the entities a question names, only through assets the person may see;
search results gain the assets a result's lineage reaches. The `impact-cross-team` eval case passes.

### Q5. Decisions (K5)

Decisions extracted from Jira (resolutions, decision comments) and Confluence (decision macros and decision pages),
with dates and who made them; a decision that replaced another ranks first and says what it replaced. The
`decision-latest-first` eval case passes.

### Q6. Who to ask knows who has left (K6)

`people.py`'s who to ask drops, or marks, people the directory says have left (`directory_people`), and suggests their
team instead.

### Q7. Attachments, and Git authors (K2b)

Attachments (Confluence, Jira, Drive) become their own documents, linked to their page, with its readers, size-capped,
through the extractors. Git authors (who wrote what, from commits) feed who to ask.

### Q8. Diagrams, audio, and video (K11)

Opt-in per team: diagrams and images read by a vision model the team chose; audio and video transcripts (the source's
own, or a speech model's) with timestamps a citation can point to.

### E1. Search by source

Filters by source kind and container on `/v1/search` and Ask (the API first, then chips in the app), and each result
showing its source and container by name.

### E2. Knowledge health, and the review queue (K10)

A page per team listing what's stale, contradicted, orphaned, or without an owner, and a queue where an owner
confirms, updates, or retires each, audited.

### E3. The Map (K7)

Clusters keep their colors across reloads (derived from a stable cluster id), and are named from what's in them.

### A1 to A4. Agents and integration (K12)

- **A1:** MCP tools `neighbors`, `path`, and `why-linked`, each within the caller's permissions.
- **A2:** Python and TypeScript clients for search, Ask, documents, and pushes.
- **A3:** a change feed: what changed since a cursor, per caller, never naming what they can't see.
- **A4:** OKF export: a team's curated pages as an OKF repository.

### G1 to G3. Governance and security

- **G1:** the services' OpenBao token gets a policy for exactly their paths, instead of root; provisioning creates
  it, in dev (`deploy/openbao/start.sh`) and on a VM (`deploy/gcp/vm/deploy.sh`).
- **G2:** the rest of the provider: routes, pipelines, imports, secret stores, compliance rules; and "manage with
  Terraform" import snippets for connections that exist (`terraform.js`).
- **G3:** credential references to HashiCorp Vault, AWS Secrets Manager, and Azure Key Vault, fenced per team as
  OpenBao's and GCP's are, each read with the runner's own identity where the platform allows.

## 8. Traps already met

- **The shared sync loop puts a container's documents in `team_for(connection, container)`:** a routed container's
  team, not always the connection's own. Anything that touches "this connection's documents" must use it.
- **A container ref or key is unique per site, not per org:** two sites can both have project `EDP` and issue
  `EDP-1`. A connection's documents carry its `connection_id` (migration 0040), and `sync_container` reads and updates
  only those. A source that writes documents outside it (as Drive's `sync_drive` does) must match `connection_id` too,
  unless its ids are global.
- **Jobs in Kubernetes can't be changed in place:** `deploy/k8s/up.ps1` deletes and re-creates them.
- **A new setting needs three places:** `config.py`, `docker-compose.yml` (dev default, fakes included), and the
  production override (`deploy/gcp/compose.gcp.yml`), which must clear any dev fake.
- **Windows PowerShell 5.1:** piping native output re-encodes it, and `>` writes UTF-16. Use files, not pipes, for
  binary data or anything kubectl or docker will read back.
- **Edits that collide:** when a change inserts text similar to what follows, apply edits from the bottom of the file up.
- **The fakes are smoke's truth:** if the real client and the fake disagree on a path or a shape, smoke passes and
  production fails. Test them against each other.
- **Local smoke isn't CI's smoke when `.secrets/` or `.env` hold more than the example:** a GitHub App there makes
  the optional checks sync your sandbox org (they remove their connection when done), `KL_SMOKE_GITHUB_WRITES=1`
  also lets the curator check merge a real pull request in it, and a leftover `KL_OTEL_ENDPOINT` fails the trace
  check. Prove an item as CI does, leaving both files alone: `COMPOSE_ENV_FILES=.env.example`, and an override file
  (in `COMPOSE_FILE`) that mounts an empty folder at `/run/kl-secrets` for `provision` and `smoke`.

## 9. When unsure

Stop and ask in the pull request rather than guess, when a choice would widen who can read something, change a
public API's meaning, or need a new external dependency. Otherwise, choose the narrower, failing-closed option, and
say why in the code's comment and in `CLAUDE.md`.
