# Checks how Aerugo's services reach their secrets on a local cluster (M6): no OpenBao token is stored in the cluster;
# each service's pod signs in with its own service account and gets an hour at most, holding only the services' policy;
# the service itself (its OpenBao client) signs in that way; and a pod running as any other service account is refused.
# After make kind-up, from the repository's root:
#   powershell -ExecutionPolicy Bypass -File deploy/k8s/check.ps1      (or: make kind-check)
$ErrorActionPreference = "Continue"   # each native call's exit status is checked here; its stderr is part of the answer
$ns = "aerugo"
$failures = @()

$stored = (kubectl -n $ns get secret aerugo-dev -o json | ConvertFrom-Json).data.PSObject.Properties.Name
if ($stored -contains "openbao-services-token") { $failures += "the cluster still stores a services' OpenBao token" }

# In each service's pod, as its service account: sign in with the token Kubernetes mounted, look the token up, and use
# it for a secret's round trip. This is OpenBao's side and the pod's identity, whatever the image.
$podSignIn = @'
import httpx
bao = "http://openbao:8200/v1"
jwt = open("/var/run/secrets/kubernetes.io/serviceaccount/token").read()
r = httpx.post(f"{bao}/auth/kubernetes/login", json={"role": "aerugo-services", "jwt": jwt}, timeout=10)
assert r.status_code == 200, f"sign-in refused ({r.status_code})"
h = {"X-Vault-Token": r.json()["auth"]["client_token"]}
me = httpx.get(f"{bao}/auth/token/lookup-self", headers=h, timeout=10).json()["data"]
assert sorted(me["policies"]) == ["aerugo-services", "default"], f"policies {me['policies']}"
assert 0 < me["ttl"] <= 3600, f"a token for {me['ttl']} seconds"
path = f"{bao}/secret/data/kl/acme/m6-check/{me['meta']['service_account_name']}"
assert httpx.post(path, headers=h, json={"data": {"value": "ok"}}, timeout=10).status_code == 200, "a secret couldn't be written"
assert httpx.get(path, headers=h, timeout=10).json()["data"]["data"] == {"value": "ok"}, "a secret didn't round-trip"
httpx.delete(path.replace("/data/", "/metadata/"), headers=h, timeout=10)
assert httpx.get(f"{bao}/sys/policies/acl/root", headers=h, timeout=10).status_code == 403, "the token reaches beyond its paths"
print(f"signed in as {me['meta']['service_account_name']}, {me['ttl']}s left, policies {sorted(me['policies'])}")
'@
# The service's own OpenBao client: it signs in, rather than holding a static token.
$clientSignsIn = @'
from kl.vault import vault
assert getattr(vault, "login", None), "the service's client holds a static OpenBao token instead of signing in"
vault.get(vault.put("kl/acme/m6-check/client", {"value": "ok"}))
vault.destroy("openbao://secret/kl/acme/m6-check/client")
print("its client signed in and round-tripped a secret")
'@
foreach ($service in "api", "worker", "mcp") {
  foreach ($probe in @(@{name = "pod"; code = $podSignIn}, @{name = "client"; code = $clientSignsIn})) {
    $out = ($probe.code | kubectl -n $ns exec -i "deploy/$service" -c $service -- python - 2>&1) -join " "
    if ($LASTEXITCODE -ne 0) { $failures += "$service ($($probe.name)): $out" } else { Write-Host "  ok    $service ($($probe.name)): $out" }
  }
}

# The web app's pod runs as the namespace's default service account: a real token, for an account the role doesn't name.
$outsider = @'
const jwt = require("fs").readFileSync("/var/run/secrets/kubernetes.io/serviceaccount/token", "utf8");
fetch("http://openbao:8200/v1/auth/kubernetes/login", { method: "POST", headers: { "content-type": "application/json" },
  body: JSON.stringify({ role: "aerugo-services", jwt }) }).then((r) => console.log(`status=${r.status}`));
'@
$out = ($outsider | kubectl -n $ns exec -i deploy/web -c web -- node 2>&1) -join " "
if ($out -match "status=(\d+)" -and $Matches[1] -eq "403") { Write-Host "  ok    another service account was refused (403)" }
else { $failures += "a pod as another service account wasn't refused: $out" }

if ($failures.Count) {
  $failures | ForEach-Object { Write-Host "  FAIL  $_" -ForegroundColor Red }
  exit 1
}
Write-Host "Secrets: every service signs in as itself, for an hour at most; nothing long-lived is stored." -ForegroundColor Green
