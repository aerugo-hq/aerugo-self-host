# Production review

A review of the code at the tip of the build (`g-governance`), against what a company's production system needs:
security, reliability, scale, operability, and testing. 2026-10-16. Read with `docs/REVIEW.md` (process, the claim
checker, real-vendor validation); this adds the code-level findings. Fix the critical and high findings before any
company's data goes in; each says what was found, where, why it matters, and what fixed looks like.

**Verdict:** the security core is production-grade, and better than most systems at this stage. Reliability and scale
are not yet: one critical bug can stop a connection syncing forever, and the system as written runs well only as a
single instance under light load.

## What's already production-grade (keep it that way)

- **Authentication** (`kl/auth.py`): RS256 pinned (a token can't choose its algorithm); issuer, audience, expiry, and
  issue time required; a disabled service account refused on its next call; group names from tokens sanitized; an
  email used only when the identity provider verified it.
- **Authorization:** row-level security set per transaction (`kl/db.py`, `scoped`), relationship checks
  (`kl/policy.py`), and every route authenticated; the six without a dependency verify their own way by design (health
  checks, pipeline routes checking signed pipeline tokens, the webhook checking signatures).
- **SQL:** parameterized throughout; the two dynamic `SET` clauses take column names from fixed lists, never the request.
- **Outbound calls:** a timeout on every one, no redirects followed, TLS never disabled, addresses allowlisted,
  pagination on the same host only.
- **Webhooks** (`kl/hooks.py`): a secret per connection, signatures checked first in constant time, replays refused.
- **Supply chain** (`services/api/Dockerfile`): a hash-pinned lockfile installed with `--require-hashes`, the build
  stopped if it's stale, a non-root user, and test tooling stripped from the image that ships.
- **Abuse and cost:** per-user and per-team rate limits with `Retry-After`; monthly model budgets, with admins warned.
- **Privacy in operations:** no query or question text in logs or traces; no auto-instrumentation recording SQL or URLs.
- **The job queue** (`kl/worker.py`): `FOR UPDATE SKIP LOCKED`, retries with backoff, failures visible on the job and
  its connection, `SIGTERM` handled, heartbeats feeding alerts; no exception swallowed silently anywhere in the core.
- **Errors to callers:** crafted messages only; the model gateway's carry a status or a type, never a provider's body.
- **Web sign-in** (`services/web/lib/session.js`): PKCE, state bound to a cookie, a nonce; tokens never reach the
  browser (an opaque `httpOnly`, `SameSite` cookie, `Secure` under HTTPS); cross-site writes refused by origin.
- **Slack's readers** (`kl/slack.py`): the org only for a public, unshared channel on a confirmed company workspace;
  otherwise members of this workspace who haven't left and aren't bots; past 50,000 messages the sync fails rather
  than reading the rest as deleted.

## Critical

**C1. A job a dead worker leaves `running` is never reclaimed, and its connection never syncs again.**
`CLAIM_SQL` (`kl/worker.py`) sets `locked_at`, but nothing reads it (the only reset is an admin's manual retry,
`main.py` around line 2415). `SCHEDULE_SQL` skips any connection with a job `queued` or `running`. A worker killed
mid-sync (a deploy past its grace period, out of memory, a crash) leaves that connection's job `running` forever: no
new content, and no permission changes, revocations included. Deploys make this routine.
*Fixed when:* every running job has a lease (claimed until `locked_at` plus a per-kind duration, extended by the
handler as it progresses); a reaper on the scheduler's tick re-queues expired leases (counting the attempt); on
`SIGTERM` the worker stops at a safe point and re-queues its job; and smoke kills a worker mid-sync and sees the
connection sync to completion afterwards.

## High

**H1. No database connection pool, and no statement timeout.** `connect()` (`kl/db.py`) opens a new Postgres
connection for every `scoped()` block, every rate-limit check, and every service-account check. Under load that's
latency on each request and exhausted `max_connections`. With no `statement_timeout` or
`idle_in_transaction_session_timeout`, a runaway query or a stuck transaction holds a connection indefinitely.
*Fixed when:* a `psycopg_pool` pool per process (sized against Postgres's limit and the process count), timeouts set
per role (short for the API, longer for the worker), and readiness failing when the pool can't serve.

**H2. Only Slack survives a rate limit.** Jira, Confluence, Drive, Okta, Google's directory, Graph, Notion, and the
catalogs don't honor 429 or `Retry-After`: one throttled call fails a whole sync, and Confluence makes a call per page.
Slack's retry lives inside its own client. *Fixed when:* one shared HTTP helper (429 and 503, `Retry-After`, capped
exponential backoff with jitter, a per-connection concurrency cap) carries every connector's calls, the fakes can
throttle, and smoke proves a sync survives it.

**H3. Web sessions live in one process's memory** (`services/web/lib/session.js`, which says "fine for dev"). A
restart signs everyone out, a second replica can't see the first one's sessions (so the app can't scale past one
instance), and sessions never expire (only sign-out removes one), so memory grows and refresh tokens are kept
forever. *Fixed when:* sessions live in a shared store (Postgres or Redis) with an idle and an absolute lifetime,
refresh tokens are encrypted at rest, and expired sessions are swept.

**H4. The claim checker passes contradictions** (`kl/claims.py`; `docs/REVIEW.md`, finding 5): with "not" among its
stop words and short words dropped, a negated claim and a wrong number are judged supported. *Fixed when:* negations
and every number count, a mismatch is unsupported, and those cases are in its tests and in smoke.

**H5. Network calls inside open database transactions.** OpenBao writes, rotations, and destructions, relationship
checks, and search's embedding call (`main.py` around line 204) run inside `scoped()`: a slow OpenBao or model holds
a connection, and a transaction that fails after a secret was written leaves the secret orphaned. *Fixed when:*
external calls happen outside the transaction, with the secret written first and destroyed if the transaction then
fails (or an outbox the worker completes), and search embeds the query before opening its transaction.

**H6. Every sync re-reads everything.** Containers are listed in full and permissions re-read item by item every
sync; Slack re-reads its whole history window (a year by default) each time. Correct, but slow and costly at a real
company's size. *Fixed when:* each source reads changes since its last cursor where it offers one (Jira's `updated`,
Confluence's last-modified order, Drive's changes feed, Graph's delta, Slack's cursors), with a full reconcile on a
slower cadence for deletions and permissions.

## Medium

- **M1. No Content Security Policy or HSTS** (`services/web/next.config.mjs`). Add a CSP allowing only the app's own
  scripts and the video players the reader embeds (YouTube's privacy domain, Vimeo, Loom), and HSTS (or set it at
  Caddy, the edge).
- **M2. Concurrent token refreshes race** (`accessToken` in `session.js`): with refresh-token rotation, a user with
  several tabs can be signed out at random. Refresh once per session at a time.
- **M3. The API runs as one Uvicorn process.** Run several workers, sized with the pool (H1).
- **M4. No unit tests.** Every test is a smoke test against the full stack. Add fast tests (pytest) for the pure
  logic that decides who reads what (each connector's readers function), the parsers, and the claim checker, run
  first in CI.
- **M5. The webhook receiver reads the whole body before checking its size** (`kl/hooks.py`): stream it with a cap.
- **M6. The services' OpenBao token lives ten years with no rotation** (G1). Add a rotation: a new token minted and
  rolled out before the old one is revoked, on a schedule, or an auth method the services log in with themselves.
- **M7. No time limit on a job.** A handler that hangs (an endless listing) holds its worker; give each kind a limit,
  enforced with the lease (C1).
- **M8. The cross-site check fails open without an `Origin` header** (the API proxy, `app/api/[...path]/route.js`).
  `SameSite=Lax` covers most of it; also require a matching `Referer` when `Origin` is absent.
- **M9. The web server's calls to the API have no timeout, and uploads are held in memory** (up to 51 MB each).
- **M10. Measurement and reality** (`docs/REVIEW.md`, findings 10 and 11): the evaluation set is saturated, and every
  source is proven only against fakes.

## Low

- **L1.** Base images are pinned by tag, not by digest: a rebuild can pick up a different image.
- **L2.** Builds skip ESLint (`ignoreDuringBuilds`); run it in CI instead.
- **L3.** The web server reads the ID token's nonce without verifying its signature. It arrives over TLS straight
  from the token endpoint, which makes this acceptable, but verifying it is cheap.
- **L4.** On Slack Enterprise Grid, members of sibling workspaces get no access to a channel shared between them
  (failing closed, as designed). Say so in `docs/connectors/slack.md`.

## Status, and the permissions audit (after C1)

**Done and verified, 2026-10-18:** L3 (`l3-id-token-signature`): a sign-in's ID token is verified (signature,
issuer, audience, expiry, nonce) against the identity provider's published keys before a session is made.

**Done and verified, 2026-10-18:** L2 (`l2-eslint-in-ci`): the web app's lint runs in CI, errors failing it and
warnings held to today's count; the web image is two stages, so the lint's packages never ship.

**Done, 2026-10-18:** L4 (`l4-slack-grid-doc`): the Slack doc says a channel shared between Enterprise Grid's
sibling workspaces reaches only the connected workspace's members, failing closed.

**Done and verified, 2026-10-18:** L1 (`l1-images-by-digest`): every image Aerugo ships is pinned by digest, checked
in CI (`make pins`), with Dependabot proposing new digests.

**Done and verified, 2026-10-18:** M10, its evaluation half (`m10-evaluation-set`): 49 cases with traps (near
duplicates, stale against current, distractors, two-source answers through the fakes, deep lineage, permission traps
in every kind of source), a gate that fails on rank falls and on Ask's citations, and a new baseline that names what
now misses. The real-vendor half (contract fixtures) is set aside by decision.

**Done and verified, 2026-10-17:** M6 (`m6-openbao-platform-login`): the services sign in to OpenBao with the
platform's identity (a Kubernetes service account, or the GCP VM's), an hour at most; no OpenBao token is stored in a
deployment. The cluster's side is proven on Docker Desktop's cluster; the VM's, at a real deploy.

**Done and verified, 2026-10-17:** M9 (`m9-api-timeouts-uploads`): the web server's calls to the API have a time
limit (504 when it passes), and request bodies stream through, cut off past the upload limit, never held whole.

**Done and verified, 2026-10-17:** M8 (`m8-referer-check`): a write through the API proxy without an `Origin` must
carry the app's own `Referer`; one with neither is refused.

**Done and verified, 2026-10-17:** M7 (`m7-job-time-limits`): every job kind has a time limit (changeable per
deployment); at it, the lease settles the job as a failed attempt and ends the worker held by it, which is restarted.

**Done and verified, 2026-10-17:** M5 (`m5-webhook-body-cap`): both webhook receivers read a body only up to their
limit, refusing a declared length over it at the headers and a streamed one where it passes the limit.

**Done and verified, 2026-10-17:** M3 (`m3-api-processes`): the API runs several processes (2 by default), each
with its own pool of 10, the connection budget written down; each loads its model before it serves.

**Done and verified, 2026-10-17:** M1 (`m1-csp-hsts`): a Content Security Policy with a fresh nonce on every page,
frames only from the reader's video players; HSTS at Caddy on every public address.

**Done and verified, 2026-10-17:** M4 (`m4-unit-tests`): 51 unit tests (each connector's readers, the parsers, the
claim checker), run first in CI, shown to catch the bugs they guard.

**Done and verified, 2026-10-17:** H3 with M2 (`h3-m2-web-sessions`): web sessions in Postgres (a hash of the
cookie, tokens encrypted, a role that reaches nothing else, idle and absolute lifetimes, swept by the worker), shared
by every web server; one refresh per session at a time, with Keycloak rotating refresh tokens.

**Done and verified, 2026-10-17:** H6b (`h6b-expired-cursors`, the Phase 0 report's first risk): a cursor Graph or
Drive no longer accepts is read in full at once, instead of failing every attempt until the connection stopped.

**Done and verified, 2026-10-17:** P1 to P3, H1, H5, H2, H6, and H4, one pull request with the plans
(`o0-h2-h6-http-and-cursors`), each recorded in `CLAUDE.md` with what proved it. The whole loop at its tip found two
failures, now fixed: H6 had left a group's membership change waiting for the next readers refresh (S5's check), and
P3's check asked with cursor 0 on a fresh database.

**Done and verified:** C1 (`c1-job-leases`, merged as pull request 23): every running job holds a lease renewed in the
background, a reaper takes over expired ones, a stopping worker puts its job back without counting the attempt, and
each run is fenced by its attempt number, so a worker that comes back late can't settle a job taken over. Proven red
then green, and the whole loop is green at its commit: smoke 155 passed (the S2 to G3 checks included), 31 browser
tests, 12 provider acceptance tests, no evaluation regression. `main` on GitHub holds the chain; a local `main` is
stale until pulled. Migration 0040 fixed a real bug: two connections to two sites could share keys.

**The provider repository** is on `main` at `f127b3d`, with nothing that `origin/main` lacks (checked 2026-10-17).

**The permissions audit** (migrations 0039 to 0049; row-level security is where every read is finally decided):

- **P1. Three tables are scoped to the org, but hold what's scoped to a team or a document.** Their policies check
  only `org_id`, so any signed-in person can read every row of them whenever a query reads the table without joining
  through the protected one; today's queries may all join, but a future one that doesn't leaks, and the database
  won't stop it:
  - `asset_owners` (0042): every team's asset owners, by email, though its own comment says only the asset's team sees them;
  - `document_authors` (0046): who wrote documents the person can't read, and that those documents exist;
  - `page_reviews` (0048): other teams' review history of their curated pages, free-text notes included.

  *Fixed when:* each policy checks the caller can see what the row is about. A subquery against the protected table
  runs under that table's own row-level security, so `USING (org_id = kl.current_org() AND EXISTS (SELECT 1 FROM
  documents d WHERE d.id = document_id))` shows `document_authors` rows only for documents the caller may read; the
  same with `assets` for `asset_owners`, and with the page's concept and team for `page_reviews`. Smoke proves a
  stranger reads none of them directly.
- **P2. `team_media`'s writes are guarded only by the API** (0047: `kl_app` may write any team's row). A bug there
  would let one team choose the models another team's documents are sent to. Add a policy `WITH CHECK` on the team.
- **P3. The change feed is never pruned** (`document_changes`, 0049): every add, change, and removal is kept forever.
  Keep a window (90 days, say), and say how far back `since` can reach.
- **Done right:** `directory_groups` (0039: only the worker reads it; the API has no grant at all), the change feed's
  reading (a change shown only to its readers, a removal only to who could read it before), and 0040's fail-closed
  cleanup.

## Order

C1 first (it silently stops permission changes, the product's core promise). Then H1 and H5 together (both are about
connections), H2 and H6 together (both are about the HTTP helper and cursors), H3 with M2 (sessions), H4. Then the
mediums, M4 early (the unit tests make every later fix safer). Each fix on its own branch, proven in the stack before
the next, as `docs/REVIEW.md` asks.
