# Jira Cloud

Who reads each Jira issue in Aerugo, and which parts of it. Aerugo only reads: the connection's account sets what's
indexed, and each issue keeps Jira's own readers.

## Who reads each issue

- **A project anyone with Jira access may browse** reaches the whole org, but only on a connection whose admin confirmed
  the site is the company's own. Otherwise its readers are the people Jira names.
- **Any other project, and any issue under a security level,** reaches the accounts Jira's own permission search says
  may browse it. Each account reaches a person once identity matching, or the person, links it to their Aerugo account.
- **An issue nobody may read** isn't indexed at all.

## Comments narrower than their issue

Jira can show a comment to fewer people than its issue: a comment restricted to a group or a project role, and, in
Jira Service Management, an internal note the customer doesn't see. Aerugo leaves every such comment out. It isn't
indexed, embedded, found by search, given to a model, shown in the reader, or read live for a pointer, so it reaches no
one through Aerugo, not even the people Jira shows it to. A decision recorded in one isn't a decision in Aerugo either.

Why: Aerugo doesn't yet work out a restricted comment's own readers (the issue's readers who are also in that group or
role), and giving it to all the issue's readers would widen who sees it. Everything else in the issue, its open comments
included, reaches the issue's readers as before.

When a comment's restriction changes, the issue is read again at its connection's next full read: once a day
(`KL_FULL_SYNC_HOURS`), or at once with Re-sync. That holds whether or not Jira marks the issue updated, since the
issue's version in Aerugo includes which of its comments are left out. Issues indexed before this rule are read again
the same way, once.

## Connecting it

- **In the app:** Admin, then Sources, then Connect a source, then Jira Cloud.
- **In Terraform:** an `aerugo_connection` with `kind = "jira"`, its `site`, a `credential_ref`, and `projects`.
- **Confirm the company site** only for your company's own Jira, never a partner's or a vendor's.
- **Its text can stay in the source:** with pointers only, Aerugo reads each issue live for each person Jira still
  lists, and leaves out the same comments; see `storage-modes.md`.
- **Faster permission changes:** see `permission-changes.md`.
