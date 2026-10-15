# Slack

What the identity team creates for Aerugo to read a Slack workspace, and exactly the access it needs. Aerugo only
reads: the bot sets what's indexed, and each message keeps its channel's own readers.

## The app

- **A Slack app created in your own workspace,** and never distributed to other workspaces. An internal app keeps
  Slack's usual rate limits for reading history; one distributed outside the Marketplace is held to one history call
  a minute, which can't keep up.
- **A bot, with these read-only scopes and no others:**

  | Scope | Why |
  |---|---|
  | `channels:read`, `groups:read` | To list the public and private channels the bot is in, and their members |
  | `channels:history`, `groups:history` | To read those channels' messages and threads |
  | `users:read` | To tell this workspace's people from a shared channel's other side, and who has left |
  | `users:read.email` | To link Slack accounts to people in Aerugo by verified company email |

  No `im:` or `mpim:` scope: direct messages are never read. No write scope.
- **The bot token** (`xoxb-...`), never a person's token. Keep it in your secrets backend as a secret holding
  `{"bot_token": "xoxb-..."}`, under `secret/kl/<org>/<team>/connections/` in OpenBao, or paste it once into the
  connection form and Aerugo keeps it in OpenBao itself.
- **Invite the bot to each channel to index.** It reads only channels it's in; remove it from one and that channel's
  messages leave the index on the next sync.

## What Aerugo reads, and how it treats it

- **It reads:** the channels the bot is in (names, whether private or shared, members), their messages and thread
  replies (text, author, time, file names), and the workspace's people (names, emails, whether they've left).
- **Documents:** a thread is one document; other messages are one document per channel per day. Joins, leaves, and
  topic changes are skipped.
- **Who reads each one:** a private channel's members; a public channel's members, or the whole org if you confirm
  the workspace is the company's own; a channel shared with another organization (Slack Connect), only its members
  from this workspace, whatever you confirm. Someone who has left the workspace, and bots, read nothing.
- **What it follows:** each sync re-reads the history window in full (a year unless you set `history_days`), so an
  edit, a deleted message, a member who left a channel, a message Slack's retention removed, and a day that fell out
  of the window all carry over on that sync. Older messages aren't indexed.
- **Rate limits:** a 429 is waited out for as long as Slack's `Retry-After` says, then the sync carries on.
- **It never writes** to Slack.

## Connecting it

- **In the app:** Admin, then Sources, then Connect a source, then Slack.
- **In Terraform:** an `aerugo_connection` with `kind = "slack"`, a `credential_ref` (or the write-only `api_token`,
  the bot token), and optionally `channels` (by id), `history_days`, and `company_workspace`.
- **Confirm the company workspace** only for your company's own Slack. It never widens a shared channel.
- **Proven against a fake:** the client follows Slack's documentation (auth.test, conversations.list, .info,
  .members, .history, .replies, users.list, cursors, and rate limits), and smoke proves it against a fake that answers
  the same calls. Try it on a test workspace before relying on it.
