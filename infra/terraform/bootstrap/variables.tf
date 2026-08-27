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
  description = "GCP region for regional state bucket and KMS."
  default     = "asia-east1"

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]+$", var.region))
    error_message = "region must be a valid GCP region such as asia-east1."
  }
}

variable "environment" {
  type        = string
  description = "Deployment environment (dev, staging, prod)."

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of dev, staging, prod."
  }
}

variable "name_prefix" {
  type        = string
  description = "Optional name prefix for state bucket and KMS. Defaults to oday-tfstate-${var.environment}."
  default     = ""
}

variable "labels" {
  type        = map(string)
  description = "Additional labels applied to state backend resources."
  default     = {}
}

variable "retention_period_days" {
  type        = number
  description = "Governed state retention period in days."
  default     = 30

  validation {
    condition     = var.retention_period_days >= 7 && var.retention_period_days <= 2557
    error_message = "retention_period_days must be between 7 and 2557 days."
  }
}

variable "deployer_member_emails" {
  type        = list(string)
  description = "List of deployer service accounts or identity members granted least-privilege state access."
  default     = []
}
