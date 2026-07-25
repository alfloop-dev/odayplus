resource "google_cloud_run_v2_service" "api" {
  name     = "${local.name_prefix}-api"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  template {
    service_account                  = google_service_account.runtime.email
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"
    timeout                          = "300s"
    max_instance_request_concurrency = 80

    scaling {
      min_instance_count = var.api_min_instances
      max_instance_count = var.api_max_instances
    }

    vpc_access {
      network_interfaces {
        network    = google_compute_network.runtime.name
        subnetwork = google_compute_subnetwork.runtime.name
      }
      egress = "ALL_TRAFFIC"
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.primary.connection_name]
      }
    }

    containers {
      image = var.api_image

      ports {
        name           = "http1"
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = var.api_cpu
          memory = var.api_memory
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      dynamic "env" {
        for_each = local.runtime_plain_env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.managed_runtime_secret_refs
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value.secret_id
              version = env.value.version
            }
          }
        }
      }

      dynamic "env" {
        for_each = local.external_runtime_secret_refs
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = data.google_secret_manager_secret.external_runtime[env.key].secret_id
              version = env.value.version
            }
          }
        }
      }

      startup_probe {
        initial_delay_seconds = 5
        timeout_seconds       = 5
        period_seconds        = 10
        failure_threshold     = 30

        http_get {
          path = "/readiness"
          port = 8000
        }
      }

      liveness_probe {
        initial_delay_seconds = 30
        timeout_seconds       = 5
        period_seconds        = 30
        failure_threshold     = 3

        http_get {
          path = "/healthz"
          port = 8000
        }
      }
    }
  }

  labels = local.labels

  lifecycle {
    precondition {
      condition     = !local.is_prod || can(regex("@sha256:[0-9a-f]{64}$", var.api_image))
      error_message = "Production Cloud Run cannot deploy a mutable image tag."
    }
  }

  depends_on = [
    google_project_iam_member.runtime_cloud_sql_client,
    google_secret_manager_secret_iam_member.runtime_cursor_signing_key,
    google_secret_manager_secret_iam_member.runtime_database_url,
    google_secret_manager_secret_iam_member.runtime_external_secrets,
    google_secret_manager_secret_version.cursor_signing_key,
    google_secret_manager_secret_version.database_url,
    google_storage_bucket_iam_member.runtime_artifact_objects,
    google_storage_bucket_iam_member.runtime_snapshot_objects,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "api_invoker" {
  for_each = var.api_invoker_members

  project  = var.project_id
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = each.value
}

resource "google_cloud_run_v2_service_iam_member" "web_invokes_api" {
  project  = var.project_id
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.web.email}"
}

resource "google_cloud_run_v2_service" "web" {
  name     = "${local.name_prefix}-web"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account                  = google_service_account.web.email
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"
    timeout                          = "300s"
    max_instance_request_concurrency = 80

    scaling {
      min_instance_count = var.web_min_instances
      max_instance_count = var.web_max_instances
    }

    vpc_access {
      network_interfaces {
        network    = google_compute_network.runtime.name
        subnetwork = google_compute_subnetwork.runtime.name
      }
      egress = "ALL_TRAFFIC"
    }

    containers {
      image = var.web_image

      ports {
        name           = "http1"
        container_port = 3000
      }

      resources {
        limits = {
          cpu    = var.web_cpu
          memory = var.web_memory
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = local.web_plain_env
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name = "ODP_WEB_SESSION_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.web_session_secret.secret_id
            version = google_secret_manager_secret_version.web_session_secret.version
          }
        }
      }

      dynamic "env" {
        for_each = local.web_oidc_secret_refs
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = data.google_secret_manager_secret.web_oidc_client[env.key].secret_id
              version = env.value.version
            }
          }
        }
      }

      startup_probe {
        initial_delay_seconds = 2
        timeout_seconds       = 3
        period_seconds        = 5
        failure_threshold     = 30

        tcp_socket {
          port = 3000
        }
      }

      liveness_probe {
        initial_delay_seconds = 30
        timeout_seconds       = 3
        period_seconds        = 30
        failure_threshold     = 3

        tcp_socket {
          port = 3000
        }
      }
    }
  }

  labels = local.labels

  lifecycle {
    precondition {
      condition     = !local.is_prod || can(regex("@sha256:[0-9a-f]{64}$", var.web_image))
      error_message = "Production Web cannot deploy a mutable image tag."
    }
  }

  depends_on = [
    google_cloud_run_v2_service_iam_member.web_invokes_api,
    google_compute_subnetwork_iam_member.web_network_user,
    google_secret_manager_secret_iam_member.web_session_secret,
    google_secret_manager_secret_iam_member.web_oidc_client_secret,
    google_secret_manager_secret_version.web_session_secret,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "web_invoker" {
  for_each = var.web_invoker_members

  project  = var.project_id
  location = google_cloud_run_v2_service.web.location
  name     = google_cloud_run_v2_service.web.name
  role     = "roles/run.invoker"
  member   = each.value
}
