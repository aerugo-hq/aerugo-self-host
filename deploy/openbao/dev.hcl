# Dev OpenBao with storage on a Docker volume (slice I3a), started by start.sh. Not for production: the unseal
# key lives on the same volume, and the listener is plain HTTP inside the compose network.
storage "file" {
  path = "/openbao/data"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = 1
}

api_addr      = "http://openbao:8200"
disable_mlock = true
ui            = false
