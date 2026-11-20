# Persistent custom-domain load balancer (owner decision 2026-11-20).
#
# Each environment serves its governed Web origin (var.web_base_url) -- and in
# production the API host (var.api_public_host) -- through one persistent
# global external HTTPS Application Load Balancer with a reserved static IP and
# a Google-managed certificate. The oday-plus.com.tw zone is not in Cloud DNS:
# the owner adds the A records at the external registrar, pointing at the
# web_load_balancer_ip output, and the managed certificate only becomes ACTIVE
# after they resolve to that address.
#
# Backends:
#   * dev/prod: serverless NEGs for the Terraform-managed Cloud Run Web (and
#     API) services, owned here.
#   * staging: Web services are ephemeral per release
#     (modules/ephemeral_staging). Each release owns only its serverless NEG.
#     product_ops/deployment/staging_lifecycle.py attaches it to the backend
#     service below during the web_custom_domain verify stage and detaches it
#     before the release is destroyed, so Terraform here owns the backend
#     service but deliberately not its backend list.
#
# The staging foundation state is a targeted subset of this root. Applying
# `-target=google_compute_global_forwarding_rule.web_lb` and
# `-target=google_compute_global_forwarding_rule.web_lb_http_redirect` creates
# only the load balancer chain (plus any missing google_project_service.required):
# nothing here references a Cloud Run, SQL, Pub/Sub or Secret resource.

locals {
  web_lb_enabled           = var.web_load_balancer_enabled
  web_lb_release_attached  = local.web_lb_enabled && var.environment == "staging"
  web_lb_terraform_backend = local.web_lb_enabled && var.environment != "staging"
  web_lb_api_routed        = local.web_lb_enabled && var.api_public_host != ""

  # Every load balancer resource shares this name; staging_lifecycle.py pins
  # the staging value (STAGING_WEB_LOAD_BALANCER_NAME = "oday-staging-web-lb").
  web_lb_name    = "${local.name_prefix}-web-lb"
  web_lb_domains = compact([local.web_public_host, local.web_lb_api_routed ? var.api_public_host : ""])

  web_lb_backend_service_id = one(concat(
    google_compute_backend_service.web_lb[*].id,
    google_compute_backend_service.web_lb_release[*].id,
  ))
}

resource "google_compute_global_address" "web_lb" {
  count = local.web_lb_enabled ? 1 : 0

  name         = local.web_lb_name
  address_type = "EXTERNAL"
  ip_version   = "IPV4"
  description  = "Static IP for ${join(", ", local.web_lb_domains)}; registrar A records point here."

  # Registrar DNS records point at this address. Replacing it silently breaks
  # the custom domains and the managed certificate.
  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.required]
}

resource "google_compute_managed_ssl_certificate" "web_lb" {
  count = local.web_lb_enabled ? 1 : 0

  # Managed certificate domains are immutable. The name carries the domain set
  # so a change creates the replacement before the old certificate is removed.
  name = "${local.web_lb_name}-${substr(sha256(join(",", local.web_lb_domains)), 0, 8)}"

  managed {
    domains = local.web_lb_domains
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [google_project_service.required]
}

resource "google_compute_region_network_endpoint_group" "web_lb" {
  count = local.web_lb_terraform_backend ? 1 : 0

  name                  = local.web_lb_name
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  # By name (cloud_run.tf uses the same local), not by resource reference:
  # see local.web_service_name. A serverless NEG may precede its service.
  cloud_run {
    service = local.web_service_name
  }
}

resource "google_compute_backend_service" "web_lb" {
  count = local.web_lb_terraform_backend ? 1 : 0

  name                  = local.web_lb_name
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"

  backend {
    group = google_compute_region_network_endpoint_group.web_lb[0].id
  }
}

resource "google_compute_backend_service" "web_lb_release" {
  count = local.web_lb_release_attached ? 1 : 0

  name                  = local.web_lb_name
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"
  description           = "Serves the verified ephemeral staging release; staging_lifecycle.py owns the backend list."

  # The backend is the current release's serverless NEG, attached and
  # detached by staging_lifecycle.py. A foundation apply must not undo it.
  lifecycle {
    ignore_changes = [backend]
  }

  depends_on = [google_project_service.required]
}

resource "google_compute_region_network_endpoint_group" "api_lb" {
  count = local.web_lb_api_routed ? 1 : 0

  name                  = "${local.name_prefix}-api-lb"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = local.api_service_name
  }
}

# The API stays IAM-only behind the load balancer: Cloud Run still requires an
# invoker identity token (api_invoker_members, the uptime service agent, the
# deploy smoke identity). INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER admits it.
resource "google_compute_backend_service" "api_lb" {
  count = local.web_lb_api_routed ? 1 : 0

  name                  = "${local.name_prefix}-api-lb"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"

  backend {
    group = google_compute_region_network_endpoint_group.api_lb[0].id
  }
}

resource "google_compute_url_map" "web_lb" {
  count = local.web_lb_enabled ? 1 : 0

  name            = local.web_lb_name
  default_service = local.web_lb_backend_service_id

  dynamic "host_rule" {
    for_each = local.web_lb_api_routed ? [var.api_public_host] : []
    content {
      hosts        = [host_rule.value]
      path_matcher = "api"
    }
  }

  dynamic "path_matcher" {
    for_each = local.web_lb_api_routed ? ["api"] : []
    content {
      name            = path_matcher.value
      default_service = google_compute_backend_service.api_lb[0].id
    }
  }

  lifecycle {
    precondition {
      condition = (
        can(regex("^https://[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", var.web_base_url))
        && !endswith(var.web_base_url, ".run.app")
      )
      error_message = "web_load_balancer_enabled requires web_base_url to be a bare https custom-domain origin (lowercase host, no port, path or trailing slash, not run.app)."
    }
    precondition {
      condition     = var.environment != "staging" || var.api_public_host == ""
      error_message = "Staging APIs are release-scoped and IAM-only on run.app; api_public_host must stay empty in staging."
    }
    precondition {
      condition     = var.api_public_host != local.web_public_host
      error_message = "api_public_host must differ from the Web host: the load balancer routes the API by host name."
    }
  }
}

# TLS 1.2 is the minimum the HTTPS frontend negotiates. staging_lifecycle.py's
# web_custom_domain verify stage reads this policy back from the proxy.
resource "google_compute_ssl_policy" "web_lb" {
  count = local.web_lb_enabled ? 1 : 0

  name            = local.web_lb_name
  profile         = "MODERN"
  min_tls_version = "TLS_1_2"
}

resource "google_compute_target_https_proxy" "web_lb" {
  count = local.web_lb_enabled ? 1 : 0

  name             = local.web_lb_name
  url_map          = google_compute_url_map.web_lb[0].id
  ssl_certificates = [google_compute_managed_ssl_certificate.web_lb[0].id]
  ssl_policy       = google_compute_ssl_policy.web_lb[0].id
}

resource "google_compute_global_forwarding_rule" "web_lb" {
  count = local.web_lb_enabled ? 1 : 0

  name                  = local.web_lb_name
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_protocol           = "TCP"
  port_range            = "443"
  ip_address            = google_compute_global_address.web_lb[0].id
  target                = google_compute_target_https_proxy.web_lb[0].id
  labels                = local.labels
}

# Plain HTTP on the same address only redirects to HTTPS (301, same host, path
# and query); it never reaches a backend.
resource "google_compute_url_map" "web_lb_http_redirect" {
  count = local.web_lb_enabled ? 1 : 0

  name = "${local.web_lb_name}-http"

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

resource "google_compute_target_http_proxy" "web_lb_http_redirect" {
  count = local.web_lb_enabled ? 1 : 0

  name    = "${local.web_lb_name}-http"
  url_map = google_compute_url_map.web_lb_http_redirect[0].id
}

resource "google_compute_global_forwarding_rule" "web_lb_http_redirect" {
  count = local.web_lb_enabled ? 1 : 0

  name                  = "${local.web_lb_name}-http"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_protocol           = "TCP"
  port_range            = "80"
  ip_address            = google_compute_global_address.web_lb[0].id
  target                = google_compute_target_http_proxy.web_lb_http_redirect[0].id
  labels                = local.labels
}
