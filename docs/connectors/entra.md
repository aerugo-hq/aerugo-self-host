# Microsoft Entra, as a directory

What the identity team creates for Aerugo to read your Entra directory, and exactly the access it needs. A directory
adds no documents. It tells Aerugo who's at the company, who has left, and who is in each group, so a Microsoft 365
file shared with a group reaches that group's members and nobody else (S5). Aerugo only reads.

## The app registration

- **An app registration** in your tenant, such as `aerugo-directory`, single tenant. Note its **Application (client)
  ID** and the **Directory (tenant) ID**.
- **Application permissions, for Microsoft Graph, and only these two:**
  - `User.Read.All`: users, whether each account is enabled, and recently deleted users;
  - `GroupMember.Read.All`: groups and their members.

  An admin grants consent for them. No delegated permission, no write permission, and no `Directory.Read.All`.
- **A certificate, never a client secret.** Create an RSA certificate (self-signed is fine), upload its public half
  under **Certificates & secrets**, and keep the private key and certificate together as one PEM bundle in your secrets
  backend, as a secret holding `{"certificate": {"private_key": "...", "certificate": "...", "thumbprint": "..."}}`,
  under `secret/kl/<org>/<team>/connections/` in OpenBao. Or paste the PEM bundle once into the connection form and
  Aerugo keeps it in OpenBao itself, with the thumbprint it computes. An encrypted key isn't supported. Aerugo signs a
  short-lived client assertion (PS256, naming the certificate by its SHA-256 thumbprint) and sends it only to
  `login.microsoftonline.com`, for your tenant.

## What Aerugo reads, and how it treats it

- **It reads:** users (their id, mail or user principal name, display name, and whether the account is enabled),
  recently deleted users, groups (their id, mail, and name), and each group's members with nested groups flattened
  (transitive membership).
- **Who has left:** an account that's disabled or deleted, and one a previous sync listed and this one doesn't. Their
  matched access is revoked at once, grants and all; signing in won't match them again. Links a person proved
  themselves stay.
- **Groups:** each group is kept by its email, or by its id when it has none (a security group), with its id beside
  it, because Microsoft 365 shares a file with a group by its id. Only enabled users count; a group of more than 2,000
  people grants nothing (fail closed).
- **It fails closed:** a listing that stops early (an error, throttling that won't clear, a next page Graph names
  anywhere but Graph, more pages than allowed) fails the sync, and nothing changes. If Graph doesn't say whether
  accounts are enabled (the app lacks `User.Read.All`), the sync fails rather than reading everyone as gone.
- **It never writes** to your tenant.

## Connecting it

- **Only an org admin** connects, changes, re-syncs, or removes a directory: it decides who has left, and who a group
  reaches, across the whole company.
- **In the app:** Admin, then Sources, then Connect a source, then Directory, then Microsoft Entra.
- **In Terraform:** an `aerugo_connection` with `kind = "directory"`, `directory = "entra"`, its `tenant_id` and
  `client_id`, and a `credential_ref` (or the write-only `certificate`; change `credential_version` to rotate it).
- **Not yet:** a federated credential (the runner's own workload identity, with no certificate to keep). Room is left
  for it: signing in is one function.
- **Proven against a fake:** the client follows Microsoft's documentation (users, deleted items, groups, transitive
  members, the client assertion, and throttling), and smoke proves it against a fake that answers the same calls.
  Try it on a test tenant before relying on it.
