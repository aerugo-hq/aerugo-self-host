#!/bin/sh
# L1: every image Aerugo ships is pinned by digest, so a rebuild or a pull never quietly picks up a different image.
# Lists any reference that isn't, and fails. Images Aerugo builds itself (aerugo/..., kl-postgres, the registry's) and
# the optional development tools (Ollama, and the observability stack that `make obs` starts) are left out.
# Run from the repository's root: sh scripts/check-pinned-images.sh   (or: make pins)
unpinned=$(grep -nE '^FROM |^[[:space:]]+image: ' \
    services/api/Dockerfile services/web/Dockerfile services/e2e/Dockerfile deploy/postgres-image/Dockerfile \
    docker-compose.yml deploy/gcp/compose.gcp.yml deploy/k8s/*.yaml \
  | grep -v '@sha256:' \
  | grep -vE 'FROM base AS |\$\{KL_REGISTRY\}|aerugo/[a-z]+:dev|kl-postgres:|ollama/ollama|busybox:|otel/|prom/prometheus|jaegertracing/|grafana/grafana')
if [ -n "$unpinned" ]; then
  echo "Images not pinned by digest (pin each as image:tag@sha256:...):"
  echo "$unpinned"
  exit 1
fi
echo "Every image Aerugo ships is pinned by digest."
