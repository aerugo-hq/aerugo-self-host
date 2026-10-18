---
type: Runbook
title: ORD_LOAD runbook (2023)
description: The old failure procedure for ORD_LOAD, from before the load was made idempotent in 2024.
status: deprecated
sources:
  - id: runbook
    resource: https://github.com/acme/edw-etl/blob/2023-12/runbooks/ord_load.md
x-kl:
  id: kb-ord-load-runbook-2023
  aliases: [old ORD_LOAD runbook, 2023 ORD_LOAD runbook]
  classification: internal
  audience: workspace/payments
  owners: ["group:payments"]
---
# ORD_LOAD runbook (2023)

Before 2024, a failed ORD_LOAD had to be cleaned up by hand [^runbook]: truncate the night's partition of ORDER_HIST, then rerun the job at 04:00. Rerunning without the truncate duplicated that night's orders.

This procedure was replaced in 2024, when the load was made idempotent.
