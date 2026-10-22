# Microsoft Teams

What the identity team creates for Aerugo to read Microsoft Teams, and exactly the access it needs. Aerugo reads the
standard channels of the teams whose owners add your company's Teams app, and nothing else: each post keeps its team's
own readers. Its one write is capture's notice, in a channel whose capture is on (below).

## The app

Your company's own Teams app, never distributed outside your tenant. It's three things, all with one id:

- **An Entra app registration** with a certificate credential (upload the certificate; never a client secret). It needs
  **no Graph application permissions at all**: it reads a team only through that team's resource-specific consent
  (below), so it can't read a team whose owner hasn't added it, and nothing tenant-wide (no `ChannelMessage.Read.All`).
  Keep its private key and certificate (PEM) in your secrets backend as a secret holding `{"certificate": ...}` under
  `secret/kl/<org>/<team>/connections/`, or paste them once into the connection form and Aerugo keeps them in OpenBao.
- **An Azure Bot**, single tenant, whose Microsoft App ID is that app registration's. Its messaging endpoint is
  `https://<your Aerugo API>/v1/hooks/teams/<connection id>/messages`: connect first, then copy the connection's id from
  its row (IDs and Terraform). Turn on its Microsoft Teams channel.
- **A Teams app package**: `deploy/teams/manifest.json` with your app's id and your company's details filled in, and
  your own two icons (`color.png`, 192 by 192, and `outline.png`, 32 by 32), zipped together. Upload it in the Teams
  admin center, and allow it for the teams that should use it.

When a team's owner adds the app to their team, they grant it these permissions **for that team only**:

| Permission (resource-specific) | Why |
|---|---|
| `ChannelMessage.Read.Group` | To read the team's channel posts and replies |
| `TeamMember.Read.Group` | To read who's in the team, which is who may read its posts |
| `ChannelSettings.Read.Group` | To list the team's channels and tell standard ones from private and shared ones |
| `TeamSettings.Read.Group` | To read the team's name, for Aerugo's Documents tree |

Removing the app from a team takes them all back: Aerugo can then read nothing of it, and on its next sync the team's
posts, and anything proposed from them that no one confirmed, leave Aerugo.

## What Aerugo reads, and how it treats it

- **It reads:** the standard channels of the teams the connection names (by id) whose owners added the app, their posts
  and replies within the history window (a year unless set), and the team's members.
- **It never reads:** a team whose owner hasn't added the app, private and shared channels, group chats, and 1:1 chats.
- **Who may read each post:** the team's members and owners, as Teams lists them now, each as their company email
  (`ext:microsoft.<email>`, the identity Entra and Microsoft 365 also report, so signing in links it). A guest reaches
  no one, nor does anyone Teams gives no email for, and the whole org never does, even for an org-wide team.
- **A post and its replies are one document.** Each sync reads the window again, so an edit, a deletion, or someone
  leaving the team carries over on that sync; a readers refresh every few minutes re-reads only the team's members.
- **Sensitive messages are left out:** one speaking of one person's health, discipline, performance, or pay, or holding
  personal data, is kept nowhere, found by no one, and sent to no model, though the team can read it in Teams.
- **Its text is indexed.** Teams has no pointer or live-only mode yet.

## Capture

Capture proposes knowledge from a channel's posts, for the connection's team's curators to confirm, readable only by
the channel's team. It takes two keys:

1. **The team's owner adds the app.** That consent is the first key for each of the team's standard channels, which
   arrive in the connection's Capture panel as requested.
2. **The connection's team admin confirms a channel** in Aerugo (the Capture panel). Only then is it proposed from.

The bot posts one notice in the channel when capture starts and one when it stops, and nothing else. Anyone in the
team can keep a message out: choose **Don't capture** from the message's More actions menu. Aerugo takes it once Graph
shows that message in a channel the connection reads and the person who chose it in the team, then never keeps the
message again and drops what was proposed from its post (a unit a curator already confirmed goes back to them for
review). Turning a channel off is the team admin's, in Aerugo; removing the app ends everything for the team.

## How the bot's deliveries are checked

Bot Framework signs no HMAC over a delivery. It sends a token Microsoft signs, which Aerugo checks before anything else:
RS256 with a key Bot Framework publishes and endorses for Teams, issued by Bot Framework, for this connection's app,
unexpired, and naming the service address the delivery came from, which must be one the deployment allows
(`KL_TEAMS_BOT_SERVICES`, Teams' own `https://smba.trafficmanager.net/` by default; the keys come from
`KL_TEAMS_BOT_OPENID`, Bot Framework's own by default). Anything wrong is the same 401. A delivery is taken once, by
its activity's id, and only queues a job; because the body itself isn't signed, that job reads back from Graph whatever
it acts on. The bot posts only to the service address the connection names, never one a delivery names.

## Not built yet

The bot answering questions and Save to Aerugo (C1b), pointer and live-only modes, private and shared channels, and
meeting transcripts (C3).
