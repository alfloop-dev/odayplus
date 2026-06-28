output "api_uri" {
  value       = google_cloud_run_v2_service.api.uri
  description = "Cloud Run API URI."
}

output "artifact_bucket" {
  value       = google_storage_bucket.artifacts.name
  description = "Bucket for snapshots, evidence, model artifacts, and release packages."
}

output "database_instance" {
  value       = google_sql_database_instance.primary.connection_name
  description = "Cloud SQL connection name."
}

output "jobs_topic" {
  value       = google_pubsub_topic.jobs.name
  description = "Pub/Sub topic for asynchronous jobs."
}
