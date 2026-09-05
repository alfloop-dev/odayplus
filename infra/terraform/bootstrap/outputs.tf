output "state_bucket_name" {
  value       = google_storage_bucket.terraform_state.name
  description = "Governed GCS state bucket name."
}

output "state_bucket_url" {
  value       = google_storage_bucket.terraform_state.url
  description = "Governed GCS state bucket URL (gs://...)."
}

output "state_kms_key_id" {
  value       = google_kms_crypto_key.state_backend.id
  description = "CMEK crypto key id encrypting Terraform state."
}

output "backend_config_hcl_example" {
  value       = "bucket = \"${google_storage_bucket.terraform_state.name}\"\nprefix = \"oday-plus/${var.environment}\""
  description = "HCL backend configuration snippet for root Terraform init."
}

output "staging_ephemeral_release_prefix_pattern" {
  value       = "oday-plus/staging/releases/{release_id}"
  description = "Governed state prefix pattern for isolated ephemeral staging releases."
}
