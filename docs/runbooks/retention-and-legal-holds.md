# Runbook: retention and legal holds

Org admins decide how long records are kept and freeze them when the law requires. The worker applies retention hourly and records every run.

## Setting retention

A policy keeps one kind of record for a number of days: documents (for the whole org or one team, and optionally one classification), concept versions, audit events, or model usage. No policy means keep forever, and the most specific policy wins.

```bash
# Keep payments' confidential documents for a year
curl -X PUT "$API/v1/retention/policies" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"workspace": "payments", "target": "documents", "classification": "confidential", "keep_days": 365}'
```

Things to know before you set one:

- A document is removed only when **every** team holding it has expired it, and its age counts from when it was last seen. A document its source still has keeps being re-synced, so it never expires here: retention removes what its source stopped sending.
- A concept's current version is never removed, only its older versions.
- The audit trail can't be kept less than 30 days.
- Removal is permanent in the live database. Copies stay in backups until the last full backup holding them expires.

To preview, run it now and read the counts: `POST /v1/retention/apply`, then `GET /v1/retention` shows the run.

## Placing a legal hold

When litigation, an investigation, or a regulator requires records to be kept:

```bash
curl -X POST "$API/v1/legal-holds" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"workspace": "legal", "reason": "Matter 2026-14: preserve all records"}'
```

Leave `workspace` out to hold the whole org. While a hold is active, retention removes nothing it covers, and a held team can't be purged (the request is refused with 409). Record the hold's id with the matter.

## Releasing a hold

When counsel says the obligation has ended: `POST /v1/legal-holds/{id}/release`. The hold stays on record as released, with who released it and when. Retention resumes on its next run; anything already past its policy is removed then.

## Evidence for auditors

- `GET /v1/retention`: every policy (who set it, when), every hold (active and released), and the last 20 runs with what each removed.
- Audit events `retention.policy_set`, `retention.policy_removed`, `legal_hold.placed`, `legal_hold.released`.
