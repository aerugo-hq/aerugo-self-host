---
type: System
title: ORDER_HIST_ARCHIVE (EDW)
description: Orders from 2009 to 2014, moved out of ORDER_HIST in 2015 and loaded once; no job writes to it.
status: stable
sources:
  - id: catalog
    resource: https://edw.acme.test/catalog/ORDER_HIST_ARCHIVE
x-kl:
  id: kb-order-hist-archive
  aliases: [ORDER_HIST_ARCHIVE, order archive]
  classification: internal
  audience: workspace/payments
  owners: ["group:payments"]
---
# ORDER_HIST_ARCHIVE (EDW)

ORDER_HIST_ARCHIVE holds the order lines from 2009 to 2014, moved out of the live order table in 2015 and loaded once [^catalog]. No job writes to it, and nothing reads it on a schedule; auditors query it a few times a year.

It is not part of wave 1. Whether it moves to the EDP at all depends on Legal's retention rules.
