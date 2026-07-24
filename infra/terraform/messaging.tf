resource "google_pubsub_topic" "jobs" {
  name         = "${local.name_prefix}-jobs"
  labels       = local.labels
  kms_key_name = google_kms_crypto_key.runtime.id

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }

  depends_on = [google_kms_crypto_key_iam_member.pubsub]
}

resource "google_pubsub_topic" "dead_letter" {
  name         = "${local.name_prefix}-jobs-dlq"
  labels       = local.labels
  kms_key_name = google_kms_crypto_key.runtime.id

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }

  depends_on = [google_kms_crypto_key_iam_member.pubsub]
}

resource "google_pubsub_subscription" "jobs" {
  name  = "${local.name_prefix}-jobs"
  topic = google_pubsub_topic.jobs.id

  ack_deadline_seconds       = var.pubsub_ack_deadline_seconds
  message_retention_duration = "${var.pubsub_message_retention_seconds}s"
  retain_acked_messages      = false
  enable_message_ordering    = true
  filter                     = "attributes.environment = \"${var.environment}\""

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = var.pubsub_max_delivery_attempts
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  expiration_policy {
    ttl = ""
  }

  depends_on = [
    google_pubsub_topic_iam_member.pubsub_service_agent_dlq_publisher,
  ]
}

resource "google_pubsub_subscription" "dead_letter" {
  name  = "${local.name_prefix}-jobs-dlq"
  topic = google_pubsub_topic.dead_letter.id

  ack_deadline_seconds       = 60
  message_retention_duration = "${var.pubsub_message_retention_seconds}s"
  retain_acked_messages      = false

  expiration_policy {
    ttl = ""
  }
}

resource "google_pubsub_topic_iam_member" "pubsub_service_agent_dlq_publisher" {
  topic  = google_pubsub_topic.dead_letter.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_project_service_identity.pubsub.email}"
}

resource "google_pubsub_subscription_iam_member" "pubsub_service_agent_source_subscriber" {
  subscription = google_pubsub_subscription.jobs.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_project_service_identity.pubsub.email}"
}

resource "google_pubsub_topic_iam_member" "api_job_publisher" {
  topic  = google_pubsub_topic.jobs.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_pubsub_subscription_iam_member" "worker_job_subscriber" {
  subscription = google_pubsub_subscription.jobs.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_pubsub_subscription_iam_member" "worker_dlq_subscriber" {
  subscription = google_pubsub_subscription.dead_letter.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.worker.email}"
}
