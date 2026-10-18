---
type: Schedule
title: EDP cutover calendar
description: When each wave's cutover happens, and the change freeze around it.
status: stable
verified: [{by: "human:olivia", at: "2025-07-28T10:00:00Z"}]
sources:
  - id: calendar
    resource: https://confluence.acme.test/display/EDP/Cutover+calendar
x-kl:
  id: kb-cutover-calendar
  aliases: [cutover calendar, cutover window, change freeze]
  classification: internal
  audience: org
---
# EDP cutover calendar

| Wave | What cuts over | Window | Change freeze |
|---|---|---|---|
| 1 | ORDER_HIST to edp.orders | 18 August 2025, 01:00 to 03:00 | 11 to 25 August 2025 |
| 2 | Customers and returns | to be set in January 2026 | |

During a change freeze, no change reaches the tables in the wave, or the jobs that read them, without the data platform office's approval [^calendar].
