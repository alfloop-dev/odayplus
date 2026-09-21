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

# This root owns only recovery storage. Existing foundation network, SQL, KMS,
# state, snapshot, model and lease resources retain their original owners.
resource "google_storage_bucket" "recovery" {
  name                        = var.bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels = {
    app         = "oday-plus"
    environment = "staging"
    managed_by  = "terraform"
    purpose     = "release-recovery"
  }

  encryption {
    default_kms_key_name = var.kms_key_id
  }

  versioning {
    enabled = true
  }

  retention_policy {
    retention_period = 30 * 86400
    is_locked        = false
  }

  # No automatic object deletion: release lifecycle controls bundle cleanup,
  # including failed-release holds, after retention expires.
  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = var.bucket_name != var.state_bucket_name && !contains(var.protected_bucket_names, var.bucket_name)
      error_message = "Recovery storage must be distinct from state, snapshot, model, MLflow and lease buckets."
    }

    precondition {
      condition     = startswith(var.kms_key_id, "projects/${var.project_id}/locations/${var.region}/keyRings/")
      error_message = "Use the verified staging foundation CMEK in the same project and region."
    }

    precondition {
      condition     = endswith(var.deployer_service_account, "@${var.project_id}.iam.gserviceaccount.com")
      error_message = "The recovery deployer must be a verified service account in the staging project."
    }
  }
}

# Add a single grant; never replace a whole bucket or project IAM policy.
resource "google_storage_bucket_iam_member" "deployer" {
  bucket = google_storage_bucket.recovery.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${var.deployer_service_account}"
}

output "recovery_bucket_name" {
  value = google_storage_bucket.recovery.name
}
