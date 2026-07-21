output "bucket_name" {
  value       = google_storage_bucket.worm.name
  description = "Versioned retention-policy bucket used as the WORM-capable audit evidence sink."
}

output "worm_sink_uri" {
  value       = "gs://${google_storage_bucket.worm.name}/audit-evidence"
  description = "Runtime ODP_AUDIT_WORM_SINK_URI for immutable audit evidence writes."
}

output "writer_service_account" {
  value       = google_service_account.writer.email
  description = "Append-only writer service account; it has objectCreator and no delete/update grant."
}

output "retention_manager_service_account" {
  value       = google_service_account.retention_manager.email
  description = "Separated service account for governed retention operations."
}
