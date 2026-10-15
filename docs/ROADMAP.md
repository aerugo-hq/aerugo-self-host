# Roadmap: the knowledge layer

What's left to build in Aerugo itself. Deployment comes last, once this is done. Build in the order below, one item
at a time, and tick an item when its definition of done holds. IDs are stable: refer to items by them (S1, Q2).

**Done means**, for every item: its smoke leak checks pass (`make smoke`), the evaluation set shows no regression
(`make eval-scenario`), the browser tests pass where it touches the app (`make e2e`), and `CLAUDE.md` records it.

**A source (S) is done only when it also has:** a fake in `kl/fake_llm.py`, so smoke never calls the real service; a
leak check in smoke (a reader sees it, a stranger doesn't, a revoked reader loses it); a form in the Connect-a-source
gallery; a `kind` in the Terraform provider's `aerugo_connection`, with an acceptance test; and its account's setup
written down (what the identity team creates, and exactly which read-only permissions it needs).

## Build order

Section by section: every source, then quality, then experience, agents and integration, and governance. Q1's
baseline is already in place (only its golden-set part is left), so each source is measured as it lands.

| # | Items | |
|---|---|---|
| 0 | **V** | Prove what's built |
| 1 | **S1 to S8** | Sources: Confluence, Google Workspace, Entra, Slack, Microsoft 365, Notion, data catalogs, faster permission changes |
| 2 | **Q1 to Q8** | Quality: the baseline's last part, context and entities, claim checking, lineage, decisions, who to ask, attachments and authors, diagrams and media |
| 3 | **E1 to E3** | Experience: search by source, knowledge health, the Map |
| 4 | **A1 to A4** | Agents and integration: MCP tools, an SDK, a change feed, OKF export |
| 5 | **G1 to G3** | Governance and security: a least-privilege token, the rest of the provider, more secret stores |

## V. Prove what's built

- [ ] A pull request runs the smoke workflow green on GitHub
- [x] `make eval-scenario`: no permission leak; then `make eval-baseline` records the scorecard (Q1)
- [x] `make web` and `make e2e` pass: the Documents shell, the gallery, the reader
- [x] The provider: `go build ./...`, `go vet ./...`, and the acceptance tests (`TestAccConnection`, against the fake Jira)
- [x] `make kind-up` brings the stack up on kind, and `make kind-open` signs in
- [x] Whatever failed is fixed, and recorded

## 1. Sources: what it learns from

### S1. Confluence Cloud, live
- [x] Client: the site (`https://<site>.atlassian.net/wiki`), the Atlassian service account's email and API token or a
      `credential_ref`, the REST API, paginated
- [x] Discovery: spaces as containers, by their names
- [x] Sync: pages and blog posts, their bodies (storage format to text, tables kept as tables), titles, versions,
      authors, and parents
- [x] Permissions: space permissions and page restrictions (inherited down the tree), mirrored as readers; a page the
      account can't read is never indexed
- [x] Filters: by label, and by page tree (a root page and everything under it)
- [x] Attachments: listed and linked to their page (their text is Q7)
- [x] Documents: the page tree as folders, parents first
- [x] Identities: each account it names, recorded for matching by verified email
- [x] Exports keep working; a space moved from an export to the live connector isn't indexed twice
- [x] Done's list: wired in (route, worker), a fake and a smoke leak check, the gallery's form, the Terraform `kind`,
      and the account's setup written down (`docs/connectors/confluence.md`)

### S2. Google Workspace as a directory
- [ ] The Admin SDK's Directory API, through the service account with domain-wide delegation, read-only scopes only
- [ ] People and their state (active, suspended, deleted) into `directory_people`; leavers revoked at once, as Okta's are
- [ ] Groups and their members, nested groups included, kept current
- [ ] Drive: a file shared with a group reaches its members (today it reaches no one)
- [ ] Smoke: a group share reaches a member and not a stranger; a leaver loses it

### S3. Entra as a directory
- [ ] Microsoft Graph through an app registration (a certificate or a federated credential, never a client secret if
      avoidable), `User.Read.All` and `GroupMember.Read.All`
- [ ] People and their state; groups and their members; leavers revoked at once

### S4. Slack
- [ ] A Slack app's bot token; the channels the bot is in, as containers by name
- [ ] Messages and threads as documents (a thread is one document), with their authors
- [ ] Readers: a public channel, the workspace's members; a private one, its members; a shared channel, only this
      workspace's members
- [ ] Edits and deletions followed; the workspace's retention respected

### S5. Microsoft 365
- [ ] SharePoint sites granted through `Sites.Selected` (never tenant-wide); document libraries as containers
- [ ] Files (Word, PowerPoint, Excel, PDF) through the existing extractors
- [ ] Permissions: each item's sharing (people, groups through Entra, links), mirrored

### S6. Notion, live
- [ ] An internal integration's token; the pages and databases shared with it
- [ ] The page hierarchy; a database's rows as documents; blocks to text
- [ ] Readers: Notion's API doesn't say who can read a page, so decide and document what Aerugo does (as exports do
      today: the connecting team only), and never widen it

### S7. Data catalogs
- [ ] Dataplex Universal Catalog: entries (datasets, tables, views, columns), descriptions, aspects, glossary terms,
      owners, and lineage (the Data Lineage API); keyless on GCP
- [ ] Then the Glue Data Catalog and SageMaker Catalog (an IAM role through OIDC), Purview, and Unity Catalog
- [ ] dbt and Dataform manifests (models, columns, tests, lineage), from a repository
- [ ] DataHub or OpenMetadata
- [ ] Assets on the Map; descriptions as entries linked to the documents that mention them; owners into who to ask;
      lineage into impact walks
- [ ] Metadata only, never data; each entry's readers mirrored from the catalog's access control, failing closed

### S8. Faster permission changes
- [ ] Permission-only refreshes between content syncs: readers, without re-reading bodies
- [ ] Webhooks where sources offer them: GitHub, Jira, Confluence, Slack, Microsoft Graph change notifications, Drive's
      push notifications
- [ ] A revoked share takes effect within minutes, and smoke proves it

## 2. Quality: how good its answers are

### Q1. The quality baseline (K1)
- [x] The scorecard recorded: each case's result and the totals (recall and MRR), in `kl/evals_baseline.json`
- [x] `make eval-baseline` records it, deliberately; a run with a permission leak is never recorded
- [x] `make eval-scenario` fails on a regression (a case that finds less, or MRR falling past a tolerance), and says
      what improved, to record next
- [x] CI runs it after smoke
- [ ] The golden set (`kl.eval`, slice M2) in the same scorecard

### Q2. Context at index time, and entity resolution (K4)
- [ ] Each chunk carries where it sits: its document's title, its section path, and a one-line summary of the document,
      embedded with it
- [ ] Entities (tables, jobs, tickets, services), with their aliases (`ORDER_HIST`, `edp.orders`, "the EDW orders table")
- [ ] A query naming one alias finds documents that name the entity any other way
- [ ] Measured: Q1's scorecard improves on the lookup and cross-team cases

### Q3. Claim-level checking (K9)
- [ ] Each sentence of an answer checked against the passages it cites
- [ ] A claim its citations don't support is marked in the answer, or removed
- [ ] Confidence thresholds calibrated against the evaluation set, not guessed

### Q4. Lineage in search and Ask (K8)
- [ ] "What breaks if..." walks lineage edges, only within what the person may see
- [ ] Search results gain the assets a result's lineage reaches
- [ ] The `impact-cross-team` case passes

### Q5. Decisions (K5)
- [ ] Decisions extracted from Jira and Confluence (decision records, "we decided", resolutions), with their dates and
      who made them
- [ ] A decision that replaced another ranks first, and says what it replaced
- [ ] The `decision-latest-first` case passes

### Q6. Who to ask knows who has left (K6)
- [ ] Who to ask drops, or marks, the people a directory says have left
- [ ] It suggests their team instead

### Q7. Attachments, and Git authors (K2b)
- [ ] Attachments (Confluence, Jira, Drive) as their own documents, linked to their page, with its readers
- [ ] Git authors: who wrote what, into who to ask

### Q8. Diagrams, audio, and video (K11)
- [ ] Diagrams and images: their text and structure, read by a vision model the team chose (opt-in)
- [ ] Audio and video: transcripts (a source's own, or a speech model's), with timestamps a citation can point to

## 3. Experience: how people use it

### E1. Search by source
- [ ] Filter by source (Confluence, Jira, Drive...) and by container, in search and in Ask
- [ ] Each result shows its source and its container, by name

### E2. Knowledge health, and the review queue (K10)
- [ ] A page per team: what's stale, contradicted, orphaned, or without an owner
- [ ] A review queue: an owner confirms, updates, or retires each

### E3. The Map (K7)
- [ ] Clusters keep their colors across reloads
- [ ] Clusters are named from what's in them

## 4. Agents and integration (K12)

### A1. The remaining MCP tools
- [ ] `neighbors`, `path`, and `why-linked`: the graph as tools, within the caller's permissions

### A2. An SDK
- [ ] Python and TypeScript clients for search, Ask, documents, and pushes

### A3. A change feed
- [ ] What changed since a cursor, per caller: only what they may see

### A4. OKF export
- [ ] A team's curated pages, as an OKF repository

## 5. Governance and security

### G1. A least-privilege OpenBao token
- [ ] The services' token gets a policy for exactly their paths, instead of root; provisioning creates it

### G2. The rest of the Terraform provider
- [ ] Resources for routes, pipelines, imports, secret stores, and compliance rules
- [ ] "Manage with Terraform" import snippets for connections that already exist

### G3. More secret stores
- [ ] References to HashiCorp Vault, AWS Secrets Manager, and Azure Key Vault, fenced per team as OpenBao's and GCP's are
