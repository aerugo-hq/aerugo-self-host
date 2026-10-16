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

## S7. Data catalogs

1. **Catalogs read as the runner's own identity (Dataplex, Glue) are an org admin's to connect.** Every team shares
   the runner's identity, so a team admin naming any project or role the runner can reach would see that catalog's
   table names and lineage in their team. *Alternative:* a team admin connects them, with the deployment listing the
   projects and roles each team may name.
2. **Readers are who may read the data, not only the metadata,** a subset of who may see an entry in the catalog:
   SELECT (or ALL) in Unity Catalog and Lake Formation; data viewers, editors, owners, and admins in BigQuery;
   Purview's data readers and curators. Browse, describe, and metadata-viewer grants reach no one. Narrower.
3. **DataHub's readers are its owners,** since a token can't read DataHub's view policies, which by default let every
   DataHub user view everything. Narrower. *Alternative:* the whole org on an instance its admin confirms, like
   `company_site`.
4. **Purview's readers are only the collection's own policy,** without what a child collection inherits from its
   parent. Narrower.
5. **Unity Catalog's service principal needs MANAGE on a catalog** (or to own it) to read its grants: Databricks has
   no read-only privilege for that. The guide says so; Aerugo never writes one.
6. **A catalog's asset names, columns, and lineage are the connecting team's,** as K3's pushed lineage is, whoever may
   read each entry's document; an entry's description and owners are in its document only, behind its own readers.
7. **The fakes are proof of shape, not of each vendor:** each client was written from the vendor's API reference and
   checked against a fake of it, and Glue's request signing against AWS's own test vector. None has been run against
   a real Dataplex, Databricks, AWS, Purview, or DataHub yet.

## S8. Faster permission changes

1. **Microsoft Graph and Drive get no webhook.** Their notifications carry a shared token (`clientState`, a channel
   token), not an HMAC signature, and invariant 13 accepts only signed, replay-protected deliveries; subscribing would
   also mean Aerugo writing to each source. Their permission changes arrive with the readers refresh (every five
   minutes by default). *Alternative:* accept their token in constant time, with Drive's message numbers for replays.
2. **A readers refresh never adds an item,** and keeps an indexed item's old content even when its version changed:
   only the next content sync reads bodies. It only narrows or keeps who reads what.
3. **A webhook only ever queues a readers refresh of its own connection,** whatever the event says; each connection
   has its own hook and secret, so one team's secret can't trigger another's refresh.

## Q1 to Q8. Quality

1. **Claims are checked by words, not a model** (Q3): the share of a sentence's content words its cited passage holds,
   at a threshold calibrated on the golden set. A paraphrase can read as unsupported, never the other way round. A
   team's own model judging entailment comes on top later, never instead.
2. **The claim threshold is calibrated from only five key facts** in the golden set today; until the set has at least
   eight labeled claims of both kinds, answers use the default (0.5) and say so.
3. **Attachments get their page's readers exactly** (Q7), and Drive's "attachments" aren't separate: a Drive file is a
   document already.
4. **Git authors name people only through links they made** (Q7): an author no one in Aerugo has linked is never named.
5. **Media models are opt-in per team, and egress-checked per file** (Q8): no vision or speech model reads anything a
   team didn't choose, or anything its allowed classifications exclude; without a speech model, audio and video aren't
   indexed at all.
6. **Q2's measurement can't show a gain yet:** the lookup and cross-team cases were already at their best. The new
   channels are proven by smoke instead.

## E1 to E3. Experience

1. **Facets count only what the caller may already see** (E1), among the top 50 results: never a count of what's
   hidden (invariant 11).
2. **"Contradicted" is read as conflicting names and replaced pages** (E2), which Aerugo can tell for certain; two pages
   saying different things about the same fact needs claim-level comparison across pages, not built.
3. **Retiring a page leaves its source alone** (E2): search and Ask stop returning it, and the Map and the documents
   list still show it, until someone confirms it again or removes it at its source.
4. **Cluster names are built in the browser** (E3), from the titles of the nodes the viewer was sent, so a name never
   mixes in what they can't read (invariant 16).
