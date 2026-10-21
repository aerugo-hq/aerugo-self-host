# Slack

What the identity team creates for Aerugo to read a Slack workspace, and exactly the access it needs. Aerugo reads:
the bot sets what's indexed, and each message keeps its channel's own readers. Its writes are optional and few: capture's
notice in a channel that opted in, and a private reply to someone who saves a message (below).

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

  No `im:` or `mpim:` scope: direct messages are never read. No write scope, unless you turn capture on (below).
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
- **Enterprise Grid:** a connection is one workspace's bot, and "from this workspace" means that workspace's own
  members. So a channel shared between sibling workspaces in your Grid org reaches only the connected workspace's
  members; members of the sibling workspaces get no access to it through Aerugo. This fails closed, by design: Aerugo
  can't tell a sibling workspace from another company's. Connecting each sibling workspace doesn't open it either: a
  channel is indexed once, by the first connection that reads it, and another connection never takes it over.
- **What it follows:** each sync re-reads the history window in full (a year unless you set `history_days`), so an
  edit, a deleted message, a member who left a channel, a message Slack's retention removed, and a day that fell out
  of the window all carry over on that sync. Older messages aren't indexed.
- **Rate limits:** a 429 is waited out for as long as Slack's `Retry-After` says, then the sync carries on.
- **It writes only capture's notice and private replies:** one message in a channel when its capture starts, and one
  when it stops; and a reply only the person who saved a message sees, saying what became of it. Nothing else.
- **Sensitive messages are never kept:** a message speaking of one person's health, discipline, performance, or pay,
  or holding personal data (a government id, a card number, a date of birth), is left out of what Aerugo keeps from
  its channel, though the channel's members can still read it in Slack: it's never searched, shown in the reader, or
  sent to a model, and a thread opening with one is titled by its channel and day. The channel's other messages are
  kept as usual. It's a word check, so it errs on the side of leaving a message out.
- **Its text can stay in the source:** with pointers only, Aerugo keeps each item's title, readers, and embeddings, and
  reads its text live, for each person the source still lists; see `storage-modes.md`.

## Capture (optional)

Capture proposes knowledge from a channel's threads, a procedure, an answer, a decision, through the team's own model
(the team admin opts in under the team's extraction setting), for the team's curators to confirm. Nothing it proposes
is used until a curator confirms it, and each proposal is readable only by the channel's members, like the messages it
came from.

- **Two keys, for each channel.** Someone who created the channel, or an admin or owner of the workspace, runs
  `/aerugo capture on` in it; then the connection's team admin confirms it in Aerugo (Admin, Sources, the connection's
  Capture panel). Neither key alone captures anything, and a confirm nobody asked for in the channel is refused. Aerugo
  checks the first key against Slack itself (who created the channel, who administers the workspace), never trusting
  what the command says, and the bot must be in the channel.
- **The channel is told.** When capture starts, the bot posts one notice saying which team's curators review what's
  proposed and how to keep a message out; when it stops, one more. The command's reply is private to whoever ran it.
- **"Don't capture."** Anyone in the channel can react :no_entry_sign: to a message. Aerugo reads the reaction back from
  Slack (a reaction event alone changes nothing), records the mark, reads the channel again without that message, and
  removes any proposal not yet confirmed that came from it. A proposal a curator already confirmed stays, and goes back
  to them for review once its evidence changes. Removing the reaction later doesn't bring the message back.
- **Turning it off.** `/aerugo capture off` in the channel (by its creator or a workspace admin), or Turn off in the
  Capture panel. Nothing more is proposed from it; what was proposed earlier stays for the curators. A re-sync or an
  edit of the connection keeps each channel's capture as it was; a channel the bot leaves loses it, and needs both keys
  again if the bot comes back.
- **Sensitive messages are never proposed from,** captured or not: they're never kept at all (above).
- **Set up in the Slack app:**

  | Setting | Value |
  |---|---|
  | Scopes, added to the bot's | `commands` (the `/aerugo` command), `chat:write` (the notice), `reactions:read` (to read a mark back) |
  | Slash command `/aerugo` | Request URL `https://<your Aerugo>/v1/hooks/slack/<connection id>/commands` |
  | Event subscription | `reaction_added`, on the Events URL the connection's webhook already uses (`permission-changes.md`) |

  The command and the events are signed with the app's signing secret, the one the connection's webhook holds; an
  unsigned, stale, or repeated delivery is refused or ignored.

## Save to Aerugo (optional)

Anyone in a channel the bot reads can save one message for the connection's team: choose **Save to Aerugo** from the
message's menu. It becomes a proposed unit with the message's text as written, for the team's curators to confirm,
readable only by the channel's members (it cites the channel's own document, so it follows its membership).

- **Whose message:** your own, in any channel the bot reads; someone else's, only in a channel whose capture is on,
  where everyone was told.
- **Never saved:** a message marked "don't capture", or one speaking of someone's personal matters; anything from a
  connection that keeps pointers only or is live only, since those keep no text. Marking a message "don't capture"
  later removes its save, if no curator has confirmed it yet.
- **Who can save:** someone in this workspace and in the channel; never a guest from a shared channel's other side,
  someone who has left, or a bot. Aerugo reads the person, the message, and the channel again from Slack; it takes
  nothing else from the shortcut.
- **The person who saved is told privately** what became of it (saved, already saved, or why not).
- **A note:** `/aerugo save` doesn't take text. Post the note in the channel, then save it.
- **Set up in the Slack app:**

  | Setting | Value |
  |---|---|
  | Interactivity Request URL | `https://<your Aerugo>/v1/hooks/slack/<connection id>/interactions` |
  | A message shortcut | Name "Save to Aerugo", callback id `aerugo_save` |
  | Scopes | `chat:write` (already added for capture) covers the private reply |
- **Not built yet:** the bot answering privately (C2d).

## Connecting it

- **In the app:** Admin, then Sources, then Connect a source, then Slack.
- **In Terraform:** an `aerugo_connection` with `kind = "slack"`, a `credential_ref` (or the write-only `api_token`,
  the bot token), and optionally `channels` (by id), `history_days`, and `company_workspace`.
- **Confirm the company workspace** only for your company's own Slack. It never widens a shared channel.
- **Proven against a fake:** the client follows Slack's documentation (auth.test, conversations.list, .info,
  .members, .history, .replies, users.list, cursors, and rate limits), and smoke proves it against a fake that answers
  the same calls. Try it on a test workspace before relying on it.
- **Faster permission changes:** a member who leaves a channel loses it within minutes (the readers refresh), or at
  once with a webhook signed by the app's signing secret; see `permission-changes.md`.
