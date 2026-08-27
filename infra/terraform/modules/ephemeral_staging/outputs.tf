output "staging_api_uri" {
  value       = google_cloud_run_v2_service.staging_api.uri
  description = "Staging API Cloud Run URI."
}

output "staging_web_uri" {
  value       = google_cloud_run_v2_service.staging_web.uri
  description = "Staging Web Cloud Run URI."
}

output "staging_api_service_name" {
  value       = google_cloud_run_v2_service.staging_api.name
  description = "Release-scoped staging API Cloud Run service name."
}

output "staging_web_service_name" {
  value       = google_cloud_run_v2_service.staging_web.name
  description = "Release-scoped staging Web Cloud Run service name."
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

output "staging_scheduler_trigger_name" {
  value       = google_cloud_scheduler_job.staging_worker_trigger.name
  description = "Cloud Scheduler trigger job name (starts paused)."
}

output "staging_migration_job_name" {
  value       = google_cloud_run_v2_job.staging_migration.name
  description = "Release-scoped migration Cloud Run Job name."
}

output "staging_worker_job_name" {
  value       = google_cloud_run_v2_job.staging_worker.name
  description = "Release-scoped worker Cloud Run Job name."
}

output "staging_scheduler_job_name" {
  value       = google_cloud_run_v2_job.staging_scheduler.name
  description = "Release-scoped scheduler Cloud Run Job name."
}

output "staging_cloud_sql_instance" {
  value       = var.cloud_sql_instance_name
  description = "Long-lived Cloud SQL foundation instance hosting the isolated database."
}

output "staging_api_image" {
  value       = var.api_image
  description = "Exact API image digest used by the release-scoped service."
}

output "staging_web_image" {
  value       = var.web_image
  description = "Exact Web image digest used by the release-scoped service."
}

output "staging_worker_image" {
  value       = var.worker_image
  description = "Exact Worker image digest used by the release-scoped jobs."
}

output "staging_scheduler_image" {
  value       = var.scheduler_image
  description = "Exact Scheduler image digest used by the release-scoped job."
}

output "resource_labels" {
  value       = local.resource_labels
  description = "Canonical labels applied to label-capable ephemeral resources, for cleanup targeting."
}

output "ownership_manifest" {
  value       = terraform_data.staging_ownership.output
  description = "Release ownership metadata for provider resources that cannot expose native labels."
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

output "staging_tenant_id" {
  value       = local.tenant_id
  description = "Release-scoped staging tenant identifier."
}
