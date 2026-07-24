data "google_secret_manager_secret" "external_runtime" {
  for_each = local.external_runtime_secret_refs

  project   = var.project_id
  secret_id = each.value.secret_id

  depends_on = [google_project_service.required]
}

resource "google_project_iam_member" "runtime_cloud_sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "runtime_database_url" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.database_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "runtime_cursor_signing_key" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.cursor_signing_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "runtime_external_secrets" {
  for_each = data.google_secret_manager_secret.external_runtime

  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_storage_bucket_iam_member" "runtime_artifact_objects" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_storage_bucket_iam_member" "runtime_snapshot_objects" {
  bucket = google_storage_bucket.source_snapshots.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.runtime.email}"
}
