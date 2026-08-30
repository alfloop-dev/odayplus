terraform {
  required_version = ">= 1.6.0"

  backend "gcs" {}

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.35"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.35"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

locals {
  is_prod      = var.environment == "prod"
  oidc_enabled = var.auth_mode == "oidc"
  name_prefix  = "oday-${var.environment}"

  # ODP_AUTH_ISSUER / ODP_AUTH_JWKS_URI / ODP_AUTH_AUDIENCES verify two kinds of
  # token: end-user OIDC tokens and the Cloud Run service-identity token the
  # deploy smoke stage mints. Only the first is optional. The API verifier is
  # still single-issuer, so these keep resolving to the OIDC issuer while OIDC
  # is on and fall back to the service-identity issuer when it is off; the
  # service audience list is carried in both modes.
  auth_issuer            = local.oidc_enabled ? var.oidc_issuer : var.service_auth_issuer
  auth_jwks_uri          = local.oidc_enabled ? var.oidc_jwks_uri : var.service_auth_jwks_uri
  service_auth_audiences = length(var.service_auth_audiences) > 0 ? var.service_auth_audiences : var.oidc_audiences
  auth_audiences = sort(tolist(setunion(
    local.service_auth_audiences,
    local.oidc_enabled ? var.oidc_audiences : toset([]),
  )))
  labels = merge(
    {
      app         = "oday-plus"
      environment = var.environment
      managed_by  = "terraform"
    },
    var.labels,
  )

  required_apis = toset([
    "cloudkms.googleapis.com",
    "compute.googleapis.com",
    "iam.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
  ])

  required_model_config_env_names = toset([
    "ODP_AVM_LIQUIDITY_APPROVED_AT",
    "ODP_AVM_LIQUIDITY_APPROVED_BY",
    "ODP_AVM_LIQUIDITY_ARTIFACT_SHA256",
    "ODP_AVM_LIQUIDITY_ARTIFACT_URI",
    "ODP_AVM_LIQUIDITY_DATASET_SNAPSHOT_ID",
    "ODP_AVM_LIQUIDITY_MODEL_VERSION",
    "ODP_AVM_MODEL_NAME",
    "ODP_FORECAST_ENGINE",
    "ODP_FORECAST_MODEL",
  ])

  fixed_runtime_env_names = toset([
    "APP_ENV",
    "MLFLOW_TRACKING_URI",
    "ODAY_ENV",
    "ODAY_LOG_FORMAT",
    "ODAY_RELEASE_SHA",
    "ODP_AUDIT_WORM_SINK_URI",
    "ODP_AUTH_AUDIENCES",
    "ODP_AUTH_ISSUER",
    "ODP_AUTH_JWKS_CACHE_TTL_SECONDS",
    "ODP_AUTH_JWKS_URI",
    "ODP_AUTH_LEEWAY_SECONDS",
    "ODP_DEPLOY_ENV",
    "ODP_EXTERNAL_PROVIDER_MODE",
    "ODP_JOBS_DLQ_TOPIC",
    "ODP_JOBS_SUBSCRIPTION",
    "ODP_JOBS_TOPIC",
    "ODP_MODEL_ARTIFACT_BUCKET",
    "ODP_OBJECT_STORE",
    "ODP_PERSISTENCE",
    "ODP_PRODUCT_MODE",
    "ODP_REQUIRE_LIVE_DATA",
    "ODP_RESIDENCY_APPROVED_BUCKETS",
    "ODP_SOURCE_SNAPSHOT_BUCKET",
  ])
  managed_runtime_secret_env_names = toset([
    "ODAY_DATABASE_URL",
    "ODP_INTAKE_CURSOR_SIGNING_KEY",
  ])
  managed_web_secret_env_names = toset([
    "ODP_WEB_SESSION_SECRET",
  ])
  external_runtime_secret_env_names = setunion(
    toset(keys(var.model_secret_refs)),
    toset(keys(var.runtime_additional_secret_refs)),
  )
  runtime_plain_env_names = setunion(
    local.fixed_runtime_env_names,
    toset(keys(var.model_runtime_config)),
    toset(keys(var.runtime_additional_env)),
  )
  runtime_secret_env_names_contract = setunion(
    local.managed_runtime_secret_env_names,
    local.external_runtime_secret_env_names,
  )
  web_plain_env = merge(
    {
      ODAY_RELEASE_SHA                     = var.release_sha
      ODP_API_BASE_URL                     = google_cloud_run_v2_service.api.uri
      ODP_API_SERVICE_AUDIENCE             = google_cloud_run_v2_service.api.uri
      ODP_AUTH_MODE                        = var.auth_mode
      ODP_AUTH_OIDC_ENABLED                = tostring(local.oidc_enabled)
      ODP_DATA_BINDING_MODE                = "live"
      ODP_DEPLOY_ENV                       = var.environment
      ODP_PRODUCT_MODE                     = (local.is_prod || var.live_data_enabled) ? "production" : "poc"
      ODP_REQUIRE_LIVE_DATA                = tostring(local.is_prod || var.live_data_enabled)
      ODP_WEB_ALLOW_LEGACY_TRUSTED_HEADERS = "false"
      ODP_WEB_BASE_URL                     = var.web_base_url
    },
    local.oidc_enabled ? {
      ODP_WEB_OIDC_CLIENT_ID = var.web_oidc_client_id
      ODP_WEB_OIDC_ISSUER    = var.oidc_issuer
      ODP_WEB_OIDC_SCOPES    = var.web_oidc_scopes
    } : {},
  )
  web_oidc_secret_refs = local.oidc_enabled && var.web_oidc_client_secret_ref != null ? {
    ODP_WEB_OIDC_CLIENT_SECRET = var.web_oidc_client_secret_ref
  } : {}

  fixed_runtime_env = {
    APP_ENV                         = var.environment
    MLFLOW_TRACKING_URI             = var.mlflow_tracking_uri
    ODAY_ENV                        = var.environment
    ODAY_LOG_FORMAT                 = "json"
    ODAY_RELEASE_SHA                = var.release_sha
    ODP_AUTH_LEEWAY_SECONDS         = tostring(var.oidc_leeway_seconds)
    ODP_DEPLOY_ENV                  = var.environment
    ODP_EXTERNAL_PROVIDER_MODE      = "fixture"
    ODP_OBJECT_STORE                = "gcs"
    ODP_PERSISTENCE                 = "postgresql"
    ODP_PRODUCT_MODE                = (local.is_prod || var.live_data_enabled) ? "live" : "development"
    ODP_REQUIRE_LIVE_DATA           = tostring(local.is_prod || var.live_data_enabled)
    ODP_RESIDENCY_APPROVED_BUCKETS  = join(",", [google_storage_bucket.source_snapshots.name, google_storage_bucket.artifacts.name, module.audit_evidence.bucket_name])
    ODP_AUDIT_WORM_SINK_URI         = module.audit_evidence.worm_sink_uri
    ODP_SOURCE_SNAPSHOT_BUCKET      = google_storage_bucket.source_snapshots.name
    ODP_MODEL_ARTIFACT_BUCKET       = google_storage_bucket.artifacts.name
    ODP_JOBS_TOPIC                  = google_pubsub_topic.jobs.id
    ODP_JOBS_SUBSCRIPTION           = google_pubsub_subscription.jobs.name
    ODP_JOBS_DLQ_TOPIC              = google_pubsub_topic.dead_letter.id
    ODP_AUTH_AUDIENCES              = join(",", local.auth_audiences)
    ODP_AUTH_ISSUER                 = local.auth_issuer
    ODP_AUTH_JWKS_CACHE_TTL_SECONDS = tostring(var.oidc_jwks_cache_ttl_seconds)
    ODP_AUTH_JWKS_URI               = local.auth_jwks_uri
  }

  runtime_plain_env = merge(
    local.fixed_runtime_env,
    var.model_runtime_config,
    var.runtime_additional_env,
  )

  managed_runtime_secret_refs = {
    ODAY_DATABASE_URL = {
      secret_id = google_secret_manager_secret.database_url.secret_id
      version   = google_secret_manager_secret_version.database_url.version
    }
    ODP_INTAKE_CURSOR_SIGNING_KEY = {
      secret_id = google_secret_manager_secret.cursor_signing_key.secret_id
      version   = google_secret_manager_secret_version.cursor_signing_key.version
    }
  }
  external_runtime_secret_refs = merge(
    var.model_secret_refs,
    var.runtime_additional_secret_refs,
  )
  forbidden_production_value_pattern = "(?i)(^|[-_./:])(mock|fixture|synthetic|seed|memory|sqlite|local|localhost|stub|replay|sandbox|development|latest|placeholder|changeme|change-me|todo|example)([-_./:]|$)"
  production_contract_values = concat(
    [
      var.project_id,
      var.api_image,
      var.web_image,
      var.release_sha,
      var.database_name,
      var.database_user,
      var.mlflow_tracking_uri,
      var.web_base_url,
      local.auth_issuer,
      local.auth_jwks_uri,
    ],
    local.auth_audiences,
    local.oidc_enabled ? [
      var.web_oidc_client_id,
      var.web_oidc_scopes,
    ] : [],
    tolist(var.api_invoker_members),
    tolist(var.web_invoker_members),
    values(var.model_runtime_config),
    flatten([
      for ref in values(var.model_secret_refs) :
      [ref.secret_id, ref.version]
    ]),
    values(var.runtime_additional_env),
    flatten([
      for ref in values(var.runtime_additional_secret_refs) :
      [ref.secret_id, ref.version]
    ]),
    (local.oidc_enabled && var.web_oidc_client_secret_ref != null) ? [
      var.web_oidc_client_secret_ref.secret_id,
      var.web_oidc_client_secret_ref.version,
    ] : [],
  )
}

resource "google_project_service" "required" {
  for_each = local.required_apis

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false

  depends_on = [terraform_data.production_contract]
}

resource "google_service_account" "runtime" {
  account_id   = "${local.name_prefix}-runtime"
  display_name = "ODay Plus ${var.environment} API runtime"
  description  = "Runtime-only identity for ODay Plus API; no project-wide editor grants."

  depends_on = [google_project_service.required]
}

resource "google_service_account" "web" {
  account_id   = "${local.name_prefix}-web"
  display_name = "ODay Plus ${var.environment} Web BFF"
  description  = "Public web identity that invokes the private API with a separate service token."

  depends_on = [google_project_service.required]
}

resource "google_service_account" "worker" {
  account_id   = "${local.name_prefix}-worker"
  display_name = "ODay Plus ${var.environment} asynchronous worker"
  description  = "Subscriber identity for ODay Plus asynchronous jobs."

  depends_on = [google_project_service.required]
}
