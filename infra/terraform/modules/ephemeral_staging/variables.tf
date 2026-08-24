variable "project_id" {
  type        = string
  description = "GCP project id for staging resources."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a valid GCP project id."
  }
}

variable "region" {
  type        = string
  description = "GCP region for regional resources."
  default     = "asia-east1"
}

variable "release_id" {
  type        = string
  description = "Unique release identifier scoping all ephemeral staging resources."

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$", var.release_id))
    error_message = "release_id must match the release manifest identifier pattern."
  }
}

variable "candidate_sha" {
  type        = string
  description = "Exact 40-character lowercase git SHA deployed in this staging release."

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.candidate_sha))
    error_message = "candidate_sha must be a full 40-character lowercase git commit SHA."
  }
}

variable "manifest_digest" {
  type        = string
  description = "SHA-256 digest of the immutable release manifest."

  validation {
    condition     = can(regex("^sha256:[0-9a-f]{64}$", var.manifest_digest))
    error_message = "manifest_digest must be sha256:<64 lowercase hex>."
  }
}

variable "api_image" {
  type        = string
  description = "Immutable API image reference with @sha256 digest."

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.api_image))
    error_message = "api_image must use an immutable @sha256 digest."
  }
}

variable "web_image" {
  type        = string
  description = "Immutable Web image reference with @sha256 digest."

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.web_image))
    error_message = "web_image must use an immutable @sha256 digest."
  }
}

variable "ttl_hours" {
  type        = number
  description = "Maximum hours before staging resources are eligible for cleanup."
  default     = 24

  validation {
    condition     = var.ttl_hours >= 1 && var.ttl_hours <= 168
    error_message = "ttl_hours must be between 1 and 168 (7 days)."
  }
}

variable "created_at" {
  type        = string
  description = "Required fixed RFC3339 timestamp (e.g. 2026-08-24T12:00:00Z) when staging was created. Ensures idempotent Terraform applies."

  validation {
    condition     = can(regex("^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:\\d{2})$", var.created_at))
    error_message = "created_at must be an RFC3339 timestamp string (e.g. 2026-08-24T12:00:00Z)."
  }
}

variable "owner_task_id" {
  type        = string
  description = "Task ID that owns this ephemeral staging release."

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$", var.owner_task_id))
    error_message = "owner_task_id must be a non-empty task identifier."
  }
}

variable "cloud_sql_instance_name" {
  type        = string
  description = "Name of the long-lived staging Cloud SQL instance to create the release database on."
}

variable "cloud_sql_connection_name" {
  type        = string
  description = "Connection name of the long-lived staging Cloud SQL instance."
}

variable "network_name" {
  type        = string
  description = "Name of the shared staging VPC network."
}

variable "subnetwork_name" {
  type        = string
  description = "Name of the shared staging VPC subnetwork."
}

variable "kms_key_id" {
  type        = string
  description = "CMEK key id for encrypting ephemeral staging resources."
}

variable "deployer_service_account_email" {
  type        = string
  description = "Deployer service account email for IAM bindings."
}

variable "additional_labels" {
  type        = map(string)
  description = "Additional labels to merge onto ephemeral resources."
  default     = {}
}

variable "tenant_id" {
  type        = string
  description = "Release-scoped tenant identifier for staging tenant isolation."
  default     = null

  validation {
    condition     = var.tenant_id == null || can(regex("^[a-z0-9][a-z0-9._-]{1,62}[a-z0-9]$", var.tenant_id))
    error_message = "tenant_id must be a valid tenant identifier (lowercase letters, digits, '.', '_', '-')."
  }
}


