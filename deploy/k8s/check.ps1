# Checks how Aerugo's services reach their secrets on a local cluster (M6): no OpenBao token is stored in the cluster;
# the API, the worker, and the MCP server each sign in with their own service account and get an hour at most, holding
# only the services' policy; and a pod running as any other service account is refused. After make kind-up, from the
# repository's root:
#   powershell -ExecutionPolicy Bypass -File deploy/k8s/check.ps1      (or: make kind-check)
$ErrorActionPreference = "Continue"   # each native call's exit status is checked here; its stderr is part of the answer
$ns = "aerugo"
$failures = @()

$stored = (kubectl -n $ns get secret aerugo-dev -o json | ConvertFrom-Json).data.PSObject.Properties.Name
if ($stored -contains "openbao-services-token") { $failures += "the cluster still stores a services' OpenBao token" }

# Run in each service's own pod, as that service: sign in, look the token up, and use it for a secret's round trip.
$signedIn = @'
import httpx
from kl.vault import vault
assert vault.login, "it holds a static OpenBao token instead of signing in"
me = httpx.get(f"{vault.url}/v1/auth/token/lookup-self", headers=vault.headers(), timeout=10).json()["data"]
assert sorted(me["policies"]) == ["aerugo-services", "default"], f"policies {me['policies']}"
assert 0 < me["ttl"] <= 3600, f"a token for {me['ttl']} seconds"
ref = vault.put("kl/acme/m6-check/probe", {"value": "ok"})
assert vault.get(ref) == {"value": "ok"}, "a secret didn't round-trip"
vault.destroy(ref)
print(f"signed in as {me['meta']['service_account_name']}, {me['ttl']}s left, policies {sorted(me['policies'])}")
'@
foreach ($service in "api", "worker", "mcp") {
  $out = ($signedIn | kubectl -n $ns exec -i "deploy/$service" -c $service -- python - 2>&1) -join " "
  if ($LASTEXITCODE -ne 0) { $failures += "${service}: $out" } else { Write-Host "  ok    ${service}: $out" }
}

# A pod as the namespace's default service account presents a real token, for an account the role doesn't name.
$outsider = @'
import httpx
jwt = open("/var/run/secrets/kubernetes.io/serviceaccount/token").read()
r = httpx.post("http://openbao:8200/v1/auth/kubernetes/login", json={"role": "aerugo-services", "jwt": jwt}, timeout=10)
print(f"status={r.status_code}")
'@
$out = ($outsider | kubectl -n $ns run m6-check --rm -i --restart=Never --image=aerugo/api:dev --image-pull-policy=IfNotPresent --command -- python - 2>&1) -join " "
if ($out -match "status=(\d+)" -and $Matches[1] -eq "403") { Write-Host "  ok    another service account was refused (403)" }
else { $failures += "a pod as another service account wasn't refused: $out" }

if ($failures.Count) {
  $failures | ForEach-Object { Write-Host "  FAIL  $_" -ForegroundColor Red }
  exit 1
}
Write-Host "Secrets: every service signs in as itself, for an hour at most; nothing long-lived is stored." -ForegroundColor Green
