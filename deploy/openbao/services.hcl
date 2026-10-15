# The services' OpenBao policy (G1): what the API, the worker, the MCP server, and provisioning use, and nothing more.
# Secrets live under secret/kl/ (the ones Aerugo stores, and the ones teams reference; the team fence is the API's).
# Encryption keys are transit keys named kl-<org>-... (a team's, or the org's). Provisioning reads the mount list to
# check KV v2 is there. No other mount, no sys/ beyond that list, no policy, no token, no auth method.

path "secret/data/kl/*" {
  capabilities = ["create", "read", "update"]
}

# Removing a secret with every version it had (destroy), and an old version after a rotation (rotate).
path "secret/metadata/kl/*" {
  capabilities = ["delete"]
}
path "secret/destroy/kl/*" {
  capabilities = ["update"]
}

# A team's key: made when the team is, deletable when it's purged (which shreds what it encrypted).
path "transit/keys/kl-*" {
  capabilities = ["create", "read", "update", "delete"]
}
path "transit/encrypt/kl-*" {
  capabilities = ["update"]
}
path "transit/decrypt/kl-*" {
  capabilities = ["update"]
}

path "sys/mounts" {
  capabilities = ["read"]
}
