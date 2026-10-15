---
type: Project
title: EDW to EDP migration program
description: How Acme moves its legacy enterprise data warehouse onto the BigQuery data platform, wave by wave.
status: stable
sources:
  - id: charter
    resource: https://confluence.acme.test/display/EDP/Migration+program+charter
  - id: epic
    resource: https://jira.acme.test/browse/EDP-100
x-kl:
  id: kb-edp-program
  aliases: [EDP migration, EDW migration, migration program, warehouse migration]
  classification: internal
  audience: org
  relations:
    - {type: depends-on, target: /edp-platform-standards.md}
---
# EDW to EDP migration program

Acme is retiring its legacy enterprise data warehouse (EDW) and moving its tables and loads to the enterprise data platform (EDP) on BigQuery, with Dataform models for transformations and Composer DAGs for scheduling [^charter]. The program runs in waves, tracked under the epic EDP-100 [^epic].

## Waves

- **Wave 1, 2025: order history.** The EDW table ORDER_HIST and its nightly load ORD_LOAD move to the BigQuery table edp.orders. The Payments data team owns it, and the Sales dashboard in Finance is its main reader.
- **Wave 2, 2026: customers and returns.** The customer and returns tables, and the loads behind them.
- **Wave 3, 2026: the reporting marts.** The remaining marts move, and then the EDW is switched off.

Every wave follows the EDP platform standards for cutover: dual running, daily reconciliation, repointing every reader, and retiring the old load.
