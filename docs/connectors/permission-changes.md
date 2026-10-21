# How fast permission changes arrive

When someone loses access in a source, they lose it in Aerugo within minutes, without waiting for the next content
sync. Three things make that happen, from slowest to fastest.

## Content syncs (every connection's own cadence)

Each sync reads what changed since the last one, on the connection's own cadence (`every_minutes`), and once a day
(`KL_FULL_SYNC_HOURS`, 24) it reads everything again, every item's readers included. That daily full read is what
makes permissions certain; the two below make them fast. A cursor the source no longer accepts (Microsoft Graph
expiring a delta link, Drive refusing a changes token) is read in full at once, so it never stops a connection.

## The readers refresh (every few minutes)

Between content syncs, the worker re-reads only who may read each item, every `KL_READERS_REFRESH_MINUTES` (5 by
default), for Google Drive, Jira, Confluence, Slack, Microsoft 365, Teams, and Notion. No body is read: an item someone may
no longer read leaves at once, and a changed or new item waits for the next content sync. GitHub's sync is its readers
(its webhooks keep it fresh), a directory's sync is who has left (on its own cadence), and a catalog's readers come
with its listing. A connection's row says when its permissions were last read. When a Google Workspace or Entra
directory sees a group's members change, it queues this refresh at once for every Drive or Microsoft 365 connection,
so someone taken out of a group loses the files shared with it on the directory's sync.

## Webhooks (at once)

Jira, Confluence, and Slack sign their webhooks, so a connection to one of them can take a webhook: the source says
something changed, and Aerugo reads that connection's readers again straight away. The message is a hint and nothing
more: Aerugo checks its signature, records it once (a repeat is ignored), queues the refresh, and re-reads the source.
It never takes content or permissions from the message.

Set one up from the connection's row (Webhook), or `POST /v1/workspaces/<team>/connections/<name>/webhook`:

- **Jira:** Aerugo makes the secret and shows it once, with the URL. In Jira, System, WebHooks, create a webhook with
  that URL and secret, for issue, project, and permission changes. Jira signs each delivery (`X-Hub-Signature`).
- **Confluence:** the same, in Confluence's Settings, Webhooks, for page, space, and permission changes.
- **Slack:** give the app's signing secret (Basic Information). In the app's Event Subscriptions, set the Request URL
  to the one Aerugo gives (Slack checks it, and Aerugo answers only a signed check), and subscribe the bot to
  `member_joined_channel`, `member_left_channel`, and `channel_left`. A delivery more than five minutes old is refused.
- **GitHub** has its own webhook, on the GitHub App (`/v1/webhooks/github`).

Setting a webhook up again replaces its secret; removing it (or the connection) destroys the secret.

**Microsoft 365 and Google Drive get no webhook.** Microsoft Graph's change notifications and Drive's push
notifications carry a shared token, not a signature, and Aerugo takes only signed webhooks. Their permission changes
arrive with the readers refresh instead.
