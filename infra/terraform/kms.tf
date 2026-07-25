resource "google_kms_key_ring" "runtime" {
  name     = "${local.name_prefix}-runtime"
  location = var.region

  depends_on = [google_project_service.required]
}

resource "google_kms_crypto_key" "runtime" {
  name            = "${local.name_prefix}-runtime"
  key_ring        = google_kms_key_ring.runtime.id
  rotation_period = "7776000s"

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_project_service_identity" "cloud_sql" {
  provider = google-beta
  project  = var.project_id
  service  = "sqladmin.googleapis.com"

  depends_on = [google_project_service.required]
}

resource "google_project_service_identity" "pubsub" {
  provider = google-beta
  project  = var.project_id
  service  = "pubsub.googleapis.com"

  depends_on = [google_project_service.required]
}

data "google_storage_project_service_account" "gcs" {
  project = var.project_id

  depends_on = [google_project_service.required]
}

resource "google_kms_crypto_key_iam_member" "cloud_sql" {
  crypto_key_id = google_kms_crypto_key.runtime.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_project_service_identity.cloud_sql.email}"
}

resource "google_kms_crypto_key_iam_member" "gcs" {
  crypto_key_id = google_kms_crypto_key.runtime.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"
}

resource "google_kms_crypto_key_iam_member" "pubsub" {
  crypto_key_id = google_kms_crypto_key.runtime.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_project_service_identity.pubsub.email}"
}
