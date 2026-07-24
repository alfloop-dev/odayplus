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

resource "google_compute_address" "nat" {
  name   = "${local.name_prefix}-nat"
  region = var.region

  depends_on = [google_project_service.required]
}

resource "google_compute_router" "runtime" {
  name    = "${local.name_prefix}-runtime"
  region  = var.region
  network = google_compute_network.runtime.id
}

resource "google_compute_router_nat" "runtime" {
  name                                = "${local.name_prefix}-runtime"
  router                              = google_compute_router.runtime.name
  region                              = var.region
  nat_ip_allocate_option              = "MANUAL_ONLY"
  nat_ips                             = [google_compute_address.nat.self_link]
  source_subnetwork_ip_ranges_to_nat  = "LIST_OF_SUBNETWORKS"
  min_ports_per_vm                    = 128
  enable_endpoint_independent_mapping = true

  subnetwork {
    name                    = google_compute_subnetwork.runtime.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}
