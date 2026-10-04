variable "environment" {
  type    = string
  default = "dev"
  validation {
    condition     = var.environment == "dev"
    error_message = "This narrowly owned root is dev-only, not staging or production."
  }
}

variable "project_id" {
  type    = string
  default = "odayplus-runtime-20260825"
  validation {
    condition     = var.project_id == "odayplus-runtime-20260825"
    error_message = "Use only the exact read-back dev project."
  }
}

variable "region" {
  type    = string
  default = "asia-east1"
  validation {
    condition     = var.region == "asia-east1"
    error_message = "Use only the exact read-back connector region."
  }
}

variable "network_name" {
  type    = string
  default = "default"
  validation {
    condition     = var.network_name == "default"
    error_message = "Existing dev connector and SQL are on default; staging-runtime is not evidence."
  }
}

variable "connector_name" {
  type    = string
  default = "oday-staging-vpc"
  validation {
    condition     = var.connector_name == "oday-staging-vpc"
    error_message = "Exact existing connector bare name only (despite its historical staging name)."
  }
}

variable "connector_cidr" {
  type    = string
  default = "10.8.0.0/28"
  validation {
    condition     = var.connector_cidr == "10.8.0.0/28"
    error_message = "Exact existing connector CIDR only; no widening or recreation."
  }
}

variable "sql_private_cidr" {
  type    = string
  default = "10.50.0.3/32"
  validation {
    condition     = var.sql_private_cidr == "10.50.0.3/32"
    error_message = "Only reviewed oday-dev-sql private IPv4 /32; drift requires new source review."
  }
}

variable "enable_firewall" {
  description = "Explicit opt-in; does not grant apply authority. All clients of this connector are affected."
  type        = bool
  default     = false
}

variable "enable_shared_dns" {
  description = "Separate opt-in for VPC-wide private googleapis.com/run.app zones. Requires independent impact review."
  type        = bool
  default     = false
}

variable "shared_dns_scope_ack" {
  description = "Exact acknowledged shared scope: default:googleapis.com.,run.app."
  type        = string
  default     = ""
}

variable "shared_dns_review_ref" {
  description = "Actual separately approved authority record ODP-DEV-<task>:<receipt>; never invent a reference."
  type        = string
  default     = ""
}

variable "connector_scope_review_confirmed" {
  description = "Set only after supported connector identity/network/CIDR/READY readback, review of the automatic immutable unique-tag contract and all connector consumers. Not VM/tag readback or enforcement proof."
  type        = bool
  default     = false
}
