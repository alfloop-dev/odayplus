# Ephemeral Staging Module
#
# Creates release-scoped isolation resources for a single ephemeral staging
# deployment. Long-lived infrastructure (Cloud SQL instance, VPC, KMS key,
# Artifact Registry) is referenced by input, not created here.
#
# Each release gets:
#   - Isolated database + user with per-release credentials
#   - Release-scoped GCS bucket prefix for snapshots/artifacts
#   - Dedicated service accounts with minimal IAM
#   - Release-scoped Pub/Sub messaging (jobs + DLQ) with CMEK encryption
#   - Release-scoped Cloud Run services (API + Web)
#   - Cloud Scheduler trigger starting paused
#   - Label-capable resources tagged with owner/created_at/expires_at labels;
#     unsupported child resources tracked by the release ownership manifest
#
# Cleanup relies on precise label matching, never broad wildcards.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
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

locals {
  # Release ID normalized for naming and exact release_hash to guarantee uniqueness and prevent collision.
  # Lowercase before replacing punctuation so Terraform derives the same
  # canonical identity as staging_lifecycle.py.  Replacing uppercase bytes
  # first would make REL_1.0 become el-1-0 instead of rel-1-0 and leave the
  # release label/name cleanup paths unable to address the same resources.
  release_clean  = trim(replace(lower(var.release_id), "/[^a-z0-9-]/", "-"), "-")
  release_hash   = substr(sha256(var.release_id), 0, 8)
  release_prefix = length(local.release_clean) > 0 ? trim(substr(local.release_clean, 0, 54), "-") : "rel"
  release_label  = length(local.release_prefix) > 0 ? "${local.release_prefix}-${local.release_hash}" : "rel-${local.release_hash}"
  owner_clean    = trim(replace(lower(var.owner_task_id), "/[^a-z0-9_-]/", "-"), "-")
  owner_label    = length(local.owner_clean) <= 63 ? local.owner_clean : "${substr(local.owner_clean, 0, 54)}-${substr(sha256(var.owner_task_id), 0, 8)}"

  # Release-scoped tenant isolation.
  # The derived tenant is bounded to 63 characters ("tenant-" + 47 slug + "-" +
  # 8 hash) so it satisfies the tenant_id validation on a rerun and can be used
  # as a label value verbatim. staging_lifecycle.derive_release_tenant_id
  # computes the identical value, which is what the generated tfvars pass back
  # in; a null or empty var.tenant_id must resolve to the same tenant.
  tenant_slug    = length(local.release_clean) > 0 ? local.release_clean : "rel"
  tenant_derived = "tenant-${length(local.tenant_slug) <= 47 ? local.tenant_slug : trim(substr(local.tenant_slug, 0, 47), "-")}-${local.release_hash}"
  tenant_id      = try(length(var.tenant_id) > 0, false) ? var.tenant_id : local.tenant_derived
  tenant_clean   = trim(replace(lower(local.tenant_id), "/[^a-z0-9_-]/", "-"), "-")
  tenant_label   = length(local.tenant_clean) <= 63 ? local.tenant_clean : "${substr(local.tenant_clean, 0, 54)}-${substr(sha256(local.tenant_id), 0, 8)}"

  # Service Account ID max 30 chars: "stg-" (4) + slug (13) + "-" (1) + hash (8) + suffix (3-4) = 29-30 chars.
  sa_slug       = substr(local.release_clean, 0, 13)
  sa_prefix     = "stg-${local.sa_slug}-${local.release_hash}"
  sa_runtime_id = "${local.sa_prefix}-rt"
  sa_web_id     = "${local.sa_prefix}-web"
  sa_worker_id  = "${local.sa_prefix}-wkr"

  # Cloud SQL database name max 63 chars: "stg_" (4) + slug (40) + "_" (1) + hash (8) = 53 chars.
  db_slug_clean = replace(local.release_clean, "-", "_")
  db_slug       = substr(local.db_slug_clean, 0, 40)
  database_name = "stg_${local.db_slug}_${local.release_hash}"

  # Cloud SQL user name max 63 chars: "stg_" (4) + slug (36) + "_" (1) + hash (8) + "_app" (4) = 53 chars.
  db_user_slug  = substr(local.db_slug_clean, 0, 36)
  database_user = "stg_${local.db_user_slug}_${local.release_hash}_app"

  # Bucket name max 63 chars: "stg-" (4) + slug (12) + "-" (1) + hash (8) + "-data-" (6) + project_id (<= 30) = <= 61 chars.
  bucket_slug = substr(local.release_clean, 0, 12)
  bucket_name = "stg-${local.bucket_slug}-${local.release_hash}-data-${var.project_id}"

  # Cloud Run & Secret Manager & Pub/Sub prefix: "stg-" (4) + slug (24) + "-" (1) + hash (8) = 37 chars.
  res_slug    = substr(local.release_clean, 0, 24)
  name_prefix = "stg-${local.res_slug}-${local.release_hash}"

  # Immutable creation and expiration timestamps (ensures applies do not refresh TTL).
  # created_at is deliberately a required input. Terraform cannot safely invent
  # a creation time here: doing so would make a later, otherwise idempotent
  # apply move the cleanup deadline.
  created_at = var.created_at
  expires_at = timeadd(local.created_at, "${var.ttl_hours}h")

  # Caller-supplied labels are only additive. The lifecycle labels below are
  # authoritative and must win over an attempted override.
  resource_labels = merge(
    var.additional_labels,
    {
      app                    = "oday-plus"
      environment            = "staging"
      managed_by             = "terraform"
      ephemeral              = "true"
      release_id             = local.release_label
      tenant                 = local.tenant_label
      owner_task             = local.owner_label
      candidate_sha          = substr(var.candidate_sha, 0, 40)
      manifest_digest_prefix = substr(replace(var.manifest_digest, "sha256:", ""), 0, 16)
      created_at             = formatdate("YYYY-MM-DD-hh-mm-ss", local.created_at)
      expires_at             = formatdate("YYYY-MM-DD-hh-mm-ss", local.expires_at)
    },
  )

  ownership_description = join("; ", [
    "release=${local.release_label}",
    "tenant=${local.tenant_label}",
    "owner=${local.owner_label}",
    "expires=${local.expires_at}",
  ])
}

# Some GCP child resources (Cloud SQL databases/users, Secret Manager
# versions, service accounts, Scheduler jobs and IAM bindings) do not expose
# a labels field in the provider API. Keep their ownership metadata in the
# Terraform state as one release-scoped manifest, while label-capable parent
# resources carry the same labels in GCP. Cleanup must first match a
# label-capable parent and then destroy this exact release state; it must never
# infer ownership from a project-wide wildcard.
resource "terraform_data" "staging_ownership" {
  input = {
    labels = local.resource_labels
    resources = {
      tenant_id            = local.tenant_id
      database             = local.database_name
      database_user        = local.database_user
      bucket               = local.bucket_name
      runtime_service_acct = local.sa_runtime_id
      web_service_acct     = local.sa_web_id
      worker_service_acct  = local.sa_worker_id
      name_prefix          = local.name_prefix
      api_image            = var.api_image
      web_image            = var.web_image
      worker_image         = var.worker_image
      scheduler_image      = var.scheduler_image
    }
  }

  lifecycle {
    precondition {
      condition     = timecmp(var.created_at, timeadd(plantimestamp(), "5m")) <= 0
      error_message = "created_at cannot be in the future (exceeds current plan timestamp + 5 minutes)."
    }
    precondition {
      condition     = timecmp(local.expires_at, timeadd(plantimestamp(), "${var.ttl_hours + 1}h")) <= 0
      error_message = "expires_at exceeds maximum allowed TTL window from the current plan timestamp."
    }
  }

  triggers_replace = [
    var.release_id,
    local.tenant_id,
    var.candidate_sha,
    var.manifest_digest,
    var.api_image,
    var.web_image,
    var.worker_image,
    var.scheduler_image,
    var.created_at,
    local.expires_at,
  ]
}

# --- Isolated Database ---

resource "random_password" "staging_db" {
  length  = 48
  special = false

  keepers = {
    release_id = var.release_id
  }
}

resource "google_sql_database" "staging" {
  project  = var.project_id
  name     = local.database_name
  instance = var.cloud_sql_instance_name

  depends_on = [terraform_data.staging_ownership]
}

resource "google_sql_user" "staging" {
  project  = var.project_id
  name     = local.database_user
  instance = var.cloud_sql_instance_name
  password = random_password.staging_db.result

  depends_on = [terraform_data.staging_ownership]
}

# Staging database URL secret.
resource "google_secret_manager_secret" "staging_database_url" {
  project   = var.project_id
  secret_id = "${local.name_prefix}-database-url"

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  labels = local.resource_labels
}

resource "google_secret_manager_secret_version" "staging_database_url" {
  secret = google_secret_manager_secret.staging_database_url.id
  secret_data = join("", [
    "postgresql://",
    local.database_user,
    ":",
    random_password.staging_db.result,
    "@/",
    local.database_name,
    "?host=/cloudsql/",
    var.cloud_sql_connection_name,
  ])

  depends_on = [
    terraform_data.staging_ownership,
    google_sql_database.staging,
    google_sql_user.staging,
  ]
}

# --- Isolated Bucket Prefix ---
# Use a dedicated bucket per release to guarantee label-precise cleanup.

resource "google_storage_bucket" "staging_data" {
  project                     = var.project_id
  name                        = local.bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  labels                      = local.resource_labels

  versioning {
    enabled = true
  }

  encryption {
    default_kms_key_name = var.kms_key_id
  }

  # Auto-cleanup: objects older than TTL are deleted.
  lifecycle_rule {
    condition {
      age = var.ttl_hours < 24 ? 1 : ceil(var.ttl_hours / 24)
    }
    action {
      type = "Delete"
    }
  }
}

# --- Isolated Service Accounts ---

resource "google_service_account" "staging_runtime" {
  project      = var.project_id
  account_id   = local.sa_runtime_id
  display_name = "Staging ${var.release_id} runtime"
  description  = substr("Ephemeral staging runtime identity, TTL ${var.ttl_hours}h. ${local.ownership_description}", 0, 256)

  depends_on = [terraform_data.staging_ownership]
}

resource "google_service_account" "staging_web" {
  project      = var.project_id
  account_id   = local.sa_web_id
  display_name = "Staging ${var.release_id} web BFF"
  description  = substr("Ephemeral staging web identity, TTL ${var.ttl_hours}h. ${local.ownership_description}", 0, 256)

  depends_on = [terraform_data.staging_ownership]
}

resource "google_service_account" "staging_worker" {
  project      = var.project_id
  account_id   = local.sa_worker_id
  display_name = "Staging ${var.release_id} worker"
  description  = substr("Ephemeral staging worker identity, TTL ${var.ttl_hours}h. ${local.ownership_description}", 0, 256)

  depends_on = [terraform_data.staging_ownership]
}

# --- IAM: Runtime -> DB, Secrets, Bucket ---

resource "google_project_iam_member" "staging_runtime_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.staging_runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "staging_runtime_db_url" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.staging_database_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.staging_runtime.email}"
}

resource "google_storage_bucket_iam_member" "staging_runtime_data" {
  bucket = google_storage_bucket.staging_data.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.staging_runtime.email}"
}

# Deployer can act as the staging service accounts.
resource "google_service_account_iam_member" "deployer_sa_user_runtime" {
  service_account_id = google_service_account.staging_runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.deployer_service_account_email}"
}

resource "google_service_account_iam_member" "deployer_sa_user_web" {
  service_account_id = google_service_account.staging_web.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.deployer_service_account_email}"
}

resource "google_service_account_iam_member" "deployer_sa_user_worker" {
  service_account_id = google_service_account.staging_worker.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.deployer_service_account_email}"
}

# Web -> API invoker.
resource "google_cloud_run_v2_service_iam_member" "staging_web_invokes_api" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.staging_api.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.staging_web.email}"
}

# Worker / Cloud Scheduler -> API invoker.
resource "google_cloud_run_v2_service_iam_member" "staging_worker_invokes_api" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.staging_api.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.staging_worker.email}"
}

# --- Pub/Sub Messaging ---

resource "google_pubsub_topic" "staging_jobs" {
  project      = var.project_id
  name         = "${local.name_prefix}-jobs"
  kms_key_name = var.kms_key_id
  labels       = local.resource_labels

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }
}

resource "google_pubsub_topic" "staging_jobs_dlq" {
  project      = var.project_id
  name         = "${local.name_prefix}-jobs-dlq"
  kms_key_name = var.kms_key_id
  labels       = local.resource_labels

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }
}

resource "google_pubsub_subscription" "staging_jobs" {
  project = var.project_id
  name    = "${local.name_prefix}-jobs"
  topic   = google_pubsub_topic.staging_jobs.id

  ack_deadline_seconds       = 60
  retain_acked_messages      = false
  message_retention_duration = "${var.ttl_hours * 3600}s"

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.staging_jobs_dlq.id
    max_delivery_attempts = 5
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "300s"
  }

  labels = local.resource_labels
}

resource "google_pubsub_subscription" "staging_jobs_dlq" {
  project = var.project_id
  name    = "${local.name_prefix}-jobs-dlq"
  topic   = google_pubsub_topic.staging_jobs_dlq.id

  ack_deadline_seconds       = 60
  retain_acked_messages      = false
  message_retention_duration = "${var.ttl_hours * 3600}s"

  labels = local.resource_labels
}

resource "google_pubsub_topic_iam_member" "staging_runtime_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.staging_jobs.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.staging_runtime.email}"
}

resource "google_pubsub_subscription_iam_member" "staging_worker_subscriber" {
  project      = var.project_id
  subscription = google_pubsub_subscription.staging_jobs.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.staging_worker.email}"
}

# --- Cloud Run Staging Services ---

resource "random_password" "staging_cursor_signing_key" {
  length  = 64
  special = false
}

resource "google_secret_manager_secret" "staging_cursor_signing_key" {
  project   = var.project_id
  secret_id = "${local.name_prefix}-cursor-signing-key"

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  labels = local.resource_labels
}

resource "google_secret_manager_secret_version" "staging_cursor_signing_key" {
  secret      = google_secret_manager_secret.staging_cursor_signing_key.id
  secret_data = random_password.staging_cursor_signing_key.result

  depends_on = [terraform_data.staging_ownership]
}

resource "google_secret_manager_secret_iam_member" "staging_runtime_cursor_key" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.staging_cursor_signing_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.staging_runtime.email}"
}

resource "random_password" "staging_web_session" {
  length  = 64
  special = false
}

resource "google_secret_manager_secret" "staging_web_session" {
  project   = var.project_id
  secret_id = "${local.name_prefix}-web-session"

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  labels = local.resource_labels
}

resource "google_secret_manager_secret_version" "staging_web_session" {
  secret      = google_secret_manager_secret.staging_web_session.id
  secret_data = random_password.staging_web_session.result

  depends_on = [terraform_data.staging_ownership]
}

resource "google_secret_manager_secret_iam_member" "staging_web_session" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.staging_web_session.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.staging_web.email}"
}

resource "google_cloud_run_v2_service" "staging_api" {
  project  = var.project_id
  name     = "${local.name_prefix}-api"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  template {
    service_account       = google_service_account.staging_runtime.email
    execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
    timeout               = "300s"

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    vpc_access {
      network_interfaces {
        network    = var.network_name
        subnetwork = var.subnetwork_name
      }
      egress = "ALL_TRAFFIC"
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [var.cloud_sql_connection_name]
      }
    }

    containers {
      image = var.api_image

      ports {
        name           = "http1"
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      env {
        name  = "APP_ENV"
        value = "staging"
      }
      env {
        name  = "ODAY_ENV"
        value = "staging"
      }
      env {
        name  = "ODAY_LOG_FORMAT"
        value = "json"
      }
      env {
        name  = "ODAY_RELEASE_SHA"
        value = var.candidate_sha
      }
      env {
        name  = "ODP_DEPLOY_ENV"
        value = "staging"
      }
      env {
        name  = "ODP_TENANT_ID"
        value = local.tenant_id
      }
      env {
        name  = "ODP_SCHEDULED_INGESTION_TENANT_ID"
        value = local.tenant_id
      }
      env {
        name  = "ODP_EXTERNAL_PROVIDER_MODE"
        value = "fixture"
      }
      env {
        name  = "ODP_OBJECT_STORE"
        value = "gcs"
      }
      env {
        name  = "ODP_PERSISTENCE"
        value = "postgresql"
      }
      env {
        name  = "ODP_PRODUCT_MODE"
        value = "development"
      }
      env {
        name  = "ODP_REQUIRE_LIVE_DATA"
        value = "false"
      }
      env {
        name  = "ODP_SOURCE_SNAPSHOT_BUCKET"
        value = google_storage_bucket.staging_data.name
      }
      env {
        name  = "ODP_MODEL_ARTIFACT_BUCKET"
        value = google_storage_bucket.staging_data.name
      }
      env {
        name  = "ODP_STAGING_RELEASE_ID"
        value = var.release_id
      }
      env {
        name  = "ODP_JOBS_TOPIC"
        value = google_pubsub_topic.staging_jobs.id
      }
      env {
        name  = "ODP_JOBS_SUBSCRIPTION"
        value = google_pubsub_subscription.staging_jobs.name
      }
      env {
        name  = "ODP_JOBS_DLQ_TOPIC"
        value = google_pubsub_topic.staging_jobs_dlq.id
      }
      env {
        name = "ODAY_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.staging_database_url.secret_id
            version = google_secret_manager_secret_version.staging_database_url.version
          }
        }
      }
      env {
        name = "ODP_INTAKE_CURSOR_SIGNING_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.staging_cursor_signing_key.secret_id
            version = google_secret_manager_secret_version.staging_cursor_signing_key.version
          }
        }
      }

      startup_probe {
        initial_delay_seconds = 5
        timeout_seconds       = 5
        period_seconds        = 10
        failure_threshold     = 30

        http_get {
          path = "/readiness"
          port = 8000
        }
      }

      liveness_probe {
        initial_delay_seconds = 30
        timeout_seconds       = 5
        period_seconds        = 30
        failure_threshold     = 3

        http_get {
          path = "/healthz"
          port = 8000
        }
      }
    }
  }

  labels = local.resource_labels

  depends_on = [
    google_project_iam_member.staging_runtime_cloudsql,
    google_secret_manager_secret_iam_member.staging_runtime_db_url,
    google_secret_manager_secret_iam_member.staging_runtime_cursor_key,
    google_secret_manager_secret_version.staging_database_url,
    google_secret_manager_secret_version.staging_cursor_signing_key,
    google_storage_bucket_iam_member.staging_runtime_data,
  ]
}

resource "google_cloud_run_v2_service" "staging_web" {
  project  = var.project_id
  name     = "${local.name_prefix}-web"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account       = google_service_account.staging_web.email
    execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
    timeout               = "300s"

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    vpc_access {
      network_interfaces {
        network    = var.network_name
        subnetwork = var.subnetwork_name
      }
      egress = "ALL_TRAFFIC"
    }

    containers {
      image = var.web_image

      ports {
        name           = "http1"
        container_port = 3000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      env {
        name  = "ODAY_RELEASE_SHA"
        value = var.candidate_sha
      }
      env {
        name  = "ODP_API_BASE_URL"
        value = google_cloud_run_v2_service.staging_api.uri
      }
      env {
        name  = "ODP_API_SERVICE_AUDIENCE"
        value = google_cloud_run_v2_service.staging_api.uri
      }
      env {
        name  = "ODP_DEPLOY_ENV"
        value = "staging"
      }
      env {
        name  = "ODP_TENANT_ID"
        value = local.tenant_id
      }
      env {
        name  = "ODP_PRODUCT_MODE"
        value = "poc"
      }
      env {
        name  = "ODP_REQUIRE_LIVE_DATA"
        value = "false"
      }
      env {
        name  = "ODP_STAGING_RELEASE_ID"
        value = var.release_id
      }
      env {
        name = "ODP_WEB_SESSION_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.staging_web_session.secret_id
            version = google_secret_manager_secret_version.staging_web_session.version
          }
        }
      }

      startup_probe {
        initial_delay_seconds = 2
        timeout_seconds       = 3
        period_seconds        = 5
        failure_threshold     = 30

        tcp_socket {
          port = 3000
        }
      }

      liveness_probe {
        initial_delay_seconds = 30
        timeout_seconds       = 3
        period_seconds        = 30
        failure_threshold     = 3

        tcp_socket {
          port = 3000
        }
      }
    }
  }

  labels = local.resource_labels

  depends_on = [
    google_cloud_run_v2_service_iam_member.staging_web_invokes_api,
    google_secret_manager_secret_iam_member.staging_web_session,
    google_secret_manager_secret_version.staging_web_session,
  ]
}

# --- Cloud Scheduler Trigger (Starts Paused) ---

resource "google_cloud_scheduler_job" "staging_worker_trigger" {
  project     = var.project_id
  name        = "${local.name_prefix}-worker-trigger"
  description = substr("Release-scoped ephemeral staging worker trigger (starts paused, TTL ${var.ttl_hours}h). ${local.ownership_description}", 0, 256)
  schedule    = "*/15 * * * *"
  time_zone   = "UTC"
  paused      = true
  region      = var.region

  depends_on = [
    terraform_data.staging_ownership,
    google_cloud_run_v2_service_iam_member.staging_worker_invokes_api,
  ]

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.staging_api.uri}/api/v1/jobs/trigger"

    headers = {
      "X-Tenant-Id" = local.tenant_id
    }

    oidc_token {
      service_account_email = google_service_account.staging_worker.email
      audience              = google_cloud_run_v2_service.staging_api.uri
    }
  }
}
