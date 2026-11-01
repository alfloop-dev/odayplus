resource "google_compute_network" "runtime" {
  name                    = "${local.name_prefix}-runtime"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "runtime" {
  name                     = "${local.name_prefix}-runtime"
  region                   = var.region
  network                  = google_compute_network.runtime.id
  ip_cidr_range            = var.network_cidr
  private_ip_google_access = true

  log_config {
    aggregation_interval = "INTERVAL_5_SEC"
    flow_sampling        = 0.5
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_global_address" "private_services" {
  name          = "${local.name_prefix}-private-services"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = var.private_service_prefix_length
  network       = google_compute_network.runtime.id
}

resource "google_service_networking_connection" "private_services" {
  network                 = google_compute_network.runtime.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]
}

resource "google_compute_subnetwork_iam_member" "network_users" {
  for_each = var.network_user_members

  project    = var.project_id
  region     = var.region
  subnetwork = google_compute_subnetwork.runtime.name
  role       = "roles/compute.networkUser"
  member     = each.value
}

resource "google_compute_firewall" "deny_all_egress" {
  name      = "${local.name_prefix}-deny-all-egress"
  network   = google_compute_network.runtime.name
  direction = "EGRESS"
  priority  = 65534

  deny {
    protocol = "all"
  }

  destination_ranges = ["0.0.0.0/0"]
}

resource "google_compute_firewall" "allow_private_egress" {
  name      = "${local.name_prefix}-allow-private-egress"
  network   = google_compute_network.runtime.name
  direction = "EGRESS"
  priority  = 1000

  allow {
    protocol = "all"
  }

  destination_ranges = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    var.network_cidr,
  ]
}

resource "google_compute_firewall" "allow_restricted_google_apis" {
  name      = "${local.name_prefix}-allow-restricted-google-apis"
  network   = google_compute_network.runtime.name
  direction = "EGRESS"
  priority  = 1000

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }

  destination_ranges = [
    "199.36.153.4/30",
    "199.36.153.8/30",
  ]
}

# Direct VPC egress is ALL_TRAFFIC behind deny_all_egress, so Google API and
# run.app hostnames must resolve to a VIP inside allow_restricted_google_apis.
# Same zone/record shape as dev_connector_egress, but bound only to this
# module's runtime network and named per environment, so it never collides with
# the default-network oday-dev-connector-* zones.
#
# Both zones use private.googleapis.com (199.36.153.8/30), not
# restricted.googleapis.com: restricted blocks services without VPC Service
# Controls support, and the API's service-token JWKS host www.googleapis.com is
# not on the restricted VIP service list. *.run.app is only listed for
# private.googleapis.com. Secret Manager, Cloud Storage, Pub/Sub, Cloud SQL
# Admin and IAM Credentials are all covered by private's *.googleapis.com.
# Google requires one VIP per zone set; do not mix the two ranges.
locals {
  private_google_api_ips = ["199.36.153.8", "199.36.153.9", "199.36.153.10", "199.36.153.11"]
  private_google_dns_zones = {
    googleapis = "googleapis.com."
    run        = "run.app."
  }
}

resource "google_dns_managed_zone" "private_google" {
  for_each    = local.private_google_dns_zones
  project     = var.project_id
  name        = "${local.name_prefix}-runtime-${each.key}"
  dns_name    = each.value
  description = "Private Google Access VIP DNS for the ${local.name_prefix} runtime network only"
  visibility  = "private"
  labels      = local.labels

  private_visibility_config {
    networks {
      network_url = google_compute_network.runtime.id
    }
  }
}

resource "google_dns_record_set" "private_google_vip" {
  for_each     = local.private_google_dns_zones
  project      = var.project_id
  managed_zone = google_dns_managed_zone.private_google[each.key].name
  # Pin the VIP name explicitly: the wildcard alone would shadow it.
  name    = each.key == "googleapis" ? "private.googleapis.com." : each.value
  type    = "A"
  ttl     = 300
  rrdatas = local.private_google_api_ips
}

resource "google_dns_record_set" "private_google_wildcard" {
  for_each     = local.private_google_dns_zones
  project      = var.project_id
  managed_zone = google_dns_managed_zone.private_google[each.key].name
  name         = "*.${each.value}"
  type         = "CNAME"
  ttl          = 300
  rrdatas      = [google_dns_record_set.private_google_vip[each.key].name]
}
