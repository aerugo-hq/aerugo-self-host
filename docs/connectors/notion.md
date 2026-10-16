# Notion, live

What a team's admin creates for Aerugo to read Notion, and exactly the access it needs. Aerugo only reads.

## Who reads it: read this first

**Notion's API doesn't say who can read a page.** So Aerugo can't mirror Notion's sharing, as it does for every other
source. Instead, what you share with the integration becomes readable by **the connecting team's members**, as the
team holds them (its groups and people), and no one else. Not the whole org, even for a team anyone may join. If the
team changes, the next sync follows it. This is the same rule as a Notion export imported into a team.

So share with the integration only what the whole team may read. Something only a few people may read belongs in a
smaller team's connection, or not in Aerugo.

## The integration

- **An internal integration** in your Notion workspace (Settings, Connections, Develop or manage integrations), with
  **Read content** only. No insert or update capability, and no user information beyond what's needed.
- **Its token** (`ntn_...`, or an older `secret_...`). Keep it in your secrets backend as a secret holding
  `{"token": "ntn_..."}`, under `secret/kl/<org>/<team>/connections/` in OpenBao, or paste it once into the connection
  form and Aerugo keeps it in OpenBao itself.
- **Share pages and databases with it** (a page's menu, Connections). Sharing a page shares the pages under it.

## What Aerugo reads, and how it treats it

- **It reads:** every page and data source shared with the integration (Notion's search), each data source's rows as
  pages of their own, and each page's blocks: headings become sections, with lists, to-dos and their state, quotes,
  code, and tables as text. Up to 5,000 blocks a page.
- **Where things sit:** a page under its parent pages, a row under its database, as far as those are shared too.
- **What it follows:** each sync reads everything shared now, so a page shared later arrives, and one unshared or
  moved to the trash leaves.
- **Exports:** once a page is read live, the copy a Notion export brought into the same team is retired, so none is
  indexed twice. Stop importing that workspace.
- **Rate limits:** a 429 is waited out for as long as Notion's `Retry-After` says.
- **It never writes** to Notion. It sends the Notion-Version it was written for (`2026-03-11`).

## Connecting it

- **In the app:** Admin, then Sources, then Connect a source, then Notion.
- **In Terraform:** an `aerugo_connection` with `kind = "notion"` and a `credential_ref` (or the write-only
  `api_token`, the integration's token).
- **Proven against a fake:** the client follows Notion's documentation (users/me, search, data source queries, block
  children, cursors, and rate limits), and smoke proves it against a fake that answers the same calls. Try it on a
  test workspace before relying on it.
