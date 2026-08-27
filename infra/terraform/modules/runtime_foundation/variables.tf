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
  description = "GCP region for regional services."
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
  description = "Prefix for resource naming. Defaults to oday-<environment>."
  default     = ""
}

variable "labels" {
  type        = map(string)
  description = "Additional labels applied to managed foundation resources."
  default     = {}
}

variable "network_cidr" {
  type        = string
  description = "Primary Direct VPC egress subnet CIDR within RFC1918 private address space."
  default     = "10.42.0.0/24"

  validation {
    condition = (
      can(cidrhost(var.network_cidr, 1))
      && can(regex("^(10\\.|172\\.(1[6-9]|2[0-9]|3[0-1])\\.|192\\.168\\.)", var.network_cidr))
    )
    error_message = "network_cidr must be a valid RFC1918 private CIDR (10.0.0.0/8, 172.16.0.0/12, or 192.168.0.0/16)."
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

variable "enable_deletion_protection" {
  type        = bool
  description = "Explicit deletion protection override. If null, defaults to true for prod and false for non-prod."
  default     = null
}

variable "network_user_members" {
  type        = list(string)
  description = "List of IAM member principals granted roles/compute.networkUser on the foundation subnet."
  default     = []
}

variable "kms_encrypter_decrypter_members" {
  type        = list(string)
  description = "Additional IAM member principals granted roles/cloudkms.cryptoKeyEncrypterDecrypter on the foundation KMS key."
  default     = []
}
