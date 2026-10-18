#!/usr/bin/env bash
# Deploys one release on the Aerugo VM. The deploy workflow (.github/workflows/deploy-gcp.yml) copies the release
# here and runs this over IAP SSH, as root, with the image tag to run. It reads the VM's settings (instance metadata)
# and every credential (Secret Manager) as the VM's own service account, writes them to a root-only .env, builds the
# production realm and seed tuples (prepare.py), makes sure OpenBao is initialized (Cloud KMS unseals it; its root
# token and recovery key go to Secret Manager, never to disk) and Keycloak has its database, then starts the release.
set -euo pipefail
TAG="${1:?usage: deploy.sh <image tag>}"
cd "$(dirname "$0")/../../.."   # the release's root

md() { curl -sf -H 'Metadata-Flavor: Google' "http://metadata.google.internal/computeMetadata/v1/$1"; }
attr() { md "instance/attributes/$1"; }
PROJECT="$(md project/project-id)"
ZONE="$(md instance/zone)"; ZONE="${ZONE##*/}"; REGION="${ZONE%-*}"
TOKEN="$(md instance/service-accounts/default/token | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"
SM="https://secretmanager.googleapis.com/v1/projects/$PROJECT/secrets"
secret() {
  curl -sf -H "Authorization: Bearer $TOKEN" "$SM/aerugo-$1/versions/latest:access" \
    | python3 -c 'import base64,json,sys; print(base64.b64decode(json.load(sys.stdin)["payload"]["data"]).decode(), end="")'
}

DOMAIN="$(attr aerugo-domain)"
REGISTRY="$(attr aerugo-registry)"
OWNER_PASSWORD="$(secret owner-password)"
KEYCLOAK_DB_PASSWORD="$(secret keycloak-db-password)"
TERRAFORM_SECRET="$(secret terraform-client-secret)"

umask 077   # .env holds every credential: root only
cat > .env <<ENV
KL_IMAGE_TAG=$TAG
KL_REGISTRY=$REGISTRY
KL_DOMAIN=$DOMAIN
KL_ACME_EMAIL=$(attr aerugo-acme-email)
KL_EMAIL_DOMAINS=$(attr aerugo-email-domains)
KL_GCP_PROJECT=$PROJECT
KL_OWNER_PASSWORD=$OWNER_PASSWORD
KL_APP_PASSWORD=$(secret app-password)
KL_INGEST_PASSWORD=$(secret ingest-password)
KL_FGA_PASSWORD=$(secret fga-password)
KL_WEB_PASSWORD=$(secret web-password)
KL_WEB_SESSION_KEY=$(secret web-session-key)
KL_BACKUP_CIPHER_PASS=$(secret backup-cipher)
KC_ADMIN_PASSWORD=$(secret keycloak-admin-password)
KL_KEYCLOAK_DB_PASSWORD=$KEYCLOAK_DB_PASSWORD
ENV

KL_DOMAIN="$DOMAIN" KL_FIRST_ADMIN="$(attr aerugo-admin)" KL_TERRAFORM_CLIENT_SECRET="$TERRAFORM_SECRET" \
  python3 deploy/gcp/vm/prepare.py
mkdir -p deploy/gcp/openbao
cat > deploy/gcp/openbao/prod.hcl <<HCL
# Written by deploy.sh: storage on the kl-openbao volume, unsealed by Cloud KMS through the VM's identity.
storage "file" {
  path = "/openbao/data"
}
listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = true # the compose network only: no port is published beyond the VM's loopback
}
seal "gcpckms" {
  project    = "$PROJECT"
  region     = "$REGION"
  key_ring   = "aerugo"
  crypto_key = "openbao-unseal"
}
api_addr = "http://openbao:8200"
HCL

COMPOSE=(docker compose -f docker-compose.yml -f deploy/gcp/compose.gcp.yml --profile web)
printf '%s' "$TOKEN" | docker login -u oauth2accesstoken --password-stdin "https://${REGISTRY%%/*}" >/dev/null
"${COMPOSE[@]}" pull --quiet

# OpenBao: initialized once. Cloud KMS unseals it, so the root token and recovery key are needed only now: they go
# straight to Secret Manager (aerugo-openbao-init), and the services sign in with the VM's identity.
"${COMPOSE[@]}" up -d --no-build postgres openbao
bao() { "${COMPOSE[@]}" exec -T -e BAO_ADDR=http://127.0.0.1:8200 "$@"; }
for _ in $(seq 60); do
  STATUS="$(bao openbao bao status -format=json 2>/dev/null || true)"
  [ -n "$STATUS" ] && break
  sleep 2
done
if [ "$(printf '%s' "$STATUS" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("initialized"))')" != "True" ]; then
  INIT="$(bao openbao bao operator init -recovery-shares=1 -recovery-threshold=1 -format=json)"
  curl -sf -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' "$SM/aerugo-openbao-init:addVersion" \
    -d "{\"payload\": {\"data\": \"$(printf '%s' "$INIT" | base64 -w0)\"}}" >/dev/null
  ROOT="$(printf '%s' "$INIT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["root_token"])')"
  unset INIT
  for _ in $(seq 30); do bao -e BAO_TOKEN="$ROOT" openbao bao token lookup >/dev/null 2>&1 && break; sleep 2; done
  bao -e BAO_TOKEN="$ROOT" openbao bao secrets enable -path=secret -version=2 kv >/dev/null
  bao -e BAO_TOKEN="$ROOT" openbao bao secrets enable transit >/dev/null
  echo "deploy: OpenBao initialized; its root token and recovery key are in Secret Manager (aerugo-openbao-init)"
fi

# The services sign in with the VM's own identity (M6): Google signs an identity token for OpenBao's audience that
# names the VM's service account, OpenBao checks it against Google's published keys, and each sign-in gets an hour with
# exactly the services' paths (deploy/openbao/services.hcl). Nothing long-lived is stored. Set up on every deploy, with
# the root token read from Secret Manager for this alone and never written down.
ROOT="${ROOT:-$(secret openbao-init | python3 -c 'import json,sys; print(json.load(sys.stdin)["root_token"])')}"
for _ in $(seq 30); do bao -e BAO_TOKEN="$ROOT" openbao bao token lookup >/dev/null 2>&1 && break; sleep 2; done
root() { bao -e BAO_TOKEN="$ROOT" openbao bao "$@"; }
root policy write aerugo-services - < deploy/openbao/services.hcl >/dev/null
root auth list | grep -q '^gcp/' || root auth enable -path=gcp jwt >/dev/null
root write auth/gcp/config oidc_discovery_url=https://accounts.google.com bound_issuer=https://accounts.google.com >/dev/null
printf '{"role_type": "jwt", "user_claim": "sub", "bound_audiences": ["openbao://aerugo"], "bound_claims": {"email": "%s"},
  "token_policies": ["aerugo-services"], "token_ttl": "1h", "token_max_ttl": "1h"}' "$(md instance/service-accounts/default/email)" \
  | root write auth/gcp/role/aerugo-services - >/dev/null
# A static services' token from before M6 is revoked, so nothing long-lived stays valid: any token holding their
# policy that was made by hand (auth/token/create...) rather than by signing in.
for ACCESSOR in $(root list -format=json auth/token/accessors | python3 -c 'import json,sys; print(" ".join(json.load(sys.stdin)))'); do
  if root token lookup -accessor -format=json "$ACCESSOR" | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]
sys.exit(0 if "aerugo-services" in d["policies"] and d["path"].startswith("auth/token/create") else 1)'; then
    root token revoke -accessor "$ACCESSOR" >/dev/null
    echo "deploy: revoked a static services' OpenBao token (M6: the services sign in now)"
  fi
done
unset ROOT

# Keycloak keeps its users in Postgres, in a database of its own.
for _ in $(seq 60); do "${COMPOSE[@]}" exec -T postgres pg_isready -h 127.0.0.1 -q && break; sleep 2; done
psql_owner() { "${COMPOSE[@]}" exec -T -e PGPASSWORD="$OWNER_PASSWORD" postgres psql -h 127.0.0.1 -U kl_owner -d kl -v ON_ERROR_STOP=1 -tAc "$1"; }
if [ "$(psql_owner "SELECT 1 FROM pg_roles WHERE rolname = 'keycloak'")" != "1" ]; then
  psql_owner "CREATE ROLE keycloak LOGIN PASSWORD '$KEYCLOAK_DB_PASSWORD'" >/dev/null
  psql_owner "CREATE DATABASE keycloak OWNER keycloak" >/dev/null
  echo "deploy: Keycloak's database created"
fi

"${COMPOSE[@]}" up -d --no-build --remove-orphans
docker image prune -f >/dev/null
echo "deploy: $TAG is live at https://app.$DOMAIN"
