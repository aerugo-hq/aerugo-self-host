#!/bin/sh
# Dev OpenBao that keeps its secrets and encryption keys across restarts (slice I3a). Dev mode (server -dev)
# holds everything in memory, so a restart lost every model key, and with per-team encryption keys it would
# make every encrypted record unreadable for good. This server stores to a volume, initializes once, and
# unseals itself on every start with a key kept on that same volume: fine for a laptop, never for
# production, where deployment (slice E) unseals properly. The services keep the fixed dev token they use.
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

# The token every service is configured with, created once and never expiring.
bao token lookup "$KL_DEV_TOKEN" >/dev/null 2>&1 || bao token create -id="$KL_DEV_TOKEN" -policy=root -orphan >/dev/null
bao secrets list | grep -q '^secret/' || bao secrets enable -path=secret -version=2 kv >/dev/null
bao secrets list | grep -q '^transit/' || bao secrets enable transit >/dev/null
echo "openbao: unsealed, storage on the kl-openbao volume"
wait "$SERVER"
