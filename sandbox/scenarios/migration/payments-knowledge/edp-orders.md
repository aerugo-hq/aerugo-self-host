---
type: System
title: edp.orders (BigQuery)
description: The BigQuery table replacing ORDER_HIST, built by the Dataform model orders and scheduled by the Composer DAG orders_daily.
status: stable
sources:
  - id: model
    resource: https://github.com/acme/edp-dataform/blob/main/definitions/orders.sqlx
  - id: dag
    resource: https://github.com/acme/edp-composer/blob/main/dags/orders_daily.py
x-kl:
  id: kb-edp-orders
  aliases: [edp.orders, orders table, BigQuery orders]
  classification: internal
  audience: workspace/payments
  owners: ["group:payments"]
  relations:
    - {type: derived-from, target: /order-hist.md}
---
# edp.orders (BigQuery)

edp.orders is the EDP's table of order lines. The Dataform model orders builds it [^model], and the Composer DAG orders_daily runs that model every night [^dag].

During the dual run it is derived from ORDER_HIST. After the cutover it reads the order system's export directly, and ORDER_HIST is archived.
