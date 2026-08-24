resource "google_compute_network" "runtime" {
  name                    = "${local.name_prefix}-runtime"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"

  depends_on = [google_project_service.required]
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

  depends_on = [google_project_service.required]
}

resource "google_service_networking_connection" "private_services" {
  network                 = google_compute_network.runtime.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]

  depends_on = [google_project_service.required]
}

resource "google_compute_subnetwork_iam_member" "runtime_network_user" {
  project    = var.project_id
  region     = var.region
  subnetwork = google_compute_subnetwork.runtime.name
  role       = "roles/compute.networkUser"
  member     = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_compute_subnetwork_iam_member" "web_network_user" {
  project    = var.project_id
  region     = var.region
  subnetwork = google_compute_subnetwork.runtime.name
  role       = "roles/compute.networkUser"
  member     = "serviceAccount:${google_service_account.web.email}"
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
