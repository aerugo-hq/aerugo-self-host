output "ip_address" {
  description = "Point app., api., auth., and mcp. of the domain here (A records), unless dns_zone made them."
  value       = google_compute_address.edge.address
}

output "dns_records" {
  description = "The A records the domain needs."
  value       = [for h in local.hosts : "${h}.${var.domain} A ${google_compute_address.edge.address}"]
}

output "github_variables" {
  description = "Set these as the repository's Actions variables (Settings, Secrets and variables, Actions, Variables)."
  value = {
    GCP_PROJECT            = var.project_id
    GCP_REGION             = var.region
    GCP_ZONE               = var.zone
    GCP_WORKLOAD_PROVIDER  = google_iam_workload_identity_pool_provider.github.name
    GCP_DEPLOYER           = google_service_account.deployer.email
    AERUGO_DOMAIN          = var.domain
  }
}

output "app_url" {
  value = "https://app.${var.domain}"
}
