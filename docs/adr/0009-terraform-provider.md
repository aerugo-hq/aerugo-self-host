# ADR 0009: Aerugo's own Terraform provider

**Status:** Accepted. Decided: customers configuring their own org use it first; it covers every setting an org manages, delivered in phases; its code lives in its own repository; the product and the provider are named **Aerugo** (Latin for verdigris, the patina of copper; Verdigris stays the name of the design system).

## Context

Organizations configure Aerugo by hand today: teams and their members, the models each team may ask with and their budgets, the sources that bring knowledge in, and the compliance rules that keep it. Platform teams manage everything else as code, reviewed in pull requests and reproducible across environments, the way they manage a cloud with its Terraform provider beside its console. They'll expect the same here.

This is about managing *Aerugo itself* as code, through its API. Deploying Aerugo's own infrastructure is a separate discussion (deployment, slice E; the SaaS control plane, slice Q).

## Decision

**One provider, one API, two ways to run.** The provider talks only to the public `/v1` API, so every change it makes passes the same gates, audit, and validation as a change made in the app. Whether an org runs Aerugo in its own cloud or as our SaaS changes only the provider's `endpoint` and where its service account comes from; the Terraform files are otherwise identical, so an org can move between the two without rewriting them.

**Names.** The provider's name is `aerugo`, so resources read `aerugo_team`, `aerugo_model`, `aerugo_secret_store`, and so on. The registry address is `<GitHub org>/aerugo`. The GitHub name `aerugo` belongs to an individual's account, so the org will be `aerugo-hq` (or similar, once claimed), making the address `aerugo-hq/aerugo`; the repository is `terraform-provider-aerugo`, as the Terraform and OpenTofu registries require. Before launch: claim the org, check the domains, and run a proper trademark search. A first-pass check found the name used only by unrelated open-source projects (an embedded real-time operating system from a European Space Agency activity, and an archived window manager).

**It signs in as a service account** (slice T1, built): an OAuth client using the client-credentials flow, registered by an org admin. Provider settings: `endpoint`, `token_url`, `client_id`, `client_secret`, or the same from environment variables.

**Secrets never enter Terraform state, and ideally never pass through Terraform at all.** Two ways, both supported:

1. *Pass-through:* the customer's own cloud provider (google, aws, azurerm, vault) reads a secret, and ours receives it as a write-only argument (Terraform 1.11 and later; OpenTofu's equivalent). It never lands in state, but it passes through the Terraform run. A version argument, such as `api_key_version`, triggers a rotation.
2. *Reference (slice S1):* ours receives only the secret's address in the customer's own secret manager, and Aerugo reads the secret when it needs it. The secret never touches Terraform or our database, and rotating it in their manager just works.

**It never manages knowledge itself** (documents and conversations come in through sources), people's own identity links, or actions on people such as data-subject erasure, which are one-time decisions rather than declared state.

## Secret stores (S1): all four, one interface

An `aerugo_secret_store` names where a customer's secrets live; references then point at a store and a secret's name. No long-lived credential is ever exchanged between Aerugo and the customer: every store trusts Aerugo through an identity.

| Store | Self-hosted: how Aerugo signs in | SaaS: how the org's cell signs in to *their* store |
|---|---|---|
| Google Secret Manager | The deployment's own service account | Workload identity federation: their service account trusts the cell |
| AWS Secrets Manager | The deployment's IAM role | A role in their account the cell assumes, guarded by an external ID |
| Azure Key Vault | A managed identity | A federated credential on their app registration, trusting the cell |
| HashiCorp Vault or OpenBao | AppRole or Kubernetes auth | JWT auth, trusting the cell's identity issuer |

A secret is read only when it's used, never returned by the API and never logged; each read is audited by store and name, never by value. Vault and OpenBao are tested for real (the development stack runs OpenBao); the three clouds against faithful fakes in smoke, plus optional checks against real accounts when credentials are provided.

## The app and Terraform, side by side (T2 and U1)

- **One source of truth:** both write through the same API. There's no separate "Terraform mode".
- **Ownership** (T2, started): a change sent with `Aerugo-Managed-By: terraform` (and, optionally, `Aerugo-Managed-Workspace: <name>`) records Terraform as that resource's manager, and every read returns it as `managed_by`. A change made by hand keeps the record, because to Terraform it's drift that its next plan shows; removing the resource removes it. A malformed header is refused. Built for teams, members, models, and budgets; each later resource gains it as the provider covers it.
- **In the app** (U1): a resource Terraform manages says "Managed by Terraform" and warns that changes will be undone on the next apply; an org setting can lock those fields. A resource created by clicking offers "Use in Terraform": its resource block and an import block, so it can be adopted without being recreated. Every resource has one Properties panel with its ID, a copy button, who created it, and what manages it.

## Every resource, and what the API needs for it

Terraform reads each resource on every plan to detect drift, and updates in place where it can, so each needs create, read, update, delete, and import.

| Resource | Today | Needs |
|---|---|---|
| `aerugo_service_account` | Built in T1: register, read, update, remove | Nothing |
| `aerugo_team` | Create, read by slug (T2), update; removal is the asynchronous purge | The provider waits for a purge to finish |
| `aerugo_team_member` | Set, list, remove; ownership recorded (T2) | Nothing |
| `aerugo_model`, `aerugo_model_budget` | Create, list, update, remove, cap; 409 on a duplicate; ownership recorded (T2) | Nothing |
| `aerugo_connection_github`, `_google_drive` | Create, read | **Update (rotate credentials) and delete (with its documents)**: the rest of T2 |
| `aerugo_repo_route` | Set, read through the connection | Nothing |
| `aerugo_pipeline` | Create, list, remove | Nothing (a change replaces it) |
| `aerugo_import` | Upload, list, remove | An archive path and its hash, so a new export is a new upload |
| `aerugo_retention_policy`, `aerugo_legal_hold` | Set, list, remove; place, list, release | Nothing |
| `aerugo_alert_webhook`, `aerugo_reranker`, `aerugo_google_signin` | Set, read, remove | Nothing |
| `aerugo_secret_store` | Not yet | Slice S1 |

Data sources: `aerugo_team`, `aerugo_status`. Everywhere a resource is managed: 404 for a missing object and 409 for a duplicate, never a 500. The API's own description, `GET /v1/openapi.json`, generates the provider's client, so the two can't drift apart.

## Phases

1. **T1 · Service accounts** (built).
2. **T2 · API completeness and ownership** (started: team by slug, ownership for teams, members, models, and budgets). Next: connection update and delete.
3. **S1 · Secret stores and references**, all four.
4. **U1 · "Managed by Terraform", IDs, and "Use in Terraform"** (built): a team Terraform manages says so at the top of its panel; managed members and models wear a Terraform tag; every panel ends with a collapsed "IDs and Terraform" section listing each resource's ID (with a copy button) and what manages it, plus, for whatever was made by clicking, the configuration with import blocks that adopts it as it is. A model's key never appears: the snippet points at an ephemeral variable. Org admins manage service accounts on the Developer page.
5. **T3 · `terraform-provider-aerugo`** (built): teams, members, models, budgets, and service accounts, with unit tests in CI and acceptance tests against the development stack; `examples/complete` builds an org from scratch.
6. **The end-to-end demo:** `terraform apply` and see it in the app; change something in the app and watch `plan` catch it; import a team created by clicking; destroy cleanly.
7. **T4 · Sources**, then **T5 · compliance and org settings** (and service accounts themselves); signed releases to both registries.

## Service accounts (T1)

- A token is a service account's when it follows Keycloak's convention: its user is `service-account-<client>`, and that client is the token's authorized party.
- The REST API refuses it (401) unless an org admin has registered that client and it's enabled, checked on every request. The MCP server refuses service accounts entirely: assistants act for people.
- A service account gets only its own principal, never the org-wide one, so it reads no documents unless granted. It holds roles like a person: org admin if registered with it, team roles through the usual members API.
- A person can never sign in with a name starting `service-account-`, so nobody can inherit one's roles.
- Registering, changing, and removing one is audited; removing one also removes every role it held.
- The development realm has a `kl-terraform` client for building the provider (its secret is a development value, never a real one).

## Testing and release

- **Acceptance tests** run against the development stack (`make up`) in CI: create, read, update, import, and destroy for each resource, and a drift test (change a setting in the app; the next plan must show it).
- **Releases** use GoReleaser with signed checksums, published to the Terraform and OpenTofu registries.

## Consequences

- Platform teams can review Aerugo changes in pull requests, reproduce an org across environments, and roll back.
- Every API object the provider manages becomes a compatibility promise: breaking changes need a new API version or a provider major version.
- Service accounts are powerful, so they're opt-in, least-privilege, audited by name, and removable at once.
- The ownership mark is informational, not a lock: anyone who may change a resource may also send the header. It can't grant anything; locking managed fields is a separate, deliberate org setting (U1).
- Internal names (the `kl` Python package, database roles, `KL_` settings) stay as they are; only what people see is named Aerugo.
