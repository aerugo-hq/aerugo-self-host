# Aerugo on a local Kubernetes cluster

The whole stack on kind (or Docker Desktop's Kubernetes): the same services, settings, and order as
`docker-compose.yml`, in the `aerugo` namespace, reached at the same addresses. Development credentials and people
only: it refuses a cluster that isn't local.

## Run it

Stop Compose's stack first (`make down`): it uses the same ports. Then, from the repository's root:

```powershell
make kind-up     # builds the images, loads them into the cluster, applies deploy/k8s, waits (first run: ~10 minutes)
make kind-open   # serves it at http://localhost:3000; leave it running
```

Sign in as `alice` / `alice-dev-only` (or bob, carol, dave). Keycloak's admin console is at
`http://localhost:8081/admin` (`admin` / `admin-dev-only`). `make kind-down` removes everything, data included.

Without make: `powershell -ExecutionPolicy Bypass -File deploy/k8s/up.ps1`, then `deploy/k8s/open.ps1`.
After a code change, `make kind-up` again: it rebuilds, reloads, and restarts what changed.

## What it runs

| | | |
|---|---|---|
| `postgres` | StatefulSet, two volumes | pgvector and pgBackRest; `deploy/postgres` initializes it |
| `openfga` | Deployment, after the `openfga-migrate` Job | permissions |
| `openbao` | StatefulSet, a volume | secrets; the dev server that keeps its data |
| `keycloak` | StatefulSet, a volume | sign-in; the dev realm, and any users you add, kept |
| `provision` | Job | migrations, the authorization model, OpenBao's mounts, the GitHub App's credentials |
| `api`, `worker`, `mcp` | Deployments | each waits for provisioning, as Compose's `depends_on` does |
| `web` | Deployment | the app |

The repository's own files become ConfigMaps on each `make kind-up` (the Postgres init SQL, OpenBao's config, the
realm and its theme, the authorization model), so the file you edit is the file the cluster uses. There is no demo
content: connect your own sources from Admin, then Sources.

## With your real sources

- **Sign in as yourself**, so a source's permissions match you: in Keycloak's console, in the **acme** realm, add a
  user with your real email (marked verified) and a password. Add your email's domain to `KL_ORG_EMAIL_DOMAINS` in
  `app.yaml` (`acme.test,yourdomain.com`), then `make kind-up`.
- **A GitHub App** (`make github-app ORG=<org>`) writes `.secrets/`; `make kind-up` loads it.
- **Ask needs a model.** Register a hosted one under Admin, then Models. A model running on your machine (Ollama)
  isn't reachable from inside kind by default.
- **Webhooks can't reach your machine**: syncs run on their schedule instead.

## When something's stuck

```powershell
kubectl -n aerugo get pods                      # what's running, waiting, or failing
kubectl -n aerugo logs job/provision            # provisioning: migrations and the authorization model
kubectl -n aerugo logs deploy/api               # or worker, mcp, web; statefulset/keycloak
kubectl -n aerugo describe pod <name>           # why a pod won't start (an image not loaded: ErrImagePull)
```

- **A forward stopped** (`make kind-open` says so): its pod restarted. Run `make kind-open` again.
- **`ErrImagePull` for an `aerugo/` image**: it wasn't loaded into the cluster. Install the kind CLI
  (`winget install Kubernetes.kind`) and `make kind-up` again.
- **A changed realm** (`deploy/keycloak/realm-acme.json`) is imported only into a new Keycloak volume:
  `kubectl -n aerugo delete statefulset keycloak; kubectl -n aerugo delete pvc users-keycloak-0`, then `make kind-up`
  (this removes users you added).
