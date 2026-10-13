---
type: Playbook
title: Declaring a payments Sev1
description: What to do in the first 15 minutes when payments are down.
status: stable
generated: {by: "claude-opus/2026-09", at: "2026-09-28T10:00:00Z"}
verified: [{by: "human:alice", at: "2026-09-29T09:00:00Z"}]
sources:
  - id: runbook
    resource: https://github.com/acme/payments-api/blob/main/docs/incident-response.md
x-kl:
  id: kb-sev1-playbook
  aliases: [sev1 playbook, payments sev1]
  classification: internal
  audience: workspace/payments
  owners: ["group:payments"]
  relations:
    - {type: depends-on, target: /ledger.md}
    - {type: depends-on, target: "kl:kb-incident-comms"}
    - {type: depends-on, target: "kl:kb-legal-escalation"}
---
# Declaring a payments Sev1

When payments are down, page the payments on-call lead first, then declare a Sev1 in the incident channel [^runbook]. The on-call lead owns the incident until they hand it off.

Check the [ledger service](/ledger.md) before restarting anything: a restart during settlement can double-post entries.
