terraform {
  required_version = ">= 1.7.0, < 2.0.0"
  backend "gcs" {}
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.35"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  active      = var.enable_firewall || var.enable_shared_dns
  network_url = "https://www.googleapis.com/compute/v1/projects/${var.project_id}/global/networks/${var.network_name}"
  # Automatically assigned immutable unique tag per Serverless VPC Access's
  # documented contract, not a VM inventory readback. Never use universal
  # vpc-connector or derive a target from internal aet-* firewall names.
  target_tag     = "vpc-connector-${var.region}-${var.connector_name}"
  restricted_ips = ["199.36.153.4", "199.36.153.5", "199.36.153.6", "199.36.153.7"]
  private_ips    = ["199.36.153.8", "199.36.153.9", "199.36.153.10", "199.36.153.11"]
  dns_zones = var.enable_shared_dns ? {
    googleapis = "googleapis.com."
    run        = "run.app."
  } : {}
}

# Read-only lookups, not resource ownership/import of the existing path.
data "google_compute_network" "existing" {
  count   = local.active ? 1 : 0
  project = var.project_id
  name    = var.network_name
}

data "google_vpc_access_connector" "existing" {
  count   = local.active ? 1 : 0
  project = var.project_id
  region  = var.region
  name    = var.connector_name
}

resource "terraform_data" "binding" {
  count = local.active ? 1 : 0
  lifecycle {
    precondition {
      condition = (
        data.google_compute_network.existing[0].name == var.network_name &&
        data.google_vpc_access_connector.existing[0].network == var.network_name &&
        data.google_vpc_access_connector.existing[0].ip_cidr_range == var.connector_cidr &&
        data.google_vpc_access_connector.existing[0].state == "READY"
      )
      error_message = "Existing READY connector must be on default with the exact reviewed CIDR. Do not move/recreate it."
    }
    precondition {
      condition     = !var.enable_shared_dns || (var.shared_dns_scope_ack == "default:googleapis.com.=restricted4,run.app.=private8" && can(regex("^ODP-DEV-[A-Z0-9-]+:[A-Za-z0-9._/-]+$", var.shared_dns_review_ref)))
      error_message = "Shared default-network DNS requires reviewed restricted4 Google/private8 run.app scope and authority reference; old private8 Google scope is invalid. No connector-local DNS scope exists here."
    }
    precondition {
      condition     = !var.enable_firewall || (var.enable_shared_dns && var.connector_scope_review_confirmed)
      error_message = "Deny requires reviewed DNS and connector metadata/unique-tag contract/all-consumer scope review; this acknowledgement is not live enforcement proof."
    }
  }
}

# Priority 100 managed connector/control-plane rules remain authoritative.
# Only application destinations are added; no RFC1918 blanket or internet allow.
resource "google_compute_firewall" "google_https" {
  count       = var.enable_firewall ? 1 : 0
  project     = var.project_id
  name        = "oday-dev-connector-google-https"
  network     = local.network_url
  direction   = "EGRESS"
  priority    = 800
  target_tags = [local.target_tag]
  destination_ranges = [
    "199.36.153.4/30",
    "199.36.153.8/30",
  ]
  allow {
    protocol = "tcp"
    ports    = ["443"]
  }
  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
  depends_on = [terraform_data.binding]
}

resource "google_compute_firewall" "sql" {
  count              = var.enable_firewall ? 1 : 0
  project            = var.project_id
  name               = "oday-dev-connector-sql"
  network            = local.network_url
  direction          = "EGRESS"
  priority           = 800
  target_tags        = [local.target_tag]
  destination_ranges = [var.sql_private_cidr, var.staging_sql_private_cidr]
  allow {
    protocol = "tcp"
    # PostgreSQL direct and Cloud SQL Auth Proxy/connector private-IP transport.
    ports = ["5432", "3307"]
  }
  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
  depends_on = [terraform_data.binding]
}

resource "google_compute_firewall" "deny" {
  count              = var.enable_firewall ? 1 : 0
  project            = var.project_id
  name               = "oday-dev-connector-deny"
  network            = local.network_url
  direction          = "EGRESS"
  priority           = 900
  target_tags        = [local.target_tag]
  destination_ranges = ["0.0.0.0/0"]
  deny {
    protocol = "all"
  }
  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
  # Install application allows and DNS records before deny; rollback removes
  # deny before either dependency, even if an operator plans both stages at once.
  depends_on = [terraform_data.binding, google_compute_firewall.google_https, google_compute_firewall.sql, google_dns_record_set.wildcard]
}

# Private zones attach to the ENTIRE shared default VPC, including GKE and all
# other DNS clients. No response policy overrides the existing sqladmin zone.
resource "google_dns_managed_zone" "private" {
  for_each    = local.dns_zones
  project     = var.project_id
  name        = "oday-dev-connector-${each.key}"
  dns_name    = each.value
  description = "Separately reviewed shared default VPC private Google VIP DNS"
  visibility  = "private"
  private_visibility_config {
    networks {
      network_url = local.network_url
    }
  }
  depends_on = [terraform_data.binding]
}

resource "google_dns_record_set" "vip" {
  for_each     = local.dns_zones
  project      = var.project_id
  managed_zone = google_dns_managed_zone.private[each.key].name
  # Pin the canonical restricted endpoint explicitly: a wildcard alone would
  # shadow it (and a wildcard CNAME to itself would produce a DNS loop).
  name         = each.key == "googleapis" ? "restricted.googleapis.com." : each.value
  type         = "A"
  ttl          = 300
  rrdatas      = each.key == "googleapis" ? local.restricted_ips : local.private_ips
}

resource "google_dns_record_set" "wildcard" {
  for_each     = local.dns_zones
  project      = var.project_id
  managed_zone = google_dns_managed_zone.private[each.key].name
  name         = "*.${each.value}"
  type         = "CNAME"
  ttl          = 300
  rrdatas      = [google_dns_record_set.vip[each.key].name]
}

output "review_surface" {
  value = {
    network              = local.network_url
    connector_target_tag = local.target_tag
    firewall_enabled     = var.enable_firewall
    shared_dns_enabled   = var.enable_shared_dns
    live_protection      = "UNKNOWN: source/plan is not effective firewall, DNS or candidate probe evidence"
  }
}
