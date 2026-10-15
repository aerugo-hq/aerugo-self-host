---
type: Directory
title: Data owners
description: Who owns each EDW and EDP data asset, and who to ask about a migration ticket.
status: stable
sources:
  - id: owners
    resource: https://confluence.acme.test/display/EDP/Data+owners
x-kl:
  id: kb-data-owners
  aliases: [data owners, who to ask, owners directory]
  classification: internal
  audience: org
---
# Data owners

| Asset | Owning team | Who to ask |
|---|---|---|
| ORDER_HIST in the EDW, and its load ORD_LOAD | Payments data team | alice |
| edp.orders in BigQuery | Payments data team | alice or carol |
| The Sales dashboard | Finance analytics | bob |
| Retention rules for personal data | Legal | dave |
| The migration program, EDP-100 | Data platform office | olivia |

For a migration ticket that has gone quiet, such as EDP-412, ask its assignee first, then the owner of the asset it changes [^owners].
