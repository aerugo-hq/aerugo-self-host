# S2: Google Workspace as a directory, and Drive's group shares

**Goal:** a Drive file shared with a Google group reaches that group's members and nobody else; a Google leaver
loses their matched access at once; and only an org admin can connect, change, or remove a directory.

**Branch:** `s2-google-workspace-directory`, cut from `v-prove-whats-built` (rebased onto `main` once #8 merges).

**Spec:** `docs/BUILD_PLAN.md` section 7, S2, and `docs/ROADMAP.md`, S2. S1 is the template to mirror.

## Confirmed against Google's documentation (2026-10-15)

| What | The facts the client relies on |
|---|---|
| [users.list](https://developers.google.com/workspace/admin/directory/reference/rest/v1/users/list) | `GET https://admin.googleapis.com/admin/directory/v1/users?customer=my_customer`, `maxResults` up to 500, `pageToken`. `showDeleted=true` returns *only* deleted users, so it's a second call (as Okta's deprovisioned are). Fields used: `primaryEmail`, `suspended`, `archived`, `name.fullName`. Scope `admin.directory.user.readonly`. |
| [groups.list](https://developers.google.com/workspace/admin/directory/reference/rest/v1/groups/list) | `GET .../groups?customer=my_customer`, `maxResults` up to 200, `pageToken`. Scope `admin.directory.group.readonly`. |
| [members.list](https://developers.google.com/workspace/admin/directory/reference/rest/v1/members/list) | `GET .../groups/{groupKey}/members`, `includeDerivedMembership=true` lists indirect (nested) members, `maxResults` up to 200. A [member's type](https://developers.google.com/workspace/admin/directory/reference/rest/v1/members) is `USER`, `GROUP`, `CUSTOMER` ("all users in a domain", no email), or `EXTERNAL`. Scope `admin.directory.group.member.readonly`. |
| [Delegation](https://developers.google.com/identity/protocols/oauth2/service-account) | A JWT of `iss` (the service account), `sub` (the admin to act as), `scope` (space separated), `aud` `https://oauth2.googleapis.com/token`, `iat`, `exp` at most an hour later; grant `urn:ietf:params:oauth:grant-type:jwt-bearer`. Not authorized: `unauthorized_client`; a scope not granted: `access_denied`. |
| [Limits](https://developers.google.com/workspace/admin/directory/v1/limits) | 2,400 queries a minute per user per project by default. `403 userRateLimitExceeded`, `403 quotaExceeded`, and `429 rateLimitExceeded` are retried with exponential backoff (about 1, 2, 4, 8, 16 seconds plus jitter), then the error stands. |

The Directory API's host is `admin.googleapis.com`, not Drive's `www.googleapis.com`: it gets its own allowed base.

## Decisions to confirm

1. **Groups expand into each file's readers** (the build plan's design), rather than a group held as one identity by
   its members. `principal_aliases` holds one person per source identity (`PRIMARY KEY (org_id, source)`), so a shared
   group identity would change the alias model behind invariants 4 and 12. The ceiling: one reader and one
   authorization tuple per member per file, written 50 tuples a request. A group over `MAX_GROUP_MEMBERS` (2,000)
   therefore grants nothing (fail closed), and the sync's result names it. The upgrade, when big groups matter: group
   identities with membership tuples in OpenFGA, under its own ADR.
2. **Who has left:** a user who is suspended, archived, listed as deleted, or missing since the previous sync. "Missing"
   applies to every directory, Okta included (today a user deleted outright in Okta just vanishes, and keeps their
   matches). The guard: a listing cut short (the page cap, an error) fails the whole sync, so it never reads as a mass
   departure. Okta's `_pages` raises at `MAX_PAGES` instead of returning what it had.
3. **The org-admin gate:** creating, changing, pausing, re-syncing, and removing a directory connection take an org
   admin (`403 org admin required`, as every org-level action answers); reading one stays with its team's admins.
   `require_org_admin` moves from `main.py` to `policy.py`, beside `require_workspace_admin`. Directory connections a
   team admin made before this keep working; none are Google ones, so none feed groups.
4. **Members are recorded as identities:** every active Workspace user becomes `ext:google.<primary email>` in
   `source_identities`, so identity matching links whoever signs in with that verified company email. Only `USER`
   members count; `CUSTOMER` (a whole domain), `EXTERNAL`, and a member that isn't active grant nothing.
5. **A membership change re-queues each Drive connection's sync**, so someone removed from a group loses the file
   within one Drive sync, not at Drive's next scheduled one. A leaver loses everything at once anyway, through the
   revocation that exists.
6. **The credential:** the service account's JSON key, handed over once (OpenBao, `kl/connections/<id>`) or referenced
   (`credential_ref`, the team fence as today); plus `admin_email`, the account to act as, which must be a well formed
   email in a company domain (`KL_ORG_EMAIL_DOMAINS`). It isn't secret: it's shown in the settings Terraform reads back.

## Threat model

S2 touches authorization, credentials, and outbound calls.

| Risk | What stops it |
|---|---|
| A team admin connects their own directory to revoke someone's access, or (with groups) to widen their own | The org-admin gate (decision 3); groups come only from Google directories, which only org admins can make |
| A crafted key file sends the token request elsewhere | The token address stays Google's (or the allowed dev fake); `gdrive.service_account_key` already drops the key's `token_uri` |
| A response steers requests elsewhere (SSRF) | Google pages by `pageToken`, never a URL; the client only calls its allowed base, checked when it's built |
| A group email lands in a request path | Only emails `groups.list` returned, checked against `EMAIL`, go into a members path (encoded); a group named by Drive is only ever looked up in the table |
| A partial listing revokes everyone | A cut-short listing fails the sync; nothing changes (decision 2) |
| One huge group floods OpenFGA | `MAX_GROUP_MEMBERS`, failing closed per group (decision 1) |
| The key leaks | It never reaches Postgres, logs, audits, or Terraform state; `repr=False` on the field |
| The account can do more than read | Exactly three read-only scopes, written down; acting as a dedicated admin with a custom read-only role, not a super admin |

## Breadth pass

- **Smallest useful version:** everything above, and nothing else.
- **Likely asked for within a week:** keyless delegation (the runner's own identity signing the JWT through IAM
  `signJwt`, so there's no key; room is left: token minting takes the signing as one function); matching on a user's
  alias addresses (room left: users' `emails` are in the response); seeing a group's members in the app (room left:
  the table exists, and `kl_app` deliberately can't read it yet); Entra groups (S3 writes the same table).
- **Forecloses:** nothing permanent. Per-file expansion can be replaced by group identities later.
- **At 100x:** listing members is a call per group per sync (10,000 groups is about four minutes of the default quota:
  fine hourly, heavy at 15 minutes), and the per-file expansion breaks first (decision 1).

## Files

- **Create:** `services/api/kl/migrations/0039_directory_groups.sql`; `docs/connectors/google-workspace.md`.
- **Modify, API:** `directory.py` (the Google client, `client_for`, `sync_directory`), `gdrive.py` (token minting as a
  function; `readers` expanding groups; `sync_drive` loading them), `connections_api.py` (the model, the gate,
  `SETTINGS`), `policy.py` and `main.py` (the gate's move), `config.py` (`GOOGLE_ADMIN_API_BASES`).
- **Modify, stack:** `docker-compose.yml` (the fake's base), `deploy/gcp/compose.gcp.yml` (cleared), `fake_llm.py`
  (the Admin SDK's users, groups, and members; the delegation token check), `smoke.py`.
- **Modify, app:** `services/web/app/admin-view.js` (the directory card: Okta or Google Workspace, shown to org admins);
  `services/e2e/test_web.py`.
- **Modify, provider:** `internal/provider/connection_resource.go` and a test; the onboarding module's type; README.
- **Record:** `CLAUDE.md`, `docs/ROADMAP.md` (S2's boxes), `docs/BUILD_PLAN.md` (the next migration number).

## Tasks

Each ends with its check passing and a commit. Checks are written first and seen to fail.

1. **The org-admin gate.** Move `require_org_admin` to `policy.py`. In `connections_api.py`, call it for a directory
   connection's create, update, refresh, and removal. Smoke: the existing Directories check creates its connection as
   olivia; alice, a payments admin, gets 403 creating, pausing, and removing one, and still reads it.
2. **Missing means left; short listings fail.** `sync_directory` reads the emails it held, writes the new listing,
   and adds each email that disappeared as inactive. `OktaClient._pages` raises at `MAX_PAGES`. Smoke: an Okta user
   dropped from the fake's listing loses their matched access on the next sync, and matching refuses them after.
3. **Migration 0039:** `directory_groups (org_id, connection_id, group_email, member_email, seen_at)`, primary key
   `(connection_id, group_email, member_email)`, both emails lowercase and at most 254 characters, an index on
   `(org_id, group_email)`, row-level security in the org for `kl_ingest` (`SELECT, INSERT, DELETE`), and no access
   for `kl_app`.
4. **The Google directory client** (`directory.py`), `GoogleDirectoryClient(api_base, service_account, admin_email)`:
   `test() -> Health`, `discover() -> [Container("people")]`, `people() -> list[dict]` (current users, then deleted
   ones), and `groups() -> dict[str, list[str]]` (each group's active `USER` members, derived membership included,
   deduplicated, capped). Retries on the three rate-limit answers, honoring `Retry-After`, as the limits page says.
   Its token comes from `gdrive.service_account_token(key, scopes, subject)`, the token request Drive makes today,
   now taking scopes and a subject. The fake answers the same paths behind the same token check (the three scopes and
   a `sub` it was told to allow); the client is run against the fake over HTTP before smoke relies on it.
5. **The directory sync, for Google:** groups replaced per connection in one transaction; users recorded as
   identities; Drive connections re-queued when membership changed.
6. **Drive's group shares:** `gdrive.readers` takes the org's groups (from Google directories only) and turns a
   `group` permission into its members' `ext:google.<email>`; with no directory, a group grants nothing, as now.
   Smoke, the leak check the build plan names: a file shared with a group is found by a member and not a stranger;
   a nested group's member finds it; the member suspended, then the directory synced, loses it; a member taken out of
   the group loses it after the Drive sync; a user deleted between syncs loses their matched access; and refusals of a
   team admin, an internal address, a malformed admin email, and another team's secret. The fake records the token
   request, so smoke proves it went only to Google's address, with the three scopes and the `sub`.
7. **The route and its read-back:** `DirectoryConnectionIn` takes `provider` `okta` or `google`; for Google, a
   `service_account_key` (`repr=False`) or a `credential_ref`, and `admin_email`. `settings_of` returns
   `admin_email`; an update can rotate the stored key.
8. **The app:** the gallery's directory card becomes "Directory", choosing Okta or Google Workspace, its form following
   the existing connector forms exactly (no new visual patterns). Only org admins see the form; a team admin sees who
   can connect one. Browser test: olivia opens the Google Workspace form, its key field masked; alice doesn't get it.
9. **Terraform:** `directory = "google"` beside `"okta"`, an `admin_email` attribute, the key as the existing
   write-only `service_account_key`; an acceptance test against the fake; the onboarding module's type; the README.
10. **`docs/connectors/google-workspace.md`:** what the identity team creates (the service account; a super admin
    authorizing its client ID, under Security, API controls, Domain-wide delegation, for exactly the three scopes;
    a dedicated admin with a custom read-only role for users and groups, to act as), and why no Drive scope is needed
    here.
11. **Prove and record:** smoke as CI runs it, `make eval-scenario` (no regression), `make e2e`, the provider's
    acceptance tests; tick S2; a `CLAUDE.md` paragraph; the next migration number in the build plan.

## Review focus

1. A listing cut short being read as everyone leaving (task 2's guard, and a smoke case where the fake fails mid-way).
2. Email case: Drive's `emailAddress` and the Directory's `primaryEmail` compared lowercase everywhere.
3. A group that contains itself, or a member reached two ways: deduplicated before anything is written.
4. A Drive sync before the first directory sync: the group's files aren't indexed, then appear once the directory's
   sync re-queues Drive (task 5).
5. A file shared through a user's alias address: not matched (a known limit, written in the connector's page).

## Risks

- **Migration numbers:** the document-scoping session may also take 0039. Whichever merges second renumbers.
- **The fake isn't Google:** the client follows the documentation above, but only a real Workspace proves it. Without
  a test tenant, S2 ships proven against the fake, and its connector page says so.
- **Okta's behavior changes** with decision 2: a user deleted outright now loses their matches.
- **Big groups** above the cap don't share files (decision 1); a domain share still reaches the org.

**Not built:** keyless delegation, alias matching, group members in the app, push notifications (S8).
