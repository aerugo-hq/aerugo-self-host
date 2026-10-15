# Decisions taken while building the roadmap, for review

The build plan (section 9) says to stop and ask when a choice would widen who can read something, change a public
API's meaning, or need a new external dependency. This roadmap build ran end to end at the owner's request, so each
such choice took the narrower, fail-closed option and is listed here, with its alternative, for review before merging.

## S2. Google Workspace as a directory

1. **Only org admins connect, change, re-sync, or remove a directory** (Okta's too). Before, a team admin could, and a
   team admin's directory could revoke anyone's matched access company-wide. Narrower. A team admin still reads the
   directory connected in their team. *Alternative:* keep team admins, and gate only Google directories (which also
   expand groups).
2. **"Listed before, not now" means left, in every directory, Okta's included.** Before, a user deleted outright in
   Okta just vanished and kept their matches. Narrower. A listing cut short fails the sync, so a partial listing is
   never read as everyone leaving.
3. **Groups expand into each file's readers** (one reader and one tuple per member per file), with groups over 2,000
   people granting nothing. *Alternative, for big groups:* group identities with membership tuples in OpenFGA, which
   changes the alias model behind invariants 4 and 12 (one person per source identity), so it needs its own ADR.
4. **Every active Workspace user is recorded as `ext:google.<email>`** for identity matching. Matching still needs a
   verified email in a company domain (`KL_ORG_EMAIL_DOMAINS`), as before.
5. **Follow-up, not built:** data-subject requests cover `principal_aliases`, but not `source_identities`,
   `directory_people`, or the new `directory_groups`, which all hold emails. They're mirrors of the source (the next
   sync writes them again), so erasure there means the source's own deletion. The gap predates S2.

## S3. Entra as a directory

1. **A certificate only, never a client secret.** The roadmap allows a federated credential too; that's left for
   later (it needs the runner's own workload identity), and a client secret isn't offered at all. *Alternative:*
   accept a client secret for tenants that can't issue certificates.
2. **Tenants and apps are named by id only**, not by a domain like `contoso.onmicrosoft.com`. Narrower and simpler to
   validate.
3. **A security group without an email is keyed by its id** in `directory_groups.group_email`, beside the new
   `group_id` (migration 0041), rather than changing the table's key.

## S4. Slack

1. **A public channel reaches the whole org only on a confirmed company workspace;** otherwise, and always for a
   private or shared channel, its members from this workspace. The roadmap's "a public channel, the workspace's
   members" reads as the org; the confirmation guards a partner's workspace. Narrower.
2. **A shared channel (Slack Connect) is members-only even on a confirmed workspace,** though its public side could be
   read by anyone in the workspace. Narrower.
3. **A full history window per sync** (`history_days`, a year by default) instead of incremental reads from a
   cursor: deletions and retention are followed exactly, and older messages aren't indexed. *Alternative:* cursors,
   with a periodic full pass for deletions.
4. **Documents of containers a connection no longer reads are removed** (`retire_unlisted`), for Jira and Confluence
   too: a project taken out of a connection's scope no longer keeps its issues with frozen readers. Narrower; it
   changes what a scope edit does for Jira and Confluence.
