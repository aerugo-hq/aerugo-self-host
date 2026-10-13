---
type: Playbook
title: Rolling back a payments deploy
description: How to undo a bad payments release safely.
status: stable
verified: [{by: "human:alice", at: "2026-10-01T09:00:00Z"}]
x-kl:
  id: gh-kb-rollback
  aliases: [payments rollback]
  classification: internal
  audience: org
  relations:
    - {type: depends-on, target: /deploys.md}
---
# Rolling back a payments deploy

If error rates climb after a release, roll back to the previous build before debugging. Rolling back is always safe during the [deploy windows](/deploys.md), because both builds read the same schema.
