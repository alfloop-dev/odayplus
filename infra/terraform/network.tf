module "runtime_foundation" {
  source = "./modules/runtime_foundation"

  project_id                               = var.project_id
  region                                   = var.region
  environment                              = var.environment
  name_prefix                              = local.name_prefix
  labels                                   = local.labels
  network_cidr                             = var.network_cidr
  private_service_prefix_length            = var.private_service_prefix_length
  cloud_sql_tier                           = var.cloud_sql_tier
  cloud_sql_disk_gb                        = var.cloud_sql_disk_gb
  cloud_sql_backup_start_time              = var.cloud_sql_backup_start_time
  cloud_sql_retained_backups               = var.cloud_sql_retained_backups
  cloud_sql_transaction_log_retention_days = var.cloud_sql_transaction_log_retention_days
  cloud_sql_maintenance_day                = var.cloud_sql_maintenance_day
  cloud_sql_maintenance_hour               = var.cloud_sql_maintenance_hour
  enable_deletion_protection               = local.is_prod
  network_user_members = [
    "serviceAccount:${google_service_account.runtime.email}",
    "serviceAccount:${google_service_account.web.email}",
  ]

  depends_on = [
    google_project_service.required,
    terraform_data.production_contract,
  ]
}

# Preserve Terraform resource identity for zero-replacement refactoring
moved {
  from = google_compute_network.runtime
  to   = module.runtime_foundation.google_compute_network.runtime
}

moved {
  from = google_compute_subnetwork.runtime
  to   = module.runtime_foundation.google_compute_subnetwork.runtime
}

moved {
  from = google_compute_global_address.private_services
  to   = module.runtime_foundation.google_compute_global_address.private_services
}

moved {
  from = google_service_networking_connection.private_services
  to   = module.runtime_foundation.google_service_networking_connection.private_services
}

moved {
  from = google_compute_firewall.deny_all_egress
  to   = module.runtime_foundation.google_compute_firewall.deny_all_egress
}

moved {
  from = google_compute_firewall.allow_private_egress
  to   = module.runtime_foundation.google_compute_firewall.allow_private_egress
}

moved {
  from = google_compute_firewall.allow_restricted_google_apis
  to   = module.runtime_foundation.google_compute_firewall.allow_restricted_google_apis
}

moved {
  from = google_kms_key_ring.runtime
  to   = module.runtime_foundation.google_kms_key_ring.runtime
}

moved {
  from = google_kms_crypto_key.runtime
  to   = module.runtime_foundation.google_kms_crypto_key.runtime
}

moved {
  from = google_project_service_identity.cloud_sql
  to   = module.runtime_foundation.google_project_service_identity.cloud_sql
}

moved {
  from = google_project_service_identity.pubsub
  to   = module.runtime_foundation.google_project_service_identity.pubsub
}

moved {
  from = google_kms_crypto_key_iam_member.cloud_sql
  to   = module.runtime_foundation.google_kms_crypto_key_iam_member.cloud_sql
}

moved {
  from = google_kms_crypto_key_iam_member.gcs
  to   = module.runtime_foundation.google_kms_crypto_key_iam_member.gcs
}

moved {
  from = google_kms_crypto_key_iam_member.pubsub
  to   = module.runtime_foundation.google_kms_crypto_key_iam_member.pubsub
}

moved {
  from = google_sql_database_instance.primary
  to   = module.runtime_foundation.google_sql_database_instance.primary
}
