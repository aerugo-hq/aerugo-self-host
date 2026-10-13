# Control map

How knowledge-layer supports the controls that SOC 2, ISO/IEC 27001:2022, GDPR, and the HIPAA Security Rule ask for, where the evidence lives, and which smoke check proves it on every run. This map shows how the platform supports each control; it isn't a certification. An organization's own policies, people, and processes complete every control here, and the gaps at the end are as much a part of this map as the rows.

Smoke checks are named by the start of their description in `make smoke` output.

## Access control and least privilege

| Controls | How the platform supports them | Evidence | Proved on every run by |
|---|---|---|---|
| SOC 2 CC6.1 · ISO A.5.15, A.8.3 · HIPAA §164.312(a)(1) | Every read passes row-level security, then OpenFGA, then the audience rule; access is denied by default, and hidden content answers 404, not 403 | Audit trail per team (`GET /v1/workspaces/{ws}/audit`); `docs/security/threat-model.md` | "RLS: …", "Scope: …", "Private workspace: 404 …", "ACL drift: …" |
| SOC 2 CC6.2, CC6.3 · ISO A.5.18 · HIPAA §164.308(a)(4) | Team membership and roles live in OpenFGA; team admins grant and revoke; access follows on the next request | `GET /v1/access-review`: every team's admins and members, groups expanded, documents by classification | "Teams: …", "Access reviews: …" |
| ISO A.8.2 | Org admins alone set budgets, rate limits, retention, legal holds, and erasures; team admins manage only their teams | Audit events `budget.set`, `limits.set`, `retention.policy_set`, `legal_hold.placed`, `subject.erased` | "Budgets: …", "Retention: only org admins …", "Data subjects: erasing …" |
| ISO A.8.5 · HIPAA §164.312(d) | Sign-in through the identity provider (OIDC); tokens are signature-checked and bound to their audience | Keycloak realm configuration | "API rejects missing tokens and forged ones …", "MCP: a token issued for the REST API is refused …" |

## Confidentiality and data protection

| Controls | How the platform supports them | Evidence | Proved on every run by |
|---|---|---|---|
| SOC 2 C1.1, CC6.7 · ISO A.8.12 | Every document carries a classification; each model has an allowlist, and a source it may not receive is withheld before the call | Withheld counts in ask responses and alerts (`withheld_surge`) | "Egress: confidential text never reaches a model …" |
| ISO A.5.34 · GDPR Art. 25 | Documents that look like they hold personal data are flagged with a suggested classification; admins reclassify or dismiss; flags hold counts, never values | `GET /v1/personal-data`; audit events `personal_data.reclassify` and `personal_data.dismiss` | "Personal data: …" |
| ISO A.8.24 · HIPAA §164.312(a)(2)(iv), §164.312(e)(1) | Credentials live in OpenBao, never Postgres; stored originals are encrypted under per-team Transit keys; the backup repository is AES-256 encrypted | `kl/originals.py`, ADR 0006 | "Secrets: …", "Per-team keys: …", "Backups: …" |
| GDPR Art. 5(1)(c) | Telemetry and alerts carry ids and counts, never content, questions, or tokens | ADR 0003, ADR 0005 | "Optional: exported telemetry holds no document text …", "Alerts: …" |

## Retention, deletion, and data subjects

| Controls | How the platform supports them | Evidence | Proved on every run by |
|---|---|---|---|
| SOC 2 C1.2, P4.2, P4.3 · ISO A.8.10 · GDPR Art. 5(1)(e) | Retention policies per team and classification; legal holds override them; purging a team deletes everything it owns and destroys its encryption key | `GET /v1/retention` (policies, holds, and each run's counts); `kl.backup_drills` | "Retention: an expired document disappears …", "Purge: …" |
| SOC 2 P5.1 · GDPR Art. 15, Art. 20 | A person exports everything held about them, with everyone else redacted; org admins can export on their behalf | Audit events `subject.export` | "Data subjects: alice's export …" |
| GDPR Art. 17 | Erasure removes a person's access and identity links and replaces their name everywhere with an irreversible pseudonym; audit entries are kept, pseudonymized | Audit event `subject.erased` (pseudonym only) | "Data subjects: erasing a person …" |
| ISO A.5.33 · HIPAA §164.312(c)(1) | The audit trail is append-only for the application and kept at least 30 days under any policy | Grants in `deploy/postgres/02-schema.sql`; the retention floor in migration 0020 | "Audit log is append-only for the app role", "Retention: only org admins … at least 30 days …" |

## Logging, monitoring, and incident response

| Controls | How the platform supports them | Evidence | Proved on every run by |
|---|---|---|---|
| SOC 2 CC7.2, CC7.3 · ISO A.8.15, A.8.16 · HIPAA §164.308(a)(1)(ii)(D), §164.312(b) | Every read, change, and refusal is audited; alerts in the app fire on permission drift, server errors, failing or stale syncs, failing jobs, withheld surges, spent budgets, and a stopped worker | Alerts page and `GET /v1/alerts`; audit trails | "Alerts: …" |
| SOC 2 CC7.4, CC7.5 | Each alert has a response in the incident runbook | `docs/runbooks/alerts-and-incidents.md` | — |

## Availability, backup, and recovery

| Controls | How the platform supports them | Evidence | Proved by |
|---|---|---|---|
| SOC 2 A1.2, A1.3 · ISO A.8.13 · HIPAA §164.308(a)(7)(ii)(A), (B), (D) | Continuous, encrypted WAL archiving and pgBackRest backups; a restore drill restores into a throwaway database and checks it | `make backups`; `kl.backup_drills`; ADR 0006; `docs/runbooks/restore.md` | Smoke: "Backups: Postgres archives …"; `make restore-drill` |
| SOC 2 A1.1 | Rate limits per person, team, and org, and monthly model budgets | `GET /v1/limits`; budget alerts | "Rate limits: …", "Budgets: …" |

## Change management

| Controls | How the platform supports them | Evidence |
|---|---|---|
| SOC 2 CC8.1 · ISO A.8.25, A.8.29, A.8.32 | Every change goes through a pull request and the smoke suite in CI; architecture decisions are recorded as ADRs; every table ships with its row-level security in the same migration | `.github/workflows/smoke.yml`, `docs/adr/`, `CLAUDE.md` invariants |

## Gaps

- **Deployment controls** (TLS between services, network segmentation, real secret-store unsealing, a KMS for keys, a full-cell restore drill) arrive with slice E. Until then, the threat model's dev-credential gap stands.
- **OpenBao's own backup.** Transit keys and credentials live in OpenBao, not Postgres; a Postgres backup without them can't open encrypted originals. In dev, back up the `kl-openbao` volume alongside (see the restore runbook); in production this is the secret store's snapshot or KMS seal.
- **Organizational controls** (security awareness, HR screening, vendor management, physical security, breach notification under GDPR Art. 33) are the organization's. The audit trail and alerts support detecting and investigating a breach; the notification process isn't software.
