---
type: Standard
title: EDP platform standards
description: The rules every table follows when it moves from the EDW to BigQuery, from naming to cutover.
status: stable
verified: [{by: "human:olivia", at: "2025-03-04T15:00:00Z"}]
sources:
  - id: standards
    resource: https://confluence.acme.test/display/EDP/Platform+standards
x-kl:
  id: kb-edp-standards
  aliases: [EDP standards, platform standards, cutover standards, cutover checklist]
  classification: internal
  audience: org
  relations:
    - {type: supersedes, target: /edp-standards-2024-draft.md}
---
# EDP platform standards

Tables on the EDP live in BigQuery datasets named by domain, such as edp.orders and edp.customers. Dataform models build them, and Composer DAGs schedule them [^standards].

## Cutover

1. Dual-run the old load and the new model for at least 30 days.
2. Reconcile row counts and totals every day. A difference above 0.1 percent blocks the cutover.
3. Repoint every reader (dashboards, extracts, and jobs) to the new table, and confirm the switch with each reader's owner.
4. Retire the old load, then archive the EDW table.

## Data retention

Order and customer data with personal information follows Legal's retention rules. Before backfilling history into BigQuery, confirm with Legal how many years may be kept.
