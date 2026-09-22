terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.35"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.35"
    }
  }
}

locals {
  is_prod     = var.environment == "prod"
  name_prefix = var.name_prefix != "" ? var.name_prefix : "oday-${var.environment}"
  labels = merge(
    {
      app         = "oday-plus"
      environment = var.environment
      managed_by  = "terraform"
    },
    var.labels,
  )
}
