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
