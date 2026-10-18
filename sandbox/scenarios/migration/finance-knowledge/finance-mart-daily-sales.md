---
type: System
title: finance_mart.daily_sales (BigQuery)
description: Finance's daily sales table on the EDP, built from edp.orders by the Dataform model daily_sales.
status: stable
sources:
  - id: model
    resource: https://github.com/acme/finance-dataform/blob/main/definitions/daily_sales.sqlx
x-kl:
  id: kb-finance-mart
  aliases: [finance_mart.daily_sales, daily sales mart, finance mart]
  classification: internal
  audience: workspace/finance
  owners: ["group:finance"]
---
# finance_mart.daily_sales (BigQuery)

finance_mart.daily_sales holds revenue and order counts by day and channel. Finance's Dataform model daily_sales builds it every morning from edp.orders [^model].

The Executive revenue dashboard reads it. Finance analytics owns it, and bob is the person to ask.
