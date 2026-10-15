# Google Workspace, as a directory

What the identity team creates for Aerugo to read your Google Workspace directory, and exactly the access it needs.
A directory adds no documents. It tells Aerugo who's at the company, who has left, and who is in each group, so a
Drive file shared with a group reaches that group's members and nobody else. Aerugo only reads.

## The account

- **A service account** in a Google Cloud project you control, such as `aerugo-directory@yourproject.iam.gserviceaccount.com`,
  and a **JSON key** for it. Keep the key in your secrets backend, never in a repository, as a secret holding
  `{"service_account": {"client_email": "...", "private_key": "..."}}`, under `secret/kl/<org>/<team>/connections/`
  in OpenBao, or paste it once into the connection form and Aerugo keeps it in OpenBao itself. Only its email and
  private key are kept; anything else in the file, its `token_uri` included, is ignored.
- **Domain-wide delegation, for exactly three scopes.** A super admin opens the Admin console, Security, Access and
  data control, API controls, Manage domain-wide delegation, and adds the service account's client ID with these
  scopes and no others:
  - `https://www.googleapis.com/auth/admin.directory.user.readonly`
  - `https://www.googleapis.com/auth/admin.directory.group.readonly`
  - `https://www.googleapis.com/auth/admin.directory.group.member.readonly`
- **An admin for it to act as**, the connection's `admin_email`: a dedicated account, such as
  `directory-reader@yourcompany.com`, with a custom admin role that can only read users and groups. Not a super admin.
  It must be in one of the company's email domains (`KL_ORG_EMAIL_DOMAINS`).

No Drive scope is needed here: Drive keeps its own connection and its own service account.

## What Aerugo reads, and how it treats it

- **It reads:** users (their primary email, name, and whether they're suspended or archived), recently deleted users,
  groups, and each group's members with nested groups flattened (Google's derived membership).
- **Who has left:** someone suspended, archived, or deleted, and someone a previous sync listed and this one doesn't.
  Their matched access is revoked at once, grants and all; signing in won't match them again. Links a person proved
  themselves stay.
- **Groups:** a Drive file shared with a group becomes readable by its members, each as their own Google identity, so
  each person still links their account (by verified company email at sign-in) before reading anything. Only active
  Workspace users count. A whole-domain member or an outside one reaches no one through a group. A group of more than
  2,000 people grants nothing (fail closed); the sync's result names it. A file shared with everyone at the company's
  domain keeps reaching the whole org through Drive's own rule.
- **It fails closed:** a listing that stops early (an error, a rate limit that won't clear, more pages than allowed)
  fails the sync, and nothing changes. It's never read as everyone leaving.
- **Membership changes take effect at the next Drive sync,** which the directory's sync starts as soon as it sees one.
- **It never writes** to Google.
- **Not matched:** a file shared through someone's alias address, rather than their primary one.

## Connecting it

- **Only an org admin** connects, changes, re-syncs, or removes a directory: it decides who has left, and who a group
  reaches, across the whole company. The team it's connected in can see it, and nothing more.
- **In the app:** Admin, then Sources, then Connect a source, then Directory, then Google Workspace.
- **In Terraform:** an `aerugo_connection` with `kind = "directory"`, `provider = "google"`, its `admin_email`, and a
  `credential_ref` (or the write-only `service_account_key`).
- **Proven against a fake:** the client follows Google's documentation (users.list, groups.list, members.list, the
  token exchange, and the rate limits), and smoke proves it against a fake that answers the same calls. Try it on a
  test tenant before relying on it.
