---
type: Standard
title: EDP platform standards (2024 draft)
description: The first draft of the cutover rules, from October 2024, replaced by the EDP platform standards in March 2025.
status: deprecated
sources:
  - id: draft
    resource: https://confluence.acme.test/display/EDP/Platform+standards+draft+2024
x-kl:
  id: kb-edp-standards-draft
  aliases: [standards draft, 2024 standards draft]
  classification: internal
  audience: org
---
# EDP platform standards (2024 draft)

This draft was written in October 2024 and replaced in March 2025 by the EDP platform standards [^draft].

## Cutover

1. Dual-run the old load and the new model for 14 days.
2. Reconcile row counts once a week. A difference above 0.5 percent blocks the cutover.
3. Repoint dashboards to the new table.
4. Retire the old load.
