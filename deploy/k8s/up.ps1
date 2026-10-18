# Aerugo on a local Kubernetes cluster (kind, or Docker Desktop's): builds the three images Aerugo builds itself,
# loads them into the cluster's nodes, turns the repository's configuration files into ConfigMaps (so the file you
# edit is the file the cluster uses), applies deploy/k8s, and waits until it's up. From the repository's root:
#   powershell -ExecutionPolicy Bypass -File deploy/k8s/up.ps1      (or: make kind-up)
# It refuses a cluster that isn't local: these are development credentials, with development people in them.
param(
  [string]$Cluster = "",   # the kind cluster's name, when kubectl's context doesn't say it
  [switch]$SkipBuild       # reuse the images already built
)
$ErrorActionPreference = "Stop"
$ns = "aerugo"

function Invoke-Native([string]$File, [string[]]$Arguments) {
  & $File @Arguments
  if ($LASTEXITCODE -ne 0) { throw "failed (exit $LASTEXITCODE): $File $($Arguments -join ' ')" }
}

if (-not (Test-Path "docker-compose.yml")) { throw "Run this from the repository's root, where docker-compose.yml is." }
$context = "$(& kubectl config current-context)".Trim()
if ($LASTEXITCODE -ne 0 -or -not $context) { throw "kubectl has no current cluster: is kind running?" }
if (-not ($context -like "kind-*" -or $context -eq "docker-desktop")) {
  throw "kubectl points at '$context', which isn't a local cluster. Switch to yours first: kubectl config use-context kind-<name>"
}
Write-Host "Deploying Aerugo to $context" -ForegroundColor Cyan

$images = [ordered]@{ "aerugo/postgres:dev" = "deploy/postgres-image"; "aerugo/api:dev" = "services/api"; "aerugo/web:dev" = "services/web" }
if (-not $SkipBuild) {
  foreach ($image in $images.Keys) {
    Write-Host "Building $image"
    Invoke-Native "docker" @("build", "-t", $image, $images[$image])
  }
}

# A kind cluster's nodes don't see Docker's images: each image is loaded into each node.
if ((Get-Command kind -ErrorAction SilentlyContinue) -and $context -like "kind-*") {
  if (-not $Cluster) { $Cluster = $context.Substring(5) }
  foreach ($image in $images.Keys) {
    Write-Host "Loading $image into kind cluster $Cluster"
    Invoke-Native "kind" @("load", "docker-image", $image, "--name", $Cluster)
  }
} else {
  $nodes = @(& docker ps --filter "label=io.x-k8s.kind.cluster" --format "{{.Names}}" | Where-Object { $_ })
  $kindNodes = @(& kubectl get nodes -o name | Where-Object { $_ -like "node/desktop-*" })
  if ($nodes.Count -eq 0 -and $context -eq "docker-desktop" -and $kindNodes.Count -gt 0) {
    # Docker Desktop's kind-based cluster (nodes named desktop-*) keeps its own images: without its nodes in view,
    # nothing could be loaded, and the cluster would quietly run whatever older image it already holds.
    throw "Docker Desktop's cluster runs on kind nodes Docker hides: turn on Settings, Kubernetes, 'Show system containers (advanced)', then run this again."
  } elseif ($nodes.Count -eq 0 -and $context -eq "docker-desktop") {
    Write-Host "Docker Desktop's cluster shares Docker's images: nothing to load"
  } elseif ($nodes.Count -eq 0) {
    throw "No kind nodes found. Install kind (winget install Kubernetes.kind), or check the cluster is running."
  } else {
    $tar = Join-Path ([System.IO.Path]::GetTempPath()) "aerugo-image.tar"
    foreach ($image in $images.Keys) {
      Invoke-Native "docker" @("save", "-o", $tar, $image)
      foreach ($node in $nodes) {
        Write-Host "Loading $image into $node"
        Invoke-Native "docker" @("cp", $tar, "${node}:/aerugo-image.tar")
        Invoke-Native "docker" @("exec", $node, "ctr", "--namespace=k8s.io", "images", "import", "/aerugo-image.tar")
        Invoke-Native "docker" @("exec", $node, "rm", "-f", "/aerugo-image.tar")
      }
    }
    Remove-Item $tar -ErrorAction SilentlyContinue
  }
}

Invoke-Native "kubectl" @("apply", "-f", "deploy/k8s/namespace.yaml")

# The repository's configuration files, as ConfigMaps, replaced on each run: what's in the repository is what runs.
$fileMaps = [ordered]@{
  "aerugo-postgres-init"  = @("deploy/postgres/00-init.sh", "deploy/postgres/sql/01-roles.sql", "deploy/postgres/sql/02-schema.sql",
                              "deploy/postgres/sql/03-seed.sql")
  "aerugo-openbao"        = @("deploy/openbao/dev.hcl", "deploy/openbao/services.hcl", "deploy/openbao/start.sh")
  "aerugo-keycloak-realm" = @("deploy/keycloak/realm-acme.json")
  "aerugo-keycloak-theme" = @("deploy/keycloak/themes/kl/login/theme.properties",
                              "deploy/keycloak/themes/kl/login/messages/messages_en.properties",
                              "deploy/keycloak/themes/kl/login/resources/css/kl.css")
  "aerugo-authz"          = @("authz")
}
foreach ($name in $fileMaps.Keys) {
  Invoke-Native "kubectl" @("-n", $ns, "delete", "configmap", $name, "--ignore-not-found")
  $from = @($fileMaps[$name] | ForEach-Object { "--from-file=$_" })
  Invoke-Native "kubectl" (@("-n", $ns, "create", "configmap", $name) + $from)
}

# The GitHub App's credentials, if you've made one (.secrets/, as Compose mounts it).
Invoke-Native "kubectl" @("-n", $ns, "delete", "secret", "aerugo-kl-secrets", "--ignore-not-found")
$credentials = @(Get-ChildItem -Path ".secrets" -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne ".gitkeep" })
if ($credentials.Count -gt 0) {
  $from = @($credentials | ForEach-Object { "--from-file=$($_.FullName)" })
  Invoke-Native "kubectl" (@("-n", $ns, "create", "secret", "generic", "aerugo-kl-secrets") + $from)
}

# Jobs can't be changed in place: they're made again, and run again (both are safe to repeat).
Invoke-Native "kubectl" @("-n", $ns, "delete", "job", "openfga-migrate", "provision", "--ignore-not-found")
Invoke-Native "kubectl" @("apply", "-k", "deploy/k8s")
if (-not $SkipBuild) {  # a rebuilt image keeps its tag: restart what runs it, so it's picked up
  Invoke-Native "kubectl" @("-n", $ns, "rollout", "restart", "deployment/api", "deployment/worker", "deployment/mcp", "deployment/web")
}

Write-Host "Waiting for provisioning (the first run initializes the database: a few minutes)" -ForegroundColor Cyan
& kubectl -n $ns wait --for=condition=complete job/provision --timeout=15m
if ($LASTEXITCODE -ne 0) { throw "Provisioning didn't finish. Its log: kubectl -n $ns logs job/provision" }
foreach ($workload in @("statefulset/keycloak", "deployment/api", "deployment/worker", "deployment/mcp", "deployment/web")) {
  Invoke-Native "kubectl" @("-n", $ns, "rollout", "status", $workload, "--timeout=10m")
}
Write-Host ""
Write-Host "Aerugo is up. Open it with: make kind-open   (then http://localhost:3000)" -ForegroundColor Green
