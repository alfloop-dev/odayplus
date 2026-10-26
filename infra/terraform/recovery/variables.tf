variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "asia-east1"
}

variable "bucket_name" {
  type = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.bucket_name))
    error_message = "bucket_name must be a bare GCS bucket name, not a URI or prefix."
  }
}

variable "state_bucket_name" {
  type = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.state_bucket_name))
    error_message = "state_bucket_name must be the bare verified backend bucket name."
  }
}

variable "protected_bucket_names" {
  type        = set(string)
  description = "Existing snapshot, model, MLflow and lease buckets from the live inventory; these cannot be repurposed."

  validation {
    condition     = length(var.protected_bucket_names) > 0 && alltrue([for name in var.protected_bucket_names : can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", name))])
    error_message = "Supply the non-empty protected inventory as bare bucket names."
  }
}

variable "kms_key_id" {
  type = string

  validation {
    condition     = can(regex("^projects/[^/]+/locations/[^/]+/keyRings/[^/]+/cryptoKeys/[^/]+$", var.kms_key_id))
    error_message = "kms_key_id must identify an existing regional CryptoKey."
  }
}

variable "deployer_service_account" {
  type = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]+@[^/ ]+\\.iam\\.gserviceaccount\\.com$", var.deployer_service_account))
    error_message = "deployer_service_account must be a service account email, not a user or public principal."
  }
}
