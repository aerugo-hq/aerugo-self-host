# ADR 0011: Connectors: a service account reads, and people see only their own access

Status: Accepted (2026-10-14)

## Context

Aerugo indexes a company's sources (code, documents, tickets, chat, catalogs) so people and agents can find and use what
they're allowed to. Two questions decide whether that's safe: whose access does the indexing use, and what does each
person get to see? Getting either wrong leaks data. If a connector runs on one person's token, the index reflects that
person's access, and it breaks when they leave. If what a person sees follows what the indexer could read, everyone
inherits the indexer's access.

GitHub already works the right way: a GitHub App, a non-human identity, reads what it's installed on, and each file
carries the repository's own readers. Google Drive (ADR 0008) still runs on a person's refresh token. Jira, Confluence,
Slack, Microsoft 365, Notion, directories, and data catalogs come next, so the rule needs writing down once.

## Decision

1. **Ingestion always runs as a non-human identity.** No connector runs on a person's token:

   | Source | Its account | What it can reach |
   |---|---|---|
   | GitHub | A GitHub App | The repositories it's installed on |
   | Google Drive | A service account | The shared drives it's a member of, and folders shared with it |
   | Jira, Confluence | An Atlassian service account, or a dedicated user, with an API token | The projects and spaces granted to it |
   | Slack | A bot | Public channels it joins; private channels only where it's invited |
   | Microsoft 365 | An Entra app registration | Only the sites an admin grants it (`Sites.Selected`) |
   | Notion | An internal integration | Only the pages shared with it |
   | Directories (Okta, Entra, Google Workspace) | A read-only API client | People, emails, and groups, for identity matching |

2. **The account sets the ceiling, never anyone's access.** What the account can read is what gets indexed, so a
   company chooses its index by what it grants the account. Each item carries its source's own readers, mirrored and
   failing closed: a person, a group, or a company-wide grant the source states for that item. Nothing becomes readable
   because the account can read it, and an item no reader maps to isn't indexed at all.

3. **People see their own access through identity matching.** A source names its readers in its own terms (an account
   id, an email); a person signs in through the company's SSO. They're matched, in order of preference:
   - **Automatically, by verified company email:** the SSO token's email is verified, its domain is one the company
     declared as its own, and the source reports the same email for that account to the admin-granted account.
   - **Through a directory connector,** for sources that hide emails and for group grants.
   - **By the person linking the account themselves,** as a fallback.

   A person with no match sees nothing from that source. Every match records how it was made, can be revoked, and is
   re-checked; a person leaving the directory loses their matches.

4. **Terraform declares, the runner runs.** A connection declares its source, its scope (which projects, spaces,
   channels, or drives), the team it feeds, how often it syncs, and a reference to its credential in the company's own
   secret manager. The runner executes on that cadence. Self-hosted, the runner is the worker. Hosted, it runs in the
   company's cluster under its own workload identity, calling out only, and source credentials never leave the
   company's environment; Aerugo keeps references. Applying Terraform changes the declaration, never runs a sync.

5. **Permissions stay fresh, and their age is visible.** Every sync re-reads every item's readers, and fetches content
   only when it changed, so permissions can refresh more often than content. Where a source offers webhooks, changes
   arrive within seconds. Each connection shows when its permissions were last read: revocation takes at most one
   permission refresh.

6. **One sync contract.** A connector hands over items: a key, where people open it, a version that changes with its
   content, a title, its readers, and its content, fetched only when needed. One loop (`kl/sync_items.py`) does the
   rest for every source: indexing, the authorization store's differences, deletions, and team routing. The permission
   logic lives in one place and is tested once; each connector ships a fake of its service so smoke proves its
   permission mapping, leaks included.

## Consequences

- Google Drive moves from a person's refresh token to a service account, which also lets a personal Google account
  share a folder with it.
- Identity matching is new: people's verified emails from sign-in, the source identities connectors report, and a
  matcher that records each match and how it was made.
- Connections gain a cadence, a scope, and a credential reference; the Terraform provider gains the matching resources.
- Every new connector is a client, a mapping from its permissions to readers, and a fake. It never writes documents,
  grants, or deletions itself.
