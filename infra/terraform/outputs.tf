output "api_uri" {
  value       = one(google_cloud_run_v2_service.api[*].uri)
  description = "Cloud Run API URI. The service remains IAM-protected and uses internal/load-balancer ingress. Null when cloud_run_services_managed is false."
}

output "api_service_account" {
  value       = google_service_account.runtime.email
  description = "Least-privilege API runtime service account."
}

output "web_uri" {
  value       = one(google_cloud_run_v2_service.web[*].uri)
  description = "Cloud Run Web URI. Application routes are protected by the Web OIDC session. Null when cloud_run_services_managed is false."
}

output "web_service_account" {
  value       = google_service_account.web.email
  description = "Least-privilege Web BFF identity and private API invoker."
}

output "worker_service_account" {
  value       = google_service_account.worker.email
  description = "Least-privilege asynchronous worker service account."
}

output "web_session_secret_id" {
  value       = google_secret_manager_secret.web_session_secret.secret_id
  description = "Secret Manager id containing the generated Web session encryption key."
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
  value       = module.runtime_foundation.cloud_sql_instance_connection_name
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

output "intake_cursor_signing_key_secret_ref" {
  description = "Pinned Secret Manager reference for the release-owned API's shared cursor signing key."
  value       = "${google_secret_manager_secret.cursor_signing_key.secret_id}:${google_secret_manager_secret_version.cursor_signing_key.version}"
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
  value       = module.runtime_foundation.network_name
  description = "Private runtime VPC."
}

output "runtime_kms_key" {
  value       = module.runtime_foundation.kms_crypto_key_id
  description = "CMEK resource id. No key material is exposed."
}

output "web_load_balancer_ip" {
  value       = one(google_compute_global_address.web_lb[*].address)
  description = "Reserved static IP of the custom-domain load balancer. The registrar A record of every web_load_balancer_hosts entry must point here. Null when web_load_balancer_enabled is false."
}

output "web_load_balancer_hosts" {
  value       = local.web_lb_enabled ? local.web_lb_domains : []
  description = "Host names on the Google-managed certificate: the DNS A records the owner adds at the registrar."
}

output "web_load_balancer_backend_service" {
  value       = one(google_compute_backend_service.web_lb_release[*].name)
  description = "Staging only: the backend service staging_lifecycle.py attaches each verified release's serverless NEG to."
}
