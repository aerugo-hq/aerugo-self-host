# Recipes start with ">" instead of a tab (.RECIPEPREFIX, GNU make 3.82+), so editors can't break them.
.RECIPEPREFIX = >
.PHONY: env up smoke eval eval-mine backup backups restore-drill web e2e obs lock github-app curator-app sandbox-github ps logs down reset

## Provision the stack, run the smoke suite, create the GitHub App (make github-app ORG=<org>), inspect, tear down.

env:
>@test -f .env || cp .env.example .env

up: env
>docker compose up -d --build
>@echo 'Stack is starting. Keycloak takes ~30-60s on first boot. Then: make smoke'

smoke: env
>docker compose --profile smoke build smoke fake-llm
>docker compose --profile smoke up -d --force-recreate fake-llm
>docker compose --profile smoke run --rm smoke

# The web app, separate from `make up` so the backend never depends on its build.
web: env
>docker compose --profile web up -d --build web
>@echo 'Web app: http://localhost:3000 (alice / alice-dev-only, or bob, carol, dave with <name>-dev-only)'

# Browser tests (slice D5): Chromium signs in at Keycloak and uses the web app as people do. Needs `make up`; starts
# the web app. Failures leave screenshots and traces in services/e2e/results.
e2e: env
>docker compose --profile web up -d --build web
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
