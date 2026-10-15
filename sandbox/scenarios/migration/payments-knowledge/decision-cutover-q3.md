---
type: Decision
title: Cut over order history in Q3 2025
description: Decided on 12 June 2025, to dual-run for 30 days and cut over in Q3 2025, replacing the Q2 plan.
status: stable
verified: [{by: "human:alice", at: "2025-06-13T09:00:00Z"}]
sources:
  - id: comment
    resource: https://jira.acme.test/browse/EDP-412?focusedCommentId=1031
x-kl:
  id: kb-decision-cutover-q3
  aliases: [cutover decision, Q3 cutover, order history cutover]
  classification: internal
  audience: workspace/payments
  owners: ["group:payments"]
  relations:
    - {type: supersedes, target: /decision-cutover-q2.md}
---
# Cut over order history in Q3 2025

On 12 June 2025, the Payments data team and the data platform office decided to cut ORDER_HIST over to edp.orders in Q3 2025, after a 30-day dual run [^comment]. Reconciliation in May showed differences above the 0.1 percent threshold, so the Q2 date was dropped.

It was decided by alice, for the Payments data team, and olivia, for the data platform office.
