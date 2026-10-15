# Opens Aerugo, running on the local cluster, at Compose's addresses: the app at http://localhost:3000, sign-in at
# 8081 (the issuer the app and the API expect), the API at 8000, MCP at 8090. Leave it running; Ctrl+C closes them.
# Stop Compose's stack first (make down): it uses the same ports.
$ErrorActionPreference = "Stop"
$forwards = [ordered]@{ "web" = "3000:3000"; "keycloak" = "8081:8080"; "api" = "8000:8000"; "mcp" = "8090:8090" }
$jobs = @()
foreach ($svc in $forwards.Keys) {
  $jobs += Start-Job -Name $svc -ArgumentList $svc, $forwards[$svc] -ScriptBlock {
    param($name, $ports)
    kubectl -n aerugo port-forward "svc/$name" $ports 2>&1
  }
}
Start-Sleep -Seconds 3
Write-Host "Aerugo:             http://localhost:3000   (alice / alice-dev-only, or bob, carol, dave)" -ForegroundColor Green
Write-Host "Keycloak's console: http://localhost:8081/admin   (admin / admin-dev-only)"
Write-Host "Ctrl+C closes them."
try {
  while ($true) {
    foreach ($job in $jobs) {
      if ($job.State -ne "Running") {
        Receive-Job $job | Write-Host
        throw "The $($job.Name) forward stopped. Is Compose's stack using its port (make down), or did its pod restart (run this again)?"
      }
    }
    Start-Sleep -Seconds 2
  }
} finally {
  $jobs | Stop-Job -PassThru | Remove-Job
}
