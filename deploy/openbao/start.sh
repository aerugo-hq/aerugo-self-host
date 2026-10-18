#!/bin/sh
# Dev OpenBao that keeps its secrets and encryption keys across restarts (slice I3a). Dev mode (server -dev)
# holds everything in memory, so a restart lost every model key, and with per-team encryption keys it would
# make every encrypted record unreadable for good. This server stores to a volume, initializes once, and
# unseals itself on every start with a key kept on that same volume: fine for a laptop, never for
# production, where deployment (slice E) unseals properly. The services use their own scoped token (G1) in Compose; in
# Kubernetes they sign in with their own service accounts instead, and no services' token exists (M6).
set -eu
DATA=/openbao/data
INIT="$DATA/init.txt"
export BAO_ADDR=http://127.0.0.1:8200

bao server -config=/openbao/config/dev.hcl &
SERVER=$!

# Wait for the listener: `bao status` exits 0 when unsealed, 2 when sealed, and 1 when it can't connect.
until bao status >/dev/null 2>&1 || [ $? -eq 2 ]; do sleep 0.5; done

if [ ! -s "$INIT" ]; then
  bao operator init -key-shares=1 -key-threshold=1 > "$INIT.tmp"
  mv "$INIT.tmp" "$INIT"
fi
bao operator unseal "$(sed -n 's/^Unseal Key 1: //p' "$INIT")" >/dev/null
BAO_TOKEN="$(sed -n 's/^Initial Root Token: //p' "$INIT")"
export BAO_TOKEN

# The operator's dev token (root), for poking at OpenBao by hand. No service holds it.
bao token lookup "$KL_DEV_TOKEN" >/dev/null 2>&1 || bao token create -id="$KL_DEV_TOKEN" -policy=root -orphan >/dev/null
# The services' policy (G1): exactly their paths (services.hcl), never root.
bao policy write aerugo-services /openbao/config/services.hcl >/dev/null
if [ -n "${KL_K8S_SERVICE_ACCOUNTS:-}" ]; then
  # Kubernetes (M6): a pod signs in with its own service account's token, which OpenBao has the cluster check
  # (TokenReview, as OpenBao's own service account, bound to system:auth-delegator). Only the named service accounts
  # in this namespace pass, and each sign-in gets an hour.
  bao auth list | grep -q '^kubernetes/' || bao auth enable kubernetes >/dev/null
  bao write auth/kubernetes/config kubernetes_host="https://$KUBERNETES_SERVICE_HOST:$KUBERNETES_SERVICE_PORT" >/dev/null
  bao write auth/kubernetes/role/aerugo-services bound_service_account_names="$KL_K8S_SERVICE_ACCOUNTS" \
    bound_service_account_namespaces="$KL_K8S_NAMESPACE" token_policies=aerugo-services token_ttl=1h token_max_ttl=1h >/dev/null
else
  # Compose (dev and CI): a static token scoped to their paths. A token that isn't root expires with the token mount's
  # default (32 days) and nothing renews it, so the mount's ceiling rises and this one lasts ten years. Made before
  # Transit is mounted, so the healthcheck (the services' token listing Transit) means it works.
  [ "$KL_SERVICES_TOKEN" != "$KL_DEV_TOKEN" ] || { echo "openbao: the services' token can't be the root one" >&2; exit 1; }
  bao auth tune -max-lease-ttl=87600h token/ >/dev/null
  bao token lookup "$KL_SERVICES_TOKEN" >/dev/null 2>&1 || \
    bao token create -id="$KL_SERVICES_TOKEN" -policy=aerugo-services -orphan -ttl=87600h >/dev/null
fi
bao secrets list | grep -q '^secret/' || bao secrets enable -path=secret -version=2 kv >/dev/null
bao secrets list | grep -q '^transit/' || bao secrets enable transit >/dev/null
echo "openbao: unsealed, storage on the kl-openbao volume"
wait "$SERVER"
