#!/usr/bin/env bash
#
# Cloud Run traffic helpers for deploy_cloud_run_waji.sh. This file only
# defines functions so rollback behavior can be exercised independently.

ODP_TRAFFIC_HELPER="${ODP_TRAFFIC_HELPER:-product_ops/deployment/cloud_run_traffic.py}"
ODP_SCHEDULER_HELPER="${ODP_SCHEDULER_HELPER:-product_ops/deployment/cloud_scheduler_trigger.py}"
ODP_JOB_VALIDATOR="${ODP_JOB_VALIDATOR:-product_ops/deployment/validate_cloud_run_live_deployment.py}"

resolve_image_digest() {
  local image="$1" digest
  if [[ "${image}" =~ ^[^[:space:]]+@sha256:[0-9a-f]{64}$ ]]; then
    printf '%s' "${image}"
    return 0
  fi
  digest="$(gcloud artifacts docker images describe "${image}" \
    --format='value(image_summary.digest)' 2>/dev/null | tr -d '\r\n')" || return 1
  if [[ ! "${digest}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    return 1
  fi
  printf '%s@%s' "${image%:*}" "${digest}"
}

capture_service_traffic() {
  local service="$1"
  local output="$2"
  local existing
  existing="$(gcloud run services list \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --filter="metadata.name=${service}" \
    --format='value(metadata.name)')" || return 1
  if [ -z "${existing}" ]; then
    python3 "${ODP_TRAFFIC_HELPER}" write-absent \
      --description="${output}" \
      --service="${service}"
    return
  fi
  if [ "${existing}" != "${service}" ]; then
    echo "Error: Cloud Run service lookup for '${service}' was ambiguous: ${existing}" >&2
    return 1
  fi
  gcloud run services describe "${service}" \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --format=json >"${output}"
  python3 "${ODP_TRAFFIC_HELPER}" restore-arg --description="${output}" >/dev/null || return 1
  python3 "${ODP_TRAFFIC_HELPER}" service-url --description="${output}" >/dev/null
}

# ODP-DEPLOY-FIRST-RELEASE-NO-TRAFFIC-FIX-001: `gcloud run deploy --no-traffic`
# is rejected when it would create the service, so the candidate deploy has to
# know whether the service exists right now. That is read from `gcloud run
# services describe` at deploy time -- not from the pre-release snapshot or the
# recovery mode an operator expected. Prints `present` or `absent`; only the
# explicit not-found answer means absent. Any other describe failure
# (permission, API, network) returns non-zero so the release fails closed
# rather than creating a service over one it could not read.
cloud_run_service_presence() {
  local service="$1"
  local stderr_file name
  stderr_file="$(mktemp)"
  if name="$(gcloud run services describe "${service}" \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --format='value(metadata.name)' 2>"${stderr_file}")"; then
    rm -f "${stderr_file}"
    if [ "${name}" != "${service}" ]; then
      echo "Error: Cloud Run describe for '${service}' returned '${name}'; presence is unknown." >&2
      return 1
    fi
    printf 'present'
    return 0
  fi
  if grep -qF "Cannot find service [${service}]" "${stderr_file}"; then
    rm -f "${stderr_file}"
    printf 'absent'
    return 0
  fi
  cat "${stderr_file}" >&2
  rm -f "${stderr_file}"
  echo "Error: cannot determine whether Cloud Run service '${service}' exists; refusing to deploy." >&2
  return 1
}

# A first release cannot hide its Web candidate behind --no-traffic: the first
# revision of a new service takes 100% of it. So a new Web service is created
# without public invocation, the smoke reaches it through Cloud Run IAM with a
# short-lived identity token sent as X-Serverless-Authorization (the
# application still sees an anonymous browser request), and allUsers is only
# granted at promotion. A failed verification deletes the service before that.
grant_service_invoker() {
  local service="$1"
  local member="$2"
  gcloud run services add-iam-policy-binding "${service}" \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --member="${member}" \
    --role="roles/run.invoker" \
    --quiet >/dev/null
}

# Proves the candidate refuses anonymous callers, then waits (bounded) for the
# fresh invoker binding to admit the smoke identity. IAM bindings propagate
# asynchronously, so without this the smoke can fail on a 403 that is only
# propagation lag.
wait_for_private_candidate_invoker() {
  local url="$1"
  local token="$2"
  local attempts="${ODP_CANDIDATE_INVOKER_WAIT_ATTEMPTS:-24}"
  local delay="${ODP_CANDIDATE_INVOKER_WAIT_SECONDS:-5}"
  local header_file status attempt
  status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "${url}/operator" || true)"
  if [ "${status}" != "401" ] && [ "${status}" != "403" ]; then
    echo "Error: first-release Web candidate answered an anonymous request with HTTP ${status}; it must be private until promotion." >&2
    return 1
  fi
  header_file="$(mktemp)"
  chmod 600 "${header_file}"
  printf 'X-Serverless-Authorization: Bearer %s\n' "${token}" >"${header_file}"
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 \
      -H "@${header_file}" "${url}/operator" || true)"
    case "${status}" in
      000 | 401 | 403) ;;
      *)
        rm -f "${header_file}"
        return 0
        ;;
    esac
    if [ "${attempt}" -lt "${attempts}" ]; then
      sleep "${delay}"
    fi
  done
  rm -f "${header_file}"
  echo "Error: smoke identity was not admitted to the private Web candidate (last HTTP ${status})." >&2
  return 1
}

# ODP-DEV-PRIVATE-API-TRANSPORT-AUTH-001: the API service is never public.
# Every caller reaches it through Cloud Run IAM: the Web BFF as the runtime
# identity, and the release's own probes (migration compatibility, candidate
# smoke, live E2E) as the smoke identity. Both get a run.invoker binding scoped
# to this one service -- never a public principal or a project-level role.
# The probes carry a short-lived ID token in X-Serverless-Authorization, which
# Cloud Run checks and strips, so Authorization stays free for the
# application's own bearer token and an anonymous app request stays anonymous.
# The audience is always the API's stable service URL: Cloud Run accepts that
# audience on revision tag URLs too, and it is never the Web service's URL.
grant_api_service_invokers() {
  local service="$1"
  grant_service_invoker "${service}" "serviceAccount:${ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT}" \
    || return 1
  grant_service_invoker "${service}" "serviceAccount:${ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT}"
}

# Mints (or refreshes) ODP_API_INVOKER_TOKEN for the API's stable service URL
# and exports it to the probes. The value is masked in GitHub Actions and is
# never printed or written to a report.
mint_api_invoker_token() {
  local audience="$1"
  if [[ -z "${audience}" ]]; then
    echo "Error: the API service has no stable URL to use as the transport token audience." >&2
    return 1
  fi
  ODP_API_INVOKER_TOKEN="$(gcloud auth print-identity-token \
    --impersonate-service-account="${ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT}" \
    --audiences="${audience}" \
    --include-email)" || return 1
  if [[ -z "${ODP_API_INVOKER_TOKEN}" ]]; then
    echo "Error: failed to mint the private API transport identity token." >&2
    return 1
  fi
  if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
    echo "::add-mask::${ODP_API_INVOKER_TOKEN}"
  fi
  export ODP_API_INVOKER_TOKEN
}

# Proves the API refuses anonymous callers at the Cloud Run front end, then
# waits (bounded) for the service-scoped invoker binding to admit the smoke
# identity, so a probe never fails on a 403 that is only IAM propagation lag.
wait_for_private_api_invoker() {
  local url="$1"
  local token="$2"
  local attempts="${ODP_API_INVOKER_WAIT_ATTEMPTS:-${ODP_CANDIDATE_INVOKER_WAIT_ATTEMPTS:-24}}"
  local delay="${ODP_API_INVOKER_WAIT_SECONDS:-${ODP_CANDIDATE_INVOKER_WAIT_SECONDS:-5}}"
  local header_file status attempt
  status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "${url}/platform/version" || true)"
  if [ "${status}" != "401" ] && [ "${status}" != "403" ]; then
    echo "Error: the API service answered an anonymous request with HTTP ${status}; it must stay private." >&2
    return 1
  fi
  header_file="$(mktemp)"
  chmod 600 "${header_file}"
  printf 'X-Serverless-Authorization: Bearer %s\n' "${token}" >"${header_file}"
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 \
      -H "@${header_file}" "${url}/platform/version" || true)"
    case "${status}" in
      000 | 401 | 403) ;;
      *)
        rm -f "${header_file}"
        return 0
        ;;
    esac
    if [ "${attempt}" -lt "${attempts}" ]; then
      sleep "${delay}"
    fi
  done
  rm -f "${header_file}"
  echo "Error: smoke identity was not admitted to the private API service (last HTTP ${status})." >&2
  return 1
}

prepare_private_api_transport() {
  local service="$1"
  local audience="$2"
  grant_api_service_invokers "${service}" || return 1
  mint_api_invoker_token "${audience}" || return 1
  wait_for_private_api_invoker "${audience}" "${ODP_API_INVOKER_TOKEN}"
}

# Waits (bounded) for the fresh allUsers invoker binding on a first-release Web
# service to propagate to anonymous callers before live E2E runs. IAM bindings
# propagate asynchronously, so without this the anonymous /operator check in
# check_live_e2e_gate can fail on a 403.
wait_for_public_service_invoker() {
  local url="$1"
  local attempts="${ODP_PUBLIC_INVOKER_WAIT_ATTEMPTS:-${ODP_CANDIDATE_INVOKER_WAIT_ATTEMPTS:-24}}"
  local delay="${ODP_PUBLIC_INVOKER_WAIT_SECONDS:-${ODP_CANDIDATE_INVOKER_WAIT_SECONDS:-5}}"
  local status attempt
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "${url}/operator" || true)"
    case "${status}" in
      000 | 401 | 403) ;;
      *)
        return 0
        ;;
    esac
    if [ "${attempt}" -lt "${attempts}" ]; then
      sleep "${delay}"
    fi
  done
  echo "Error: public invocation was not admitted on the promoted Web service (last HTTP ${status})." >&2
  return 1
}

service_snapshot_url() {
  local snapshot="$1"
  if [ "$(python3 "${ODP_TRAFFIC_HELPER}" exists --description="${snapshot}")" != "true" ]; then
    printf ''
    return
  fi
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
    --quiet || return 1
  verify_service_traffic "${service}" "${revision}=100"
}

verify_service_traffic() {
  local service="$1" expected="$2" readback actual
  readback="$(mktemp)"
  if ! gcloud run services describe "${service}" --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" --format=json >"${readback}"; then
    rm -f "${readback}"
    return 1
  fi
  actual="$(python3 "${ODP_TRAFFIC_HELPER}" restore-arg --description="${readback}")" || { rm -f "${readback}"; return 1; }
  rm -f "${readback}"
  [ "${actual}" = "${expected}" ] || { echo "Error: traffic readback drift for ${service}." >&2; return 1; }
}

restore_service_traffic() {
  local service="$1"
  local snapshot="$2"
  local exists
  exists="$(python3 "${ODP_TRAFFIC_HELPER}" exists --description="${snapshot}")" || return 1
  if [ "${exists}" != "true" ]; then
    local existing
    existing="$(gcloud run services list \
      --region="${GCP_REGION}" \
      --project="${GCP_PROJECT}" \
      --filter="metadata.name=${service}" \
      --format='value(metadata.name)')" || return 1
    if [ -z "${existing}" ]; then
      return 0
    fi
    if [ "${existing}" != "${service}" ]; then
      echo "Error: Cloud Run rollback lookup for '${service}' was ambiguous: ${existing}" >&2
      return 1
    fi
    echo "Deleting bootstrap candidate service ${service}; it did not exist before this release..." >&2
    gcloud run services delete "${service}" \
      --region="${GCP_REGION}" \
      --project="${GCP_PROJECT}" \
      --quiet || return 1
    existing="$(gcloud run services list --region="${GCP_REGION}" --project="${GCP_PROJECT}" \
      --filter="metadata.name=${service}" --format='value(metadata.name)')" || return 1
    if [ -n "${existing}" ]; then
      echo "Error: deleted bootstrap service '${service}' is still present in readback." >&2
      return 1
    fi
    return 0
  fi
  local traffic
  traffic="$(python3 "${ODP_TRAFFIC_HELPER}" restore-arg --description="${snapshot}")" || return 1
  echo "Restoring ${service} traffic to ${traffic}..." >&2
  gcloud run services update-traffic "${service}" \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --to-revisions="${traffic}" \
    --quiet || return 1
  verify_service_traffic "${service}" "${traffic}"
}

# Which recovery this release can actually perform, read from what was captured
# before any runtime mutation rather than from what the operator expected.
#
# ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001: a first release into an empty target
# has no previous version, so "rolling back" would name a version that does not
# exist. `restore_service_traffic` has always done the right thing here -- it
# deletes the bootstrap candidate -- but the failure path announced a traffic
# restore either way, which is the one moment an operator reads the log to learn
# whether the old version is back.
#
# ODP-DEV-EGRESS-READBACK-RECOVERY-001: this helper and restore_service_traffic
# run inside the deploy script's EXIT trap. There a bare `return` reports the
# status of the last command before the trap -- the failed deployment step --
# not the helper's own outcome, so a completed recovery read back as failed.
# Every success path in trap-reachable helpers returns 0 explicitly.
release_recovery_mode() {
  local api_snapshot="$1"
  local web_snapshot="$2"
  local api_exists web_exists
  if ! api_exists="$(python3 "${ODP_TRAFFIC_HELPER}" exists --description="${api_snapshot}")" \
    || ! web_exists="$(python3 "${ODP_TRAFFIC_HELPER}" exists --description="${web_snapshot}")"; then
    echo "Error: cannot determine whether a previous API/Web release exists; recovery mode is unknown." >&2
    return 1
  fi
  if [ "${api_exists}" = "true" ] || [ "${web_exists}" = "true" ]; then
    printf 'rollback'
    return 0
  fi
  if [ "${api_exists}" = "false" ] && [ "${web_exists}" = "false" ]; then
    printf 'initial-release-cleanup'
    return 0
  fi
  echo "Error: API/Web pre-deploy snapshot has an invalid existence value; recovery mode is unknown." >&2
  return 1
}

delete_candidate_job() {
  local job="$1"
  local existing
  if ! existing="$(gcloud run jobs list \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --filter="metadata.name=${job}" \
    --format='value(metadata.name)')"; then
    echo "Error: cannot read back candidate Cloud Run Job '${job}' for cleanup." >&2
    return 1
  fi
  if [ -z "${existing}" ]; then
    return 0
  fi
  if [ "${existing}" != "${job}" ]; then
    echo "Error: candidate Cloud Run Job lookup for '${job}' was ambiguous: ${existing}" >&2
    return 1
  fi
  echo "Deleting first-release candidate Cloud Run Job '${job}'..." >&2
  gcloud run jobs delete "${job}" \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --quiet
}

cleanup_initial_release_candidates() {
  local failed=0
  local job
  for job in "$@"; do
    delete_candidate_job "${job}" || failed=1
  done
  return "${failed}"
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
  local validation_args=()
  if [ "${3:-}" = "identity-only" ]; then
    validation_args=(--identity-only)
  fi
  existing="$(gcloud scheduler jobs list \
    --location="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --filter="name=projects/${GCP_PROJECT}/locations/${GCP_REGION}/jobs/${trigger}" \
    --format='value(name)')" || return 1
  if [ -n "${existing}" ]; then
    if [ "${existing}" != "projects/${GCP_PROJECT}/locations/${GCP_REGION}/jobs/${trigger}" ]; then
      echo "Error: ambiguous scheduler lookup for ${trigger}." >&2
      return 1
    fi
    gcloud scheduler jobs describe "${trigger}" \
      --location="${GCP_REGION}" \
      --project="${GCP_PROJECT}" \
      --format=json >"${output}" || return 1
    python3 "${ODP_SCHEDULER_HELPER}" validate --description="${output}" --trigger="${trigger}" \
      --project="${GCP_PROJECT}" --location="${GCP_REGION}" "${validation_args[@]}" || return 1
    if [ "${3:-}" = "with-job-binding" ]; then
      local job description binding
      job="$(python3 "${ODP_SCHEDULER_HELPER}" target-job --description="${output}" \
        --project="${GCP_PROJECT}" --location="${GCP_REGION}" --trigger="${trigger}")" || return 1
      description="$(mktemp)"
      binding="$(mktemp)"
      if ! gcloud run jobs describe "${job}" --region="${GCP_REGION}" --project="${GCP_PROJECT}" --format=json >"${description}" \
        || ! python3 "${ODP_JOB_VALIDATOR}" job-binding --job-description="${description}" \
          --expected-job="projects/${GCP_PROJECT}/locations/${GCP_REGION}/jobs/${job}" --record >"${binding}" \
        || ! python3 "${ODP_SCHEDULER_HELPER}" attach-binding --description="${output}" --binding="${binding}"; then
        rm -f "${description}" "${binding}"
        return 1
      fi
      rm -f "${description}" "${binding}"
      python3 "${ODP_SCHEDULER_HELPER}" recovery --description="${output}" \
        || { rm -f "${output}"; return 1; }
    fi
    # This capture also runs during rollback in a failed deployment's EXIT
    # trap. A bare return there inherits the failed step's status.
    return 0
  fi
  python3 "${ODP_SCHEDULER_HELPER}" write-absent --description="${output}" --trigger="${trigger}" \
    --project="${GCP_PROJECT}" --location="${GCP_REGION}"
}

pause_scheduler_trigger() {
  local trigger="$1" readback state
  readback="$(mktemp)"
  capture_scheduler_trigger "${trigger}" "${readback}" identity-only || { rm -f "${readback}"; return 1; }
  if [ "$(python3 "${ODP_SCHEDULER_HELPER}" exists --description="${readback}")" = "false" ]; then
    rm -f "${readback}"
    return 0
  fi
  state="$(python3 "${ODP_SCHEDULER_HELPER}" field --description="${readback}" --field=state)" || { rm -f "${readback}"; return 1; }
  if [ "${state}" = "ENABLED" ]; then
    gcloud scheduler jobs pause "${trigger}" --location="${GCP_REGION}" --project="${GCP_PROJECT}" --quiet \
      || { rm -f "${readback}"; return 1; }
  fi
  gcloud scheduler jobs describe "${trigger}" --location="${GCP_REGION}" --project="${GCP_PROJECT}" \
    --format=json >"${readback}" || { rm -f "${readback}"; return 1; }
  python3 "${ODP_SCHEDULER_HELPER}" validate --description="${readback}" --trigger="${trigger}" \
    --project="${GCP_PROJECT}" --location="${GCP_REGION}" --identity-only \
    || { rm -f "${readback}"; return 1; }
  state="$(python3 "${ODP_SCHEDULER_HELPER}" field --description="${readback}" --field=state)" || { rm -f "${readback}"; return 1; }
  rm -f "${readback}"
  [ "${state}" = "PAUSED" ]
}

verify_release_job() {
  local job="$1" image="$2" release="$3" readback execution_file
  local execution_args=()
  readback="$(mktemp)"
  execution_file="$(mktemp)"
  if [ -n "${4:-}" ]; then
    if ! gcloud run jobs executions describe "$4" --region="${GCP_REGION}" --project="${GCP_PROJECT}" \
      --format=json >"${execution_file}"; then
      rm -f "${readback}" "${execution_file}"
      return 1
    fi
    execution_args=(--execution="${execution_file}" --expected-execution="$4")
  fi
  if ! gcloud run jobs describe "${job}" --region="${GCP_REGION}" --project="${GCP_PROJECT}" \
    --format=json >"${readback}" \
    || ! python3 "${ODP_JOB_VALIDATOR}" job-binding --job-description="${readback}" \
      --expected-job="projects/${GCP_PROJECT}/locations/${GCP_REGION}/jobs/${job}" \
      --expected-image="${image}" --expected-sha="${release}" "${execution_args[@]}"; then
    rm -f "${readback}" "${execution_file}"
    return 1
  fi
  rm -f "${readback}" "${execution_file}"
}

upsert_scheduler_trigger() {
  local trigger="$1" job="$2" image="$3" cron="$4" timezone="$5" account="$6" before="$7" release="$8"
  local candidate
  verify_release_job "${job}" "${image}" "${release}" || return 1
  candidate="$(mktemp)"
  if ! python3 "${ODP_SCHEDULER_HELPER}" candidate --description="${before}" --trigger="${trigger}" \
      --project="${GCP_PROJECT}" --location="${GCP_REGION}" --job="${job}" \
      --cron="${cron}" --time-zone="${timezone}" --service-account="${account}" >"${candidate}" \
    || ! restore_scheduler_trigger "${trigger}" "${candidate}" \
    || ! verify_release_job "${job}" "${image}" "${release}"; then
    rm -f "${candidate}"
    return 1
  fi
  rm -f "${candidate}"
}

verify_scheduler_snapshot() {
  local trigger="$1" snapshot="$2" expected_state="$3" readback
  readback="$(mktemp)"
  if ! capture_scheduler_trigger "${trigger}" "${readback}"; then
    echo "Error: failed to capture or validate readback snapshot for Cloud Scheduler trigger '${trigger}'." >&2
    rm -f "${readback}"
    return 1
  fi
  if ! python3 "${ODP_SCHEDULER_HELPER}" compare --before="${snapshot}" --after="${readback}" --state="${expected_state}"; then
    echo "Error: trigger '${trigger}' readback configuration drift detected." >&2
    rm -f "${readback}"
    return 1
  fi
  rm -f "${readback}"
}

resume_scheduler_trigger() {
  local trigger="$1" job="$2" image="$3" cron="$4" timezone="$5" account="$6" before="$7" release="$8" kind="$9"
  local receipt="${10}" first_state="${11}" candidate readback desired execution current resume_status=0
  python3 "${ODP_SCHEDULER_HELPER}" first-state --state="${first_state}" >/dev/null || return 1
  python3 "${ODP_SCHEDULER_HELPER}" receipt --description="${receipt}" --release="${release}" \
    --job="projects/${GCP_PROJECT}/locations/${GCP_REGION}/jobs/${job}" --image="${image}" --kind="${kind}" || return 1
  execution="$(python3 "${ODP_SCHEDULER_HELPER}" field --description="${receipt}" --field=execution_name)" || return 1
  verify_release_job "${job}" "${image}" "${release}" "${execution}" || return 1
  candidate="$(mktemp)"
  readback="$(mktemp)"
  desired="${first_state}"
  if [ "$(python3 "${ODP_SCHEDULER_HELPER}" exists --description="${before}")" = "true" ]; then
    desired="$(python3 "${ODP_SCHEDULER_HELPER}" field --description="${before}" --field=state)" || { rm -f "${candidate}" "${readback}"; return 1; }
  fi
  if ! python3 "${ODP_SCHEDULER_HELPER}" candidate --description="${before}" --trigger="${trigger}" \
      --project="${GCP_PROJECT}" --location="${GCP_REGION}" --job="${job}" \
      --cron="${cron}" --time-zone="${timezone}" --service-account="${account}" >"${candidate}" \
    || ! capture_scheduler_trigger "${trigger}" "${readback}" \
    || ! current="$(python3 "${ODP_SCHEDULER_HELPER}" resume-state --before="${candidate}" --after="${readback}" --state="${desired}")"; then
    rm -f "${candidate}" "${readback}"
    return 1
  fi
  rm -f "${readback}"
  if [ "${12:-resume}" = "verify-only" ] || [ "${current}" = "${desired}" ]; then
    rm -f "${candidate}"
    return 0
  fi
  gcloud scheduler jobs resume "${trigger}" --location="${GCP_REGION}" --project="${GCP_PROJECT}" --quiet || resume_status=$?
  # A lost response is resolved by the exact positive postcondition, never by
  # accepting an unverified RPC failure or changing the intended prior state.
  if ! verify_scheduler_snapshot "${trigger}" "${candidate}" "${desired}" \
    || ! verify_release_job "${job}" "${image}" "${release}" "${execution}"; then
    echo "Error: resume verification failed for '${trigger}' (resume RPC status ${resume_status})." >&2
    rm -f "${candidate}"
    return 1
  fi
  if [ "${resume_status}" -ne 0 ]; then
    echo "Resume RPC for '${trigger}' returned ${resume_status}; exact desired state was read back and verified." >&2
  fi
  rm -f "${candidate}"
}

restore_scheduler_trigger() {
  local trigger="$1"
  local snapshot="$2"
  local failed=0
  local old_job="" old_image="" old_release=""
  python3 "${ODP_SCHEDULER_HELPER}" validate --description="${snapshot}" --trigger="${trigger}" \
    --project="${GCP_PROJECT}" --location="${GCP_REGION}" || return 1
  if python3 "${ODP_SCHEDULER_HELPER}" field --description="${snapshot}" --field=jobBinding.job_name >/dev/null 2>&1; then
    old_job="$(python3 "${ODP_SCHEDULER_HELPER}" field --description="${snapshot}" --field=jobBinding.job_name)" || return 1
    old_image="$(python3 "${ODP_SCHEDULER_HELPER}" field --description="${snapshot}" --field=jobBinding.job_image)" || return 1
    old_release="$(python3 "${ODP_SCHEDULER_HELPER}" field --description="${snapshot}" --field=jobBinding.release_sha)" || return 1
    verify_release_job "${old_job##*/}" "${old_image}" "${old_release}" || return 1
  fi
  local current
  current="$(mktemp)"
  capture_scheduler_trigger "${trigger}" "${current}" identity-only || { rm -f "${current}"; return 1; }

  echo "Restoring Cloud Scheduler trigger '${trigger}'..." >&2

  if [ "$(python3 "${ODP_SCHEDULER_HELPER}" exists --description="${snapshot}")" != "true" ]; then
    echo "Trigger '${trigger}' was absent prior to deploy; deleting candidate if present..." >&2
    if [ "$(python3 "${ODP_SCHEDULER_HELPER}" exists --description="${current}")" = "true" ]; then
      gcloud scheduler jobs delete "${trigger}" --location="${GCP_REGION}" \
        --project="${GCP_PROJECT}" --quiet || { rm -f "${current}"; return 1; }
    fi
    capture_scheduler_trigger "${trigger}" "${current}" \
      && python3 "${ODP_SCHEDULER_HELPER}" compare --before="${snapshot}" --after="${current}" || failed=1
    rm -f "${current}"
    [ "${failed}" -eq 0 ] || return 1
    return 0
  fi

  local action="create"
  if [ "$(python3 "${ODP_SCHEDULER_HELPER}" exists --description="${current}")" = "true" ]; then
    action="update"
  fi
  rm -f "${current}"

  local gcloud_args=()
  mapfile -d '' gcloud_args < <(python3 "${ODP_SCHEDULER_HELPER}" restore-args \
    --description="${snapshot}" \
    --location="${GCP_REGION}" \
    --project="${GCP_PROJECT}") || failed=1

  if [ "${failed}" -ne 0 ] || [ "${#gcloud_args[@]}" -eq 0 ]; then
    echo "Error: failed to generate restore arguments for trigger '${trigger}'." >&2
    return 1
  fi

  # `gcloud scheduler jobs create http` accepts --headers, while the update
  # command requires --update-headers.  Snapshots are restored through either
  # path, so adapt only the update form here.  The helper emits one combined
  # header map, so a single rewrite covers every restored header.
  if [ "${action}" = "update" ]; then
    local i
    for i in "${!gcloud_args[@]}"; do
      if [[ "${gcloud_args[$i]}" == --headers=* ]]; then
        gcloud_args[$i]="--update-headers=${gcloud_args[$i]#--headers=}"
      fi
    done
    pause_scheduler_trigger "${trigger}" || return 1
    gcloud scheduler jobs update http "${trigger}" --location="${GCP_REGION}" \
      --project="${GCP_PROJECT}" --clear-headers --quiet || return 1
  else
    # Cloud Scheduler has no create-paused flag. Bootstrap with GET, which
    # cannot invoke the POST-only :run endpoint, then pause before installing
    # the actual POST target. This also closes the first-release cron race.
    local bootstrap_args=() arg
    for arg in "${gcloud_args[@]}"; do
      case "${arg}" in
        --message-body=*) ;;
        --http-method=*) bootstrap_args+=(--http-method=GET) ;;
        *) bootstrap_args+=("${arg}") ;;
      esac
    done
    gcloud scheduler jobs create http "${trigger}" "${bootstrap_args[@]}" --quiet || return 1
    gcloud scheduler jobs pause "${trigger}" --location="${GCP_REGION}" --project="${GCP_PROJECT}" --quiet || return 1
    pause_scheduler_trigger "${trigger}" || return 1
    action="update"
    local i
    for i in "${!gcloud_args[@]}"; do
      if [[ "${gcloud_args[$i]}" == --headers=* ]]; then
        gcloud_args[$i]="--update-headers=${gcloud_args[$i]#--headers=}"
      fi
    done
  fi

  if ! gcloud scheduler jobs "${action}" http "${trigger}" "${gcloud_args[@]}" --quiet; then
    echo "Error: failed to ${action} Cloud Scheduler trigger '${trigger}'." >&2
    return 1
  fi

  local desired_state
  desired_state="$(python3 "${ODP_SCHEDULER_HELPER}" field --description="${snapshot}" --field=state)" || return 1
  # The current resource needs identity/state validation to be safely paused;
  # the restored configuration must pass full validation before any resume.
  pause_scheduler_trigger "${trigger}" || return 1
  verify_scheduler_snapshot "${trigger}" "${snapshot}" PAUSED || return 1
  if [ -n "${old_job}" ]; then
    verify_release_job "${old_job##*/}" "${old_image}" "${old_release}" || return 1
  fi
  if [ "${desired_state}" = "ENABLED" ]; then
    local resume_status=0
    gcloud scheduler jobs resume "${trigger}" --location="${GCP_REGION}" \
      --project="${GCP_PROJECT}" --quiet || resume_status=$?
    if ! verify_scheduler_snapshot "${trigger}" "${snapshot}" ENABLED; then
      echo "Error: restored trigger '${trigger}' failed ENABLED readback (resume RPC status ${resume_status})." >&2
      pause_scheduler_trigger "${trigger}" || echo "Error: failed to hold '${trigger}' PAUSED after restore verification failure." >&2
      return 1
    fi
    if [ -n "${old_job}" ] && ! verify_release_job "${old_job##*/}" "${old_image}" "${old_release}"; then
      pause_scheduler_trigger "${trigger}" || echo "Error: failed to hold '${trigger}' PAUSED after Job drift." >&2
      return 1
    fi
  fi

  echo "Cloud Scheduler trigger '${trigger}' successfully restored." >&2
  return 0
}

# ODP-DEV-EGRESS-READBACK-RECOVERY-001: read the VPC egress setting back from a
# `gcloud run jobs describe --format=json` payload on stdin and print it in its
# canonical v2 form. gcloud returns the v1 Job shape, where the setting lives in
# the execution-template annotation `run.googleapis.com/vpc-access-egress`
# (`all-traffic`); the v2 Admin API shape carries `vpcAccess.egress`
# (`ALL_TRAFFIC`). Every known location is read, and the readback fails closed
# when no location carries a value, when a value is not a recognised egress
# setting, or when two locations disagree. A recognised `private-ranges-only`
# readback is printed as PRIVATE_RANGES_ONLY so the caller can refuse it with
# the observed value on record.
cloud_run_job_vpc_egress() {
  python3 -c '
import json
import sys

ANNOTATION = "run.googleapis.com/vpc-access-egress"
CANONICAL = {
    "all": "ALL_TRAFFIC",
    "all-traffic": "ALL_TRAFFIC",
    "ALL_TRAFFIC": "ALL_TRAFFIC",
    "private-ranges-only": "PRIVATE_RANGES_ONLY",
    "PRIVATE_RANGES_ONLY": "PRIVATE_RANGES_ONLY",
}
PATHS = (
    ("spec", "template", "metadata", "annotations", ANNOTATION),
    ("metadata", "annotations", ANNOTATION),
    ("template", "template", "vpcAccess", "egress"),
    ("template", "vpcAccess", "egress"),
    ("spec", "template", "spec", "template", "spec", "vpcAccess", "egress"),
    ("vpcAccess", "egress"),
)

try:
    payload = json.load(sys.stdin)
except ValueError as error:
    raise SystemExit(f"Error: Cloud Run Job readback is not JSON: {error}")
if not isinstance(payload, dict):
    raise SystemExit("Error: Cloud Run Job readback is not a JSON object.")

observed = {}
for path in PATHS:
    value = payload
    for key in path:
        if not isinstance(value, dict) or key not in value:
            break
        value = value[key]
    else:
        location = ".".join(path)
        if not isinstance(value, str) or value not in CANONICAL:
            raise SystemExit(
                f"Error: Cloud Run Job VPC egress at {location} is not a recognised "
                f"setting: {value!r}"
            )
        observed[location] = CANONICAL[value]

if not observed:
    raise SystemExit("Error: Cloud Run Job readback carries no VPC egress setting.")
if len(set(observed.values())) != 1:
    raise SystemExit(f"Error: Cloud Run Job VPC egress readback is contradictory: {observed}")
print(next(iter(observed.values())))
'
}
