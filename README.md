# Aerugo

An open-source, permission-aware knowledge platform, and this repository is everything needed to run it in your own environment. Teams connect their sources. Knowledge lives in two forms: a curated layer in Open Knowledge Format (OKF), reviewed through pull requests, and a hybrid-search evidence layer for the long tail. Every read goes through one policy decision point that mirrors source permissions.

The design and decisions are in [`docs/design/v1.md`](docs/design/v1.md) and [`docs/adr/`](docs/adr/); where it stands and what comes next are in [`docs/design/build-plan.md`](docs/design/build-plan.md). Run `make web`, then open http://localhost:3000 and sign in as `alice` / `alice-dev-only` (or bob, carol, or dave, each with `<name>-dev-only`) to see the map change with who you are. Run `make obs` for Grafana at http://localhost:3001, Jaeger at http://localhost:16686, and Prometheus at http://localhost:9090.

## M0 quick start

You need Docker with Compose v2, about 4 GB of free RAM, and `make`.

```bash
make up      # builds the API image and starts Postgres, OpenFGA, OpenBao, Keycloak, and the API
make smoke   # runs the smoke suite inside the Compose network
```

Keycloak takes 30–60 seconds on first boot; the smoke suite waits for it. Required checks must all pass; the optional live checks below report SKIP until you configure them.

| Service | Local URL | What it is |
|---|---|---|
| API | http://localhost:8000/docs | FastAPI, M0 endpoints |
| MCP server | http://localhost:8090/mcp | Streamable HTTP; tools `search` and `ask`; accepts only `kl-mcp` tokens |
| Keycloak | http://localhost:8081 (admin / `admin-dev-only`) | Dev IdP, standing in for Okta, Entra, or Google |
| OpenFGA | not published (`openfga:8080` inside the network) | Relationship-based authorization |
| OpenBao | http://localhost:8200 (token `dev-root-token`) | Secrets backend, dev mode |
| Postgres | `localhost:55432`, db `kl` | pgvector, RLS, full-text search |

## What the smoke suite proves

| Area | Checks |
|---|---|
| Wiring | Postgres has pgvector; the API reaches Postgres, OpenFGA, OpenBao, and the IdP |
| Database isolation | The app role sees nothing without request context; sees exactly its ACL rows with context; sees nothing for another org; cannot delete audit rows |
| Identity | Missing and forged tokens are rejected (including HS256 algorithm confusion); groups come from the IdP token; workspaces and admin rights come from OpenFGA |
| Scope vs. permission | Workspace scope works; the org toggle widens scope but never permissions (alice, bob, and carol each see exactly their set); private workspaces return 404 to non-members and stay visible to members |
| Policy point | A document the index allows but OpenFGA denies (seeded drift) never reaches anyone, and the drop is audited |
| Secrets | A workspace admin registers a model; the key lands in OpenBao while Postgres and the audit log hold only a reference; non-admins get 403 |
| Connectors and jobs | The worker role sees nothing without org context; the worker claims and finishes queued jobs; non-admins can't connect sources to another team's workspace |
| Identities | A source identity link (bob = `octocat`) expands only its own user; carol, unlinked, gets none |
| Webhooks | Unsigned and wrongly signed GitHub deliveries are rejected (401), even with no GitHub App configured |
| Semantic search | Every seed chunk gets embedded by the local default model; a question sharing no keywords still finds the runbook; an accounting question ranks the close checklist first; matches by meaning obey the same permissions |
| Ask | Answers cite visible sources, passed to the model as numbered blocks marked untrusted; no visible sources means no model call; models in workspaces you're not in can't be used or discovered; confidential text never reaches a model that may not receive it (a test-only model records every prompt to prove it) |
| MCP | No token gets 401 pointing to protected-resource metadata (naming Keycloak); an API token is refused (audience binding); search and ask through MCP use the caller's permissions and the same egress path |
| OKF concepts | Every seed document becomes a bronze concept that passes OKF v0.2 validation; a concept is readable exactly by its source's readers (private workspaces stay private, drift is dropped, unknown keys get 404); history shows each version pinned to its source revision; the validator catches broken concepts and enforces the audience rule |
| Curated and graph | Seed bundles index as silver and gold concepts with their relations as graph edges; a reviewed playbook ranks above the runbook it summarizes; an alias finds its concept first; search follows a relation out of its scope to a concept the caller can see, never into a private workspace or past a narrower audience; MCP `get_concept` answers readers only; knowledge CI rejects missing or duplicate ids, secrets, and card numbers without echoing them |
| Compiler | Only a workspace admin compiles, with that workspace's own model; a draft's audience is the org or a group on every source's reader list, and others are left out; the draft passes knowledge CI, cites pinned sources, and can't claim review; a proposal is readable by its audience and nobody else; a draft citing a source it wasn't given is rejected and not stored |
| Curator | A PR opens only if every reader of the knowledge repo is linked to someone who can read every source (unlinked or unentitled readers block it); only an admin of the proposal's workspace can request one |
| Knowledge map | Each user's graph holds exactly what they can see (drift, narrower audiences, and private teams all respected); edges join only visible nodes and flag cross-team links; totals and team clusters count visible nodes only; a past date replays only what existed then; a private team's map is 404 to non-members |
| Admin reads | A team's members list its models with no credential or reference in sight, and non-members get 404; a workspace's admins read its audit trail, which holds only that workspace's events, while members who aren't admins get 403 and anyone outside a private team gets 404 |
| Team purge | On a throwaway team: only an admin can request a purge, with the slug typed again, and it can be cancelled inside the delay window; once it runs, the team's documents, chunks, embeddings, concepts, model and its secret (every version), and authorization tuples are all gone, a document it shared stays with the other team, and the audit log holds a deletion record |
| Optional | A live model call through the registry; GitHub App authentication; syncing the App's repos (Markdown pinned to commit SHAs, ACLs holding only GitHub's readers, the App key never in Postgres); GitHub's permissions deciding who finds the synced docs (alice = your `KL_DEV_GITHUB_LOGIN` finds them, bob, carol, and dave don't); a signed push webhook re-syncing its connection while a replayed delivery does nothing; the scheduler re-syncing a stale connection with no event at all; synced docs becoming bronze concepts pinned to their commit and readable only by their GitHub readers; a `*-knowledge` repo syncing as curated concepts pinned to its commit, with its relations as graph edges; the curator opening a proposal as a PR in that repo, which once merged is indexed at the merge commit with its history running from PR to merge to indexed; the web app (after `make web`) signing alice in with PKCE, keeping her tokens server-side, refusing anonymous and cross-site requests, and serving her map within her permissions; telemetry (after `make obs`) tracing one ask as one trace through every stage, exporting drift and egress decisions as metrics, and carrying no document text, question, answer, or token |

The seed data is one org (`acme`) and four workspaces:

| Workspace | Discoverability |
|---|---|
| `payments` | org-wide |
| `finance` | org-wide |
| `commons` | org-wide |
| `legal` | private |

There are four users, all with password `<name>-dev-only`:

| User | Groups | Notes |
|---|---|---|
| alice | payments | payments admin |
| bob | finance | |
| carol | payments, finance | |
| dave | legal | |

The table at the top of `deploy/postgres/sql/03-seed.sql` shows who should see what.

## Optional live checks

**A real model call.** Fill in the `KL_SMOKE_*` values in `.env` (created by `make up`).

- **Ollama on your machine:**

  ```
  KL_SMOKE_PROVIDER=openai_compatible
  KL_SMOKE_BASE_URL=http://host.docker.internal:11434/v1
  KL_SMOKE_MODEL=<a model you've pulled>
  KL_SMOKE_API_KEY=ollama
  ```

- **Anthropic:**

  ```
  KL_SMOKE_PROVIDER=anthropic
  KL_SMOKE_MODEL=<model id from your Console>
  KL_SMOKE_API_KEY=<key>
  ```

The key goes through the API into OpenBao like any other registration. It never touches Postgres.

**GitHub App.** Create a free GitHub org for testing, then run:

```bash
make github-app ORG=<your-org>
```

1. Open the printed URL and click **Create GitHub App**. This uses GitHub's manifest flow, so the App is created on the org with exactly the permissions it needs: Contents, Metadata, and Members, all read-only.
2. The App's ID and private key are written to `.secrets/`, which is gitignored.
3. Use the install link on the confirmation page to install the App on your test repos.
4. Run `make smoke`. The GitHub check should go from SKIP to PASS.

M1 builds the connector on top of this App.

**Real GitHub webhooks (optional).** Freshness doesn't depend on them: the worker re-syncs every connection at least every `KL_RESYNC_MINUTES` (default 10). Webhooks cut that to seconds. GitHub can't reach `localhost`, so relay through smee.io:

1. Open https://smee.io/new and copy the channel URL.
2. In your App's settings (org settings, then GitHub Apps, then the App), under **Webhook**:
   - tick **Active**;
   - paste the channel URL;
   - leave the secret as it is, because the manifest flow already set it to the one in `.secrets/`;
   - save.
3. Under **Permissions & events**, subscribe to: Push, Member, Membership, Organization, Repository, Team, and Team add.
4. Relay to the API:

   ```bash
   docker run --rm node:22-alpine npx --yes smee-client --url <channel URL> --target http://host.docker.internal:8000/v1/webhooks/github
   ```

5. Push a change to a sandbox repo. `docker compose logs -f worker` shows the sync within seconds.

**Test repos.** `make sandbox-github ORG=<your-org>` creates three private repos in your org from `sandbox/github/` (`payments-api`, `finance-close`, `handbook`) and pushes their docs. It needs the GitHub CLI, installed with `winget install --id GitHub.cli` and logged in once with `gh auth login`. Repos that already exist are skipped.

## CI

`.github/workflows/smoke.yml` runs `make up` and `make smoke` on every push to `main` and every PR. Make it a required status check so nothing merges unless the permission checks pass.

## Commands

| Command | Does |
|---|---|
| `make up` | Build and start the stack |
| `make smoke` | Run the smoke suite |
| `make github-app ORG=<org>` | Create the GitHub App (one click) |
| `make sandbox-github ORG=<org>` | Create and push the three test repos |
| `make ps` / `make logs` | Inspect |
| `make down` | Stop (keeps data) |
| `make reset` | Stop and delete all volumes (re-seeds next `up`) |

## Troubleshooting

- **A port is in use or refused** (on Windows: "forbidden by its access permissions"): Windows may have reserved it (see `netsh interface ipv4 show excludedportrange protocol=tcp`). Set a free port in `.env` (`KL_API_PORT`, `KL_KEYCLOAK_PORT`, `KL_OPENBAO_PORT`, `KL_POSTGRES_PORT`, `KL_SETUP_PORT`) and run `make up` again.
- **`openfga-migrate didn't complete successfully`:** run `docker compose logs openfga-migrate`. If it says the connection was refused, Postgres wasn't really up yet; the TCP healthcheck prevents this. If it says the password failed or the `openfga` database doesn't exist, the init scripts failed. Run `docker compose logs postgres`.
- **The worker check fails:** run `docker compose logs worker`. The worker needs `provision` to finish first, and reconnects on its own if Postgres restarts.
- **`provision` exits non-zero:** run `docker compose logs provision`. OpenFGA prints exactly which part of the model or tuples it rejected.
- **Token errors in smoke:** Keycloak is probably still importing the realm. Re-run `make smoke`, or check `docker compose logs keycloak`.
- **You changed SQL or the realm:** run `make reset && make up`. Init scripts only run on an empty database.

## Security notes for M0

Everything here is dev-only:

- OpenBao runs in dev mode with a root token.
- Keycloak allows the password grant for the CLI client so tests can get tokens.
- All ports bind to `127.0.0.1`.

M1 replaces these with production patterns (AppRole or Kubernetes auth for OpenBao, authorization code with PKCE for humans, workload identity for services). The invariants in [`CLAUDE.md`](CLAUDE.md) already apply.

## Try Ask with a local model

Ask needs a model to answer. Without an API key, run one inside the stack:

```
make up
make llm        # downloads qwen2.5:1.5b once (about 1 GB), checks it answers, registers it in every team
make demo       # optional: the migration company, so there's something real to ask about
make web
```

Then open Ask, choose `<your team>/local` as the model, and ask. The model runs in an Ollama container (`--profile llm`); nothing sent to it leaves your machine, so it may receive sources up to `confidential`. On a laptop CPU the first answer takes longest while the model loads, and later ones take roughly 10 to 60 seconds, depending on how many sources a question draws on.

For better answers, pick a bigger model; running `make llm` again switches every team to it:

```
make llm LLM_MODEL=qwen2.5:3b    # about 2 GB
make llm LLM_MODEL=llama3.2:3b   # about 2 GB
```

A small model is a useful test in itself: watch whether it cites its sources. An answer that cites nothing is marked low confidence, whatever it says.

## License

Proposed Apache-2.0 with a CLA (decision D12 in the design doc). Not yet chosen; don't accept outside contributions until it is.
