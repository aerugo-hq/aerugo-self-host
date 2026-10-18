---
type: System
title: Executive revenue dashboard
description: The leadership team's weekly revenue view, built on finance_mart.daily_sales.
status: stable
sources:
  - id: bi
    resource: https://bi.acme.test/dashboards/exec-revenue
x-kl:
  id: kb-exec-revenue
  aliases: [executive revenue dashboard, exec dashboard, revenue dashboard, exec_revenue_dashboard]
  classification: internal
  audience: workspace/finance
  owners: ["group:finance"]
---
# Executive revenue dashboard

The Executive revenue dashboard shows revenue by week, channel, and region for the leadership team [^bi]. It reads finance_mart.daily_sales, so a late or wrong edp.orders shows up here the next morning.

Finance analytics owns it, and bob is the person to ask.
