mock_provider "google" {}

variables {
  project_id               = "odayplus-runtime-20260825"
  bucket_name              = "oday-staging-recovery-odayplus-runtime-20260825"
  state_bucket_name        = "oday-tfstate-staging-odayplus-runtime-20260825"
  protected_bucket_names   = ["oday-staging-source-snapshots-odayplus-runtime-20260825", "odayplus-runtime-20260825-model-artifacts", "odayplus-runtime-20260825-release-leases"]
  kms_key_id               = "projects/odayplus-runtime-20260825/locations/asia-east1/keyRings/oday-staging-runtime/cryptoKeys/oday-staging-runtime"
  deployer_service_account = "github-deployer@odayplus-runtime-20260825.iam.gserviceaccount.com"
}

run "protected_recovery_plan" {
  command = plan

  assert {
    condition     = google_storage_bucket.recovery.force_destroy == false && google_storage_bucket.recovery.uniform_bucket_level_access && google_storage_bucket.recovery.public_access_prevention == "enforced"
    error_message = "Recovery bucket must retain public access and deletion protections."
  }
  assert {
    condition     = google_storage_bucket.recovery.retention_policy[0].retention_period == 2592000 && google_storage_bucket.recovery.versioning[0].enabled
    error_message = "Recovery storage requires 30-day retention and versioning."
  }
  assert {
    condition     = google_storage_bucket.recovery.encryption[0].default_kms_key_name == var.kms_key_id && google_storage_bucket_iam_member.deployer.role == "roles/storage.objectUser"
    error_message = "Use the existing CMEK and an additive least-privilege deployer grant."
  }
}

run "reject_state_destination" {
  command = plan
  variables {
    bucket_name = "oday-tfstate-staging-odayplus-runtime-20260825"
  }
  expect_failures = [google_storage_bucket.recovery]
}

run "reject_snapshot_destination" {
  command = plan
  variables {
    bucket_name = "oday-staging-source-snapshots-odayplus-runtime-20260825"
  }
  expect_failures = [google_storage_bucket.recovery]
}

run "reject_model_destination" {
  command = plan
  variables {
    bucket_name = "odayplus-runtime-20260825-model-artifacts"
  }
  expect_failures = [google_storage_bucket.recovery]
}

run "reject_lease_destination" {
  command = plan
  variables {
    bucket_name = "odayplus-runtime-20260825-release-leases"
  }
  expect_failures = [google_storage_bucket.recovery]
}

run "reject_wrong_key_project" {
  command = plan
  variables {
    kms_key_id = "projects/wrong-project/locations/asia-east1/keyRings/runtime/cryptoKeys/runtime"
  }
  expect_failures = [google_storage_bucket.recovery]
}

run "reject_wrong_deployer_project" {
  command = plan
  variables {
    deployer_service_account = "deployer@wrong-project.iam.gserviceaccount.com"
  }
  expect_failures = [google_storage_bucket.recovery]
}

run "reject_uri_alias" {
  command = plan
  variables {
    bucket_name = "gs://oday-tfstate-staging-odayplus-runtime-20260825"
  }
  expect_failures = [var.bucket_name]
}
