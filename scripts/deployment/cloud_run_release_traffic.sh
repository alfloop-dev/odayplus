#!/usr/bin/env bash
#
# Cloud Run traffic helpers for deploy_cloud_run_waji.sh. This file only
# defines functions so rollback behavior can be exercised independently.

ODP_TRAFFIC_HELPER="${ODP_TRAFFIC_HELPER:-scripts/deployment/cloud_run_traffic.py}"
ODP_SCHEDULER_HELPER="${ODP_SCHEDULER_HELPER:-scripts/deployment/cloud_scheduler_trigger.py}"

capture_service_traffic() {
  local service="$1"
  local output="$2"
  gcloud run services describe "${service}" \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --format=json >"${output}"
  python3 "${ODP_TRAFFIC_HELPER}" restore-arg --description="${output}" >/dev/null
  python3 "${ODP_TRAFFIC_HELPER}" service-url --description="${output}" >/dev/null
}

service_snapshot_url() {
  local snapshot="$1"
  python3 "${ODP_TRAFFIC_HELPER}" service-url --description="${snapshot}"
}

tagged_revision() {
  local description="$1"
  local tag="$2"
  python3 "${ODP_TRAFFIC_HELPER}" tagged-revision \
    --description="${description}" \
    --tag="${tag}"
}

tagged_revision_url() {
  local description="$1"
  local tag="$2"
  python3 "${ODP_TRAFFIC_HELPER}" tagged-url \
    --description="${description}" \
    --tag="${tag}"
}

promote_service_traffic() {
  local service="$1"
  local revision="$2"
  echo "Promoting ${service} revision ${revision} to 100% traffic..."
  gcloud run services update-traffic "${service}" \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --to-revisions="${revision}=100" \
    --quiet
}

restore_service_traffic() {
  local service="$1"
  local snapshot="$2"
  local traffic
  traffic="$(python3 "${ODP_TRAFFIC_HELPER}" restore-arg --description="${snapshot}")"
  echo "Restoring ${service} traffic to ${traffic}..." >&2
  gcloud run services update-traffic "${service}" \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --to-revisions="${traffic}" \
    --quiet
}

rollback_release_traffic() {
  local api_service="$1"
  local api_snapshot="$2"
  local web_service="$3"
  local web_snapshot="$4"
  local failed=0

  restore_service_traffic "${api_service}" "${api_snapshot}" || failed=1
  restore_service_traffic "${web_service}" "${web_snapshot}" || failed=1
  return "${failed}"
}

capture_scheduler_trigger() {
  local trigger="$1"
  local output="$2"
  local existing
  existing="$(gcloud scheduler jobs list \
    --location="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --filter="name:${trigger}" \
    --format='value(name)')"
  if [ -n "${existing}" ]; then
    gcloud scheduler jobs describe "${trigger}" \
      --location="${GCP_REGION}" \
      --project="${GCP_PROJECT}" \
      --format=json >"${output}"
    python3 "${ODP_SCHEDULER_HELPER}" validate --description="${output}"
    return
  fi
  python3 "${ODP_SCHEDULER_HELPER}" write-absent --description="${output}"
}

restore_scheduler_trigger() {
  local trigger="$1"
  local snapshot="$2"
  if [ "$(python3 "${ODP_SCHEDULER_HELPER}" exists --description="${snapshot}")" != "true" ]; then
    gcloud scheduler jobs delete "${trigger}" \
      --location="${GCP_REGION}" \
      --project="${GCP_PROJECT}" \
      --quiet >/dev/null 2>&1 || true
    return
  fi

  local schedule
  local time_zone
  local uri
  local service_account
  local scope
  schedule="$(python3 "${ODP_SCHEDULER_HELPER}" field --description="${snapshot}" --field=schedule)"
  time_zone="$(python3 "${ODP_SCHEDULER_HELPER}" field --description="${snapshot}" --field=timeZone)"
  uri="$(python3 "${ODP_SCHEDULER_HELPER}" field --description="${snapshot}" --field=httpTarget.uri)"
  service_account="$(python3 "${ODP_SCHEDULER_HELPER}" field --description="${snapshot}" --field=httpTarget.oauthToken.serviceAccountEmail)"
  scope="$(python3 "${ODP_SCHEDULER_HELPER}" field --description="${snapshot}" --field=httpTarget.oauthToken.scope)"

  gcloud scheduler jobs update http "${trigger}" \
    --location="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --schedule="${schedule}" \
    --time-zone="${time_zone}" \
    --uri="${uri}" \
    --http-method=POST \
    --message-body="{}" \
    --headers="Content-Type=application/json" \
    --oauth-service-account-email="${service_account}" \
    --oauth-token-scope="${scope}" \
    --quiet
}
