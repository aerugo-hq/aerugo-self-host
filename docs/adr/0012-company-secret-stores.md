# ADR 0012: Credentials in the company's own secret stores

Status: Accepted (2026-10-16)

## Context

A connection's credential is either handed over once and kept in Aerugo's OpenBao, or referenced where the team keeps
it (ADR 0011). Until now a reference could name a team's own OpenBao path or a GCP Secret Manager secret. Companies
that run on AWS or Azure, or that keep secrets in HashiCorp Vault, would have to copy each secret into one of those,
which is the copy ADR 0011 set out to avoid.

## Decision

1. **Three more kinds of reference**, each read at the moment of use and never stored: `hcvault://` (a KV v2 secret on
   the one Vault the deployment names), `awssm://` (AWS Secrets Manager), and `azkv://` (Azure Key Vault).
2. **Each is read as the runner itself.** Vault: its JWT auth, with the runner's identity token. AWS: a role the runner
   assumes with that token (`AssumeRoleWithWebIdentity`), every call SigV4-signed. Azure: an Entra app the runner
   federates into (workload identity federation, the token as the client assertion). No secret is needed to read a
   secret.
3. **Each is fenced to its team by the secret's name**, as GCP's are: `kl/<org>/<team>/` under Vault's mount,
   `kl_<org>_<team>_` in AWS (team names never hold underscores), and `kl--<org>--<team>--` in Key Vault (whose names
   allow only dashes; a team whose name holds `--` can't use it). A deployment also names what's allowed: Vault's
   address, AWS's regions and role, Azure's vaults and app. Each is off until set.
4. **These are the company's stores, not new infrastructure of Aerugo's**: nothing runs for them, and an unset one is
   simply refused.

## Consequences

- A team keeps one secret in one place, its own; rotating it there takes effect on the next sync.
- Whoever may create secrets with a team's prefix in an allowed store can give that team a credential, the same trust
  GCP references already carry; the store's own access control decides who that is.
- Each store's trust is set up once by the platform team (Vault's JWT role, the AWS role's trust policy, the Entra
  app's federated credential), written in `docs/connectors/secret-stores.md`.
