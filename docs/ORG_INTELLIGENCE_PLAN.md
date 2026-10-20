# Org intelligence: build plan

For Claude Code. This plan grows Aerugo from a knowledge layer into an org-intelligence platform: point it at
everything a company has (code, warehouses, schedulers, ETL, BI, documents, chat, tickets, incidents), and it does
the legwork a senior architect does: knows how data and systems connect, who uses what, why things are the way they
are, how changes are made here; plans migrations; and opens pull requests for people to review.

Read, in order: `CLAUDE.md` (the project's memory), `docs/BUILD_PLAN.md` (its rules, its verification loop, and its
recipe for a source all still apply), `docs/PRODUCTION_REVIEW.md` (its prerequisites, Phase 0 below), then this file.
Build the items in section 13 in order, one at a time, ticking them there.

## 1. What we're building, honestly

A senior architect's value is five kinds of knowledge, and judgment over them:

| Knowledge | What it is | Where the platform gets it |
|---|---|---|
| **The map** | Systems, jobs, tables, services, and how data and calls flow between them | Code, schedulers, ETL, warehouse logs and catalogs, BI |
| **History** | Why it's built this way: decisions, migrations tried, incidents | Docs, tickets, chat, decisions (K5), incidents |
| **People** | Who owns what, who knows what | Owners files, commits, catalogs, directories, who to ask (K6) |
| **Conventions** | How changes are made here: which repo, which pattern, which review | Repos' structure, contributing guides, merged pull requests |
| **Patterns** | What's used most, what's unused, what breaks, what changes together | Usage logs, scheduler history, incidents, co-change |

The platform holds all five as **one graph with evidence**, and an **agent** that reasons over it in the steps a
senior follows (playbooks), answering, planning, and proposing changes. It is an aid that does a senior's legwork in
minutes, not an oracle: it will sometimes be wrong, so every answer shows its evidence and how sure it is, and every
action (a plan adopted, a pull request merged) is a person's decision. It knows only what's connected, and where it
can't parse something (an unusual SQL dialect, a binary ETL export), it says so and counts it, never hides it.

## 2. How to work

Everything in `docs/BUILD_PLAN.md` sections 1 to 4 applies: one item per branch and pull request, CI green, the
verification loop run and its results recorded in `CLAUDE.md`, nothing ticked that hasn't run. In addition, every item
here ships with:

- **its slice of the synthetic enterprise** (O2): the fixtures that exercise it, with known right answers;
- **its smoke checks**, including who may see what (every node, edge, and answer obeys permissions);
- **its browser tests** when it adds or changes a screen (`services/e2e/test_web.py`);
- **its evaluation cases** when it changes what the platform concludes (section 11).

## 3. Principles that never bend

1. **Facts before AI.** Exact analysis (parsing code, SQL, job definitions, logs) builds the graph. A model is used
   only where judgment is needed (a purpose, a cluster's name, a plan), always on top of facts it cites, and re-run only
   when those facts change.
2. **Every node and edge carries its evidence**: how it's known (`confirmed` by a person, `observed` in a log,
   `declared` in code or config, `documented`, `inferred` by a model), where (a file and line, a job, a query's
   fingerprint, a page), when, and a confidence. Conflicting evidence is kept, not overwritten.
3. **Permissions on everything.** A node or edge is visible to someone only if they may see at least one piece of its
   evidence. Row-level security enforces it in the database, never only the API (the permissions audit's P1 lesson).
   The agent's tools run as the person asking, so it can't reason over what they can't see.
4. **Read-only toward sources, metadata only.** Connectors read definitions, schemas, logs, and code; never warehouse
   data rows, never writes. The one exception is the change agent (O21), which writes only new branches and pull
   requests, through the GitHub App, in repos that opted in.
5. **People approve every action.** Plans are proposals; pull requests are reviewed and merged by people; nothing
   merges itself, and nothing touches a protected branch.
6. **Privacy of usage.** Query text often holds sensitive values: literals are stripped before anything is stored,
   usage is kept as aggregates, and per-person usage is shown only to admins.
7. **Incremental, idempotent, and budgeted.** Every collector and analyzer reads changes since its cursor and can be
   re-run safely; model work is cached by the hash of its inputs and capped by budgets (the existing ones).
8. **Measured.** "Thinks like a senior" is a test set of real seniors' questions and their answers (section 11), not
   a feeling.

## 4. Architecture

```
Collect (connectors)  ->  Extract (analyzers: facts, no AI)  ->  Connect (the Atlas: one graph)
   ->  Understand (profiles, systems, links, patterns; seniors confirm)  ->  Act (the architect agent, playbooks,
       migrations, changes)
```

New code, beside what exists:

| Module | What it holds | Builds on |
|---|---|---|
| `kl/atlas/` | The graph: nodes, edges, evidence, readers, resolution, and queries (paths, impact, neighborhoods) | `assets` and `lineage_edges` (K3), `links.py`'s identifier names |
| `kl/analyze/` | Analyzers: `repo.py`, `sql.py` (lineage), `scheduler.py`, `usage.py`, `cochange.py`, `owners.py` | The connectors' listings, `extract.py` |
| `kl/<source>.py` | New connectors (BigQuery usage, Control-M, Airflow, Snowflake...), one module each, as today | `docs/BUILD_PLAN.md`'s recipe |
| `kl/understand/` | Profiles (purpose cards), systems (clusters), patterns | The gateway, budgets, `claims.py` |
| `kl/architect/` | The agent: planner, tools, playbooks (`playbooks/*.yaml`), runs | MCP tools (A1), Ask, decisions, who to ask |
| `kl/migrate/` | Migration assessments: scope, inventory, waves, scores, plans | The Atlas, usage |
| `kl/changes/` | The change agent: sandboxes, conventions, pull requests | The GitHub App, the worker |
| `services/web/app/` | The screens (section 10) | The app's shell, components, `markdown.js` |

## 5. The data model

Migrations from the next free number, each with row-level security enabled, policies that check what the row is
about (never `org_id` alone for scoped data), and only the grants each role needs.

- **`atlas_nodes`**: `id`, `org_id`, `kind` (system, repo, service, job, folder, table, view, column, topic, api,
  dashboard, dataset, team, person, document, decision, incident, ...), `key` (a stable natural key, unique per org:
  `bq:proj.dataset.table`, `repo:github/acme/payments-api`, `ctm:INV_DAILY/LOAD_INV`, `kafka:orders.v1`), `name`,
  `attrs` jsonb, `acl_principals` (the union of its evidence's readers, kept by the worker), `first_seen`, `last_seen`,
  `gone_at` (left in place, marked, when its source stops listing it).
- **`atlas_edges`**: `id`, `org_id`, `src`, `dst`, `kind` (reads, writes, calls, produces, consumes, triggers,
  depends_on, builds, deploys, owns, documents, decided_in, member_of, caused, joined_with, changes_with),
  `confidence`, `acl_principals`, `first_seen`, `last_seen`, `gone_at`.
- **`atlas_evidence`**: `id`, `org_id`, `node_id` or `edge_id`, `tier` (`confirmed`, `observed`, `declared`,
  `documented`, `inferred`), `source` (a connection, a pipeline, a person), `ref` (a URI, file and line, job id, query
  fingerprint), `detail` jsonb, `acl_principals` (who may see this evidence: the readers of the file, page, or log it
  came from), `observed_at`. Readers of a node or edge are recomputed from its evidence.
- **`atlas_aliases`**: names an entity goes by across sources (`EDW.INV_DAILY`, `edw.inv_daily`, "the daily inventory
  table"), with evidence; extends Q2's entity aliases.
- **`usage_daily`**: per table (and column where the source has it) and day: reads, writes, distinct readers and
  applications (counted, not listed), bytes, failed queries, first and last read; per-person rows only in
  `usage_people_daily`, readable by org admins only.
- **`profiles`**: a node's purpose card (section 8): `summary`, `role`, `domain`, `system`, `confidence`, `evidence`
  (ids), `inputs_hash` (re-made only when its facts change), `model`, `status` (`proposed`, `confirmed`, `corrected`).
- **`atlas_reviews`**: a person confirming or correcting a profile, an edge, or a system membership: what, before,
  after, who, when. A correction becomes `confirmed` evidence.
- **`playbook_runs`**: who ran which playbook on what, its steps (each tool call, its result's evidence ids), its
  result, its confidence, its cost, its status.
- **`assessments`** and **`assessment_items`**: a migration's scope, its objects with their wave, value, effort, risk,
  retire flag, status, and links to pull requests.
- **`change_requests`**: the change agent's work: who asked, the repo, the plan, the sandbox run (tests, linters),
  the diff's summary, the pull request, its state.

Existing `assets` and `lineage_edges` move into the Atlas with a migration; keep views by their old names until every
reader has moved.

## 6. Collect: connectors for an enterprise

Each follows `docs/BUILD_PLAN.md`'s recipe (a fake, smoke checks, the gallery's form, a Terraform `kind`,
`docs/connectors/<source>.md` with exactly the read-only access it needs). The gallery groups them by category.

| Category | First | Then | What they give the Atlas |
|---|---|---|---|
| **Code** | GitHub, organization-wide (exists; scale it to thousands of repos) | GitLab, Bitbucket, Azure DevOps | Repos, files, owners, commits, pull requests, CI |
| **Warehouses** | **BigQuery** (metadata, `INFORMATION_SCHEMA.JOBS` usage) | Snowflake (`ACCESS_HISTORY`), Teradata (DBQL), DB2 (monitoring functions, event monitors), Databricks (system tables), Redshift, SQL Server (Query Store), Oracle | Tables, columns, views, procedures, usage, lineage from query history |
| **Schedulers** | **Control-M** (Automation API: folders, jobs, commands, schedules, in and out conditions, run history) | Airflow and Composer, Autosys, Tidal | Jobs, order, schedules, SLAs, failures, what each runs |
| **ETL and ELT** | dbt and Dataform (manifests, exists) | Informatica (PowerCenter exports, IICS), DataStage exports, SSIS packages, Fivetran, Airbyte | Transformations, mappings, table lineage |
| **Streaming** | Kafka (topics, Schema Registry) | Pub/Sub, Event Hubs | Topics, schemas, producers, consumers |
| **BI** | Looker | Tableau, Power BI | Dashboards and the tables they read: who consumes data |
| **Service catalogs** | Backstage `catalog-info.yaml` (from repos) | ServiceNow CMDB | Declared systems, owners, lifecycle |
| **Incidents** | PagerDuty | ServiceNow incidents, Jira incident projects | What broke, when, what it touched |
| **Docs, tickets, chat** | Exist (Confluence, Jira, Slack, Drive, Microsoft 365, Notion) | | History, decisions, people |
| **Catalogs** | Exist (Dataplex, Glue, Purview, Unity Catalog, DataHub) | | Descriptions, owners, lineage |

Names of logs, views, and APIs above are starting points: confirm each against the vendor's current documentation
before building its client. Usage connectors need elevated read access (BigQuery's organization-wide job listing, for
one); say exactly which in its connector doc, so an identity team can approve it.

## 7. Extract: analyzers (facts, no AI)

Each analyzer is incremental (a cursor or a content hash), idempotent, and writes nodes, edges, and evidence. What it
can't parse it records as `unparsed`, with a count per source shown in the app, never dropped silently.

- **Repositories** (`analyze/repo.py`), from the default branch, skipping vendored and generated code:
  languages; build files (Maven, Gradle, npm, pip and Poetry, Go modules); CI (GitHub Actions, Jenkinsfiles, GitLab CI)
  and what it builds and deploys; deploy definitions (Dockerfiles, Helm, Kubernetes manifests, Terraform and the
  resources it defines); interfaces exposed (OpenAPI, protobuf, AsyncAPI) and consumed (HTTP clients' base addresses
  and service names from configuration); topics produced and consumed; database access (SQL files, ORM configuration,
  JDBC addresses); Airflow DAG files and dbt projects; `CODEOWNERS`, `README`, contributing guides; activity (last
  commit, active contributors).
- **SQL lineage** (`analyze/sql.py`): table and column lineage from SQL anywhere (query logs, repos' SQL files, ETL
  scripts, stored procedures, jobs' scripts), with a parser that knows many dialects (sqlglot; add to the lockfile),
  handling CTEs, temporary tables, and views; a pluggable dialect for what it doesn't handle (DB2's, mainframe SQL), and
  everything unparsed counted.
- **Schedulers** (`analyze/scheduler.py`): Control-M folders and jobs into a job graph (in and out conditions as
  `triggers`); each job's command resolved to a script (in a repo when it lives in one) and parsed for what it reads
  and writes; schedules, SLAs, and run history (failure rates feed patterns).
- **Usage** (`analyze/usage.py`): warehouse query history into `usage_daily`: literals stripped, queries fingerprinted
  (one shape, many runs), reads and writes per table, joins (two tables queried together: `joined_with`), consumers
  counted; BI tools' queries attributed to their dashboards.
- **Co-change** (`analyze/cochange.py`): repos and files changed in the same pull request, the same ticket, or the same
  short window, repeatedly (`changes_with`).
- **Owners** (`analyze/owners.py`): `CODEOWNERS`, catalogs, recent active contributors, teams from the directory.

## 8. Connect and understand

**Connect** (`atlas/`): resolution matches the same thing across sources by its aliases (a table named one way in a
log, another in code, a third in a page); producers meet consumers (a package and its importers, a service and its
callers by address or service name, a topic's two ends, a table's writers and readers). Every match keeps its evidence;
a weak match is `inferred` with its confidence, and shown that way. Queries: paths between two nodes, impact (everything
downstream, to a depth), neighborhoods, all through recursive SQL with limits on depth and fan-out, all under
row-level security.

**Understand** (`understand/`), the only place a model works on the graph:

- **Profiles** (purpose cards) for repos, jobs, tables, and systems: what it is and is for, its role (service,
  pipeline, library, infrastructure, configuration, documentation, deprecated), its domain and system, from its facts
  and their evidence, with a confidence; re-made only when its inputs' hash changes; held to `claims.py`'s checks
  (after the production review's H4 fix), so a profile never states what its evidence doesn't.
- **Systems**: clusters of repos, jobs, and tables that call, feed, own, and change together (community detection on
  the graph's weighted edges), named from their members, confirmed by owners.
- **Links**: documents, tickets, and threads linked to the nodes they mention (entity resolution with the aliases).
- **Patterns**: unused objects (no reads in a window), hotspots (what changes most, what fails most, what incidents
  touch), fragile jobs (failure rates), single points of knowledge (one active contributor).
- **Review**: owners confirm or correct profiles, system memberships, and edges in the review queue (E2's, extended);
  a correction is `confirmed` evidence, the highest tier, and the next profile starts from it.

## 9. Act: the architect

**The agent** (`architect/`) works the way a senior does: plans its steps, gathers evidence with tools, checks its
claims, and says how sure it is. Its tools, each running as the person asking: `atlas.find`, `atlas.path`,
`atlas.impact`, `atlas.neighbors`, `usage.top`, `usage.of`, `search.documents`, `code.search`, `code.read`,
`owners.of`, `decisions.about`, `incidents.about`, `warehouse.describe`. A step budget and a cost budget per run; every
run recorded in `playbook_runs`, with each step's evidence; answers held to the claim checks.

**Playbooks** (`architect/playbooks/*.yaml`): a senior's checklist as steps the agent follows, each step's output
feeding the next, and each one tested in section 11:

1. **Migration assessment**: a scope (a system, a domain, a Control-M folder, a schema) into an inventory; dependency
   waves (nothing moves before what feeds it); value within each wave (reads, distinct consumers, critical dashboards,
   SLAs) against effort (complexity, size, unusual SQL, unparsed parts); objects to retire instead of migrate (unused);
   risks; a validation plan (row counts, checksums, parallel runs). It becomes an assessment (O18) people refine.
2. **Impact analysis**: what a change to this touches downstream, who owns it, who uses it, what broke last time.
3. **Flow trace**: how data gets from here to there, every hop with its evidence.
4. **System brief**: a new architect's introduction to a system: purpose, parts, flows, owners, decisions, risks.
5. **Change placement**: where a change should go (repo, module, files), how changes are made there (its
   conventions), and who reviews.
6. **Incident context** (after O25): what changed recently near what broke, and what depends on it.

**Migrations** (`migrate/`): an assessment's waves and items, editable by people (move an item, mark it retire, assign
it), kept current as the Atlas changes (an item whose dependencies changed is flagged), exportable as a document, and
linked to the pull requests that carry it out.

**Changes** (`changes/`): a person asks for a change; the agent:

1. finds the repo and files (change placement), and learns its conventions from its merged pull requests, contributing
   guide, linters, and tests;
2. works in a **sandbox**: an ephemeral container per run, a short-lived installation token for that one repo only
   (contents on a new branch, pull requests), network limited to the git host and package registries, no other
   secrets, resource and time limits;
3. runs the repo's own tests and linters there;
4. opens a **pull request** through the GitHub App: what and why, the evidence, the risks, the test results, and the
   request that produced it;
5. never merges, never pushes to a protected branch; people review and merge.

Repos opt in. Change kinds are an allowlist, starting narrow: documentation, configuration, a Control-M job converted
to an Airflow DAG skeleton, dbt model scaffolding, dependency bumps; widened as pull requests prove themselves
(acceptance rate per kind, shown to admins). The coding engine runs behind an interface in `changes/` (a model through
the gateway with file and command tools, or a coding agent run headless in the sandbox), so it can be swapped.

## 10. Screens

Every screen follows the app's existing shell, components, and voice; permissions apply to everything shown; each
gets browser tests.

1. **Sources** (the gallery, extended): grouped by category; each connection's health, freshness, and `unparsed` count.
2. **Atlas**: search any entity; a system's map (its members and flows); a lineage view between two nodes; filters
   by kind, domain, and system; an evidence panel on every node and edge (tier, where, when, confidence).
3. **Entity pages**:
   - **Repo**: its purpose card (confirm or correct), facts, interfaces in and out, dependencies, owners, the documents
     about it, recent changes, the usage of what it writes;
   - **Table**: schema, a usage chart, producers and consumers (jobs, repos, dashboards), lineage up and down, owners,
     documents, its migration status;
   - **Job**: schedule, conditions in and out, what it runs, what it reads and writes, run history and failures;
   - **System**: members, boundaries, flows in and out, owners, documents, decisions, hotspots.
4. **Usage**: what's used most, what's unused (retire candidates), trends, by domain and system.
5. **Migrations**: new assessment (choose a scope); the waves as a board; items with value, effort, risk, and retire;
   the validation checklist; export; each item's pull requests.
6. **Ask an architect**: the agent in conversation, its steps visible (the plan, each tool call, its evidence), its
   confidence, graph paths drawn inline; "make this an assessment" and "propose a change" from an answer.
7. **Playbook runs**: history, status, results, cost; run again.
8. **Changes**: requested changes, the sandbox's run (tests, linters), the diff, the pull request and its state.
9. **Review queue** (E2's, extended): purpose cards, system memberships, and edges to confirm or correct.
10. **Admin**: every pipeline stage's freshness and backlog, model costs against budgets, change kinds allowed, repos
    opted in, audit.

## 11. Evaluation: the synthetic enterprise, and seniors' questions

- **The synthetic enterprise** (O2): a generator (`kl/enterprise.py`, on the synthetic retailer) that builds a realistic company with known
  answers: domains (inventory, orders, finance...), a few thousand repos (services, pipelines, libraries,
  infrastructure, some deprecated, some vendored), a Control-M export with folders, jobs, conditions, and scripts, a
  warehouse's schemas and query history (with heavy, light, and unused tables, and literals to be stripped), dbt
  projects, BI dashboards, docs and tickets that mention it all, owners, incidents, and the ground truth: every real
  edge, every system's members, every table's usage rank, the right migration waves. Its size is a parameter
  (`--repos 2000`); its fakes serve it through the connectors (the fakes in `fake_llm.py`, or a sibling). O2 built the
  estate and all of its truth, and what the sources built so far send (OpenLineage, a dbt manifest, DataHub); each
  later item renders its own source's fixtures (a Control-M export, query history, repositories, dashboards) from the
  same truth, shaped by the vendor's current documentation, as section 2 says.
- **Seniors' questions** (`kl/architect/eval/*.yaml`): per domain, a question, the facts a right answer contains, the
  evidence it must cite, what it must not reveal to whom, and the playbook it exercises. Graded automatically (facts
  present, evidence cited, nothing forbidden), and some by people. Held to a baseline, as `kl/evals.py` is.
- **Measures**, recorded per run: edges found against the ground truth (precision and recall, per evidence tier);
  systems recovered; usage rank correlation; migration waves against the right ones; seniors' questions passed;
  pull requests accepted per change kind; time and cost per thousand repos.

## 12. Scale, operations, and security

- **Scale**: thousands of repos and millions of edges. Incremental everything (cursors, content hashes); the worker's
  queue partitioned per connection with a concurrency cap each; backpressure when a stage falls behind; the graph
  indexed for its queries (by `org_id`, kind, key, and both ends of edges); model work cached by input hash and only
  where facts changed. Targets, measured on the synthetic enterprise at 2,000 repos: a full first pass within hours,
  an incremental pass within minutes, and model cost per repo shown.
- **Operations**: freshness per source and stage, backlogs, `unparsed` counts, model spend, and change agent outcomes,
  in the admin screen and as telemetry; alerts when a stage stalls (the existing alerting).
- **Security**: everything in section 3; least-privilege credentials per connector, written down; sandbox isolation
  for the change agent; per-person usage admin-only; audit of every action; nothing a person can't see is used to
  answer them.

## 13. Build order

Phase 0 comes first: the platform must be sound before it's widened. Then each item in order, on its own branch.

### Phase 0: prerequisites
- [x] **O0.** From `docs/PRODUCTION_REVIEW.md`: the provider repository on a branch; the permissions audit's P1 to P3;
      H1 and H5 (the pool, timeouts, no calls inside transactions); H2 and H6 (one HTTP helper with retries, and
      incremental syncs); H4 (the claim checker). Proven, and recorded.

### Phase 1: the foundation
- [x] **O1.** The Atlas: the data model (section 5) with row-level security from evidence; `assets` and
      `lineage_edges` moved in (views kept); `atlas/` queries (find, path, impact, neighbors) with depth and fan-out
      limits; an API for them; smoke: a stranger sees no node or edge whose evidence they can't see.
- [x] **O2.** The synthetic enterprise: the generator, its ground truth, and fakes serving it; a `make enterprise`
      target to load it at a chosen size; the measures (section 11) as `make atlas-eval`.
- [ ] **O3.** The Atlas screen and entity pages (repo, table, job, system) on what exists, with the evidence panel.

### Phase 2: code at scale
- [ ] **O4.** GitHub organization-wide at thousands of repos: incremental, skipping vendored and generated code,
      within rate limits; measured at 2,000 synthetic repos.
- [ ] **O5.** The repo analyzer (section 7), producing nodes, edges, and evidence.
- [ ] **O6.** SQL lineage (sqlglot, its dialects, a pluggable one; unparsed counted), from repos first.

### Phase 3: warehouses and usage
- [ ] **O7.** BigQuery: metadata, and usage from `INFORMATION_SCHEMA.JOBS` (organization-wide where granted), into
      `usage_daily`, literals stripped; lineage from query history through O6.
- [ ] **O8.** The Usage screen; usage on table pages.
- [ ] **O9.** The next warehouse the target organization runs (Snowflake, Teradata, DB2, or another), then the rest.

### Phase 4: jobs and transformations
- [ ] **O10.** Control-M: folders, jobs, conditions, commands, schedules, run history; scripts resolved and parsed.
- [ ] **O11.** Airflow and Composer: DAGs, tasks, schedules, runs (from the API and from DAG files in repos).
- [ ] **O12.** ETL: Informatica and DataStage exports, SSIS packages; job pages complete.

### Phase 5: understanding
- [ ] **O13.** Resolution at scale (aliases across sources; producers meet consumers), measured against ground truth.
- [ ] **O14.** Profiles (purpose cards), held to the claim checks, re-made only when facts change, with costs shown.
- [ ] **O15.** Systems (clusters) and their names; documents, tickets, and threads linked to nodes.
- [ ] **O16.** Patterns (unused, hotspots, fragile jobs, single points of knowledge); the review queue extended for
      profiles, systems, and edges.

### Phase 6: the architect
- [ ] **O17.** The agent: planner, tools (section 9) running as the asker, budgets, recorded runs; the Ask an
      architect screen with its steps visible.
- [ ] **O18.** The migration assessment playbook and the Migrations screen (waves, items, export).
- [ ] **O19.** The impact, flow trace, system brief, and change placement playbooks; the Playbook runs screen.
- [ ] **O20.** Seniors' question sets for the synthetic enterprise's domains, a baseline, and CI holding to it.

### Phase 7: changes
- [ ] **O21.** The sandbox and the change agent: one repo's token, tests run, pull request opened; never merges.
- [ ] **O22.** Conventions learned from merged pull requests; change placement used to choose where.
- [ ] **O23.** The Changes screen; change kinds allowlisted per organization, repos opted in; acceptance per kind.

### Phase 8: breadth
- [ ] **O24.** Streaming (Kafka and Schema Registry), BI (Looker, then Tableau and Power BI), service catalogs
      (Backstage, ServiceNow).
- [ ] **O25.** Incidents (PagerDuty, ServiceNow): incidents linked to what they touched; the incident playbook.
- [ ] **O26.** Scale hardening on the synthetic enterprise at full size: the targets in section 12 met and recorded.

## 14. When unsure

As in `docs/BUILD_PLAN.md`: stop and ask in the pull request when a choice would widen who sees something, write to
a source, or add a dependency with a large footprint; otherwise choose the narrower option and say why. Ask, too,
before connecting anything real: this plan is proven on the synthetic enterprise first, and on a real company's
systems only with its people's approval.
