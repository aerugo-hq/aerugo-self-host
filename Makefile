# Recipes start with ">" instead of a tab (.RECIPEPREFIX, GNU make 3.82+), so editors can't break them.
.RECIPEPREFIX = >
.PHONY: sdk-test pins lint-web test-web unit env up smoke eval eval-mine demo eval-scenario company eval-company enterprise atlas-eval eval-extraction eval-baseline llm backup backups restore-drill web e2e obs lock github-app curator-app sandbox-github ps logs down reset kind-up kind-open kind-check kind-down

# The local model `make llm` downloads for Ask. About 1 GB; make llm LLM_MODEL=qwen2.5:3b for better answers (about 2 GB).
LLM_MODEL ?= qwen2.5:1.5b

## Provision the stack, run the smoke suite, create the GitHub App (make github-app ORG=<org>), inspect, tear down.

env:
>@test -f .env || cp .env.example .env

up: env
>docker compose up -d --build
>@echo 'Stack is starting. Keycloak takes ~30-60s on first boot. Then: make smoke'

# Unit tests (M4): the logic that decides who reads what, the parsers, and the claim checker, in seconds and with no
# service running. CI runs them before anything else.
pins:   # L1: every image Aerugo ships is pinned by digest
>sh scripts/check-pinned-images.sh

lint-web:   # L2: the web app's lint (ESLint, Next's rules), in the npm tool image; node_modules stays out of the tree
>docker compose --profile tools run --rm -v /app/node_modules lock-web sh -c "npm ci --no-audit --no-fund && npm run lint"

test-web:   # L3: the web app's unit tests, on Node's built-in runner (no dependencies)
>docker compose --profile tools run --rm lock-web node --test "lib/*.test.mjs"

unit:
>docker compose --profile smoke build smoke
>docker compose --profile smoke run --rm --no-deps smoke python -m pytest -q -p no:cacheprovider tests

smoke: env
>docker compose --profile smoke build smoke fake-llm
>docker compose --profile smoke up -d --force-recreate fake-llm
>docker compose --profile smoke run --rm smoke

# The web app, separate from `make up` so the backend never depends on its build. Its image fetches the sources'
# brand marks as it builds (services/web/scripts/brand-icons.mjs).
web: env
>docker compose --profile web up -d --build web
>@echo 'Web app: http://localhost:3000 (alice / alice-dev-only, or bob, carol, dave with <name>-dev-only)'

# Aerugo on a local Kubernetes cluster (kind, or Docker Desktop's), from deploy/k8s: kind-up builds and loads the
# images, applies, and waits; kind-open serves it at Compose's addresses (stop Compose first: make down); kind-down
# removes it, its data included.
kind-up:
>powershell -NoProfile -ExecutionPolicy Bypass -File deploy/k8s/up.ps1

kind-open:
>powershell -NoProfile -ExecutionPolicy Bypass -File deploy/k8s/open.ps1

kind-check:   # M6: secrets reached only by signing in as each service's own account
>powershell -NoProfile -ExecutionPolicy Bypass -File deploy/k8s/check.ps1

kind-down:
>kubectl delete namespace aerugo --ignore-not-found

# Browser tests (slice D5): Chromium signs in at Keycloak and uses the web app as people do. Needs `make up`; starts
# the web app, and a second web server (web-b, H3) built from the same code. Failures leave screenshots and traces
# in services/e2e/results.
e2e: env
>docker compose --profile web --profile e2e up -d --build web web-b web-c
>docker compose --profile web --profile e2e build e2e
>docker compose --profile web --profile e2e run --rm e2e

# Observability (slice G): turns telemetry on (KL_OTEL_ENDPOINT in .env; delete that line to turn it off),
# starts the collector, Prometheus, Jaeger, and Grafana, and restarts the services so they export.
# Optional: alerts live in the app (slice P, ADR 0005), so nothing here is needed for them.
obs: env
>grep -q '^KL_OTEL_ENDPOINT=' .env || printf '\nKL_OTEL_ENDPOINT=http://otel-collector:4318\n' >> .env
>docker compose --profile observability up -d otel-collector prometheus jaeger grafana
>docker compose up -d --build api worker mcp
>@echo 'Grafana: http://localhost:3001, or your KL_GRAFANA_PORT (first boot takes a minute) | Jaeger: http://localhost:16686 | Prometheus: http://localhost:9090'

# Answer quality (slice M): the golden set's questions through search, scored per question, stored per run.
eval:
>docker compose --profile smoke build smoke
>docker compose --profile smoke run --rm smoke python -m kl.eval

# Grow the golden set (slice M2b): feedback from the app, printed as candidate cases to review. Writes nothing.
eval-mine:
>docker compose --profile smoke build smoke
>docker compose --profile smoke run --rm smoke python -m kl.golden_mine

# The fictional migration company (sandbox/scenarios/migration, slice K1): a legacy warehouse moving to BigQuery, across
# Payments, Finance, Legal, and Commons. demo loads it for the Map and Ask; eval-scenario loads it and scores its
# questions, failing only on a permission leak. Smoke expects the plain seed: make reset && make up before make smoke.
demo: env
>docker compose --profile smoke build smoke
>docker compose --profile smoke run --rm smoke python -m kl.evals --load-only

eval-scenario: env
>docker compose --profile smoke build smoke
>docker compose --profile smoke run --rm smoke python -m kl.evals

# The synthetic retailer (F9, kl.company): a fictional outdoor retailer with known truth, loaded through the connectors
# from its own fake sites. company loads it (its people sign in as ana, cora, kai, and the rest, password <name>-dev-only);
# eval-company loads it and scores it: each person opens exactly what the truth says, no forbidden page shows, and no
# question does worse than its baseline. Smoke expects the plain seed: make reset && make up before make smoke.
company: env
>docker compose --profile smoke build smoke
>docker compose --profile smoke run --rm smoke python -m kl.company_eval --load-only

eval-company: env
>docker compose --profile smoke build smoke
>docker compose --profile smoke run --rm smoke python -m kl.company_eval

# The synthetic enterprise (F10b, kl.enterprise): the retailer's data estate, its EDW moving to the EDP, with known truth.
# enterprise loads it, the retailer too (its data people sign in as nia and otto); atlas-eval loads it and scores the
# Atlas: exactly what its sources reveal, each person seeing exactly what the truth says, and no source finding less
# than its baseline. Each builds the fakes too (DataHub serves the EDW), recreating them only when they changed. The
# Atlas keeps what it was told, so run it on a fresh stack; and before make smoke afterwards, make reset && make up.
enterprise: env
>docker compose --profile smoke build smoke fake-llm
>docker compose --profile smoke up -d fake-llm
>docker compose --profile smoke run --rm smoke python -m kl.enterprise_eval --load-only

atlas-eval: env
>docker compose --profile smoke build smoke fake-llm
>docker compose --profile smoke up -d fake-llm
>docker compose --profile smoke run --rm smoke python -m kl.enterprise_eval

# Extraction's quality (F2), measured once with the local model (run make llm first; nothing sent to it leaves the
# machine): every retailer team proposes units from its pages and tickets, scored against the truth's facts (recall,
# and precision as a lower bound). Reported, never gated: CI's fake model measures the pipeline, not quality.
eval-extraction: env
>docker compose --profile smoke build smoke
>docker compose --profile smoke run --rm smoke python -m kl.extraction_eval

# Record the evaluation sets' scorecards as their baseline (slices K1, Q1, F9, and F10b): the golden set on the plain seed
# first, then the scenario, which it loads, then the synthetic retailer, then its enterprise, which each load. From then on, make eval, make eval-scenario, and smoke fail when a case gets worse. Run it deliberately, when a change made things better (eval-scenario says so), and commit the file. A run
# with a permission leak, or one that fails, is never recorded: the baseline you had stays.
# Each step reads the file the step before it wrote (mounted over the image's copy, which is as it was built), so a step
# never puts back another set's old scorecard.
BASELINE_FILE = -v "$(CURDIR)/services/api/kl/evals_baseline.json:/app/kl/evals_baseline.json:ro"
eval-baseline: env
>docker compose --profile smoke build smoke
>docker compose --profile smoke run --rm -T $(BASELINE_FILE) smoke python -m kl.eval --record > services/api/kl/evals_baseline.tmp
>mv services/api/kl/evals_baseline.tmp services/api/kl/evals_baseline.json
>docker compose --profile smoke run --rm -T $(BASELINE_FILE) smoke python -m kl.evals --record > services/api/kl/evals_baseline.tmp
>mv services/api/kl/evals_baseline.tmp services/api/kl/evals_baseline.json
>docker compose --profile smoke run --rm -T $(BASELINE_FILE) smoke python -m kl.company_eval --record > services/api/kl/evals_baseline.tmp
>mv services/api/kl/evals_baseline.tmp services/api/kl/evals_baseline.json
>docker compose --profile smoke run --rm -T $(BASELINE_FILE) smoke python -m kl.enterprise_eval --record > services/api/kl/evals_baseline.tmp
>mv services/api/kl/evals_baseline.tmp services/api/kl/evals_baseline.json

# The clients (slice A2): the Python and TypeScript clients' own tests, against stand-ins for the API, in pinned images.
sdk-test:
>docker run --rm -v "$(CURDIR)/sdk/python:/sdk" -w /sdk python:3.12.11-slim sh -c "pip install -q 'httpx>=0.27,<1' 'pytest>=8,<9' && python -m pytest -q"
>docker run --rm -v "$(CURDIR)/sdk/typescript:/sdk" -w /sdk node:24.9.0-alpine node --test test/client.test.ts

# A real model for Ask, running in the stack (Ollama): no API key, and nothing sent to it leaves the machine.
# Downloads LLM_MODEL once, checks it answers, and registers it in every team as <team>/local. Safe to rerun.
llm: env
>docker compose --profile llm up -d ollama
>docker compose --profile smoke build smoke
>docker compose --profile smoke run --rm -e KL_LLM_MODEL=$(LLM_MODEL) smoke python -m kl.local_llm

# Backups (slice I4, ADR 0006): an encrypted pgBackRest backup of the database, incremental after the first full one.
backup:
>docker compose exec -T -u postgres postgres pgbackrest --stanza=kl backup

# What the backup repository holds: each backup, its size, and the WAL archived since.
backups:
>docker compose exec -T -u postgres postgres pgbackrest --stanza=kl info

# Restore drill (slice I4): mark the live database, back it up, restore the newest backup into a throwaway database
# (pg-drill), check the copy, and record the result in kl.backup_drills. The live database and the backups are
# never touched, and the throwaway database is removed whether the drill passes or not.
restore-drill:
>docker compose --profile smoke build smoke
>docker compose --profile drill rm -sfv pg-drill
>docker compose --profile smoke run --rm smoke python -m kl.drill mark
>docker compose exec -T -u postgres postgres pgbackrest --stanza=kl backup
>docker compose --profile drill up -d --wait pg-drill
>docker compose --profile smoke run --rm smoke python -m kl.drill verify; status=$$?; docker compose --profile drill rm -sfv pg-drill; exit $$status

# Hash-pinned lockfiles for the API (requirements.lock) and the web app (package-lock.json). Commit both;
# the images install from them. Re-run after changing requirements.txt or package.json.
lock:
>docker compose --profile tools run --rm lock-python
>docker compose --profile tools run --rm lock-web
>@echo 'Wrote services/api/requirements.lock and services/web/package-lock.json. Commit them, then make up.'

github-app: env
>docker compose --profile tools build github-app-setup
>docker compose --profile tools run --rm --service-ports github-app-setup

# The curator App opens compiler proposals as PRs. Install it only on your *-knowledge repos.
curator-app: env
>docker compose --profile tools build github-app-setup
>docker compose --profile tools run --rm --service-ports -e KL_APP_KIND=curator github-app-setup

sandbox-github:
>bash scripts/sandbox-github.sh "$(ORG)"

ps:
>docker compose ps -a

logs:
>docker compose logs -f --tail=100

down:
>docker compose down

reset:
>docker compose --profile smoke down -v --remove-orphans
