# knowledge-layer: instructions for Claude Code

Open-source, permission-aware knowledge platform: curated OKF bundles (Git, PR-reviewed) plus a hybrid-search evidence layer, behind one policy decision point. The full design is in `docs/design/v1.md`; read the relevant section before changing architecture. The current milestone is **slice I (compliance operations) built: data-subject requests, access reviews, retention and legal holds, persistent OpenBao, per-team encryption, backups and the restore drill, personal-data detection, the control map, and runbooks; next is F (connectors: F1, routing each repo to its own team, F2, Google Drive (ADR 0008), and F2b's Google sign-in linking are built; Google groups and incremental Drive sync remain), with L2 (spreadsheets, OpenDocument, email, plain text, RTF, and OCR for scans and images) built, D4b (screens for everything that had only an API) built, and J1 (CI pipelines pushing with workload tokens, ADR 0007) and J2 (importing Confluence and Notion exports) built; left before deployment: the drill-down slices R1–R4 (R1's conversation backend is built; its workspace UI is next), and optionally Google groups and incremental Drive sync, with deployment (E) and Q last, and D5's browser tests (`make e2e`) built, per `docs/design/build-plan.md`**. Build the remaining slices in that order: each must keep every `make smoke` check green and add its own (see §13 of the design doc).

## Invariants (IMPORTANT: never violate; ask before any change that touches one)

1. Every content read goes through the policy path in `services/api/kl/main.py`:
   1. Resolve scope.
   2. Query inside `db.scoped()`, where RLS pre-filters by ACL.
   3. Post-check every document in OpenFGA.
   4. Write an audit event.

   Never read content tables outside `scoped()`, and never skip the post-check.
2. Scope is not permission. Workspace or org scope only chooses where to look.
3. Never weaken RLS. Every new table with tenant data gets `org_id`, `ENABLE ROW LEVEL SECURITY`, and policies in the same migration. Services never connect as `kl_owner`.
4. Principals entering RLS context must pass `principals.is_valid`. Never build principal strings from unvalidated input.
5. Secrets never go into Postgres, logs, exceptions, traces, or Git. Store a `credential_ref` and read the value from `kl.vault` at the moment of use. Pydantic fields holding secrets use `repr=False`.
6. Authenticated calls act as the end user. Never add a shared or service key that bypasses document ACLs.
7. Content may go to a model (generation or embeddings) only if its classification is allowed for that endpoint (egress policy, M4).
8. Deny by default. Treat an unknown principal, missing context, or unresolved identity as no access, and fail closed on errors in authorization code.
9. Every new read surface (REST, MCP, webhook payload, export) adds permission-leak checks to `kl/smoke.py` (later, the leak suite) in the same PR.
10. No new infrastructure dependency without an ADR in `docs/adr/`.
11. Don't return counts of hidden items or distinguish "forbidden" from "not found" for content or private workspaces. Use 404.
12. Synced content carries only the source's permissions. ACL arrays hold source identities (`ext:github.<login>`), and a caller gets them only through a `principal_aliases` link, expanded inside `db.scoped()`. Never put workspace, group, or org principals on synced content: connecting a source must never widen who can read it.
13. Webhooks are hints, never truth. A delivery must pass HMAC verification and replay protection, and it may only queue jobs. Never read permissions or content from a webhook payload; the job re-reads the source.

## Conventions

- Python 3.12, FastAPI, psycopg 3 with plain SQL (no ORM), httpx. Keep modules small and typed.
- Schema changes are numbered migrations in `services/api/kl/migrations/NNNN_name.sql`. `provision` applies pending ones on every `make up`. Never edit an applied migration; add a new one. `deploy/postgres/sql/` is the frozen M0 baseline.
- Background work goes through the `jobs` table and `kl/worker.py` handlers. The worker runs as `kl_ingest` with its org context set, so RLS still applies.
- Embeddings carry their own copy of each chunk's ACL. Any code that changes a chunk's ACL must update `chunk_embeddings` in the same transaction, or the vector path leaks.
- Authorization model: `authz/model.json` is loaded by `kl.provision`. Keep the readable DSL in `docs/design/v1.md` §5.4 in sync.
- OKF: follow OKF v0.2 exactly; our extensions live only under the `x-kl` frontmatter key. Preserve unknown keys when round-tripping.
- Tests describe behavior in plain sentences (see `kl/smoke.py` docstrings). Write permission tests first.
- Never use dev credentials outside the local stack.

## Commands

- `make up`: build and start the stack.
- `make smoke`: run the smoke suite; it must pass before you call a task done.
- `make github-app ORG=<org>`: create the dev GitHub App via the manifest flow; credentials land in `.secrets/`.
- `make reset && make up`: re-run init SQL and the realm import after schema or seed changes.
- `docker compose logs <service>`: debug a service.
