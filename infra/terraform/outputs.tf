output "api_uri" {
  value       = google_cloud_run_v2_service.api.uri
  description = "Cloud Run API URI. The service remains IAM-protected and uses internal/load-balancer ingress."
}

output "api_service_account" {
  value       = google_service_account.runtime.email
  description = "Least-privilege API runtime service account."
}

output "worker_service_account" {
  value       = google_service_account.worker.email
  description = "Least-privilege asynchronous worker service account."
}

output "artifact_bucket" {
  value       = google_storage_bucket.artifacts.name
  description = "CMEK-encrypted model and release artifact bucket."
}

output "source_snapshot_bucket" {
  value       = google_storage_bucket.source_snapshots.name
  description = "CMEK-encrypted source snapshot bucket."
}

output "audit_evidence_bucket" {
  value       = module.audit_evidence.bucket_name
  description = "WORM-capable audit evidence bucket."
}

output "audit_writer_service_account" {
  value       = module.audit_evidence.writer_service_account
  description = "Append-only audit evidence writer service account."
}

output "audit_retention_manager_service_account" {
  value       = module.audit_evidence.retention_manager_service_account
  description = "Separated service account for governed retention operations."
}

output "database_instance_connection_name" {
  value       = google_sql_database_instance.primary.connection_name
  description = "Cloud SQL connection name used by the Cloud Run Cloud SQL volume."
}

output "database_name" {
  value       = google_sql_database.app.name
  description = "Canonical runtime PostgreSQL database name."
}

output "database_user" {
  value       = google_sql_user.app.name
  description = "Runtime PostgreSQL user name. No password is exposed."
}

output "database_url_secret_id" {
  value       = google_secret_manager_secret.database_url.secret_id
  description = "Secret Manager id containing ODAY_DATABASE_URL. No secret value or version payload is exposed."
}

output "jobs_topic" {
  value       = google_pubsub_topic.jobs.name
  description = "Pub/Sub topic for asynchronous jobs."
}

output "jobs_subscription" {
  value       = google_pubsub_subscription.jobs.name
  description = "Worker job subscription."
}

output "jobs_dead_letter_topic" {
  value       = google_pubsub_topic.dead_letter.name
  description = "Dead-letter topic for exhausted jobs."
}

output "jobs_dead_letter_subscription" {
  value       = google_pubsub_subscription.dead_letter.name
  description = "Operator-visible dead-letter subscription."
}

output "runtime_network" {
  value       = google_compute_network.runtime.name
  description = "Private runtime VPC."
}

output "runtime_egress_ip" {
  value       = google_compute_address.nat.address
  description = "Static outbound IP to allowlist at approved external providers."
}

output "runtime_kms_key" {
  value       = google_kms_crypto_key.runtime.id
  description = "CMEK resource id. No key material is exposed."
}
