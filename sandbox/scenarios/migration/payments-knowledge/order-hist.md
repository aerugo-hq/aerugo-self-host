---
type: System
title: ORDER_HIST (EDW)
description: The legacy warehouse table holding every order since 2009, loaded nightly by the ORD_LOAD job.
status: stable
sources:
  - id: catalog
    resource: https://edw.acme.test/catalog/ORDER_HIST
  - id: etl
    resource: https://github.com/acme/edw-etl/blob/main/jobs/ord_load.sql
x-kl:
  id: kb-order-hist
  aliases: [ORDER_HIST, order history table, EDW orders]
  classification: internal
  audience: workspace/payments
  owners: ["group:payments"]
---
# ORDER_HIST (EDW)

ORDER_HIST is the EDW table of every order, one row per order line, including the customer's email and shipping address [^catalog]. The nightly job ORD_LOAD loads it at 02:00 from the order system's export [^etl].

Its readers are the Sales dashboard, owned by Finance, and the monthly revenue extract.

It moves to edp.orders in wave 1 of the EDP migration, and ORD_LOAD is retired after the cutover.
