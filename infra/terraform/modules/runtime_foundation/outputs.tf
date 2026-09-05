output "network_id" {
  value       = google_compute_network.runtime.id
  description = "Runtime VPC network resource id."
}

output "network_name" {
  value       = google_compute_network.runtime.name
  description = "Runtime VPC network name."
}

output "subnetwork_id" {
  value       = google_compute_subnetwork.runtime.id
  description = "Runtime subnetwork resource id."
}

output "subnetwork_name" {
  value       = google_compute_subnetwork.runtime.name
  description = "Runtime subnetwork name."
}

output "subnetwork_cidr" {
  value       = google_compute_subnetwork.runtime.ip_cidr_range
  description = "Runtime subnetwork CIDR range."
}

output "kms_key_ring_id" {
  value       = google_kms_key_ring.runtime.id
  description = "KMS key ring resource id."
}

output "kms_key_ring_name" {
  value       = google_kms_key_ring.runtime.name
  description = "KMS key ring name."
}

output "kms_crypto_key_id" {
  value       = google_kms_crypto_key.runtime.id
  description = "KMS crypto key resource id."
}

output "kms_crypto_key_name" {
  value       = google_kms_crypto_key.runtime.name
  description = "KMS crypto key name."
}

output "cloud_sql_instance_id" {
  value       = google_sql_database_instance.primary.id
  description = "Cloud SQL instance resource id."
}

output "cloud_sql_instance_name" {
  value       = google_sql_database_instance.primary.name
  description = "Cloud SQL instance name."
}

output "cloud_sql_instance_connection_name" {
  value       = google_sql_database_instance.primary.connection_name
  description = "Cloud SQL connection name for Cloud Run volume mounts."
}

output "cloud_sql_service_account_email" {
  value       = google_project_service_identity.cloud_sql.email
  description = "Cloud SQL service agent email."
}

output "pubsub_service_account_email" {
  value       = google_project_service_identity.pubsub.email
  description = "Pub/Sub service agent email."
}

output "gcs_service_account_email" {
  value       = data.google_storage_project_service_account.gcs.email_address
  description = "Cloud Storage service agent email."
}
