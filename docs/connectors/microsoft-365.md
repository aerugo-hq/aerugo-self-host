# Microsoft 365 (SharePoint)

What the identity team creates for Aerugo to read SharePoint, and exactly the access it needs. Aerugo only reads: the
sites you grant set what's indexed, and each file keeps its own sharing.

## The app registration

- **An app registration** in your tenant, such as `aerugo-sharepoint`, single tenant. Note its **Application (client)
  ID** and the **Directory (tenant) ID**.
- **One application permission, for Microsoft Graph: `Sites.Selected`.** It grants nothing until an admin grants the
  app access to a site. Never `Sites.Read.All` or `Files.Read.All`: those reach every site in the tenant.
- **Grant it read on each site to index,** site by site, as a SharePoint or Global admin, for example with Graph:
  `POST /sites/{site-id}/permissions` with `{"roles": ["read"], "grantedToIdentities": [{"application": {"id": "<client id>", "displayName": "aerugo-sharepoint"}}]}`.
  The site id is Graph's: `<tenant>.sharepoint.com,<site collection id>,<web id>` (`GET /sites/<tenant>.sharepoint.com:/sites/<name>`
  gives it).
- **A certificate, never a client secret,** as for the Entra directory (`docs/connectors/entra.md`): its private key and
  certificate as one PEM bundle, kept in your secrets backend or pasted once into the form.

## What Aerugo reads, and how it treats it

- **It reads:** the granted sites' document libraries, every file in them (a delta listing, Graph's only complete
  one), each file's sharing, and the content of files the extractors read (Word, PowerPoint, Excel, PDF, OpenDocument,
  text, and the rest of `kl/extract.py`). A file over 20 MB is indexed by its name only.
- **Who reads each file in Aerugo:**
  - the people it's shared with, as their Microsoft accounts, once they're linked (by verified company email at
    sign-in);
  - the members of an Entra group it's shared with, once an org admin connects Entra as a directory;
  - a sharing link's named people.
- **What grants nothing (fail closed):**
  - **SharePoint site groups** (a site's Members, Owners, Visitors). Graph doesn't list their members to an app with
    this little access, so a file shared only through them isn't indexed. Share files with Entra groups to have
    them indexed for their members.
  - **Sharing links** to the organization or to anyone: a link isn't a grant, as with Drive.
  - **An identity without an email**, and a site the app wasn't granted.
- **Partial sharing lists:** Graph may show an app with read access only some of a file's sharing. That can only mean
  fewer readers in Aerugo, never more. Test on a site you know.
- **Downloads** follow Graph's redirect only to your tenant's own SharePoint host.
- **It never writes** to SharePoint.

## Connecting it

- **In the app:** Admin, then Sources, then Connect a source, then Microsoft 365.
- **In Terraform:** an `aerugo_connection` with `kind = "microsoft"`, its `tenant_id`, `client_id`, `sites`, and a
  `credential_ref` (or the write-only `certificate`).
- **Proven against a fake:** the client follows Microsoft's documentation (sites, drives, delta, permissions, the
  content redirect), and smoke proves it against a fake that answers the same calls. Try it on a test site before
  relying on it.
