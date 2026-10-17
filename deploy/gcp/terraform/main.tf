# Aerugo on one GCP VM (the deployment plan's first stage): the network edge, the VM and its identity, the registry
# its images come from, Cloud KMS for OpenBao's unseal key, Secret Manager for every credential, a bucket for
# backups, and the keyless link GitHub Actions deploys through (Workload Identity Federation). Apply it once from your
# laptop (README.md); .github/workflows/deploy-gcp.yml does every deploy after that.
terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
  backend "gcs" {} # terraform init -backend-config="bucket=<your state bucket>" -backend-config="prefix=aerugo"
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

locals {
  apis = ["compute.googleapis.com", "artifactregistry.googleapis.com", "secretmanager.googleapis.com", "cloudkms.googleapis.com",
  "iam.googleapis.com", "iamcredentials.googleapis.com", "sts.googleapis.com", "iap.googleapis.com", "storage.googleapis.com", "dns.googleapis.com"]
  hosts = ["app", "api", "auth", "mcp"]
  # Every credential the stack needs, generated here and kept in Secret Manager; the VM reads them at each deploy.
  generated = ["owner-password", "app-password", "ingest-password", "fga-password", "keycloak-db-password",
  "keycloak-admin-password", "openbao-token", "backup-cipher", "terraform-client-secret", "web-password", "web-session-key"]
  vm = "serviceAccount:${google_service_account.vm.email}"
}

resource "google_project_service" "on" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

# ---- identities: the VM runs the stack; the deployer is GitHub Actions, through Workload Identity Federation
resource "google_service_account" "vm" {
  account_id   = "aerugo-vm"
  display_name = "Aerugo VM: runs the stack, reads its images and secrets, unseals OpenBao"
  depends_on   = [google_project_service.on]
}

resource "google_service_account" "deployer" {
  account_id   = "aerugo-deployer"
  display_name = "Aerugo deployer: GitHub Actions pushes images and deploys, keyless"
  depends_on   = [google_project_service.on]
}

resource "google_project_iam_member" "vm" {
  for_each = toset(["roles/logging.logWriter", "roles/monitoring.metricWriter"])
  project  = var.project_id
  role     = each.value
  member   = local.vm
}

# ---- images
resource "google_artifact_registry_repository" "images" {
  repository_id = "aerugo"
  location      = var.region
  format        = "DOCKER"
  description   = "Aerugo's images, tagged with the commit they were built from"
  depends_on    = [google_project_service.on]
}

resource "google_artifact_registry_repository_iam_member" "vm_pulls" {
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.reader"
  member     = local.vm
}

resource "google_artifact_registry_repository_iam_member" "deployer_pushes" {
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deployer.email}"
}

# ---- secrets: generated once, read by the VM only (they also sit in Terraform's state: keep its bucket private)
resource "random_password" "generated" {
  for_each = toset(local.generated)
  length   = 40
  special  = false
}

resource "google_secret_manager_secret" "generated" {
  for_each  = toset(local.generated)
  secret_id = "aerugo-${each.value}"
  replication {
    auto {}
  }
  depends_on = [google_project_service.on]
}

resource "google_secret_manager_secret_version" "generated" {
  for_each    = toset(local.generated)
  secret      = google_secret_manager_secret.generated[each.value].id
  secret_data = random_password.generated[each.value].result
}

resource "google_secret_manager_secret_iam_member" "vm_reads" {
  for_each  = toset(local.generated)
  secret_id = google_secret_manager_secret.generated[each.value].id
  role      = "roles/secretmanager.secretAccessor"
  member    = local.vm
}

# OpenBao's root token and recovery key: written once, by the VM, when it first initializes OpenBao. Never on disk.
resource "google_secret_manager_secret" "openbao_init" {
  secret_id = "aerugo-openbao-init"
  replication {
    auto {}
  }
  depends_on = [google_project_service.on]
}

resource "google_secret_manager_secret_iam_member" "vm_writes_init" {
  secret_id = google_secret_manager_secret.openbao_init.id
  role      = "roles/secretmanager.secretVersionAdder"
  member    = local.vm
}

# ---- OpenBao's unseal key: it never leaves Cloud KMS; the VM's identity uses it
resource "google_kms_key_ring" "aerugo" {
  name       = "aerugo"
  location   = var.region
  depends_on = [google_project_service.on]
}

resource "google_kms_crypto_key" "unseal" {
  name            = "openbao-unseal"
  key_ring        = google_kms_key_ring.aerugo.id
  rotation_period = "7776000s" # 90 days
  lifecycle {
    prevent_destroy = true # without it, OpenBao's data could never be unsealed again
  }
}

resource "google_kms_crypto_key_iam_member" "vm_unseals" {
  for_each      = toset(["roles/cloudkms.cryptoKeyEncrypterDecrypter", "roles/cloudkms.viewer"])
  crypto_key_id = google_kms_crypto_key.unseal.id
  role          = each.value
  member        = local.vm
}

# ---- backups
resource "google_storage_bucket" "backups" {
  name                        = "${var.project_id}-aerugo-backups"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  versioning {
    enabled = true
  }
  lifecycle_rule {
    condition {
      age = 35
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket_iam_member" "vm_backs_up" {
  bucket = google_storage_bucket.backups.name
  role   = "roles/storage.objectAdmin"
  member = local.vm
}

# ---- the VM, and the only ways in: HTTPS from anywhere, SSH only through IAP
resource "google_compute_address" "edge" {
  name       = "aerugo"
  region     = var.region
  depends_on = [google_project_service.on]
}

resource "google_compute_firewall" "web" {
  name          = "aerugo-web"
  network       = "default"
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["aerugo"]
  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }
  allow {
    protocol = "udp"
    ports    = ["443"] # HTTP/3
  }
}

resource "google_compute_firewall" "iap_ssh" {
  name          = "aerugo-iap-ssh"
  network       = "default"
  source_ranges = ["35.235.240.0/20"] # Google's IAP: SSH is never open to the internet
  target_tags   = ["aerugo"]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_instance" "aerugo" {
  name                      = "aerugo"
  machine_type              = var.machine_type
  zone                      = var.zone
  tags                      = ["aerugo"]
  allow_stopping_for_update = true

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
      size  = var.disk_gb
      type  = "pd-balanced"
    }
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.edge.address
    }
  }

  service_account {
    email  = google_service_account.vm.email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  # A Spot VM costs a fraction of the price, but Google may stop it when it needs the capacity: it stops (never
  # deleted, so every volume stays), and the stack comes back when you start it again.
  scheduling {
    provisioning_model          = var.spot ? "SPOT" : "STANDARD"
    preemptible                 = var.spot
    automatic_restart           = !var.spot
    on_host_maintenance         = var.spot ? "TERMINATE" : "MIGRATE"
    instance_termination_action = var.spot ? "STOP" : null
  }

  metadata = {
    enable-oslogin       = "TRUE"
    aerugo-domain        = var.domain
    aerugo-registry      = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
    aerugo-admin         = var.first_admin
    aerugo-email-domains = join(",", var.email_domains)
    aerugo-acme-email    = var.acme_email
    aerugo-backups       = google_storage_bucket.backups.name
  }
  metadata_startup_script = file("${path.module}/startup.sh")

  depends_on = [google_kms_crypto_key_iam_member.vm_unseals, google_secret_manager_secret_iam_member.vm_reads]
}

# The deployer reaches the VM only through IAP, as itself, with OS Login.
resource "google_project_iam_member" "deployer_tunnels" {
  project = var.project_id
  role    = "roles/iap.tunnelResourceAccessor"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_compute_instance_iam_member" "deployer_logs_in" {
  zone          = var.zone
  instance_name = google_compute_instance.aerugo.name
  role          = "roles/compute.osAdminLogin"
  member        = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_service_account_iam_member" "deployer_acts_as_vm" {
  service_account_id = google_service_account.vm.name
  role               = "roles/iam.serviceAccountUser" # OS Login needs it on a VM that runs as a service account
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

# ---- GitHub Actions, keyless: this repository's main branch only
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "aerugo-github"
  display_name              = "GitHub Actions"
  depends_on                = [google_project_service.on]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  attribute_condition = "assertion.repository == \"${var.github_repository}\" && assertion.ref == \"refs/heads/main\""
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_deploys" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

# ---- DNS, when the domain's zone is in this project; otherwise the outputs say what to create
resource "google_dns_record_set" "hosts" {
  for_each     = var.dns_zone == "" ? toset([]) : toset(local.hosts)
  managed_zone = var.dns_zone
  name         = "${each.value}.${var.domain}."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_address.edge.address]
}
