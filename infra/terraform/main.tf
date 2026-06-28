terraform {
  required_version = ">= 1.6.0"

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
  name_prefix = "oday-${var.environment}"
  labels = {
    app         = "oday-plus"
    environment = var.environment
    managed_by  = "terraform"
  }
}

resource "google_service_account" "runtime" {
  account_id   = "${local.name_prefix}-runtime"
  display_name = "ODay Plus ${var.environment} runtime"
}

resource "google_storage_bucket" "artifacts" {
  name                        = "${local.name_prefix}-artifacts-${var.project_id}"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = var.environment != "prod"
  labels                      = local.labels
}

resource "google_sql_database_instance" "primary" {
  name             = "${local.name_prefix}-sql"
  database_version = "POSTGRES_16"
  region           = var.region

  settings {
    tier              = var.cloud_sql_tier
    availability_type = var.environment == "prod" ? "REGIONAL" : "ZONAL"
    disk_size         = var.cloud_sql_disk_gb
    disk_type         = "PD_SSD"

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
    }

    ip_configuration {
      ipv4_enabled = false
    }

    user_labels = local.labels
  }

  deletion_protection = var.environment == "prod"
}

resource "google_sql_database" "app" {
  name     = "oday"
  instance = google_sql_database_instance.primary.name
}

resource "google_pubsub_topic" "jobs" {
  name   = "${local.name_prefix}-jobs"
  labels = local.labels
}

resource "google_pubsub_topic" "dead_letter" {
  name   = "${local.name_prefix}-dlq"
  labels = local.labels
}

resource "google_cloud_run_v2_service" "api" {
  name     = "${local.name_prefix}-api"
  location = var.region

  template {
    service_account = google_service_account.runtime.email

    containers {
      image = var.api_image

      env {
        name  = "ODAY_ENV"
        value = var.environment
      }

      env {
        name  = "ODAY_LOG_FORMAT"
        value = "json"
      }
    }
  }

  labels = local.labels
}
