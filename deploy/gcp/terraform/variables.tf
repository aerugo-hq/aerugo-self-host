variable "project_id" {
  description = "The GCP project Aerugo runs in."
  type        = string
}

variable "region" {
  description = "Where it runs, such as us-east1 (close to Portland, Maine: us-east4 or us-east1)."
  type        = string
  default     = "us-east1"
}

variable "zone" {
  description = "The VM's zone, in the region."
  type        = string
  default     = "us-east1-b"
}

variable "domain" {
  description = "The domain Aerugo is served under: app., api., auth., and mcp. of it (aerugo.example.com gives app.aerugo.example.com)."
  type        = string
}

variable "dns_zone" {
  description = "The Cloud DNS managed zone holding the domain, if it's in this project: its records are created. Empty: create them yourself (the outputs list them)."
  type        = string
  default     = ""
}

variable "github_repository" {
  description = "The repository allowed to deploy, as owner/name. Only its main branch can."
  type        = string
}

variable "first_admin" {
  description = "The username of the org's first admin: the account you'll create in Keycloak first."
  type        = string
}

variable "email_domains" {
  description = "The company's email domains: identity matching links people by their verified email only within these."
  type        = list(string)
}

variable "acme_email" {
  description = "Where Let's Encrypt writes about the certificates."
  type        = string
}

variable "machine_type" {
  description = "The VM's size. e2-standard-4 (4 vCPUs, 16 GB) runs the whole stack comfortably; e2-standard-2 (8 GB) is enough to try it."
  type        = string
  default     = "e2-standard-4"
}

variable "spot" {
  description = "Run as a Spot VM: a fraction of the price, but Google may stop it when it needs the capacity (it stops, never deleted; start it again and the stack returns). Good for trying it out; not for people relying on it."
  type        = bool
  default     = false
}

variable "disk_gb" {
  description = "The VM's disk, which holds every volume (Postgres, OpenBao, the index)."
  type        = number
  default     = 100
}
