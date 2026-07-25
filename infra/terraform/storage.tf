resource "google_storage_bucket" "artifacts" {
  name                        = "${local.name_prefix}-artifacts-${var.project_id}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = !local.is_prod
  labels                      = local.labels

  versioning {
    enabled = true
  }

  encryption {
    default_kms_key_name = google_kms_crypto_key.runtime.id
  }

  retention_policy {
    retention_period = var.artifact_retention_days * 86400
    is_locked        = false
  }

  lifecycle_rule {
    condition {
      age                   = var.artifact_retention_days
      with_state            = "ARCHIVED"
      num_newer_versions    = 3
      matches_storage_class = ["STANDARD"]
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_kms_crypto_key_iam_member.gcs]
}

resource "google_storage_bucket" "source_snapshots" {
  name                        = "${local.name_prefix}-source-snapshots-${var.project_id}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = !local.is_prod
  labels                      = local.labels

  versioning {
    enabled = true
  }

  encryption {
    default_kms_key_name = google_kms_crypto_key.runtime.id
  }

  retention_policy {
    retention_period = var.snapshot_retention_days * 86400
    is_locked        = false
  }

  lifecycle_rule {
    condition {
      age                = var.snapshot_retention_days
      with_state         = "ARCHIVED"
      num_newer_versions = 2
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_kms_crypto_key_iam_member.gcs]
}

module "audit_evidence" {
  source = "./audit"

  project_id                            = var.project_id
  region                                = var.region
  environment                           = var.environment
  labels                                = local.labels
  product_runtime_service_account_email = google_service_account.runtime.email
  retention_period_seconds              = var.audit_retention_period_seconds
  kms_key_name                          = google_kms_crypto_key.runtime.id
  lock_retention_policy                 = local.is_prod

  depends_on = [google_kms_crypto_key_iam_member.gcs]
}
