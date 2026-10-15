# Confluence Cloud

What the identity team creates for Aerugo to read a Confluence site, and exactly the access it needs. Aerugo only
reads: the account sets what's indexed, and each page keeps Confluence's own readers.

## The account

- **An Atlassian account that isn't a person:** an Atlassian service account, or a dedicated user such as
  `aerugo-reader@yourcompany.com`, with access to Confluence.
- **An API token for it.** Keep it in your secrets backend, never in a repository, as a secret holding
  `{"email": "...", "api_token": "..."}`:
  - OpenBao: under `secret/kl/<org>/<team>/connections/`, such as `secret/kl/acme/payments/connections/atlassian-bot`;
  - GCP Secret Manager: named `kl_<org>_<team>_<name>`, such as `kl_acme_payments_atlassian-bot`, in a project the
    deployment allows (`KL_GCP_SECRET_PROJECTS`), with the deployment's runner granted access to that one secret.

  Or paste the email and token once into the connection form, and Aerugo keeps them in OpenBao itself.

## The access it needs: read-only

| | |
|---|---|
| **View**, on each space to index | Its space permissions. No add, edit, delete, export, or admin permission is needed, or used. |
| **Being a viewer of a restricted page** | To index one, add the account to the page's view restriction (or to a group that is in it). A page the account can't view isn't indexed, and neither is anything under it. |
| **Seeing group membership** | Group grants are expanded to their members. Licensed users can see this by default. If your site hides it, a group grants nothing: fewer readers, never more. |
| **Seeing emails, when profiles allow** | Emails link Confluence's accounts to people in Aerugo, within your company's email domains. Where Atlassian hides one, that person links their account themselves. |

## What Aerugo reads, and how it treats it

- **It reads:** spaces and their names; current pages and blog posts, in Confluence's storage format; labels;
  attachment names; each space's permissions; each page's view restriction; group members; and emails, where shown.
- **Who reads each page in Aerugo:** the space's readers, narrowed by the view restriction on every restricted page
  above it, as in Confluence. On a site you confirm is the company's own, a space every Confluence user may view
  reaches the whole org, and restrictions still narrow it.
- **It fails closed:** an unreadable restriction or folder leaves that page and everything under it out; a space
  role it can't expand, or a group it can't, grants nothing.
- **It never writes** to Confluence.

## Connecting it

- **In the app:** Admin, then Sources, then Connect a source, then Confluence Cloud.
- **In Terraform:** an `aerugo_connection` with `kind = "confluence"`, its `site`, a `credential_ref`, and, to narrow
  what's indexed, `spaces`, `labels`, or `page_trees` (a root page's id, the number in its address).
- **Confirm the company site** only for your company's own Confluence, never a partner's or a vendor's.
- **Moving from exports:** once a space is connected live, the pages an export brought into the same team are
  retired at the next sync, so none is indexed twice. Stop importing that space.
