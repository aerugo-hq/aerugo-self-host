---
type: Project
title: Returns migration
description: Moving the EDW table RETURNS and its load RET_LOAD to edp.returns in wave 2, tracked in EDP-421.
status: stable
sources:
  - id: ticket
    resource: https://jira.acme.test/browse/EDP-421
x-kl:
  id: kb-returns-migration
  aliases: [EDP-421, returns migration, RETURNS migration]
  classification: internal
  audience: org
  relations:
    - {type: depends-on, target: /edp-platform-standards.md}
---
# Returns migration

EDP-421 tracks the returns part of wave 2 of the EDP migration: moving the EDW table RETURNS and its nightly load RET_LOAD to the BigQuery table edp.returns, in 2026 [^ticket]. Customers move in the same wave.

The Customer care data team owns it, and olivia coordinates it for the data platform office until the team names a lead.
