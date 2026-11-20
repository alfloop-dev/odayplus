data "google_secret_manager_secret" "external_runtime" {
  for_each = local.external_runtime_secret_refs

  project   = var.project_id
  secret_id = each.value.secret_id

  depends_on = [google_project_service.required]
}

data "google_secret_manager_secret" "web_oidc_client" {
  for_each = local.web_oidc_secret_refs

  project   = var.project_id
  secret_id = each.value.secret_id

  depends_on = [google_project_service.required]
}

data "google_secret_manager_secret" "identity_token_signing_key" {
  for_each = local.identity_token_secret_refs

  project   = var.project_id
  secret_id = each.value.secret_id

  depends_on = [google_project_service.required]
}

resource "google_project_iam_member" "runtime_cloud_sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "runtime_database_url" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.database_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "runtime_cursor_signing_key" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.cursor_signing_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "runtime_external_secrets" {
  for_each = data.google_secret_manager_secret.external_runtime

  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "web_session_secret" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.web_session_secret.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.web.email}"
}

resource "google_project_iam_member" "web_cloud_sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.web.email}"
}

resource "google_secret_manager_secret_iam_member" "web_database_url" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.database_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.web.email}"
}

resource "google_secret_manager_secret_iam_member" "web_oidc_client_secret" {
  for_each = data.google_secret_manager_secret.web_oidc_client

  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.web.email}"
}

resource "google_secret_manager_secret_iam_member" "api_identity_token_signing_key" {
  for_each = data.google_secret_manager_secret.identity_token_signing_key

  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "web_identity_token_signing_key" {
  for_each = data.google_secret_manager_secret.identity_token_signing_key

  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.web.email}"
}

resource "google_storage_bucket_iam_member" "runtime_artifact_objects" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_storage_bucket_iam_member" "runtime_snapshot_objects" {
  bucket = google_storage_bucket.source_snapshots.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

# --- Workload Identity Federation (WIF) & Deployer Identity ---

resource "google_service_account" "github_deployer" {
  account_id   = "github-deployer"
  display_name = "GitHub Actions Deployment Service Account"
  description  = "CI/CD deployment service account for GitHub Actions WIF impersonation."

  depends_on = [google_project_service.required]
}

resource "google_iam_workload_identity_pool" "github_actions" {
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions Pool"
  description               = "Workload Identity Pool for GitHub Actions CI/CD workflows."

  depends_on = [google_project_service.required]
}

resource "google_iam_workload_identity_pool_provider" "odayplus" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github_actions.workload_identity_pool_id
  workload_identity_pool_provider_id = "odayplus"
  display_name                       = "GitHub Actions Provider"
  description                        = "OIDC identity provider for GitHub Actions."

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.actor"      = "assertion.actor"
    "attribute.aud"        = "assertion.aud"
  }

  attribute_condition = "assertion.repository == 'alfloop-dev/odayplus'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_deployer_wif" {
  service_account_id = google_service_account.github_deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github_actions.name}/attribute.repository/alfloop-dev/odayplus"
}

resource "google_service_account" "smoke_operator" {
  account_id   = "oday-dev-smoke-operator"
  display_name = "ODay Dev Smoke Operator Service Account"
  description  = "Short-lived operator identity for post-deploy smoke and live E2E verification."

  depends_on = [google_project_service.required]
}

resource "google_service_account_iam_member" "github_deployer_token_creator_smoke_operator" {
  service_account_id = google_service_account.smoke_operator.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.github_deployer.email}"
}

resource "google_project_iam_member" "github_deployer_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.github_deployer.email}"
}

resource "google_project_iam_member" "github_deployer_cloudscheduler_admin" {
  project = var.project_id
  role    = "roles/cloudscheduler.admin"
  member  = "serviceAccount:${google_service_account.github_deployer.email}"
}

resource "google_project_iam_member" "github_deployer_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.github_deployer.email}"
}

# The deploy script proves sources-off egress by reading the candidate worker
# job's runtime receipt back from Cloud Logging (`gcloud logging read`).
resource "google_project_iam_member" "github_deployer_logging_viewer" {
  project = var.project_id
  role    = "roles/logging.viewer"
  member  = "serviceAccount:${google_service_account.github_deployer.email}"
}

resource "google_artifact_registry_repository_iam_member" "github_deployer_ar_writer" {
  project    = var.project_id
  location   = var.region
  repository = var.artifact_registry_repository
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.github_deployer.email}"
}

resource "google_service_account_iam_member" "github_deployer_sa_user_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.github_deployer.email}"
}

resource "google_service_account_iam_member" "github_deployer_sa_user_web" {
  service_account_id = google_service_account.web.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.github_deployer.email}"
}

resource "google_service_account_iam_member" "github_deployer_sa_user_worker" {
  service_account_id = google_service_account.worker.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.github_deployer.email}"
}

# --- Staging github-deployer grants for the ephemeral staging lifecycle ---
#
# product_ops/deployment/staging_lifecycle.py creates and destroys every
# release through modules/ephemeral_staging as the WIF github-deployer. The
# project-level grants below were made by hand on the live staging project
# (odayplus-runtime-20260825, read back 2026-11-20) and are written back here
# exactly as live; stagingWebLbAttach is the only addition (attach/detach of the
# release Web NEG on the persistent console-staging load balancer).
#
# The staging foundation state is a targeted subset that does not hold
# google_service_account.github_deployer, so these resources name the deployer
# by email and reference no other resource: a targeted apply touches only them.
# The import blocks adopt every binding and role that already exists, so that
# apply creates only stagingWebLbAttach and its binding. Dev and prod plan
# none of this (empty for_each).

locals {
  staging_deployer_enabled = var.environment == "staging"
  staging_deployer_member  = "serviceAccount:github-deployer@${var.project_id}.iam.gserviceaccount.com"

  staging_deployer_custom_roles = local.staging_deployer_enabled ? {
    stagingLifecycleSqlUsers = {
      title       = "Staging lifecycle SQL databases and users"
      description = "ephemeral_staging creates and deletes the release database and user; Cloud SQL database/user APIs ignore instance-name IAM conditions"
      permissions = [
        "cloudsql.databases.create",
        "cloudsql.databases.delete",
        "cloudsql.databases.get",
        "cloudsql.databases.list",
        "cloudsql.databases.update",
        "cloudsql.instances.export",
        "cloudsql.instances.get",
        "cloudsql.instances.import",
        "cloudsql.users.create",
        "cloudsql.users.delete",
        "cloudsql.users.get",
        "cloudsql.users.list",
        "cloudsql.users.update",
      ]
      live = true
    }
    stagingWebLbAttach = {
      title       = "Staging Web load balancer release attach"
      description = "staging_lifecycle.py creates/deletes the release Web serverless NEG, moves it on/off the oday-staging-web-lb backend service and reads back the forwarding rule, HTTPS proxy, SSL policy and managed certificate"
      permissions = [
        "compute.backendServices.get",
        "compute.backendServices.list",
        "compute.backendServices.update",
        "compute.globalForwardingRules.get",
        "compute.globalOperations.get",
        "compute.regionNetworkEndpointGroups.create",
        "compute.regionNetworkEndpointGroups.delete",
        "compute.regionNetworkEndpointGroups.get",
        "compute.regionNetworkEndpointGroups.use",
        "compute.regionOperations.get",
        "compute.sslCertificates.get",
        "compute.sslPolicies.get",
        "compute.targetHttpsProxies.get",
      ]
      live = false
    }
  } : {}

  staging_deployer_predefined_roles = local.staging_deployer_enabled ? toset([
    "roles/compute.networkViewer",
    "roles/iam.serviceAccountAdmin",
    "roles/iam.serviceAccountUser",
    "roles/pubsub.admin",
    "roles/secretmanager.admin",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/storage.admin",
  ]) : toset([])

  # ephemeral_staging grants the release runtime SA roles/cloudsql.client at
  # project level; the deployer may grant nothing else.
  staging_deployer_conditional_iam_admin = local.staging_deployer_enabled ? {
    "staging-lifecycle-cloudsql-client-only" = {
      description = "ephemeral_staging grants only roles/cloudsql.client to the release runtime SA"
      expression  = "api.getAttribute('iam.googleapis.com/modifiedGrantsByRole', []).hasOnly(['roles/cloudsql.client'])"
    }
  } : {}
}

resource "google_project_iam_custom_role" "staging_deployer" {
  for_each = local.staging_deployer_custom_roles

  project     = var.project_id
  role_id     = each.key
  title       = each.value.title
  description = each.value.description
  permissions = each.value.permissions
  stage       = "GA"
}

resource "google_project_iam_member" "staging_deployer_custom" {
  for_each = local.staging_deployer_custom_roles

  project = var.project_id
  role    = google_project_iam_custom_role.staging_deployer[each.key].name
  member  = local.staging_deployer_member
}

resource "google_project_iam_member" "staging_deployer_predefined" {
  for_each = local.staging_deployer_predefined_roles

  project = var.project_id
  role    = each.value
  member  = local.staging_deployer_member
}

resource "google_project_iam_member" "staging_deployer_project_iam_admin" {
  for_each = local.staging_deployer_conditional_iam_admin

  project = var.project_id
  role    = "roles/resourcemanager.projectIamAdmin"
  member  = local.staging_deployer_member

  condition {
    title       = each.key
    description = each.value.description
    expression  = each.value.expression
  }
}

import {
  for_each = { for name, role in local.staging_deployer_custom_roles : name => role if role.live }
  to       = google_project_iam_custom_role.staging_deployer[each.key]
  id       = "projects/${var.project_id}/roles/${each.key}"
}

import {
  for_each = { for name, role in local.staging_deployer_custom_roles : name => role if role.live }
  to       = google_project_iam_member.staging_deployer_custom[each.key]
  id       = "${var.project_id} projects/${var.project_id}/roles/${each.key} ${local.staging_deployer_member}"
}

import {
  for_each = local.staging_deployer_predefined_roles
  to       = google_project_iam_member.staging_deployer_predefined[each.key]
  id       = "${var.project_id} ${each.key} ${local.staging_deployer_member}"
}

import {
  for_each = local.staging_deployer_conditional_iam_admin
  to       = google_project_iam_member.staging_deployer_project_iam_admin[each.key]
  id       = "${var.project_id} roles/resourcemanager.projectIamAdmin ${local.staging_deployer_member} ${each.key}"
}
