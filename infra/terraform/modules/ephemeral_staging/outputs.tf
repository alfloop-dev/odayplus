output "staging_api_uri" {
  value       = google_cloud_run_v2_service.staging_api.uri
  description = "Staging API Cloud Run URI."
}

output "staging_web_uri" {
  value       = google_cloud_run_v2_service.staging_web.uri
  description = "Staging Web Cloud Run URI."
}

output "staging_database_name" {
  value       = google_sql_database.staging.name
  description = "Release-scoped staging database name."
}

output "staging_database_user" {
  value       = google_sql_user.staging.name
  description = "Release-scoped staging database user."
}

output "staging_database_url_secret_id" {
  value       = google_secret_manager_secret.staging_database_url.secret_id
  description = "Secret Manager id for the staging database URL."
}

output "staging_data_bucket" {
  value       = google_storage_bucket.staging_data.name
  description = "Release-scoped staging data bucket."
}

output "staging_runtime_service_account" {
  value       = google_service_account.staging_runtime.email
  description = "Staging runtime service account."
}

output "staging_web_service_account" {
  value       = google_service_account.staging_web.email
  description = "Staging web service account."
}

output "staging_worker_service_account" {
  value       = google_service_account.staging_worker.email
  description = "Staging worker service account."
}

output "staging_jobs_topic" {
  value       = google_pubsub_topic.staging_jobs.name
  description = "Pub/Sub topic for ephemeral staging asynchronous jobs."
}

output "staging_jobs_subscription" {
  value       = google_pubsub_subscription.staging_jobs.name
  description = "Pub/Sub subscription for ephemeral staging asynchronous jobs."
}

output "staging_jobs_dead_letter_topic" {
  value       = google_pubsub_topic.staging_jobs_dlq.name
  description = "Dead-letter topic for ephemeral staging exhausted jobs."
}

output "staging_jobs_dead_letter_subscription" {
  value       = google_pubsub_subscription.staging_jobs_dlq.name
  description = "Dead-letter subscription for ephemeral staging exhausted jobs."
}

output "staging_scheduler_job_name" {
  value       = google_cloud_scheduler_job.staging_worker_trigger.name
  description = "Cloud Scheduler trigger job name (starts paused)."
}

output "resource_labels" {
  value       = local.resource_labels
  description = "Labels applied to all ephemeral resources, for cleanup targeting."
}

output "release_id" {
  value       = var.release_id
  description = "The release_id that scopes this ephemeral staging instance."
}

output "created_at" {
  value       = local.created_at
  description = "Timestamp when this staging instance was created."
}

output "expires_at" {
  value       = local.expires_at
  description = "Timestamp when this staging instance becomes eligible for cleanup."
}
