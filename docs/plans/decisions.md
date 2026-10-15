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

## S5. Microsoft 365

1. **Sharing links grant nothing, whatever their scope,** as Drive's don't. The build plan had organization-wide and
   anonymous links reach the org on a confirmed company tenant; that would widen who reads a file (anyone at the
   company, not only those sent the link), so it's not built. *Alternative:* the build plan's rule, behind a
   `company_tenant` confirmation like Jira's `company_site`.
2. **SharePoint site groups grant nothing,** as the build plan says, so a file shared only with a site's Members
   isn't indexed. That's most of SharePoint by default: share with Entra groups to have files indexed.
3. **A site the app wasn't granted is skipped,** not an error, so one site's missing grant doesn't stop the others.

## S6. Notion, live

1. **Touches invariant 12. Please confirm before merging.** Notion's API has no permissions, so a live page's readers
   are the connecting team's members (its `group:` and `user:` principals), as the roadmap decided and as exports
   already do. The invariant says synced content carries only the source's permissions and never group principals;
   live Notion can't, so this is the one connector that breaks that rule, on purpose, and says so in its form and
   page. *Alternative:* don't connect Notion live until Notion's API reports who can read a page.
2. **The whole org never counts,** even for a team the whole org may join (an `org:` principal holding a role on the
   team is left out), so connecting Notion never makes a page org-wide. Narrower than exports, which may name the org.
