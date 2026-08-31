#!/usr/bin/env bash
#
# Deploy ODay Plus API/Web services and bounded worker/scheduler Cloud Run Jobs.
#
# The script is intentionally fail-closed. It will not build or deploy while
# the repository lacks a production database adapter, worker runtime, concrete
# live-provider adapters, or a non-seed Operator bootstrap.

set -euo pipefail

echo "=== Starting ODay Plus Cloud Run Deployment ==="

for cmd in python3 uv gcloud docker; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Error: required command '$cmd' is not installed." >&2
    exit 1
  fi
done

# Repository-aware validators import project runtime modules and dependencies.
# Always resolve them through uv.lock; the runner's system Python is reserved
# for the explicitly standard-library-only inline serializers below.
run_locked_python() {
  uv run --frozen python "$@"
}

: "${ODP_DEPLOY_ENV:?Error: ODP_DEPLOY_ENV is required.}"
: "${ODAY_RELEASE_SHA:?Error: ODAY_RELEASE_SHA is required.}"
: "${API_SERVICE:?Error: API_SERVICE is required.}"
: "${WEB_SERVICE:?Error: WEB_SERVICE is required.}"
: "${MIGRATION_JOB:?Error: MIGRATION_JOB is required.}"
: "${WORKER_JOB:?Error: WORKER_JOB is required.}"
: "${SCHEDULER_JOB:?Error: SCHEDULER_JOB is required.}"
: "${WORKER_SCHEDULE_NAME:?Error: WORKER_SCHEDULE_NAME is required.}"
: "${SCHEDULER_SCHEDULE_NAME:?Error: SCHEDULER_SCHEDULE_NAME is required.}"
: "${ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT:?Error: ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT is required.}"
: "${ODP_WORKER_CRON:?Error: ODP_WORKER_CRON is required.}"
: "${ODP_SCHEDULER_CRON:?Error: ODP_SCHEDULER_CRON is required.}"
: "${ODP_SCHEDULER_TIME_ZONE:?Error: ODP_SCHEDULER_TIME_ZONE is required.}"
: "${ODP_FORECAST_ENGINE:?Error: ODP_FORECAST_ENGINE is required for live deployments.}"
: "${ODP_FORECAST_MODEL:?Error: ODP_FORECAST_MODEL is required for live deployments.}"
: "${ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT:?Error: ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT is required.}"
if [ "${ODP_DEPLOY_ENV}" = "production" ]; then
  : "${ODP_PROD_DEPLOY_URL:?Error: ODP_PROD_DEPLOY_URL is required for production live E2E.}"
  : "${ODP_PROD_API_URL:?Error: ODP_PROD_API_URL is required for production live E2E.}"
  for production_url in "${ODP_PROD_DEPLOY_URL}" "${ODP_PROD_API_URL}"; do
    if [[ ! "${production_url}" =~ ^https://[^[:space:]]+$ ]]; then
      echo "Error: production live E2E URLs must be HTTPS custom domains." >&2
      exit 1
    fi
  done
fi

# Private-IP Cloud SQL is used by isolated staging. Keep the connectivity
# binding in this single release entrypoint so every service and job follows
# the same path; if either half is missing, fail before any mutation.
if [ -n "${ODP_CLOUD_RUN_VPC_CONNECTOR:-}" ] && [ -z "${ODP_CLOUD_RUN_VPC_EGRESS:-}" ]; then
  echo "Error: ODP_CLOUD_RUN_VPC_EGRESS is required with ODP_CLOUD_RUN_VPC_CONNECTOR." >&2
  exit 1
fi
if [ -n "${ODP_CLOUD_RUN_VPC_EGRESS:-}" ] && [ -z "${ODP_CLOUD_RUN_VPC_CONNECTOR:-}" ]; then
  echo "Error: ODP_CLOUD_RUN_VPC_CONNECTOR is required with ODP_CLOUD_RUN_VPC_EGRESS." >&2
  exit 1
fi
if [ -n "${ODP_CLOUD_RUN_VPC_EGRESS:-}" ]; then
  case "${ODP_CLOUD_RUN_VPC_EGRESS}" in
    all|all-traffic|private-ranges-only)
      ;;
    *)
      echo "Error: unsupported ODP_CLOUD_RUN_VPC_EGRESS '${ODP_CLOUD_RUN_VPC_EGRESS}'. " \
        "Expected all, all-traffic, or private-ranges-only." >&2
      exit 1
      ;;
  esac
fi

CLOUD_RUN_NETWORK_ARGS=()
if [ -n "${ODP_CLOUD_RUN_VPC_CONNECTOR:-}" ]; then
  CLOUD_RUN_NETWORK_ARGS+=("--vpc-connector=${ODP_CLOUD_RUN_VPC_CONNECTOR}")
  CLOUD_RUN_NETWORK_ARGS+=("--vpc-egress=${ODP_CLOUD_RUN_VPC_EGRESS}")
fi

if [ -z "${ODP_SCHEDULED_INGESTION_TENANT_ID:-}" ] && [ -z "${ODP_TENANT_ID:-}" ]; then
  echo "Error: ODP_SCHEDULED_INGESTION_TENANT_ID or ODP_TENANT_ID is required." >&2
  exit 1
fi

case "${ODP_FORECAST_ENGINE}:${ODP_FORECAST_MODEL}" in
  statsforecast:seasonal_naive|statsforecast:auto_arima|statsforecast:auto_ets)
    ;;
  mlforecast:hist_gradient_boosting)
    ;;
  *)
    echo "Error: unsupported production ForecastOps binding " \
      "'${ODP_FORECAST_ENGINE}:${ODP_FORECAST_MODEL}'. " \
      "Expected statsforecast:{seasonal_naive,auto_arima,auto_ets} " \
      "or mlforecast:hist_gradient_boosting." >&2
    exit 1
    ;;
esac

# Password-first is the default and OIDC is optional, so the preflight, the Web
# secret bindings, and the Web runtime environment must all read one resolved
# mode. Resolve it before the preflight so a split configuration is rejected
# before anything is built or deployed.
source product_ops/deployment/auth_mode.sh
resolve_auth_mode
echo "Authentication mode: ${ODP_AUTH_MODE} (ODP_AUTH_OIDC_ENABLED=${ODP_AUTH_OIDC_ENABLED})."

PREFLIGHT_REPORT="${PREFLIGHT_REPORT:-.odp_data/deployment/cloud-run-preflight.json}"
SMOKE_REPORT="${SMOKE_REPORT:-.odp_data/deployment/cloud-run-smoke.json}"
MIGRATION_COMPAT_REPORT="${MIGRATION_COMPAT_REPORT:-.odp_data/deployment/cloud-run-migration-compatibility.json}"
# Bounded cold-start tolerance for the old-revision compatibility probes.
# Worst case per probe: 4 x 15s of attempts + 2s + 4s + 8s of backoff = 74s,
# hard-capped by the 120s deadline. See run_migration_compatibility_gate.
MIGRATION_COMPAT_TIMEOUT="${MIGRATION_COMPAT_TIMEOUT:-15}"
MIGRATION_COMPAT_RETRY_ATTEMPTS="${MIGRATION_COMPAT_RETRY_ATTEMPTS:-4}"
MIGRATION_COMPAT_RETRY_BACKOFF="${MIGRATION_COMPAT_RETRY_BACKOFF:-2}"
MIGRATION_COMPAT_RETRY_MAX_BACKOFF="${MIGRATION_COMPAT_RETRY_MAX_BACKOFF:-8}"
MIGRATION_COMPAT_RETRY_DEADLINE="${MIGRATION_COMPAT_RETRY_DEADLINE:-120}"
LIVE_E2E_REPORT="${LIVE_E2E_REPORT:-.odp_data/deployment/live-e2e-gate.json}"
JOB_REPORT_DIR="${JOB_REPORT_DIR:-.odp_data/deployment/cloud-run-jobs}"
source product_ops/deployment/cloud_run_release_traffic.sh

echo "Running fail-closed live deployment preflight..."
run_locked_python product_ops/deployment/validate_cloud_run_live_deployment.py preflight \
  --environment "${ODP_DEPLOY_ENV}" \
  --release-sha "${ODAY_RELEASE_SHA}" \
  --output "${PREFLIGHT_REPORT}"

# No build, push, or Cloud Run mutation may occur above this line.
REVISION_SUFFIX="release-${ODAY_RELEASE_SHA:0:12}"
API_REVISION_TAG="candidate-${ODAY_RELEASE_SHA:0:16}"
WEB_REVISION_TAG="candidate-${ODAY_RELEASE_SHA:0:16}"
release_job_name() {
  local base="$1"
  local suffix="-r-${ODAY_RELEASE_SHA:0:12}"
  local prefix_length=$((63 - ${#suffix}))
  printf "%s%s" "${base:0:${prefix_length}}" "${suffix}"
}
MIGRATION_CANDIDATE_JOB="$(release_job_name "${MIGRATION_JOB}")"
WORKER_CANDIDATE_JOB="$(release_job_name "${WORKER_JOB}")"
SCHEDULER_CANDIDATE_JOB="$(release_job_name "${SCHEDULER_JOB}")"
REGISTRY_HOST="${GCP_REGION}-docker.pkg.dev"
REPO_PATH="${REGISTRY_HOST}/${GCP_PROJECT}/${GCP_AR_REPO}"
if [ "${ODP_DEPLOY_BY_DIGEST:-false}" = "true" ]; then
  : "${API_IMAGE:?Error: API_IMAGE is required for deploy-by-digest.}"
  : "${WEB_IMAGE:?Error: WEB_IMAGE is required for deploy-by-digest.}"
  : "${WORKER_IMAGE:?Error: WORKER_IMAGE is required for deploy-by-digest.}"
  : "${SCHEDULER_IMAGE:?Error: SCHEDULER_IMAGE is required for deploy-by-digest.}"
  for image in "${API_IMAGE}" "${WEB_IMAGE}" "${WORKER_IMAGE}" "${SCHEDULER_IMAGE}"; do
    if [[ ! "${image}" =~ ^[^[:space:]]+@sha256:[0-9a-f]{64}$ ]]; then
      echo "Error: deploy-by-digest requires immutable image reference, got '${image}'." >&2
      exit 1
    fi
  done
else
  IMAGE_TAG="${IMAGE_TAG:-${ODP_DEPLOY_ENV}-${ODAY_RELEASE_SHA}}"
  API_IMAGE="${REPO_PATH}/${API_SERVICE}:${IMAGE_TAG}"
  WEB_IMAGE="${REPO_PATH}/${WEB_SERVICE}:${IMAGE_TAG}"
  WORKER_IMAGE="${REPO_PATH}/${WORKER_JOB}:${IMAGE_TAG}"
  SCHEDULER_IMAGE="${REPO_PATH}/${SCHEDULER_JOB}:${IMAGE_TAG}"
fi

echo "Deployment details:"
echo "  Environment:      ${ODP_DEPLOY_ENV}"
echo "  Release SHA:      ${ODAY_RELEASE_SHA}"
echo "  GCP Project:      ${GCP_PROJECT}"
echo "  GCP Region:       ${GCP_REGION}"
echo "  Artifact Repo:    ${GCP_AR_REPO}"
echo "  API Service:      ${API_SERVICE}"
echo "  Web Service:      ${WEB_SERVICE}"
echo "  Migration Job:    ${MIGRATION_JOB}"
echo "  Worker Job:       ${WORKER_JOB}"
echo "  Scheduler Job:    ${SCHEDULER_JOB}"
echo "  Runtime Mode:     live / production / PostgreSQL"
echo "----------------------------------------------"

API_ENV_FILE="$(mktemp)"
WEB_ENV_FILE="$(mktemp)"
API_TRAFFIC_SNAPSHOT="$(mktemp)"
WEB_TRAFFIC_SNAPSHOT="$(mktemp)"
API_CANDIDATE_DESCRIPTION="$(mktemp)"
WEB_CANDIDATE_DESCRIPTION="$(mktemp)"
SCHEDULER_TRIGGER_SNAPSHOT="$(mktemp)"
WORKER_TRIGGER_SNAPSHOT="$(mktemp)"
ROLLBACK_ARMED=false
SCHEDULER_ROLLBACK_ARMED=false
DEPLOYMENT_COMMITTED=false
cleanup() {
  rm -f \
    "${API_ENV_FILE}" \
    "${WEB_ENV_FILE}" \
    "${API_TRAFFIC_SNAPSHOT}" \
    "${WEB_TRAFFIC_SNAPSHOT}" \
    "${API_CANDIDATE_DESCRIPTION}" \
    "${WEB_CANDIDATE_DESCRIPTION}" \
    "${SCHEDULER_TRIGGER_SNAPSHOT}" \
    "${WORKER_TRIGGER_SNAPSHOT}"
}
handle_deployment_exit() {
  local status=$?
  local rollback_status=0
  trap - EXIT
  set +e
  if [ "${status}" -ne 0 ] \
    && [ "${ROLLBACK_ARMED}" = "true" ] \
    && [ "${DEPLOYMENT_COMMITTED}" != "true" ]; then
    echo "Deployment failed; restoring the recorded API/Web traffic split." >&2
    rollback_release_traffic \
      "${API_SERVICE}" \
      "${API_TRAFFIC_SNAPSHOT}" \
      "${WEB_SERVICE}" \
      "${WEB_TRAFFIC_SNAPSHOT}" || rollback_status=$?
    if [ "${rollback_status}" -ne 0 ]; then
      echo "Error: one or more Cloud Run traffic restores failed." >&2
    fi
    if [ "${SCHEDULER_ROLLBACK_ARMED}" = "true" ]; then
      echo "Restoring the recorded Cloud Scheduler trigger targets." >&2
      restore_scheduler_trigger \
        "${SCHEDULER_SCHEDULE_NAME}" \
        "${SCHEDULER_TRIGGER_SNAPSHOT}" || rollback_status=$?
      restore_scheduler_trigger \
        "${WORKER_SCHEDULE_NAME}" \
        "${WORKER_TRIGGER_SNAPSHOT}" || rollback_status=$?
      if [ "${rollback_status}" -ne 0 ]; then
        echo "Error: one or more Cloud Scheduler trigger restores failed." >&2
      fi
    fi
  fi
  cleanup
  exit "${status}"
}
trap handle_deployment_exit EXIT
mkdir -p "${JOB_REPORT_DIR}"

# This deterministic serializer imports only Python's standard library.
python3 - "${API_ENV_FILE}" <<'PY'
import json
import os
import sys

keys = [
    "ODAY_RELEASE_SHA",
    "ODP_DEPLOY_ENV",
    "ODP_REQUIRE_LIVE_DATA",
    "ODP_DATA_BINDING_MODE",
    "ODP_PRODUCT_MODE",
    "ODP_FORECAST_ENGINE",
    "ODP_FORECAST_MODEL",
    "ODP_EXTERNAL_PROVIDER_MODE",
    "ODP_PERSISTENCE",
    "ODP_OBJECT_STORE",
    "ODP_SNAPSHOT_BUCKET",
    "MLFLOW_TRACKING_URI",
    "ODP_MLFLOW_CLOUD_RUN_AUDIENCE",
    "ODP_COMPETITOR_MANUAL_SOURCE_STATUS",
    "ODP_AUTH_ISSUER",
    "ODP_AUTH_AUDIENCES",
    "ODP_AUTH_JWKS_URI",
    "ODP_AUTH_LOCAL_ISSUER",
    "ODP_AUTH_LOCAL_AUDIENCES",
    # The resolved auth mode travels to the API too. The boundary reads it as
    # the authoritative "is the OIDC provider on?" gate, so an API that never
    # received it would keep trusting OIDC tokens for a password-first release
    # (ODP-WEB-LOCAL-AUTH-API-TRUST-001). resolve_auth_mode has already run, so
    # this is the resolved mode and not a raw operator input; the legacy
    # ODP_AUTH_OIDC_ENABLED alias is deliberately not forwarded, because a pair
    # that arrives split is a configuration the boundary would have to refuse.
    "ODP_AUTH_MODE",
    # True service/OIDC runtime env separation (ODP-WEB-LOCAL-AUTH-API-TRUST-001).
    # These separated variables let config_from_env route each issuer path
    # deterministically. The legacy ODP_AUTH_ISSUER / _JWKS_URI / _AUDIENCES
    # stay present as migration aliases.
    "ODP_AUTH_SERVICE_ISSUER",
    "ODP_AUTH_SERVICE_JWKS_URI",
    "ODP_AUTH_SERVICE_AUDIENCES",
    "ODP_AUTH_OIDC_ISSUER",
    "ODP_AUTH_OIDC_JWKS_URI",
    "ODP_AUTH_OIDC_AUDIENCES",
    "ODP_AUTH_SUBJECT_ROLE_BINDINGS",
    "ODP_SCHEDULED_INGESTION_TENANT_ID",
    "ODP_TENANT_ID",
]
payload = {key: os.environ[key] for key in keys if key in os.environ}
# Keep the local issuer contract explicit in the API manifest. The plain
# signing-key binding is interpreted as kid=local-default by the API resolver.
payload["ODP_AUTH_LOCAL_ISSUER"] = os.environ.get(
    "ODP_AUTH_LOCAL_ISSUER", "urn:odp:identity:local"
)
payload["ODP_AUTH_LOCAL_AUDIENCES"] = os.environ.get(
    "ODP_AUTH_LOCAL_AUDIENCES", os.environ.get("ODP_AUTH_AUDIENCES", "")
)
tenant_id = os.environ.get("ODP_SCHEDULED_INGESTION_TENANT_ID") or os.environ.get("ODP_TENANT_ID")
if not tenant_id:
    raise ValueError("ODP_SCHEDULED_INGESTION_TENANT_ID or ODP_TENANT_ID is required for Cloud Run deployment")
payload["ODP_SCHEDULED_INGESTION_TENANT_ID"] = tenant_id
payload["ODP_TENANT_ID"] = tenant_id
payload["ODAY_ENV"] = os.environ["ODP_DEPLOY_ENV"]
payload["ODP_ENV"] = os.environ["ODP_DEPLOY_ENV"]
json.dump(payload, open(sys.argv[1], "w", encoding="utf-8"), sort_keys=True)
PY

gcloud auth configure-docker "${REGISTRY_HOST}" --quiet

build_publish_sign() {
  local name="$1"
  local image="$2"
  local dockerfile="$3"
  if [ "${ODP_DEPLOY_BY_DIGEST:-false}" = "true" ] || [ "${ODP_SKIP_BUILD:-false}" = "true" ]; then
    echo "Deploy-by-digest: skipping build for ${name} image (${image})."
    return 0
  fi
  echo "Building and publishing ${name} image..."
  docker build \
    --platform linux/amd64 \
    --label "org.opencontainers.image.revision=${ODAY_RELEASE_SHA}" \
    --label "com.oday-plus.data-binding=live" \
    -t "${image}" \
    -f "${dockerfile}" \
    .
  docker push "${image}"

  if command -v cosign >/dev/null 2>&1; then
    cosign sign --yes "${image}"
    CI=true ./delivery_toolchain/security/sign_images.sh verify "${image}"
  else
    echo "Error: cosign is required for a production deployment." >&2
    exit 1
  fi
}

build_publish_sign "API" "${API_IMAGE}" "infra/docker/api.Dockerfile"
build_publish_sign "worker" "${WORKER_IMAGE}" "infra/docker/worker.Dockerfile"
build_publish_sign "scheduler" "${SCHEDULER_IMAGE}" "infra/docker/scheduler.Dockerfile"

API_SECRET_BINDINGS="ODAY_DATABASE_URL=${ODAY_DATABASE_URL_SECRET}"
API_SECRET_BINDINGS+=",ODP_AUTH_PRINCIPAL_MAP=${ODP_AUTH_PRINCIPAL_MAP_SECRET}"
API_SECRET_BINDINGS+=",ODP_IDENTITY_TOKEN_SIGNING_KEY=${ODP_IDENTITY_TOKEN_SIGNING_KEY_SECRET}"
WEB_SECRET_BINDINGS="ODAY_DATABASE_URL=${ODAY_DATABASE_URL_SECRET}"
WEB_SECRET_BINDINGS+=",ODP_WEB_SESSION_SECRET=${ODP_WEB_SESSION_SECRET_SECRET}"
# The API and Web must consume the same Secret Manager version. The API
# resolver assigns a plain key this task's canonical kid, local-default.
WEB_SECRET_BINDINGS+=",ODP_IDENTITY_TOKEN_SIGNING_KEY=${ODP_IDENTITY_TOKEN_SIGNING_KEY_SECRET}"
# Only the enabled provider's secrets reach Cloud Run. resolve_auth_mode has
# already proven ODP_WEB_OIDC_CLIENT_SECRET_SECRET is set whenever OIDC is on.
if [ "${ODP_AUTH_OIDC_ENABLED}" = "true" ]; then
  WEB_SECRET_BINDINGS+=",ODP_WEB_OIDC_CLIENT_SECRET=${ODP_WEB_OIDC_CLIENT_SECRET_SECRET}"
fi

# gcloud's shortcut for describing a job's newest execution only exists on
# recent releases, so job proof capture used to depend on the runner's CLI
# version. Resolve the newest execution through the version-stable
# `executions list` surface and then describe it by its exact name.
# Resolution is fail-closed: an empty, malformed, or ambiguous list aborts
# before any receipt is written.
capture_latest_execution() {
  local job="$1"
  local execution_file="$2"
  local list_file="${execution_file%.json}-list.json"
  local execution_name
  if ! gcloud run jobs executions list \
    --job="${job}" \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --format=json >"${list_file}"; then
    return 1
  fi
  if ! execution_name="$(run_locked_python \
    product_ops/deployment/validate_cloud_run_live_deployment.py resolve-latest-execution \
    --executions="${list_file}" \
    --job="${job}")"; then
    return 1
  fi
  if [ -z "${execution_name}" ]; then
    echo "Error: latest Cloud Run Job execution name resolved empty." >&2
    return 1
  fi
  if ! gcloud run jobs executions describe "${execution_name}" \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --format=json >"${execution_file}"; then
    return 1
  fi
}

capture_job_proof() {
  local kind="$1"
  local job="$2"
  local description_file="${JOB_REPORT_DIR}/${kind}-job.json"
  local execution_file="${JOB_REPORT_DIR}/${kind}-execution.json"
  gcloud run jobs describe "${job}" \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --format=json >"${description_file}"
  capture_latest_execution "${job}" "${execution_file}"
  run_locked_python product_ops/deployment/validate_cloud_run_live_deployment.py jobs-smoke \
    --job-kind="${kind}" \
    --job-description="${description_file}" \
    --execution="${execution_file}" \
    --expected-sha="${ODAY_RELEASE_SHA}" \
    --output="${JOB_REPORT_DIR}/${kind}-validation.json"
}

execute_job() {
  local kind="$1"
  local job="$2"
  shift 2
  echo "Executing ${kind} Cloud Run Job..."
  if ! gcloud run jobs execute "${job}" \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --wait \
    --quiet \
    "$@"; then
    # Best-effort forensic capture only; the failure below still stops the
    # deployment and falls through to the rollback trap.
    capture_latest_execution "${job}" "${JOB_REPORT_DIR}/${kind}-execution.json" || true
    echo "Error: ${kind} Cloud Run Job failed; deployment stopped." >&2
    return 1
  fi
  capture_job_proof "${kind}" "${job}"
}

echo "Recording the existing API/Web traffic before any runtime mutation..."
capture_service_traffic "${API_SERVICE}" "${API_TRAFFIC_SNAPSHOT}"
capture_service_traffic "${WEB_SERVICE}" "${WEB_TRAFFIC_SNAPSHOT}"
capture_scheduler_trigger "${SCHEDULER_SCHEDULE_NAME}" "${SCHEDULER_TRIGGER_SNAPSHOT}"
capture_scheduler_trigger "${WORKER_SCHEDULE_NAME}" "${WORKER_TRIGGER_SNAPSHOT}"
OLD_API_URL="$(service_snapshot_url "${API_TRAFFIC_SNAPSHOT}")"
OLD_WEB_URL="$(service_snapshot_url "${WEB_TRAFFIC_SNAPSHOT}")"
ROLLBACK_ARMED=true

echo "Deploying immutable migration candidate Cloud Run Job..."
gcloud run jobs deploy "${MIGRATION_CANDIDATE_JOB}" \
  --image="${WORKER_IMAGE}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT}" \
  --service-account="${ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT}" \
  --set-cloudsql-instances="${GCP_CLOUD_SQL_INSTANCE}" \
  --env-vars-file="${API_ENV_FILE}" \
  --set-secrets="${API_SECRET_BINDINGS}" \
  --command=python \
  --args="product_ops/deployment/cloud_run_job_entrypoint.py,migrate" \
  --tasks=1 \
  --max-retries=0 \
  --task-timeout=1800s \
  "${CLOUD_RUN_NETWORK_ARGS[@]}" \
  --labels="oday-release-sha=${ODAY_RELEASE_SHA},oday-runtime=migration,oday-data-binding=live" \
  --quiet

# This gate verifies both the exact migration receipt and backward
# compatibility with the old revisions that still carry all production
# traffic. No candidate service is deployed until this passes.
#
# The old revision has no minScale and receives no traffic between deploys, so
# this probe pays a Cloud Run cold start. The retry bounds below are explicit
# at the call site so the worst-case gate duration stays auditable: per probe
# at most ${MIGRATION_COMPAT_RETRY_ATTEMPTS} attempts of
# ${MIGRATION_COMPAT_TIMEOUT}s plus backoff, never past
# ${MIGRATION_COMPAT_RETRY_DEADLINE}s. Exhausting either bound still fails the
# gate closed, before any candidate traffic and with the rollback trap armed.
run_migration_compatibility_gate() {
  execute_job "migration" "${MIGRATION_CANDIDATE_JOB}"
  if [ -z "${OLD_API_URL}" ] && [ -z "${OLD_WEB_URL}" ]; then
    run_locked_python product_ops/deployment/validate_cloud_run_live_deployment.py bootstrap-compatibility \
      --environment "${ODP_DEPLOY_ENV}" \
      --release-sha "${ODAY_RELEASE_SHA}" \
      --output "${MIGRATION_COMPAT_REPORT}"
    return
  fi
  if [ -z "${OLD_API_URL}" ] || [ -z "${OLD_WEB_URL}" ]; then
    echo "Error: bootstrap state is inconsistent; API and Web must both exist or both be absent." >&2
    return 1
  fi
  run_locked_python product_ops/deployment/validate_cloud_run_live_deployment.py compatibility-smoke \
    --api-url "${OLD_API_URL}" \
    --web-url "${OLD_WEB_URL}" \
    --correlation-id "corr-cloud-run-compat-${ODP_DEPLOY_ENV}-${ODAY_RELEASE_SHA}" \
    --timeout "${MIGRATION_COMPAT_TIMEOUT}" \
    --compat-retry-attempts "${MIGRATION_COMPAT_RETRY_ATTEMPTS}" \
    --compat-retry-backoff-seconds "${MIGRATION_COMPAT_RETRY_BACKOFF}" \
    --compat-retry-max-backoff-seconds "${MIGRATION_COMPAT_RETRY_MAX_BACKOFF}" \
    --compat-retry-deadline-seconds "${MIGRATION_COMPAT_RETRY_DEADLINE}" \
    --output "${MIGRATION_COMPAT_REPORT}"
}
run_migration_compatibility_gate

echo "Deploying immutable API candidate without production traffic..."
gcloud run deploy "${API_SERVICE}" \
  --image="${API_IMAGE}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT}" \
  --platform=managed \
  --port=8000 \
  --service-account="${ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT}" \
  --add-cloudsql-instances="${GCP_CLOUD_SQL_INSTANCE}" \
  --env-vars-file="${API_ENV_FILE}" \
  --set-secrets="${API_SECRET_BINDINGS}" \
  --labels="oday-release-sha=${ODAY_RELEASE_SHA},oday-data-binding=live" \
  --revision-suffix="${REVISION_SUFFIX}" \
  "${CLOUD_RUN_NETWORK_ARGS[@]}" \
  --tag="${API_REVISION_TAG}" \
  --no-traffic \
  --no-allow-unauthenticated \
  --quiet

gcloud run services describe "${API_SERVICE}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT}" \
  --format=json >"${API_CANDIDATE_DESCRIPTION}"
API_REVISION="$(tagged_revision "${API_CANDIDATE_DESCRIPTION}" "${API_REVISION_TAG}")"
API_URL="$(tagged_revision_url "${API_CANDIDATE_DESCRIPTION}" "${API_REVISION_TAG}")"
API_SERVICE_AUDIENCE="$(service_snapshot_url "${API_CANDIDATE_DESCRIPTION}")"

echo "Deploying immutable scheduler candidate Cloud Run Job..."
gcloud run jobs deploy "${SCHEDULER_CANDIDATE_JOB}" \
  --image="${SCHEDULER_IMAGE}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT}" \
  --service-account="${ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT}" \
  --set-cloudsql-instances="${GCP_CLOUD_SQL_INSTANCE}" \
  --env-vars-file="${API_ENV_FILE}" \
  --set-secrets="${API_SECRET_BINDINGS}" \
  --command=python \
  --args="product_ops/deployment/cloud_run_job_entrypoint.py,scheduler" \
  --tasks=1 \
  --max-retries=0 \
  --task-timeout=600s \
  "${CLOUD_RUN_NETWORK_ARGS[@]}" \
  --labels="oday-release-sha=${ODAY_RELEASE_SHA},oday-runtime=scheduler,oday-data-binding=live" \
  --quiet

echo "Deploying immutable worker candidate Cloud Run Job..."
gcloud run jobs deploy "${WORKER_CANDIDATE_JOB}" \
  --image="${WORKER_IMAGE}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT}" \
  --service-account="${ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT}" \
  --set-cloudsql-instances="${GCP_CLOUD_SQL_INSTANCE}" \
  --env-vars-file="${API_ENV_FILE}" \
  --set-secrets="${API_SECRET_BINDINGS}" \
  --command=python \
  --args="product_ops/deployment/cloud_run_job_entrypoint.py,worker,--max-jobs,100" \
  --tasks=1 \
  --max-retries=3 \
  --task-timeout=900s \
  "${CLOUD_RUN_NETWORK_ARGS[@]}" \
  --labels="oday-release-sha=${ODAY_RELEASE_SHA},oday-runtime=worker,oday-data-binding=live" \
  --quiet

for job in "${SCHEDULER_CANDIDATE_JOB}" "${WORKER_CANDIDATE_JOB}"; do
  gcloud run jobs add-iam-policy-binding "${job}" \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --member="serviceAccount:${ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT}" \
    --role="roles/run.invoker" \
    --quiet
done

upsert_scheduler_trigger() {
  local trigger_name="$1"
  local target_job="$2"
  local cron="$3"
  local target_uri="https://run.googleapis.com/v2/projects/${GCP_PROJECT}/locations/${GCP_REGION}/jobs/${target_job}:run"
  local action="create"
  if gcloud scheduler jobs describe "${trigger_name}" \
    --location="${GCP_REGION}" \
    --project="${GCP_PROJECT}" >/dev/null 2>&1; then
    action="update"
  fi
  local header_arg="--headers=Content-Type=application/json"
  if [ "${action}" = "update" ]; then
    header_arg="--update-headers=Content-Type=application/json"
  fi
  gcloud scheduler jobs "${action}" http "${trigger_name}" \
    --location="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --schedule="${cron}" \
    --time-zone="${ODP_SCHEDULER_TIME_ZONE}" \
    --uri="${target_uri}" \
    --http-method=POST \
    --message-body="{}" \
    "${header_arg}" \
    --oauth-service-account-email="${ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT}" \
    --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
    --quiet
}

# The scheduler must persist an enqueue receipt. The worker must either leave a
# terminal success receipt or prove that the durable queue is drained; a
# same-minute scheduler idempotency replay may legitimately leave no new work.
# Wrapper exit codes make retry-queued work retryable by Cloud Run and make
# FAILED/CANCELLED/DLQ non-zero.
execute_job "scheduler" "${SCHEDULER_CANDIDATE_JOB}"
execute_job "worker" "${WORKER_CANDIDATE_JOB}" \
  --args="product_ops/deployment/cloud_run_job_entrypoint.py,worker,--max-jobs,1"

# This deterministic serializer imports only Python's standard library.
python3 - "${WEB_ENV_FILE}" "${API_URL}" "${API_SERVICE_AUDIENCE}" <<'PY'
import json
import os
import sys

payload = {
    "NODE_ENV": "production",
    "ODAY_ENV": os.environ["ODP_DEPLOY_ENV"],
    "ODP_DEPLOY_ENV": os.environ["ODP_DEPLOY_ENV"],
    "ODAY_RELEASE_SHA": os.environ["ODAY_RELEASE_SHA"],
    "ODP_REQUIRE_LIVE_DATA": os.environ["ODP_REQUIRE_LIVE_DATA"],
    "ODP_DATA_BINDING_MODE": os.environ["ODP_DATA_BINDING_MODE"],
    "ODP_PRODUCT_MODE": os.environ["ODP_PRODUCT_MODE"],
    "NEXT_PUBLIC_ODP_PRODUCT_MODE": os.environ["ODP_PRODUCT_MODE"],
    "NEXT_PUBLIC_ODP_DATA_BINDING_MODE": os.environ["ODP_DATA_BINDING_MODE"],
    "NEXT_PUBLIC_ODAY_RELEASE_SHA": os.environ["ODAY_RELEASE_SHA"],
    "ODP_API_BASE_URL": sys.argv[2],
    "ODP_API_SERVICE_AUDIENCE": sys.argv[3],
    "NEXT_PUBLIC_ODP_API_BASE_URL": sys.argv[2],
    # The canonical web origin backs cookies, CSRF, and redirects in every auth
    # mode; the Web runtime fails closed without it in production, so it is not
    # part of the OIDC-only block below. The preflight has already proven it is
    # set, which is why this is a direct lookup.
    "ODP_WEB_BASE_URL": os.environ["ODP_WEB_BASE_URL"],
    "ODP_AUTH_MODE": os.environ["ODP_AUTH_MODE"],
    "ODP_AUTH_OIDC_ENABLED": os.environ["ODP_AUTH_OIDC_ENABLED"],
    # These values are part of the Web-to-API local JWT contract. The API
    # resolver uses the same defaults when the optional env vars are absent,
    # but writing them explicitly keeps both runtime manifests auditable.
    "ODP_AUTH_LOCAL_ISSUER": os.environ.get(
        "ODP_AUTH_LOCAL_ISSUER", "urn:odp:identity:local"
    ),
    "ODP_AUTH_LOCAL_AUDIENCES": sys.argv[3],
    "ODP_AUTH_AUDIENCES": sys.argv[3],
}
# resolve_auth_mode owns the decision; this stage only follows it, so the Web
# runtime can never disagree with the secrets bound to the same revision.
if payload["ODP_AUTH_OIDC_ENABLED"] == "true":
    payload["ODP_WEB_OIDC_ISSUER"] = os.environ["ODP_WEB_OIDC_ISSUER"]
    payload["ODP_WEB_OIDC_CLIENT_ID"] = os.environ["ODP_WEB_OIDC_CLIENT_ID"]
    payload["ODP_WEB_OIDC_ALLOWED_ALGS"] = "RS256"
json.dump(payload, open(sys.argv[1], "w", encoding="utf-8"), sort_keys=True)
PY

if [ "${ODP_DEPLOY_BY_DIGEST:-false}" = "true" ] || [ "${ODP_SKIP_BUILD:-false}" = "true" ]; then
  echo "Deploy-by-digest: skipping build for Web image (${WEB_IMAGE})."
else
  echo "Building and publishing Web image..."
  docker build \
    --platform linux/amd64 \
    --build-arg "ODP_API_BASE_URL=${API_URL}" \
    --build-arg "ODAY_RELEASE_SHA=${ODAY_RELEASE_SHA}" \
    --build-arg "ODP_REQUIRE_LIVE_DATA=${ODP_REQUIRE_LIVE_DATA}" \
    --build-arg "ODP_DATA_BINDING_MODE=${ODP_DATA_BINDING_MODE}" \
    --build-arg "ODP_PRODUCT_MODE=${ODP_PRODUCT_MODE}" \
    --label "org.opencontainers.image.revision=${ODAY_RELEASE_SHA}" \
    --label "com.oday-plus.data-binding=live" \
    -t "${WEB_IMAGE}" \
    -f infra/docker/web.Dockerfile \
    .
  docker push "${WEB_IMAGE}"

  cosign sign --yes "${WEB_IMAGE}"
  CI=true ./delivery_toolchain/security/sign_images.sh verify "${WEB_IMAGE}"
fi

echo "Deploying immutable Web candidate without production traffic..."
gcloud run deploy "${WEB_SERVICE}" \
  --image="${WEB_IMAGE}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT}" \
  --platform=managed \
  --port=3000 \
  --service-account="${ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT}" \
  --env-vars-file="${WEB_ENV_FILE}" \
  --set-secrets="${WEB_SECRET_BINDINGS}" \
  --labels="oday-release-sha=${ODAY_RELEASE_SHA},oday-data-binding=live" \
  --revision-suffix="${REVISION_SUFFIX}" \
  "${CLOUD_RUN_NETWORK_ARGS[@]}" \
  --tag="${WEB_REVISION_TAG}" \
  --no-traffic \
  --allow-unauthenticated \
  --quiet

gcloud run services describe "${WEB_SERVICE}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT}" \
  --format=json >"${WEB_CANDIDATE_DESCRIPTION}"
WEB_REVISION="$(tagged_revision "${WEB_CANDIDATE_DESCRIPTION}" "${WEB_REVISION_TAG}")"
WEB_URL="$(tagged_revision_url "${WEB_CANDIDATE_DESCRIPTION}" "${WEB_REVISION_TAG}")"

if [[ -z "${ODP_OPERATOR_SMOKE_BEARER_TOKEN:-}" ]]; then
  smoke_audience="${ODP_AUTH_AUDIENCES%%,*}"
  if [[ -z "${smoke_audience//[[:space:]]/}" ]]; then
    echo "Error: ODP_AUTH_AUDIENCES must provide a smoke token audience." >&2
    exit 1
  fi
  ODP_OPERATOR_SMOKE_BEARER_TOKEN="$(gcloud auth print-identity-token \
    --impersonate-service-account="${ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT}" \
    --audiences="${smoke_audience}" \
    --include-email)"
  if [[ -z "${ODP_OPERATOR_SMOKE_BEARER_TOKEN}" ]]; then
    echo "Error: failed to mint short-lived smoke identity token." >&2
    exit 1
  fi
  export ODP_OPERATOR_SMOKE_BEARER_TOKEN
  if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
    echo "::add-mask::${ODP_OPERATOR_SMOKE_BEARER_TOKEN}"
  fi
fi

echo "Running release-aware smoke checks against tagged candidate revisions..."
run_locked_python product_ops/deployment/validate_cloud_run_live_deployment.py smoke \
  --api-url "${API_URL}" \
  --web-url "${WEB_URL}" \
  --expected-sha "${ODAY_RELEASE_SHA}" \
  --correlation-id "corr-cloud-run-${ODP_DEPLOY_ENV}-${ODAY_RELEASE_SHA}" \
  --output "${SMOKE_REPORT}"

SCHEDULER_ROLLBACK_ARMED=true
upsert_scheduler_trigger \
  "${SCHEDULER_SCHEDULE_NAME}" \
  "${SCHEDULER_CANDIDATE_JOB}" \
  "${ODP_SCHEDULER_CRON}"
upsert_scheduler_trigger \
  "${WORKER_SCHEDULE_NAME}" \
  "${WORKER_CANDIDATE_JOB}" \
  "${ODP_WORKER_CRON}"
promote_service_traffic "${API_SERVICE}" "${API_REVISION}"
promote_service_traffic "${WEB_SERVICE}" "${WEB_REVISION}"

# ODP-LIVE-E2E-001: the release is serving but is not committed yet. The live
# E2E gate drives the promoted release the way an operator would -- authenticate,
# read the operator bootstrap, enqueue durable work, watch the worker take it to
# a terminal state, read the durable audit receipt back -- and rejects any
# fixture/mock surrogate or missing MLflow production alias. Because this runs
# before DEPLOYMENT_COMMITTED, a failure falls through the EXIT trap and rolls
# traffic and the scheduler triggers back to the previous release.
echo "Running fail-closed live E2E acceptance gate against the promoted release..."
# Resolve the served origins into variables first. Inside the argv of the gate
# invocation a failing command substitution would expand to an empty string
# without tripping `set -e`, handing the gate a blank URL. Dev deliberately
# uses the Cloud Run default service URL; production uses the configured HTTPS
# custom domains so the final gate exercises the public production contract.
if [ "${ODP_DEPLOY_ENV}" = "production" ]; then
  LIVE_E2E_API_URL="${ODP_PROD_API_URL}"
  LIVE_E2E_WEB_URL="${ODP_PROD_DEPLOY_URL}"
else
  LIVE_E2E_API_URL="$(service_snapshot_url "${API_CANDIDATE_DESCRIPTION}")"
  LIVE_E2E_WEB_URL="$(service_snapshot_url "${WEB_CANDIDATE_DESCRIPTION}")"
fi
if [[ -z "${LIVE_E2E_API_URL}" || -z "${LIVE_E2E_WEB_URL}" ]]; then
  echo "Live E2E gate cannot run: served origin lookup returned empty" \
    "(api='${LIVE_E2E_API_URL}' web='${LIVE_E2E_WEB_URL}')." >&2
  exit 1
fi
# `deploymentMode` is what the *runtime* reports back from
# `apps/api/oday_api/runtime_mode.deployment_mode()`, which reads the
# ODP_DEPLOY_ENV/ODAY_ENV/ODP_ENV triple this script writes into the API env
# payload above. So the expectation must be derived from that same value, not
# from a hardcoded "production": a dev deploy legitimately reports
# `deploymentMode=dev` while still being a live, production-mode runtime
# (ODP_PRODUCT_MODE/ODP_REQUIRE_LIVE_DATA carry that, and the gate asserts them
# separately). Hardcoding "production" made every dev deploy promote and then
# roll straight back. The var override stays for environments whose runtime env
# name differs from the deploy env name.
LIVE_E2E_DEPLOYMENT_MODE="${ODP_LIVE_E2E_DEPLOYMENT_MODE:-${ODP_DEPLOY_ENV}}"
if [[ -z "${LIVE_E2E_DEPLOYMENT_MODE}" ]]; then
  echo "Live E2E gate cannot run: neither ODP_LIVE_E2E_DEPLOYMENT_MODE nor" \
    "ODP_DEPLOY_ENV is set, so the expected deploymentMode is unknown." >&2
  exit 1
fi
run_locked_python delivery_toolchain/e2e/check_live_e2e_gate.py \
  --api-url "${LIVE_E2E_API_URL}" \
  --web-url "${LIVE_E2E_WEB_URL}" \
  --expected-sha "${ODAY_RELEASE_SHA}" \
  --expected-deployment "${LIVE_E2E_DEPLOYMENT_MODE}" \
  --worker-job "${WORKER_CANDIDATE_JOB}" \
  --gcp-region "${GCP_REGION}" \
  --gcp-project "${GCP_PROJECT}" \
  --worker-deadline-seconds "${ODP_LIVE_E2E_WORKER_DEADLINE_SECONDS:-600}" \
  --output "${LIVE_E2E_REPORT}"

DEPLOYMENT_COMMITTED=true

echo "=== Cloud Run deployment passed all live-data gates ==="
echo "API Endpoint: ${LIVE_E2E_API_URL}"
echo "Web Endpoint: ${LIVE_E2E_WEB_URL}"
echo "Migration Job: ${MIGRATION_CANDIDATE_JOB}"
echo "Worker Job: ${WORKER_CANDIDATE_JOB} (${WORKER_SCHEDULE_NAME})"
echo "Scheduler Job: ${SCHEDULER_CANDIDATE_JOB} (${SCHEDULER_SCHEDULE_NAME})"
