---
type: Policy
title: Order data retention
description: How long order records with personal information may be kept, in the EDW and on the EDP.
status: stable
verified: [{by: "human:dave", at: "2025-02-20T14:00:00Z"}]
sources:
  - id: policy
    resource: https://sharepoint.acme.test/legal/policies/order-data-retention
x-kl:
  id: kb-order-retention
  aliases: [retention rules, order retention, data retention policy]
  classification: confidential
  audience: workspace/legal
---
# Order data retention

Order records that include a customer's email or address may be kept for seven years after the order date, and are then deleted or anonymized [^policy]. This applies to the EDW and the EDP alike: when ORDER_HIST moves to BigQuery, only the last seven years may be backfilled, and older rows are anonymized before the move.

Legal owns this policy, and dave is the person to ask.
