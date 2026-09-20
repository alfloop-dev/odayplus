resource "random_password" "database" {
  length  = 48
  special = false

  keepers = {
    instance = module.runtime_foundation.cloud_sql_instance_name
    user     = var.database_user
  }
}

resource "google_sql_database" "app" {
  name     = var.database_name
  instance = module.runtime_foundation.cloud_sql_instance_name
}

resource "google_sql_user" "app" {
  name     = var.database_user
  instance = module.runtime_foundation.cloud_sql_instance_name
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
    module.runtime_foundation.cloud_sql_instance_connection_name,
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

resource "random_password" "web_session_secret" {
  length  = 64
  special = false
}

resource "google_secret_manager_secret" "web_session_secret" {
  secret_id = "${local.name_prefix}-web-session-secret"

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

resource "google_secret_manager_secret_version" "web_session_secret" {
  secret      = google_secret_manager_secret.web_session_secret.id
  secret_data = random_password.web_session_secret.result
}
