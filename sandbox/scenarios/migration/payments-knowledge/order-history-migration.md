---
type: Project
title: Order history migration
description: Moving ORDER_HIST and its ORD_LOAD job from the EDW to edp.orders on BigQuery, tracked in EDP-412.
status: stable
sources:
  - id: ticket
    resource: https://jira.acme.test/browse/EDP-412
  - id: design
    resource: https://confluence.acme.test/display/EDP/Order+history+migration+design
x-kl:
  id: kb-order-migration
  aliases: [EDP-412, order history migration, ORDER_HIST migration]
  classification: internal
  audience: workspace/payments
  owners: ["group:payments"]
  relations:
    - {type: depends-on, target: /order-hist.md}
    - {type: depends-on, target: /edp-orders.md}
    - {type: depends-on, target: "kl:kb-edp-standards"}
---
# Order history migration

EDP-412 tracks wave 1 of the EDP migration: moving ORDER_HIST to edp.orders [^ticket]. The design follows the EDP platform standards: dual-run ORD_LOAD and the Dataform model, reconcile every day, repoint every reader, then retire ORD_LOAD [^design].

The Payments data team owns it (alice), and carol is the assignee.

## Readers to repoint

The Sales dashboard in Finance reads ORDER_HIST today. Its owner must confirm the switch to edp.orders before ORD_LOAD is retired.

## Open questions

How many years of history to backfill depends on the retention rules for personal data.
