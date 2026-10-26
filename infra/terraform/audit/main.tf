locals {
  name_prefix = "oday-${var.environment}-audit"
  bucket_name = "${local.name_prefix}-worm-${var.project_id}"
}

resource "google_service_account" "writer" {
  account_id   = "${local.name_prefix}-writer"
  display_name = "ODay Plus ${var.environment} append-only audit evidence writer"
}

resource "google_service_account" "retention_manager" {
  account_id   = "${local.name_prefix}-retention"
  display_name = "ODay Plus ${var.environment} audit retention manager"
}

resource "google_storage_bucket" "worm" {
  name                        = local.bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = var.labels

  versioning {
    enabled = true
  }

  encryption {
    default_kms_key_name = var.kms_key_name
  }

  retention_policy {
    retention_period = var.retention_period_seconds
    is_locked        = var.lock_retention_policy
  }
}

resource "google_storage_bucket_iam_member" "writer_object_create" {
  bucket = google_storage_bucket.worm.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.writer.email}"
}

resource "google_service_account_iam_member" "runtime_impersonates_writer" {
  service_account_id = google_service_account.writer.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${var.product_runtime_service_account_email}"
}

resource "google_storage_bucket_iam_member" "retention_manager_object_admin" {
  bucket = google_storage_bucket.worm.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.retention_manager.email}"
}

# Recovery can fetch the deterministic object only; it cannot create, list,
# update or delete evidence and does not impersonate the retention manager.
resource "google_service_account" "recovery_reader" {
  account_id   = "${local.name_prefix}-reader"
  display_name = "ODay Plus ${var.environment} audit recovery reader"
}

resource "google_project_iam_custom_role" "recovery_get" {
  role_id     = "${replace(local.name_prefix, "-", "_")}_evidence_get"
  title       = "Audit evidence recovery object getter"
  permissions = ["storage.objects.get"]
}

resource "google_storage_bucket_iam_member" "recovery_object_get" {
  bucket = google_storage_bucket.worm.name
  role   = google_project_iam_custom_role.recovery_get.name
  member = "serviceAccount:${google_service_account.recovery_reader.email}"
}

resource "google_service_account_iam_member" "runtime_impersonates_recovery_reader" {
  service_account_id = google_service_account.recovery_reader.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${var.product_runtime_service_account_email}"
}
