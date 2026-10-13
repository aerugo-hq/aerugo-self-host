---
type: System
title: Ledger service
description: The double-entry ledger behind every payment.
status: stable
sources:
  - id: runbook
    resource: https://github.com/acme/payments-api/blob/main/docs/incident-response.md
x-kl:
  id: kb-ledger
  aliases: [ledger, ledger service]
  classification: internal
  audience: workspace/payments
  relations:
    - {type: depends-on, target: /settlement-keys.md}
---
# Ledger service

The ledger records every payment as balanced debit and credit entries. Nightly reconciliation compares it with the bank statements and flags any mismatch for review [^runbook].
