variable "project_id" {
  type        = string
  description = "GCP project id."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a valid GCP project id."
  }
}

variable "region" {
  type        = string
  description = "GCP region for regional services and data residency."
  default     = "asia-east1"

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]+$", var.region))
    error_message = "region must be a valid GCP region such as asia-east1."
  }
}

variable "environment" {
  type        = string
  description = "Deployment environment."

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of dev, staging, prod."
  }
}

variable "labels" {
  type        = map(string)
  description = "Additional labels applied to managed resources."
  default     = {}
}

variable "api_image" {
  type        = string
  description = "API container image. Production requires an immutable sha256 digest."

  validation {
    condition = (
      length(trimspace(var.api_image)) > 0
      && !can(regex("(?i)(:latest|replace_with|placeholder|changeme)", var.api_image))
    )
    error_message = "api_image must be explicit and must not use latest or placeholder values."
  }
}

variable "web_image" {
  type        = string
  description = "Web container image. Production requires an immutable sha256 digest."

  validation {
    condition = (
      length(trimspace(var.web_image)) > 0
      && !can(regex("(?i)(:latest|replace_with|placeholder|changeme)", var.web_image))
    )
    error_message = "web_image must be explicit and must not use latest or placeholder values."
  }
}

variable "release_sha" {
  type        = string
  description = "Exact source commit deployed by the immutable API image."

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.release_sha))
    error_message = "release_sha must be a full 40-character lowercase Git commit SHA."
  }
}

variable "web_base_url" {
  type        = string
  description = "Public HTTPS origin used for OIDC redirects and secure web-session cookies."
  default     = ""
}

variable "web_oidc_client_id" {
  type        = string
  description = "OIDC client id registered for the ODay Plus web application."
  default     = ""
}

variable "web_oidc_scopes" {
  type        = string
  description = "Space-delimited OIDC scopes requested by the Web BFF."
  default     = "openid profile email"
}

variable "web_oidc_client_secret_ref" {
  type = object({
    secret_id = string
    version   = string
  })
  description = "Pinned Secret Manager reference for ODP_WEB_OIDC_CLIENT_SECRET."
  default     = null
}

variable "web_invoker_members" {
  type        = set(string)
  description = "IAM members allowed to invoke the Web service. The web login surface may be public while application access remains OIDC-protected."
  default     = []
}

variable "web_min_instances" {
  type        = number
  description = "Cloud Run Web minimum instances."
  default     = 0

  validation {
    condition     = var.web_min_instances >= 0
    error_message = "web_min_instances cannot be negative."
  }
}

variable "web_max_instances" {
  type        = number
  description = "Cloud Run Web maximum instances."
  default     = 10

  validation {
    condition     = var.web_max_instances >= 1
    error_message = "web_max_instances must be at least one."
  }
}

variable "web_cpu" {
  type        = string
  description = "Cloud Run Web CPU limit."
  default     = "1"
}

variable "web_memory" {
  type        = string
  description = "Cloud Run Web memory limit."
  default     = "1Gi"
}

variable "live_data_enabled" {
  type        = bool
  description = "Enable live provider and production model gates outside prod. Production always forces this on."
  default     = false
}

variable "network_cidr" {
  type        = string
  description = "Primary Direct VPC egress subnet CIDR."
  default     = "10.42.0.0/24"

  validation {
    condition     = can(cidrhost(var.network_cidr, 1))
    error_message = "network_cidr must be a valid CIDR."
  }
}

variable "private_service_prefix_length" {
  type        = number
  description = "Prefix length reserved for private service networking."
  default     = 16

  validation {
    condition     = var.private_service_prefix_length >= 16 && var.private_service_prefix_length <= 24
    error_message = "private_service_prefix_length must be between 16 and 24."
  }
}

variable "database_name" {
  type        = string
  description = "Canonical PostgreSQL database name."
  default     = "oday"

  validation {
    condition     = can(regex("^[a-z][a-z0-9_]{2,62}$", var.database_name))
    error_message = "database_name must be a valid PostgreSQL identifier."
  }
}

variable "database_user" {
  type        = string
  description = "Built-in PostgreSQL runtime user. Its generated password is stored only as a sensitive Terraform value and Secret Manager version."
  default     = "oday_app"

  validation {
    condition     = can(regex("^[a-z][a-z0-9_]{2,62}$", var.database_user))
    error_message = "database_user must be a valid PostgreSQL identifier."
  }
}

variable "cloud_sql_tier" {
  type        = string
  description = "Cloud SQL machine tier."
  default     = "db-custom-2-7680"

  validation {
    condition     = can(regex("^db-(custom-[0-9]+-[0-9]+|[a-z0-9-]+)$", var.cloud_sql_tier))
    error_message = "cloud_sql_tier must be a valid Cloud SQL tier."
  }
}

variable "cloud_sql_disk_gb" {
  type        = number
  description = "Initial Cloud SQL SSD size in GB."
  default     = 50

  validation {
    condition     = var.cloud_sql_disk_gb >= 20
    error_message = "cloud_sql_disk_gb must be at least 20 GB."
  }
}

variable "cloud_sql_backup_start_time" {
  type        = string
  description = "Daily UTC backup window in HH:MM format."
  default     = "18:00"

  validation {
    condition     = can(regex("^(?:[01][0-9]|2[0-3]):[0-5][0-9]$", var.cloud_sql_backup_start_time))
    error_message = "cloud_sql_backup_start_time must be HH:MM in UTC."
  }
}

variable "cloud_sql_retained_backups" {
  type        = number
  description = "Number of retained automated backups."
  default     = 30

  validation {
    condition     = var.cloud_sql_retained_backups >= 7 && var.cloud_sql_retained_backups <= 365
    error_message = "cloud_sql_retained_backups must be between 7 and 365."
  }
}

variable "cloud_sql_transaction_log_retention_days" {
  type        = number
  description = "PITR transaction log retention in days."
  default     = 7

  validation {
    condition     = var.cloud_sql_transaction_log_retention_days >= 7 && var.cloud_sql_transaction_log_retention_days <= 35
    error_message = "cloud_sql_transaction_log_retention_days must be between 7 and 35."
  }
}

variable "cloud_sql_maintenance_day" {
  type        = number
  description = "Cloud SQL maintenance day, 1 Monday through 7 Sunday."
  default     = 7

  validation {
    condition     = var.cloud_sql_maintenance_day >= 1 && var.cloud_sql_maintenance_day <= 7
    error_message = "cloud_sql_maintenance_day must be between 1 and 7."
  }
}

variable "cloud_sql_maintenance_hour" {
  type        = number
  description = "Cloud SQL maintenance hour in UTC."
  default     = 19

  validation {
    condition     = var.cloud_sql_maintenance_hour >= 0 && var.cloud_sql_maintenance_hour <= 23
    error_message = "cloud_sql_maintenance_hour must be between 0 and 23."
  }
}

variable "artifact_retention_days" {
  type        = number
  description = "Minimum artifact bucket object retention in days."
  default     = 365

  validation {
    condition     = var.artifact_retention_days >= 30
    error_message = "artifact_retention_days must be at least 30."
  }
}

variable "snapshot_retention_days" {
  type        = number
  description = "Minimum source snapshot retention in days."
  default     = 365

  validation {
    condition     = var.snapshot_retention_days >= 30
    error_message = "snapshot_retention_days must be at least 30."
  }
}

variable "audit_retention_period_seconds" {
  type        = number
  description = "WORM retention period for audit evidence objects."
  default     = 220924800 # 2557 days, approximately seven years.

  validation {
    condition     = var.audit_retention_period_seconds >= 31536000
    error_message = "audit_retention_period_seconds must be at least one year."
  }
}

variable "oidc_issuer" {
  type        = string
  description = "Trusted OIDC issuer URL."
  default     = ""
}

variable "oidc_audiences" {
  type        = set(string)
  description = "Accepted OIDC API audiences."
  default     = []
}

variable "oidc_jwks_uri" {
  type        = string
  description = "Trusted OIDC JWKS endpoint."
  default     = ""
}

variable "oidc_jwks_cache_ttl_seconds" {
  type        = number
  description = "Bounded JWKS cache lifetime."
  default     = 300

  validation {
    condition     = var.oidc_jwks_cache_ttl_seconds >= 30 && var.oidc_jwks_cache_ttl_seconds <= 3600
    error_message = "oidc_jwks_cache_ttl_seconds must be between 30 and 3600."
  }
}

variable "oidc_leeway_seconds" {
  type        = number
  description = "Maximum JWT clock-skew leeway."
  default     = 60

  validation {
    condition     = var.oidc_leeway_seconds >= 0 && var.oidc_leeway_seconds <= 300
    error_message = "oidc_leeway_seconds must be between 0 and 300."
  }
}

variable "mlflow_tracking_uri" {
  type        = string
  description = "Remote MLflow tracking/registry URI."
  default     = ""
}

variable "external_provider_endpoints" {
  type        = map(string)
  description = "Runtime environment variable to approved live-provider HTTPS endpoint."
  default     = {}
}

variable "external_provider_secret_refs" {
  type = map(object({
    secret_id = string
    version   = string
  }))
  description = "Runtime provider credential environment variable to pinned Secret Manager secret id/version. Secret values never enter Terraform variables."
  default     = {}
}

variable "model_runtime_config" {
  type        = map(string)
  description = "Non-secret production model and OSS engine metadata passed as environment variables."
  default     = {}
}

variable "model_secret_refs" {
  type = map(object({
    secret_id = string
    version   = string
  }))
  description = "Model registry credential environment variable to pinned Secret Manager secret id/version."
  default     = {}
}

variable "runtime_additional_env" {
  type        = map(string)
  description = "Additional non-secret runtime environment. Reserved production controls cannot be overridden."
  default     = {}
}

variable "runtime_additional_secret_refs" {
  type = map(object({
    secret_id = string
    version   = string
  }))
  description = "Additional runtime secret environment variable to pinned Secret Manager secret id/version."
  default     = {}
}

variable "api_invoker_members" {
  type        = set(string)
  description = "IAM members allowed to invoke the API. Production requires an explicit non-public set."
  default     = []
}

variable "api_min_instances" {
  type        = number
  description = "Cloud Run minimum instances."
  default     = 0

  validation {
    condition     = var.api_min_instances >= 0
    error_message = "api_min_instances cannot be negative."
  }
}

variable "api_max_instances" {
  type        = number
  description = "Cloud Run maximum instances."
  default     = 10

  validation {
    condition     = var.api_max_instances >= 1
    error_message = "api_max_instances must be at least one."
  }
}

variable "api_cpu" {
  type        = string
  description = "Cloud Run API CPU limit."
  default     = "2"
}

variable "api_memory" {
  type        = string
  description = "Cloud Run API memory limit."
  default     = "2Gi"
}

variable "pubsub_ack_deadline_seconds" {
  type        = number
  description = "Worker acknowledgement deadline."
  default     = 60

  validation {
    condition     = var.pubsub_ack_deadline_seconds >= 10 && var.pubsub_ack_deadline_seconds <= 600
    error_message = "pubsub_ack_deadline_seconds must be between 10 and 600."
  }
}

variable "pubsub_message_retention_seconds" {
  type        = number
  description = "Source and dead-letter subscription message retention."
  default     = 604800

  validation {
    condition     = var.pubsub_message_retention_seconds >= 86400 && var.pubsub_message_retention_seconds <= 2678400
    error_message = "pubsub_message_retention_seconds must be between one and 31 days."
  }
}

variable "pubsub_max_delivery_attempts" {
  type        = number
  description = "Delivery attempts before routing to DLQ."
  default     = 5

  validation {
    condition     = var.pubsub_max_delivery_attempts >= 5 && var.pubsub_max_delivery_attempts <= 100
    error_message = "pubsub_max_delivery_attempts must be between 5 and 100."
  }
}
