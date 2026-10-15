---
type: System
title: Sales dashboard
description: Finance's daily sales dashboard, which reads ORDER_HIST and must move to edp.orders.
status: stable
sources:
  - id: bi
    resource: https://bi.acme.test/dashboards/sales-daily
x-kl:
  id: kb-sales-dashboard
  aliases: [sales dashboard, daily sales dashboard]
  classification: internal
  audience: workspace/finance
  owners: ["group:finance"]
---
# Sales dashboard

The Sales dashboard shows daily revenue and orders by channel [^bi]. It reads the EDW table ORDER_HIST through a nightly extract. When ORD_LOAD is retired, it must read edp.orders instead, or it stops updating.

Finance analytics owns it, and bob is the person to ask.
