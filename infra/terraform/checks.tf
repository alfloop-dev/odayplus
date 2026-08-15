resource "terraform_data" "production_contract" {
  input = {
    environment = var.environment
    release_sha = var.release_sha
  }

  lifecycle {
    precondition {
      condition = length(setintersection(
        local.fixed_runtime_env_names,
        toset(keys(var.runtime_additional_env)),
      )) == 0
      error_message = "runtime_additional_env cannot override Terraform-owned production controls."
    }

    precondition {
      condition = length(setintersection(
        local.runtime_plain_env_names,
        local.runtime_secret_env_names_contract,
      )) == 0
      error_message = "An environment variable cannot be configured as both plaintext and Secret Manager-backed."
    }

    precondition {
      condition = length(setintersection(
        local.managed_runtime_secret_env_names,
        local.external_runtime_secret_env_names,
      )) == 0
      error_message = "External secret maps cannot override Terraform-managed database or cursor secrets."
    }

    precondition {
      condition = !local.is_prod || (
        can(regex("@sha256:[0-9a-f]{64}$", var.api_image))
        && startswith(var.api_image, "${var.region}-docker.pkg.dev/${var.project_id}/")
        && can(regex("@sha256:[0-9a-f]{64}$", var.web_image))
        && startswith(var.web_image, "${var.region}-docker.pkg.dev/${var.project_id}/")
      )
      error_message = "Production requires immutable API and Web digests from the selected project's regional Artifact Registry."
    }

    precondition {
      condition = !local.is_prod || (
        var.api_min_instances >= 2
        && var.api_max_instances >= var.api_min_instances
        && var.web_min_instances >= 2
        && var.web_max_instances >= var.web_min_instances
        && var.cloud_sql_disk_gb >= 100
        && var.cloud_sql_retained_backups >= 30
        && can(regex("^db-custom-([4-9]|[1-9][0-9]+)-[0-9]+$", var.cloud_sql_tier))
      )
      error_message = "Production API and Cloud SQL capacity/backup settings are below the required baseline."
    }

    precondition {
      condition = !local.is_prod || (
        startswith(var.oidc_issuer, "https://")
        && startswith(var.oidc_jwks_uri, "https://")
        && length(var.oidc_audiences) > 0
        && length(var.api_invoker_members) > 0
        && !contains(var.api_invoker_members, "allUsers")
        && !contains(var.api_invoker_members, "allAuthenticatedUsers")
        && startswith(var.web_base_url, "https://")
        && length(var.web_oidc_client_id) > 0
        && var.web_oidc_client_secret_ref != null
        && can(regex("^[1-9][0-9]*$", try(var.web_oidc_client_secret_ref.version, "")))
        && length(var.web_invoker_members) > 0
      )
      error_message = "Production requires complete API/Web OIDC configuration, pinned Web client secret, and explicit invokers."
    }

    precondition {
      condition = !local.is_prod || (
        length(setsubtract(
          local.required_provider_endpoint_env_names,
          toset(keys(var.external_provider_endpoints)),
        )) == 0
        && length(setsubtract(
          local.required_provider_secret_env_names,
          toset(keys(var.external_provider_secret_refs)),
        )) == 0
        && alltrue([
          for name in local.required_provider_endpoint_env_names :
          startswith(lookup(var.external_provider_endpoints, name, ""), "https://")
          && !can(regex("@", replace(lookup(var.external_provider_endpoints, name, ""), "https://", "")))
        ])
        && alltrue([
          for ref in values(var.external_provider_secret_refs) :
          can(regex("^[1-9][0-9]*$", ref.version))
        ])
        && alltrue([
          for ref in concat(
            values(var.model_secret_refs),
            values(var.runtime_additional_secret_refs),
          ) :
          can(regex("^[1-9][0-9]*$", ref.version))
        ])
      )
      error_message = "Production live-provider endpoints or pinned Secret Manager bindings are incomplete or unsafe."
    }

    precondition {
      condition = !local.is_prod || (
        startswith(var.mlflow_tracking_uri, "https://")
        && !can(regex(local.forbidden_production_value_pattern, var.mlflow_tracking_uri))
        && length(setsubtract(
          local.required_model_config_env_names,
          toset(keys(var.model_runtime_config)),
        )) == 0
        && can(regex(
          "^(sha256:)?[0-9a-f]{64}$",
          lookup(var.model_runtime_config, "ODP_AVM_LIQUIDITY_ARTIFACT_SHA256", ""),
        ))
        && can(regex(
          "^(gs|https|models|runs):/",
          lookup(var.model_runtime_config, "ODP_AVM_LIQUIDITY_ARTIFACT_URI", ""),
        ))
      )
      error_message = "Production MLflow, model approval, artifact, or OSS engine configuration is incomplete."
    }

    precondition {
      condition = !local.is_prod || alltrue([
        for value in local.production_contract_values :
        length(trimspace(value)) > 0
        && !can(regex(local.forbidden_production_value_pattern, value))
      ])
      error_message = "Production inputs contain empty, mock, fixture, local, placeholder, or otherwise unauthoritative values."
    }

    precondition {
      condition = !local.is_prod || (
        var.artifact_retention_days >= 365
        && var.snapshot_retention_days >= 365
        && var.audit_retention_period_seconds >= 220752000
      )
      error_message = "Production storage retention is below the one-year artifact/snapshot and seven-year audit baselines."
    }
  }
}

check "runtime_environment_names_do_not_collide" {
  assert {
    condition = length(setintersection(
      local.fixed_runtime_env_names,
      toset(keys(var.runtime_additional_env)),
    )) == 0
    error_message = "runtime_additional_env cannot override Terraform-owned production controls."
  }

  assert {
    condition = !local.is_prod || (
      can(regex("@sha256:[0-9a-f]{64}$", var.web_image))
      && startswith(
        var.web_image,
        "${var.region}-docker.pkg.dev/${var.project_id}/",
      )
    )
    error_message = "Production web_image must be an immutable digest from the selected project's regional Artifact Registry."
  }

  assert {
    condition = length(setintersection(
      local.runtime_plain_env_names,
      local.runtime_secret_env_names_contract,
    )) == 0
    error_message = "An environment variable cannot be configured as both plaintext and Secret Manager-backed."
  }

  assert {
    condition = length(setintersection(
      local.managed_runtime_secret_env_names,
      local.external_runtime_secret_env_names,
    )) == 0
    error_message = "External secret maps cannot override Terraform-managed database or cursor secrets."
  }
}

check "production_image_and_capacity" {
  assert {
    condition = !local.is_prod || can(regex(
      "@sha256:[0-9a-f]{64}$",
      var.api_image,
    ))
    error_message = "Production api_image must be pinned by an immutable sha256 digest."
  }

  assert {
    condition = !local.is_prod || startswith(
      var.api_image,
      "${var.region}-docker.pkg.dev/${var.project_id}/",
    )
    error_message = "Production api_image must come from the selected project's regional Artifact Registry."
  }

  assert {
    condition     = !local.is_prod || var.api_min_instances >= 2
    error_message = "Production requires at least two warm API Cloud Run instances."
  }

  assert {
    condition     = !local.is_prod || var.web_min_instances >= 2
    error_message = "Production requires at least two warm Web Cloud Run instances."
  }

  assert {
    condition     = var.api_max_instances >= var.api_min_instances
    error_message = "api_max_instances must be greater than or equal to api_min_instances."
  }

  assert {
    condition     = var.web_max_instances >= var.web_min_instances
    error_message = "web_max_instances must be greater than or equal to web_min_instances."
  }

  assert {
    condition     = !local.is_prod || var.cloud_sql_disk_gb >= 100
    error_message = "Production Cloud SQL requires at least 100 GB of initial SSD capacity."
  }

  assert {
    condition = !local.is_prod || (
      startswith(var.web_base_url, "https://")
      && length(var.web_oidc_client_id) > 0
      && var.web_oidc_client_secret_ref != null
      && can(regex("^[1-9][0-9]*$", try(var.web_oidc_client_secret_ref.version, "")))
      && length(var.web_invoker_members) > 0
    )
    error_message = "Production Web requires HTTPS base URL, OIDC client id, pinned client secret, and at least one invoker."
  }

  assert {
    condition     = !local.is_prod || var.cloud_sql_retained_backups >= 30
    error_message = "Production must retain at least 30 automated Cloud SQL backups."
  }

  assert {
    condition = !local.is_prod || can(regex(
      "^db-custom-([4-9]|[1-9][0-9]+)-[0-9]+$",
      var.cloud_sql_tier,
    ))
    error_message = "Production Cloud SQL requires a custom tier with at least four vCPUs."
  }
}

check "production_identity_contract" {
  assert {
    condition = !local.is_prod || (
      startswith(var.oidc_issuer, "https://")
      && startswith(var.oidc_jwks_uri, "https://")
      && length(var.oidc_audiences) > 0
    )
    error_message = "Production requires an HTTPS OIDC issuer, HTTPS JWKS URI, and at least one audience."
  }

  assert {
    condition = !local.is_prod || (
      length(var.api_invoker_members) > 0
      && !contains(var.api_invoker_members, "allUsers")
      && !contains(var.api_invoker_members, "allAuthenticatedUsers")
    )
    error_message = "Production requires explicit non-public Cloud Run invoker members."
  }
}

check "production_external_provider_contract" {
  assert {
    condition = !local.is_prod || length(setsubtract(
      local.required_provider_endpoint_env_names,
      toset(keys(var.external_provider_endpoints)),
    )) == 0
    error_message = "Production is missing one or more required live-provider endpoint environment variables."
  }

  assert {
    condition = !local.is_prod || alltrue([
      for name in local.required_provider_endpoint_env_names :
      startswith(lookup(var.external_provider_endpoints, name, ""), "https://")
      && !can(regex("@", replace(lookup(var.external_provider_endpoints, name, ""), "https://", "")))
    ])
    error_message = "Every production provider endpoint must be HTTPS and must not embed credentials."
  }

  assert {
    condition = !local.is_prod || length(setsubtract(
      local.required_provider_secret_env_names,
      toset(keys(var.external_provider_secret_refs)),
    )) == 0
    error_message = "Production is missing one or more required provider credential Secret Manager bindings."
  }

  assert {
    condition = !local.is_prod || alltrue([
      for ref in values(var.external_provider_secret_refs) :
      can(regex("^[1-9][0-9]*$", ref.version))
    ])
    error_message = "Production provider secrets must use explicit numeric Secret Manager versions, never latest."
  }
}

check "production_model_runtime_contract" {
  assert {
    condition = !local.is_prod || (
      startswith(var.mlflow_tracking_uri, "https://")
      && !can(regex(local.forbidden_production_value_pattern, var.mlflow_tracking_uri))
    )
    error_message = "Production requires a remote HTTPS MLflow tracking/registry URI."
  }

  assert {
    condition = !local.is_prod || length(setsubtract(
      local.required_model_config_env_names,
      toset(keys(var.model_runtime_config)),
    )) == 0
    error_message = "Production is missing required model approval, artifact, or OSS engine configuration."
  }

  assert {
    condition = !local.is_prod || can(regex(
      "^(sha256:)?[0-9a-f]{64}$",
      lookup(var.model_runtime_config, "ODP_AVM_LIQUIDITY_ARTIFACT_SHA256", ""),
    ))
    error_message = "ODP_AVM_LIQUIDITY_ARTIFACT_SHA256 must be a full SHA-256 digest."
  }

  assert {
    condition = !local.is_prod || can(regex(
      "^(gs|https|models|runs):/",
      lookup(var.model_runtime_config, "ODP_AVM_LIQUIDITY_ARTIFACT_URI", ""),
    ))
    error_message = "Production AVM liquidity artifact must resolve through GCS, HTTPS, or the remote MLflow registry."
  }
}

check "production_forbidden_values" {
  assert {
    condition = !local.is_prod || alltrue([
      for value in local.production_contract_values :
      length(trimspace(value)) > 0
      && !can(regex(local.forbidden_production_value_pattern, value))
    ])
    error_message = "Production inputs cannot be empty or contain mock, fixture, synthetic, seed, memory, SQLite, local, stub, replay, sandbox, development, latest, placeholder, example, or TODO markers."
  }
}
