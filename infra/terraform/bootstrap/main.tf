terraform {
  required_version = ">= 1.6.0"

  backend "gcs" {}

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
  is_prod     = var.environment == "prod"
  name_prefix = var.name_prefix != "" ? var.name_prefix : "oday-tfstate-${var.environment}"
  labels = merge(
    {
      app         = "oday-plus"
      environment = var.environment
      managed_by  = "terraform"
      purpose     = "terraform-state-backend"
    },
    var.labels,
  )
}

resource "google_kms_key_ring" "state_backend" {
  name     = "${local.name_prefix}-state"
  location = var.region
}

resource "google_kms_crypto_key" "state_backend" {
  name            = "${local.name_prefix}-state"
  key_ring        = google_kms_key_ring.state_backend.id
  rotation_period = "7776000s"

  lifecycle {
    prevent_destroy = true
  }
}

data "google_storage_project_service_account" "gcs" {
  project = var.project_id
}

resource "google_kms_crypto_key_iam_member" "state_backend_gcs" {
  crypto_key_id = google_kms_crypto_key.state_backend.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"
}

resource "google_storage_bucket" "terraform_state" {
  name                        = "${local.name_prefix}-${var.project_id}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = !local.is_prod
  labels                      = local.labels

  versioning {
    enabled = true
  }

  encryption {
    default_kms_key_name = google_kms_crypto_key.state_backend.id
  }

  retention_policy {
    retention_period = var.retention_period_days * 86400
    is_locked        = local.is_prod
  }

  lifecycle_rule {
    condition {
      age                   = var.retention_period_days
      with_state            = "ARCHIVED"
      num_newer_versions    = 3
      matches_storage_class = ["STANDARD"]
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_kms_crypto_key_iam_member.state_backend_gcs]
}

resource "google_storage_bucket_iam_member" "deployer_state_user" {
  for_each = toset(var.deployer_member_emails)

  bucket = google_storage_bucket.terraform_state.name
  role   = "roles/storage.objectUser"
  member = startswith(each.value, "serviceAccount:") || startswith(each.value, "user:") || startswith(each.value, "group:") ? each.value : "serviceAccount:${each.value}"
}
