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
  local failed=0

  echo "Restoring Cloud Scheduler trigger '${trigger}'..." >&2

  if [ "$(python3 "${ODP_SCHEDULER_HELPER}" exists --description="${snapshot}")" != "true" ]; then
    echo "Trigger '${trigger}' was absent prior to deploy; deleting candidate if present..." >&2
    gcloud scheduler jobs delete "${trigger}" \
      --location="${GCP_REGION}" \
      --project="${GCP_PROJECT}" \
      --quiet >/dev/null 2>&1 || true
    return 0
  fi

  local action="create"
  if gcloud scheduler jobs describe "${trigger}" \
    --location="${GCP_REGION}" \
    --project="${GCP_PROJECT}" >/dev/null 2>&1; then
    action="update"
  fi

  local gcloud_args=()
  mapfile -d '' gcloud_args < <(python3 "${ODP_SCHEDULER_HELPER}" restore-args \
    --description="${snapshot}" \
    --location="${GCP_REGION}" \
    --project="${GCP_PROJECT}") || failed=1

  if [ "${failed}" -ne 0 ] || [ "${#gcloud_args[@]}" -eq 0 ]; then
    echo "Error: failed to generate restore arguments for trigger '${trigger}'." >&2
    return 1
  fi

  if ! gcloud scheduler jobs "${action}" http "${trigger}" "${gcloud_args[@]}" --quiet; then
    echo "Error: failed to ${action} Cloud Scheduler trigger '${trigger}'." >&2
    return 1
  fi

  local desired_state
  desired_state="$(python3 "${ODP_SCHEDULER_HELPER}" field --description="${snapshot}" --field=state 2>/dev/null || echo "ENABLED")"
  if [ "${desired_state}" = "PAUSED" ]; then
    echo "Pausing trigger '${trigger}' to match recorded pre-deploy state..." >&2
    if ! gcloud scheduler jobs pause "${trigger}" \
      --location="${GCP_REGION}" \
      --project="${GCP_PROJECT}" \
      --quiet; then
      echo "Error: failed to pause Cloud Scheduler trigger '${trigger}'." >&2
      return 1
    fi
  elif [ "${desired_state}" = "ENABLED" ]; then
    gcloud scheduler jobs resume "${trigger}" \
      --location="${GCP_REGION}" \
      --project="${GCP_PROJECT}" \
      --quiet >/dev/null 2>&1 || true
  fi

  local readback_file
  readback_file="$(mktemp)"
  if gcloud scheduler jobs describe "${trigger}" \
    --location="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --format=json >"${readback_file}" 2>/dev/null \
    && [ -s "${readback_file}" ] \
    && python3 "${ODP_SCHEDULER_HELPER}" validate --description="${readback_file}" >/dev/null 2>&1; then
    if ! python3 "${ODP_SCHEDULER_HELPER}" compare --before="${snapshot}" --after="${readback_file}"; then
      echo "Warning: trigger '${trigger}' readback configuration drift detected." >&2
      rm -f "${readback_file}"
      return 1
    fi
  fi
  rm -f "${readback_file}"


  echo "Cloud Scheduler trigger '${trigger}' successfully restored." >&2
  return 0
}

