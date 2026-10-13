# ADR 0008: Google Drive connector

**Status:** accepted, October 2026 (slice F2)

## Context

After GitHub, Google Drive is where most teams keep documents: shared drives full of Docs, Sheets, Slides, and uploaded files, each with its own sharing settings. Indexing it must keep invariant 4 (a source's permissions decide who sees its content) without widening anything, and must work without a Google Workspace admin granting domain-wide delegation, which many teams can't get.

## Decision

- **One OAuth client and refresh token per connection.** A team's admin connects a Google account that can read the shared drives to index, giving an OAuth client (ID and secret) and a refresh token. The secret and token go to OpenBao; Postgres keeps only a reference. Purging the team destroys them.
- **Shared drives are the containers.** Discovery lists the drives the account can read; a connection can be limited to some. Each drive can be routed to its own team, as GitHub repos can (slice F1).
- **Google's formats go through the existing extractors.** Docs, Sheets, and Slides are exported as Word, Excel, and PowerPoint; other files are downloaded as they are. A file's Drive version decides whether it's re-extracted; its readers are refreshed on every sync either way.
- **Each file's permissions decide its readers, failing closed:**
  - a person it's shared with becomes `ext:google.<email>`, which grants nothing until that person links their Google identity, exactly as GitHub logins work;
  - the org's own Google domain (set on the connection) becomes the whole org;
  - "anyone with the link" grants nothing: a link isn't a grant, and indexing such a file for the whole org would make it discoverable by people who were never sent the link;
  - groups grant nothing yet, because resolving their members needs Google's Directory API;
  - a file left with no reader isn't indexed at all, and a file that's deleted or unshared is removed with everything derived from it.
- **Only allowed addresses.** A connection's API address must be on `KL_GOOGLE_API_BASES` (Google's by default), so a connection can't point the worker at another server.

## Alternatives

- **A service account with domain-wide delegation:** sees every user's files and their sharing without per-user consent, but needs a Workspace super admin, and its reach (impersonating anyone) is far wider than indexing needs. It's worth adding as a second mode for organizations that want it.
- **Indexing "anyone with the link" files for the whole org:** simpler and finds more, but turns every link-shared file into an org-wide one.
- **Treating group shares as the whole team:** guesses at membership instead of reading it.

## Consequences

- People link their Google identity by signing in to Google (slice F2b): an org admin sets the org's sign-in client (kept in OpenBao), and each person proves their account from Connected accounts. Google's ID token is checked against Google's published keys, for this org's client, and only a verified email links; sign-in states are single-use, bound to the person who started them, and to Google, so a Google state can't complete a GitHub link. Emails the principal format can't hold (with a `+`) can't be linked yet.
- Group shares stay invisible until the Directory API mapping is built.
- Incremental sync (Drive's changes API) isn't used yet: each sync lists every file in each drive and re-extracts only what changed.
- The fake model server includes a fake Drive and token endpoint for smoke. It runs only under the smoke profile, so listing its address in the dev default is harmless, and production lists only Google's.
