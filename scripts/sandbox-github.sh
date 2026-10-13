#!/usr/bin/env bash
# Creates the M1 test repos in your sandbox org from sandbox/github/<repo>/ and pushes their docs.
# Needs git and the GitHub CLI, logged in once with: gh auth login
# Usage: make sandbox-github ORG=<your-org>
set -euo pipefail

ORG="${1:-}"
if [ -z "$ORG" ]; then echo "usage: make sandbox-github ORG=<your-org>"; exit 1; fi
if ! command -v gh >/dev/null 2>&1; then
  echo "The GitHub CLI isn't installed. On Windows: winget install --id GitHub.cli"
  echo "Then open a new terminal, run: gh auth login, and rerun this."
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)/sandbox/github"
NAME="$(git config user.name || echo 'kl sandbox')"
EMAIL="$(git config user.email || echo 'sandbox@example.invalid')"

for dir in "$ROOT"/*/; do
  repo="$(basename "$dir")"
  if gh repo view "$ORG/$repo" >/dev/null 2>&1; then
    echo "exists: $ORG/$repo (skipping)"
    continue
  fi
  tmp="$(mktemp -d)"
  cp -r "$dir". "$tmp"/
  if [[ "$repo" == *-knowledge ]]; then  # knowledge CI runs the platform's own validator, copied in fresh
    mkdir -p "$tmp/.github/knowledge-ci"
    cp "$ROOT/../../services/api/kl/okf.py" "$tmp/.github/knowledge-ci/okf.py"
  fi
  (
    cd "$tmp"
    git init -q -b main
    git add .
    git -c user.name="$NAME" -c user.email="$EMAIL" commit -q -m "Seed docs for knowledge-layer testing"
  )
  gh repo create "$ORG/$repo" --private --source "$tmp" --push
  rm -rf "$tmp"
done

echo
echo "Done. If you installed the App on 'Only select repositories', add these repos under"
echo "github.com/organizations/$ORG/settings/installations. Then run: make smoke"
