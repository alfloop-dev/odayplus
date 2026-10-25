# Cloud Monitoring for the Terraform-managed runtime (ODP-PROD-MONITORING-ALERTING-001).
#
# Thresholds live in one place below; each cites the repository source that
# owns it. Notification targets are never hard-coded: they come from
# monitoring_notification_emails / monitoring_notification_channel_ids, and the
# production contract in checks.tf fails closed when prod has neither.

locals {
  # Ratified API p95 budget: docs/design/ODP-PGAP-RELIABILITY-001_SLO_AND_DR_TARGETS_RATIFICATION.md §2
  # (<= 3.0 s), measured on run.googleapis.com/request_latencies per obligation
  # ODP-NFR-PERF-001-PROD. infra/monitoring/slo.json (800 ms) is unratified and
  # the < 5 s watch-window value is a manual rollback aid, so neither is used.
  monitoring_api_latency_p95_threshold_ms = 3000

  # Watch-window error-rate SLO "< 5%": docs/evidence/runtime/ODP-PROD-OPS-WATCH-PARAMS-001/README.md §2.
  monitoring_5xx_ratio_threshold = 0.05

  # Rollback trigger windows ("持續 5 分鐘", "連續失敗 3 次"): same README §2.
  monitoring_sustained_window = "300s"
  monitoring_uptime_period    = "60s"
  monitoring_uptime_duration  = "180s"

  # Engineering defaults with no ratified source; tune after first production
  # traffic. Connection saturation is relative to the human-supplied
  # monitoring_cloud_sql_max_connections because the tier-derived default is
  # not pinned by a database flag.
  monitoring_cloud_sql_cpu_threshold        = 0.8
  monitoring_cloud_sql_disk_threshold       = 0.85
  monitoring_cloud_sql_connection_threshold = 0.8
  monitoring_cloud_sql_window               = "600s"

  monitoring_notification_channels = concat(
    [for channel in google_monitoring_notification_channel.email : channel.name],
    tolist(var.monitoring_notification_channel_ids),
  )

  monitoring_web_uptime_host = (
    var.web_base_url != ""
    ? regex("^https?://([^/:]+)", var.web_base_url)[0]
    : trimprefix(google_cloud_run_v2_service.web.uri, "https://")
  )

  monitoring_cloud_run_services = {
    api = google_cloud_run_v2_service.api.name
    web = google_cloud_run_v2_service.web.name
  }

  monitoring_cloud_sql_database_id = "${var.project_id}:${module.runtime_foundation.cloud_sql_instance_name}"

  monitoring_uptime_checks = merge(
    { web = google_monitoring_uptime_check_config.web_login.uptime_check_id },
    var.monitoring_api_uptime_host != "" ? {
      api = google_monitoring_uptime_check_config.api_health[0].uptime_check_id
    } : {},
  )

  monitoring_runbook = "Runbook: docs/runbooks/observability-and-runbook.md; incident levels: docs/runbooks/incident-management.md."
}

data "google_project" "current" {
  project_id = var.project_id
}

resource "google_monitoring_notification_channel" "email" {
  for_each = var.monitoring_notification_emails

  display_name = "ODay Plus ${var.environment} alert email ${each.key}"
  type         = "email"
  labels = {
    email_address = each.value
  }
  user_labels = local.labels

  depends_on = [google_project_service.required]
}

resource "google_monitoring_uptime_check_config" "web_login" {
  display_name = "${local.name_prefix} web /login"
  period       = local.monitoring_uptime_period
  timeout      = "10s"

  http_check {
    path           = "/login"
    port           = 443
    use_ssl        = true
    validate_ssl   = true
    request_method = "GET"

    accepted_response_status_codes {
      status_class = "STATUS_CLASS_2XX"
    }
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = local.monitoring_web_uptime_host
    }
  }

  user_labels = local.labels

  depends_on = [google_project_service.required]
}

resource "google_monitoring_uptime_check_config" "api_health" {
  # The API is IAM-only and Terraform pins INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER,
  # so external uptime checkers cannot reach the run.app host. The check is
  # created only once an operator names a host the checkers can reach; prod
  # requires it (checks.tf).
  count = var.monitoring_api_uptime_host != "" ? 1 : 0

  display_name = "${local.name_prefix} api /platform/health"
  period       = local.monitoring_uptime_period
  timeout      = "10s"

  http_check {
    path           = "/platform/health"
    port           = 443
    use_ssl        = true
    validate_ssl   = true
    request_method = "GET"

    accepted_response_status_codes {
      status_class = "STATUS_CLASS_2XX"
    }

    service_agent_authentication {
      type = "OIDC_TOKEN"
    }
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = var.monitoring_api_uptime_host
    }
  }

  user_labels = local.labels

  depends_on = [google_project_service.required]
}

resource "google_cloud_run_v2_service_iam_member" "api_uptime_invoker" {
  count = var.monitoring_api_uptime_host != "" ? 1 : 0

  project  = var.project_id
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-monitoring-notification.iam.gserviceaccount.com"
}

resource "google_monitoring_alert_policy" "uptime_failure" {
  for_each = local.monitoring_uptime_checks

  display_name          = "${local.name_prefix} ${each.key} uptime check failing"
  combiner              = "OR"
  severity              = "CRITICAL"
  notification_channels = local.monitoring_notification_channels
  user_labels           = merge(local.labels, { odp_alert_id = "api-availability-drop" })

  conditions {
    display_name = "${each.key} uptime check failed from more than one region"

    condition_threshold {
      filter          = "resource.type = \"uptime_url\" AND metric.type = \"monitoring.googleapis.com/uptime_check/check_passed\" AND metric.labels.check_id = \"${each.value}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = local.monitoring_uptime_duration

      aggregations {
        alignment_period     = local.monitoring_uptime_period
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }

      trigger {
        count = 1
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    content   = "ODay Plus ${var.environment} ${each.key} uptime check is failing (rollback trigger: health check fails 3 times in a row). ${local.monitoring_runbook}"
  }
}

resource "google_monitoring_alert_policy" "cloud_run_5xx_ratio" {
  for_each = local.monitoring_cloud_run_services

  display_name          = "${local.name_prefix} ${each.key} 5xx ratio above ${format("%.0f", local.monitoring_5xx_ratio_threshold * 100)}%"
  combiner              = "OR"
  severity              = "CRITICAL"
  notification_channels = local.monitoring_notification_channels
  user_labels           = merge(local.labels, { odp_alert_id = "api-availability-drop" })

  conditions {
    display_name = "${each.value} 5xx / all requests"

    condition_threshold {
      filter             = "resource.type = \"cloud_run_revision\" AND resource.labels.service_name = \"${each.value}\" AND metric.type = \"run.googleapis.com/request_count\" AND metric.labels.response_code_class = \"5xx\""
      denominator_filter = "resource.type = \"cloud_run_revision\" AND resource.labels.service_name = \"${each.value}\" AND metric.type = \"run.googleapis.com/request_count\""
      comparison         = "COMPARISON_GT"
      threshold_value    = local.monitoring_5xx_ratio_threshold
      duration           = local.monitoring_sustained_window

      aggregations {
        alignment_period     = local.monitoring_sustained_window
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.service_name"]
      }

      denominator_aggregations {
        alignment_period     = local.monitoring_sustained_window
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.service_name"]
      }

      trigger {
        count = 1
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    content   = "ODay Plus ${var.environment} ${each.key} 5xx ratio is above the watch-window error SLO. ${local.monitoring_runbook}"
  }
}

resource "google_monitoring_alert_policy" "api_latency_p95" {
  display_name          = "${local.name_prefix} api request latency p95 above ${local.monitoring_api_latency_p95_threshold_ms} ms"
  combiner              = "OR"
  severity              = "ERROR"
  notification_channels = local.monitoring_notification_channels
  user_labels           = merge(local.labels, { odp_alert_id = "api-availability-drop" })

  conditions {
    display_name = "${google_cloud_run_v2_service.api.name} request_latencies p95"

    condition_threshold {
      filter          = "resource.type = \"cloud_run_revision\" AND resource.labels.service_name = \"${google_cloud_run_v2_service.api.name}\" AND metric.type = \"run.googleapis.com/request_latencies\""
      comparison      = "COMPARISON_GT"
      threshold_value = local.monitoring_api_latency_p95_threshold_ms
      duration        = local.monitoring_sustained_window

      aggregations {
        alignment_period     = local.monitoring_sustained_window
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_PERCENTILE_95"
        group_by_fields      = ["resource.label.service_name"]
      }

      trigger {
        count = 1
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    content   = "ODay Plus ${var.environment} API p95 latency exceeds the ratified 3.0 s budget (ODP-PGAP-RELIABILITY-001). ${local.monitoring_runbook}"
  }
}

resource "google_monitoring_alert_policy" "cloud_run_job_failed" {
  # Migration, worker, and scheduler jobs are deployed per release as
  # <base>-r-<sha12> by deploy_cloud_run_waji.sh, so the policy matches every
  # Cloud Run job in the environment project instead of fixed job names.
  display_name          = "${local.name_prefix} Cloud Run job execution failed"
  combiner              = "OR"
  severity              = "ERROR"
  notification_channels = local.monitoring_notification_channels
  user_labels           = local.labels

  conditions {
    display_name = "failed Cloud Run job executions"

    condition_threshold {
      filter          = "resource.type = \"cloud_run_job\" AND metric.type = \"run.googleapis.com/job/completed_execution_count\" AND metric.labels.result = \"failed\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"

      aggregations {
        alignment_period     = local.monitoring_sustained_window
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.job_name"]
      }

      trigger {
        count = 1
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    content   = "ODay Plus ${var.environment} Cloud Run job execution failed (rollback trigger: job failure). ${local.monitoring_runbook}"
  }
}

resource "google_monitoring_alert_policy" "cloud_sql_saturation" {
  display_name          = "${local.name_prefix} Cloud SQL saturation"
  combiner              = "OR"
  severity              = "WARNING"
  notification_channels = local.monitoring_notification_channels
  user_labels           = local.labels

  conditions {
    display_name = "Cloud SQL CPU utilization above ${format("%.0f", local.monitoring_cloud_sql_cpu_threshold * 100)}%"

    condition_threshold {
      filter          = "resource.type = \"cloudsql_database\" AND resource.labels.database_id = \"${local.monitoring_cloud_sql_database_id}\" AND metric.type = \"cloudsql.googleapis.com/database/cpu/utilization\""
      comparison      = "COMPARISON_GT"
      threshold_value = local.monitoring_cloud_sql_cpu_threshold
      duration        = local.monitoring_cloud_sql_window

      aggregations {
        alignment_period   = local.monitoring_sustained_window
        per_series_aligner = "ALIGN_MEAN"
      }

      trigger {
        count = 1
      }
    }
  }

  conditions {
    display_name = "Cloud SQL disk utilization above ${format("%.0f", local.monitoring_cloud_sql_disk_threshold * 100)}%"

    condition_threshold {
      filter          = "resource.type = \"cloudsql_database\" AND resource.labels.database_id = \"${local.monitoring_cloud_sql_database_id}\" AND metric.type = \"cloudsql.googleapis.com/database/disk/utilization\""
      comparison      = "COMPARISON_GT"
      threshold_value = local.monitoring_cloud_sql_disk_threshold
      duration        = local.monitoring_cloud_sql_window

      aggregations {
        alignment_period   = local.monitoring_sustained_window
        per_series_aligner = "ALIGN_MEAN"
      }

      trigger {
        count = 1
      }
    }
  }

  dynamic "conditions" {
    for_each = var.monitoring_cloud_sql_max_connections != null ? [var.monitoring_cloud_sql_max_connections] : []
    content {
      display_name = "Cloud SQL backends above ${format("%.0f", local.monitoring_cloud_sql_connection_threshold * 100)}% of max_connections"

      condition_threshold {
        filter          = "resource.type = \"cloudsql_database\" AND resource.labels.database_id = \"${local.monitoring_cloud_sql_database_id}\" AND metric.type = \"cloudsql.googleapis.com/database/postgresql/num_backends\""
        comparison      = "COMPARISON_GT"
        threshold_value = floor(conditions.value * local.monitoring_cloud_sql_connection_threshold)
        duration        = local.monitoring_sustained_window

        aggregations {
          alignment_period     = local.monitoring_sustained_window
          per_series_aligner   = "ALIGN_MEAN"
          cross_series_reducer = "REDUCE_SUM"
          group_by_fields      = ["resource.label.database_id"]
        }

        trigger {
          count = 1
        }
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    content   = "ODay Plus ${var.environment} Cloud SQL ${module.runtime_foundation.cloud_sql_instance_name} is saturating CPU, disk, or connections. ${local.monitoring_runbook}"
  }
}
