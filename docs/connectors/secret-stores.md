# Keeping a connection's credential in your own secret store

A connection's credential (an API token, a certificate, a key) can stay in a secret store your company already runs.
Aerugo keeps only a reference to it, and reads the secret when it syncs, as the runner itself. The secret's value is a
JSON object with the fields the connection's form asks for, for example `{"email": "...", "api_token": "..."}` for
Jira, or `{"token": "ntn_..."}` for Notion.

Each reference names a secret of one team only. Use the team's slug exactly.

| Store | Reference | Where the secret lives |
|---|---|---|
| OpenBao (Aerugo's own) | `openbao://secret/kl/<org>/<team>/connections/<name>` | the team's path in Aerugo's OpenBao |
| GCP Secret Manager | `gcpsm://projects/<project>/secrets/kl_<org>_<team>_<name>` | a project the deployment allows (`KL_GCP_SECRET_PROJECTS`) |
| HashiCorp Vault | `hcvault://<mount>/kl/<org>/<team>/<name>` | a KV v2 mount on the deployment's Vault (`KL_HCVAULT_ADDR`) |
| AWS Secrets Manager | `awssm://<region>/kl_<org>_<team>_<name>` | a region the deployment allows (`KL_AWS_SECRET_REGIONS`) |
| Azure Key Vault | `azkv://<vault>/kl--<org>--<team>--<name>` | a vault the deployment allows (`KL_AZURE_KEY_VAULTS`) |

## What the platform team sets up once

**HashiCorp Vault.** Enable the JWT auth method (at `jwt`, or set `KL_HCVAULT_AUTH_PATH`), trusting the issuer of the
runner's identity tokens (on GCP, `https://accounts.google.com`). Create a role (`KL_HCVAULT_ROLE`, `aerugo` by
default) bound to the runner's identity (`bound_subject`, its service account's unique id) and the audience
`KL_HCVAULT_AUDIENCE` (`vault`), with a policy that only reads `<mount>/data/kl/<org>/*`. Set `KL_HCVAULT_ADDR`.

**AWS Secrets Manager.** Create an IAM role (`KL_AWS_SECRETS_ROLE`) whose trust policy lets the runner assume it with
its identity token (as for Glue: `accounts.google.com` on GCP, or the cluster's OIDC provider on EKS), allowed only
`secretsmanager:GetSecretValue` on `arn:aws:secretsmanager:<region>:<account>:secret:kl_<org>_*`. Set the regions
(`KL_AWS_SECRET_REGIONS`).

**Azure Key Vault.** Register an Entra app (`KL_AZURE_CLIENT_ID`, in `KL_AZURE_TENANT_ID`) with a federated credential
for the runner's identity (issuer `https://accounts.google.com`, its subject, audience `api://AzureADTokenExchange`):
no client secret. Give it the Key Vault Secrets User role on each vault to allow (`KL_AZURE_KEY_VAULTS`), and nothing
else.

Each store stays off until it's set. Rotating a secret in its store takes effect on the connection's next sync.
