variable "project_id" {
  type        = string
  description = "GCP project id."
}

variable "region" {
  type        = string
  description = "GCP region for regional services."
  default     = "asia-east1"
}

variable "environment" {
  type        = string
  description = "Deployment environment."

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of dev, staging, prod."
  }
}

variable "api_image" {
  type        = string
  description = "Immutable API container image."
}

variable "cloud_sql_tier" {
  type        = string
  description = "Cloud SQL machine tier."
  default     = "db-custom-2-7680"
}

variable "cloud_sql_disk_gb" {
  type        = number
  description = "Cloud SQL disk size in GB."
  default     = 50
}
