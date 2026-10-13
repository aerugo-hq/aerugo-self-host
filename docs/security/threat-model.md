# Threat model v0 (end of M1)

Method: STRIDE per trust boundary, from `docs/design/v1.md` §3.3.

**Status key:**

- ✅ enforced and covered by `make smoke`
- ◑ enforced, no automated test yet
- ◻ planned, with the milestone that adds it

Review this file whenever a boundary or invariant changes.

## TB1 · Clients ↔ API

| Threat | Example | Mitigation | Status |
|---|---|---|---|
| Spoofing | Forged or replayed bearer token; HS256 algorithm confusion | JWKS signature check with pinned RS256, issuer, audience, and expiry | ✅ |
| Elevation | Crafted group name (`a,b`, `*`) widening the RLS context | Strict principal pattern; unsafe names dropped | ✅ |
| Info disclosure | Learning that a private workspace or hidden document exists | 404 instead of 403; no counts of hidden items | ✅ |
| Elevation | Non-admin registering models or connecting sources | OpenFGA admin check on every admin route | ✅ |
| DoS | Expensive queries | Query length caps, search `limit` ≤ 50; per-person, per-team, and per-org rate limits with 429 and Retry-After (slice O) | ✅ |

## TB2 · API ↔ identity provider

| Threat | Example | Mitigation | Status |
|---|---|---|---|
| Spoofing | Token from another issuer or realm | Fixed issuer, audience bound to `kl-api` | ✅ |
| Repudiation | Disputed actions | Append-only audit log at retrieval-set level | ✅ |
| Elevation | Stale group membership after someone leaves | SCIM deprovisioning | ◻ M6 |

## TB3 · Cell ↔ source systems (GitHub)

| Threat | Example | Mitigation | Status |
|---|---|---|---|
| Spoofing | Forged webhook triggering work | HMAC-SHA256 with constant-time compare; deliveries recorded once (replays ignored); fail closed with no secret | ✅ |
| Tampering | Webhook payload claiming new permissions | Webhooks only queue jobs; the sync re-reads GitHub (invariant 13) | ✅ |
| Info disclosure | Synced content readable beyond GitHub's grants | ACLs come only from GitHub readers; no workspace grants on synced content (invariant 12) | ✅ |
| Info disclosure | Revoked access lingering | Scheduled re-sync window plus webhooks; ACL freshness enforcement for restricted content | ✅ / ◻ M2 |
| Elevation | Over-broad credentials | App with read-only contents, metadata, and members; installation tokens about 1h; one ingest identity | ✅ |
| Tampering | Path or URL injection from repo names or object ids | Repository names and git object ids validated before use | ✅ |

## TB4 · Cell ↔ customer GitHub (OKF bundles)

| Threat | Example | Mitigation | Status |
|---|---|---|---|
| Tampering | Poisoned canonical knowledge | PRs only, CODEOWNERS review, knowledge CI | ◻ M3 |
| Info disclosure | A hub laundering restricted content to a wider audience | Audience monotonicity check in knowledge CI | ◻ M3 |

## TB5 · Cell ↔ model providers

| Threat | Example | Mitigation | Status |
|---|---|---|---|
| Info disclosure | Restricted chunks sent to a disallowed model | Egress policy per classification, for ask and the compiler | ✅ |
| Tampering | Prompt injection through ingested content | Retrieved text sent as numbered blocks marked untrusted; read-only MCP tools; compiler drafts reviewed before merge | ✅ |
| Info disclosure | Keys leaking via the database or logs | Keys only in OpenBao; Postgres holds references (checked for model keys and the App key) | ✅ |

## TB6–TB8 · SIEM, operator, workspace ↔ workspace

| Threat | Example | Mitigation | Status |
|---|---|---|---|
| Info disclosure | Cross-workspace or cross-org reads | RLS on `org_id` and ACL arrays; OpenFGA post-check; drift dropped and audited | ✅ |
| Elevation | Worker or service reading across orgs | Worker and webhook run with an org context under RLS; no context returns nothing | ✅ |
| Repudiation | Audit tampering | No UPDATE or DELETE grant for app roles | ✅ (hash chain ◻ M5) |
| Elevation | Operator access in SaaS | Break-glass with customer approval and audit | ◻ M6 |

## TB9–TB29 · Surfaces added since M2

| Boundary | Threat | Mitigation | Status |
|---|---|---|---|
| MCP clients → MCP server | Token replay from the REST API, or token passthrough | Tokens must carry audience `kl-mcp`; the server never forwards a client's token and runs the API's policy path in-process | ✅ |
| Browser → web app | Token theft through XSS; login CSRF; cross-site writes | Backend-for-frontend: tokens stay server-side, the browser holds an opaque httpOnly SameSite cookie; PKCE with state and nonce; foreign-origin POSTs refused (ADR 0001) | ✅ |
| Curator App → GitHub | Publishing a draft to people who couldn't read its sources | Every reader of the knowledge repo must be linked to someone entitled to every source, or the PR is refused; the curator App is installed only on knowledge repos | ✅ |
| Compiler model | A model widening its own audience or claiming review | The model writes only type, title, and body; the platform writes sources, audience, and provenance; drafts pass knowledge CI | ✅ |
| Map | Inferring hidden content from node counts or dangling edges | Hidden nodes aren't returned; edges need both ends visible; totals count visible nodes only; batched checks past OpenFGA's list cap | ✅ |
| Purge | A partial purge leaving content readable; purging another org's team | Authorization deleted first; owner-privileged functions confined to the caller's org; verification and a deletion record | ✅ |
| Telemetry | Content leaking through spans, metrics, or errors | Hand instrumentation with ids and counts only; exception types, never messages; a canary check over the export (ADR 0003) | ✅ |
| Reranker | Passages reaching a reranker the caller couldn't read, or one whose classification it may not receive | Runs after the OpenFGA post-check, on post-checked candidates only; its own egress list, with withheld passages kept in place rather than sent; set by org admins only, key in the secrets backend; smoke proves both (ADR 0004) | ✅ |
| Eval judge | Grading sending confidential passages to a judge model | The judge has its own classification list (`KL_EVAL_JUDGE_CLASSIFICATIONS`); an answer citing a passage it may not read is reported as withheld, not graded; it runs only in test tooling | ✅ |
| Feedback | Probing for hidden documents by naming them in feedback; polluting the golden set | Every named document must pass RLS, OpenFGA, and the audience rule, or the call is 404 with nothing recorded; feedback is audited in its documents' teams; mined cases join the golden set only after a person reviews them | ✅ |
| Map links | An entity name, or a similarity between two documents, revealing content the viewer can't see | RLS on every derived table follows the documents: an edge needs both documents visible, a mention its document, an entity one visible mention; the map filters to visible nodes again; derived rows cascade from documents, and an orphaned entity is deleted with its last mention, so purge leaves none behind | ✅ |
| Alerts | Content or a person's name leaving in an alert; the admin-set webhook used to reach internal services | Signals and alerts hold ids and counts only, and smoke checks that a posted alert carries no document key or user; team admins see only their team's alerts and members none; only org admins set the webhook, its URL lives in the secrets backend (never in Postgres or the audit log) and must be http(s). The API and worker call whatever URL an org admin sets, so production egress rules should limit where they can reach (ADR 0005) | ✅ |
| Data subjects | An export leaking other people's data; an erasure missing a copy of the person, or erasing the audit record itself | Exports redact every principal and person-valued field that isn't the subject's (smoke checks alice's names no one else); erasure is org-admin only, confirmed by repeating the name, removes authorization first, finds attribution columns by name at run time, pseudonymizes audit entries instead of deleting them, and refuses to commit unless a final count of rows naming the person is zero; the erasure is audited by pseudonym only. Known limit: free text that names someone in passing (a question mentioning a colleague) is pseudonymized only when it's the subject's own words | ✅ |
| Retention | Deleting what must be kept (under a legal hold, or another team's knowledge), or leaving copies of what was deleted | Org admins only; no policy keeps records forever; a document goes only when every team holding it agrees it expired, never while a hold covers it, and a held team can't be purged; authorization tuples go before rows, and every derived row cascades from the document (smoke checks every table with a `document_id`, and authorization); the audit trail is kept at least 30 days; each run records its counts. Deleted rows stay in backups until the last full backup holding them expires (ADR 0006) | ✅ |
| Stored originals | A backup, or anyone reading the database directly, getting the platform's copies of knowledge; a purged team's content readable from an old backup | Concept versions and proposal drafts are ciphertext under a per-team Transit key (the org's for documents several teams hold) that never leaves OpenBao; a purge re-wraps survivors, then destroys the team's key, so every copy encrypted with it is unreadable (smoke decrypts a pre-purge copy and expects failure). Known limit: search indexes (chunks, embeddings) stay plaintext so search works; backups holding them are protected by retention, not keys | ✅ |
| Backups | A stolen backup read; a backup that doesn't restore; a drill damaging live data | The pgBackRest repository is encrypted (AES-256) with a passphrase kept out of the repository, and stored originals inside are already per-team ciphertext; WAL is archived continuously (smoke forces a segment switch and watches it arrive); `make restore-drill` restores into a throwaway database that reads the repository read-only and archives nothing, and checks the copy (marker, schema, row-level security, policies, rows, keys), recording the result (ADR 0006). Known limit: losing the passphrase loses the backups, and the full-cell drill waits for deployment | ✅ |
| Personal data | The detector copying what it finds; a review list naming documents its reader can't see; a reclassification silently undone | Flags store kinds and counts, never values (smoke checks the flag and the review list hold neither the email nor the phone); a flag is readable and resolvable only by someone who can see its document (RLS, then OpenFGA and the audience rule); a reclassification only ever makes a document stricter and holds as a floor through re-indexing. Known limit: pattern detection misses personal data in prose it has no pattern for | ✅ |
| Repo routing | One team's admin pushing content into, or pulling it out of, another team; a move deleting another connection's copy | Routing a repo needs admin rights on the connection's team and on the receiving team; a move removes the repo's documents only from teams this connection placed them in before, so another connection's placement of the same documents stays (smoke checks both); routing changes which team holds the documents, never who may read them, which still comes from GitHub | ✅ |
| OCR | A crafted PDF or image exploiting the renderer or OCR engine; files that exhaust memory or time | Poppler and Tesseract are maintained distribution packages, run as separate processes with timeouts on temporary copies, inside the worker's unprivileged container; images are measured from their header and refused past 50 megapixels before anything decodes them; scans are capped at 50 pages. Known limit: a parser flaw in Poppler or Leptonica still runs with the worker's rights, so keep the image updated | ✅ |
| Pipeline pushes | A stolen or forged CI token pushing content; a pipeline granting readers its team doesn't have, or overwriting someone else's documents; the API fetching keys from an attacker's server | Tokens are CI workload tokens: short-lived, RS256-signed by an issuer on `KL_PIPELINE_ISSUERS` (keys are fetched from no other server), for audience `kl-ingest`, and accepted only for a repository and ref a team's admins registered (smoke refuses another repository, another audience, an expired token, and garbage); readers must each hold a role on the team, so org-wide publishing needs a team that admits the org; a pipeline's documents are namespaced to it; pushed text is cleared from staging once applied (ADR 0007). Known limit: without a registered ref, any workflow in the repository can push | ✅ |
| Google Drive | A file indexed more widely than Drive shares it; a connection's secrets outliving its team; a connection pointing the worker at another server | Each file's own permissions decide its readers and fail closed: a person maps to an identity that grants nothing until they link it, only the org's own domain maps to the whole org, and groups and "anyone with the link" grant nothing; a file with no reader isn't indexed, and Drive revokes and deletes carry over on the next sync (smoke checks each); the OAuth secret and refresh token live in OpenBao and are destroyed when the team is purged; connections may use only addresses on `KL_GOOGLE_API_BASES` (ADR 0008). People link their Google identity by signing in: Google's ID token is verified against its published keys, for this org's sign-in client, and only a verified email links; sign-in states are single-use and bound to their user and provider (smoke refuses another user's state, a reused state, an unverified email, a token for another client, an account already linked to someone else, and a Google state at GitHub's completion). Known limit: group shares grant nothing until the Directory API mapping exists | ✅ |
| Imports | A crafted archive exhausting memory or disk, or escaping by path; an import granting readers outside its team; uploads lingering after use | Archives are read in memory, never unpacked to disk, so a path in one goes nowhere; uploads are capped at 50 MB, and an archive declaring more than 500 MB unpacked, or more than 10,000 files, is refused before anything decompresses (smoke uploads a zip bomb); the importer names readers, who must each hold a role on the team, as for pipeline pushes; the upload is cleared from staging once applied, whether it succeeded or failed (smoke checks). Known limit: an export carries no permissions, so an import's readers are only as right as the admin who chose them | ✅ |
| Conversations | Someone reading another person's conversations; a follow-up smuggling text drawn from confidential sources to a model that may not receive it; a saved answer outliving the reader's access to its sources; conversations surviving a person's erasure | Row-level security makes conversations private to their owner, so org admins can't read them either, and another person's conversation answers 404 exactly like a missing one; a follow-up carries the person's own questions, but an earlier answer only to the model that wrote it, and only while every source it drew on is one that model may receive at that source's classification today; every read re-checks each answer's sources through search's gates, and hides any answer whose sources the reader can no longer open; search covers titles and the person's own questions, never answers; data-subject export includes conversations, and erasure deletes them; a question focused (@) on documents goes through search's same gates, so a focus on one the asker can't open yields nothing and the model is never called (smoke checks each, from the fake model's received prompts) | ✅ |
| Token signing keys | An issuer withdraws a leaked signing key, but the platform keeps accepting tokens it signed; forged tokens used to make the platform flood an issuer with key requests | Every verifier (Keycloak sign-in, CI pipeline tokens, Google ID tokens) goes through `kl/jwks.py`: an issuer's key set is cached for five minutes and no single key beyond it, so a withdrawn key stops working within five minutes rather than at the next restart; a failed signature refetches the set once, for a just-rotated key, but at most once a minute per issuer, so forged tokens can't turn retries into a flood (tested against a live key server: a withdrawn key that the old per-key cache kept accepting is refused, and 50 forged tokens cause one fetch) | ✅ |
| Documents and pipeline | A document list leaking what search would hide (drift, other teams, other audiences); a source link running script; someone retrying or reading another team's runs; a retry the app role could abuse | The document list goes through search's gates, so a document the authorization engine denies never shows and the drift is audited, and asking for a hidden document by key returns nothing; every filter only narrows; source links render only for http and https addresses, since an import's site address comes from an admin; a team's runs reach only its admins (a member who isn't one gets 403, anyone else 404), each run's team comes from what it works on, and only failed syncs and removals can be retried, once, audited; the app role may update only the columns a retry resets (smoke checks each, including the seeded drift) | ✅ |
| Home and costs | A count revealing documents a person couldn't open; a team's failures or spend shown to people outside it; Costs and the budget gate disagreeing about what's been used | Home counts documents through search's gates, so its count for a team equals what Documents lists and leaves out drift and documents outside a person's audience; failed runs appear only to a team's admins; a team's costs reach only its admins (403 for other members, 404 for everyone else) and every team's costs only org admins; Costs counts the month with the budget gate's own expression, and smoke checks that the two totals are equal | ✅ |
| Service accounts | Any machine client given the API's audience becoming a caller automatically, with org-wide reach; a person signing in under a service account's name and inheriting its roles; a disabled or removed account's tokens still working; an automation account reading documents; roles left behind after removal | A machine token (Keycloak's `service-account-<client>`, issued to that same client) is refused unless an org admin registered the client and it's enabled, checked on every request so disabling takes effect at once; it gets only its own principal, never the org's, so it reads nothing unless granted; a person's token using a `service-account-` name is refused; the MCP server refuses machine tokens entirely; registering, changing, and removing accounts is org-admin only and audited, and removal sweeps every role (unit tests of the sign-in rules; a smoke check of the whole lifecycle) | ✅ |

## Known gaps to close next

- **Dev credentials:** OpenBao's dev server keeps its unseal key and root token on its own volume and serves plain HTTP, and Keycloak allows the password grant. Move to real unsealing (a KMS or Shamir shares held by people), TLS, AppRole or Kubernetes auth, and authorization code with PKCE before any shared deployment. Since I3a, secrets and encryption keys survive restarts; `make reset` (which removes the volumes) still wipes them, as it wipes the database.
- **ACL freshness enforcement:** withholding confidential or restricted synced items whose ACL is older than the SLO waits on classification (M2).
- **Retiring one connection:** team purge removes everything a team owns, but retiring a single connection (unlinking or purging only its content) isn't built yet.
- **Identity links in production:** links are seeded for dev users only; real users need proven links (slice K). Until then, synced content is invisible to everyone, which fails closed.
- **Per-team keys:** built in I3b for stored originals. Search indexes stay plaintext by design; backups (I4) hold them until the last full backup holding them expires.
- **OpenBao isn't in the Postgres backup:** its keys open the encrypted originals, so restoring Postgres without OpenBao's data leaves them unreadable. Dev backs up the `kl-openbao` volume by hand (`docs/runbooks/restore.md`); production uses the secret store's snapshots or a KMS seal (slice E).
- **Rate limits and budgets:** built in slice O. Rate counts are exact (one atomic upsert per request). Budgets are checked before each model call, so calls already in flight when a model reaches its cap can overrun it by their own tokens.
