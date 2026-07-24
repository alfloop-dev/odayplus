resource "random_password" "database" {
  length  = 48
  special = false

  keepers = {
    instance = "${local.name_prefix}-sql"
    user     = var.database_user
  }
}

resource "google_sql_database_instance" "primary" {
  name                = "${local.name_prefix}-sql"
  database_version    = "POSTGRES_16"
  region              = var.region
  encryption_key_name = google_kms_crypto_key.runtime.id

  settings {
    tier                        = var.cloud_sql_tier
    availability_type           = local.is_prod ? "REGIONAL" : "ZONAL"
    disk_size                   = var.cloud_sql_disk_gb
    disk_type                   = "PD_SSD"
    disk_autoresize             = true
    deletion_protection_enabled = local.is_prod

    backup_configuration {
      enabled                        = true
      start_time                     = var.cloud_sql_backup_start_time
      location                       = var.region
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = var.cloud_sql_transaction_log_retention_days

      backup_retention_settings {
        retained_backups = var.cloud_sql_retained_backups
        retention_unit   = "COUNT"
      }
    }

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = google_compute_network.runtime.id
      enable_private_path_for_google_cloud_services = true
      ssl_mode                                      = local.is_prod ? "ENCRYPTED_ONLY" : "ALLOW_UNENCRYPTED_AND_ENCRYPTED"
    }

    insights_config {
      query_insights_enabled  = true
      query_plans_per_minute  = 5
      query_string_length     = 4500
      record_application_tags = true
      record_client_address   = false
    }

    maintenance_window {
      day          = var.cloud_sql_maintenance_day
      hour         = var.cloud_sql_maintenance_hour
      update_track = local.is_prod ? "stable" : "canary"
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }

    user_labels = local.labels
  }

  deletion_protection = local.is_prod

  lifecycle {
    precondition {
      condition     = !local.is_prod || var.cloud_sql_transaction_log_retention_days >= 7
      error_message = "Production Cloud SQL PITR requires at least seven days of transaction logs."
    }
  }

  depends_on = [
    google_kms_crypto_key_iam_member.cloud_sql,
    google_service_networking_connection.private_services,
  ]
}

resource "google_sql_database" "app" {
  name     = var.database_name
  instance = google_sql_database_instance.primary.name
}

resource "google_sql_user" "app" {
  name     = var.database_user
  instance = google_sql_database_instance.primary.name
  password = random_password.database.result
}

resource "google_secret_manager_secret" "database_url" {
  secret_id = "${local.name_prefix}-database-url"

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  labels = local.labels

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "database_url" {
  secret = google_secret_manager_secret.database_url.id
  secret_data = join("", [
    "postgresql://",
    var.database_user,
    ":",
    random_password.database.result,
    "@/",
    var.database_name,
    "?host=/cloudsql/",
    google_sql_database_instance.primary.connection_name,
  ])

  depends_on = [
    google_sql_database.app,
    google_sql_user.app,
  ]
}

resource "random_password" "cursor_signing_key" {
  length  = 64
  special = false
}

resource "google_secret_manager_secret" "cursor_signing_key" {
  secret_id = "${local.name_prefix}-intake-cursor-signing-key"

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  labels = local.labels

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "cursor_signing_key" {
  secret      = google_secret_manager_secret.cursor_signing_key.id
  secret_data = random_password.cursor_signing_key.result
}
