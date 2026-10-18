---
type: Runbook
title: ORD_LOAD runbook
description: What to do when the nightly ORD_LOAD job fails, as of 2025.
status: stable
verified: [{by: "human:alice", at: "2025-05-02T08:00:00Z"}]
sources:
  - id: runbook
    resource: https://github.com/acme/edw-etl/blob/main/runbooks/ord_load.md
x-kl:
  id: kb-ord-load-runbook
  aliases: [ORD_LOAD runbook, ORD_LOAD failure, rerun ORD_LOAD]
  classification: internal
  audience: workspace/payments
  owners: ["group:payments"]
  relations:
    - {type: supersedes, target: /ord-load-runbook-2023.md}
---
# ORD_LOAD runbook

When ORD_LOAD fails, it retries once by itself at 03:00. If the retry fails too [^runbook]:

1. Page the Payments on-call engineer through the payments-data rotation.
2. Check the order system's export landed: the file for the night must be in the landing bucket.
3. Rerun ORD_LOAD from the scheduler with its date parameter. The load is idempotent, so a rerun never duplicates rows.
4. If it isn't done by 06:00, tell Finance analytics (bob) that the Sales dashboard will show the previous day.
