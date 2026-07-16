variable "project_id" {
  type        = string
  description = "GCP project id."
}

variable "region" {
  type        = string
  description = "GCP region used for the audit evidence bucket."
}

variable "environment" {
  type        = string
  description = "Deployment environment."
}

variable "labels" {
  type        = map(string)
  description = "Labels applied to audit evidence resources."
  default     = {}
}

variable "product_runtime_service_account_email" {
  type        = string
  description = "Runtime service account allowed to impersonate the append-only audit writer."
}

variable "retention_period_seconds" {
  type        = number
  description = "WORM retention period for audit evidence objects."
  default     = 220924800 # 2557 days, approximately seven years.
}
