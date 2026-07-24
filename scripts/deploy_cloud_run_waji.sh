#!/usr/bin/env bash
#
# Deploy ODay Plus API/Web services and bounded worker/scheduler Cloud Run Jobs.
#
# The script is intentionally fail-closed. It will not build or deploy while
# the repository lacks a production database adapter, worker runtime, concrete
# live-provider adapters, or a non-seed Operator bootstrap.

set -euo pipefail

echo "=== Starting ODay Plus Cloud Run Deployment ==="

for cmd in python3 gcloud docker; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Error: required command '$cmd' is not installed." >&2
    exit 1
  fi
done

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

PREFLIGHT_REPORT="${PREFLIGHT_REPORT:-.odp_data/deployment/cloud-run-preflight.json}"
SMOKE_REPORT="${SMOKE_REPORT:-.odp_data/deployment/cloud-run-smoke.json}"
JOB_REPORT_DIR="${JOB_REPORT_DIR:-.odp_data/deployment/cloud-run-jobs}"

echo "Running fail-closed live deployment preflight..."
python3 scripts/deployment/validate_cloud_run_live_deployment.py preflight \
  --environment "${ODP_DEPLOY_ENV}" \
  --release-sha "${ODAY_RELEASE_SHA}" \
  --output "${PREFLIGHT_REPORT}"

# No build, push, or Cloud Run mutation may occur above this line.
IMAGE_TAG="${IMAGE_TAG:-${ODP_DEPLOY_ENV}-${ODAY_RELEASE_SHA}}"
REGISTRY_HOST="${GCP_REGION}-docker.pkg.dev"
REPO_PATH="${REGISTRY_HOST}/${GCP_PROJECT}/${GCP_AR_REPO}"
API_IMAGE="${REPO_PATH}/${API_SERVICE}:${IMAGE_TAG}"
WEB_IMAGE="${REPO_PATH}/${WEB_SERVICE}:${IMAGE_TAG}"
WORKER_IMAGE="${REPO_PATH}/${WORKER_JOB}:${IMAGE_TAG}"
SCHEDULER_IMAGE="${REPO_PATH}/${SCHEDULER_JOB}:${IMAGE_TAG}"

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
cleanup() {
  rm -f "${API_ENV_FILE}" "${WEB_ENV_FILE}"
}
trap cleanup EXIT
mkdir -p "${JOB_REPORT_DIR}"

python3 - "${API_ENV_FILE}" <<'PY'
import json
import os
import sys

keys = (
    "ODAY_RELEASE_SHA",
    "ODP_DEPLOY_ENV",
    "ODP_REQUIRE_LIVE_DATA",
    "ODP_DATA_BINDING_MODE",
    "ODP_PRODUCT_MODE",
    "ODP_EXTERNAL_PROVIDER_MODE",
    "ODP_PERSISTENCE",
    "ODP_OBJECT_STORE",
    "ODP_SNAPSHOT_BUCKET",
    "MLFLOW_TRACKING_URI",
    "ODP_LISTING_PROVIDER_FEED_URL",
    "ODP_GEOCODE_PROVIDER_URL",
    "ODP_LISTING_PROVIDER_AUTH_STATUS",
    "ODP_POI_PROVIDER_AUTH_STATUS",
    "ODP_GEOCODE_PROVIDER_AUTH_STATUS",
    "ODP_ADMIN_BOUNDARY_PROVIDER_AUTH_STATUS",
    "ODP_PRODUCTION_PROVIDER_IDS",
    "ODP_COMPETITOR_MANUAL_SOURCE_STATUS",
    "ODP_AUTH_ISSUER",
    "ODP_AUTH_AUDIENCES",
    "ODP_AUTH_JWKS_URI",
)
payload = {key: os.environ[key] for key in keys}
payload["ODAY_ENV"] = os.environ["ODP_DEPLOY_ENV"]
payload["ODP_ENV"] = os.environ["ODP_DEPLOY_ENV"]
json.dump(payload, open(sys.argv[1], "w", encoding="utf-8"), sort_keys=True)
PY

gcloud auth configure-docker "${REGISTRY_HOST}" --quiet

build_publish_sign() {
  local name="$1"
  local image="$2"
  local dockerfile="$3"
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
    CI=true ./scripts/security/sign_images.sh verify "${image}"
  else
    echo "Error: cosign is required for a production deployment." >&2
    exit 1
  fi
}

build_publish_sign "API" "${API_IMAGE}" "infra/docker/api.Dockerfile"
build_publish_sign "worker" "${WORKER_IMAGE}" "infra/docker/worker.Dockerfile"
build_publish_sign "scheduler" "${SCHEDULER_IMAGE}" "infra/docker/scheduler.Dockerfile"

API_SECRET_BINDINGS="ODAY_DATABASE_URL=${ODAY_DATABASE_URL_SECRET}"
API_SECRET_BINDINGS+=",ODP_LISTING_PROVIDER_API_KEY=${ODP_LISTING_PROVIDER_API_KEY_SECRET}"
API_SECRET_BINDINGS+=",ODP_POI_PROVIDER_API_KEY=${ODP_POI_PROVIDER_API_KEY_SECRET}"
API_SECRET_BINDINGS+=",ODP_GEOCODE_PROVIDER_API_KEY=${ODP_GEOCODE_PROVIDER_API_KEY_SECRET}"
API_SECRET_BINDINGS+=",ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN=${ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN_SECRET}"
WEB_SECRET_BINDINGS="ODP_WEB_OIDC_CLIENT_SECRET=${ODP_WEB_OIDC_CLIENT_SECRET_SECRET}"
WEB_SECRET_BINDINGS+=",ODP_WEB_SESSION_SECRET=${ODP_WEB_SESSION_SECRET_SECRET}"

capture_job_proof() {
  local kind="$1"
  local job="$2"
  local description_file="${JOB_REPORT_DIR}/${kind}-job.json"
  local execution_file="${JOB_REPORT_DIR}/${kind}-execution.json"
  gcloud run jobs describe "${job}" \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --format=json >"${description_file}"
  gcloud run jobs executions describe-latest \
    --job="${job}" \
    --region="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --format=json >"${execution_file}"
  python3 scripts/deployment/validate_cloud_run_live_deployment.py jobs-smoke \
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
    gcloud run jobs executions describe-latest \
      --job="${job}" \
      --region="${GCP_REGION}" \
      --project="${GCP_PROJECT}" \
      --format=json >"${JOB_REPORT_DIR}/${kind}-execution.json" || true
    echo "Error: ${kind} Cloud Run Job failed; deployment stopped." >&2
    return 1
  fi
  capture_job_proof "${kind}" "${job}"
}

echo "Deploying migration Cloud Run Job..."
gcloud run jobs deploy "${MIGRATION_JOB}" \
  --image="${WORKER_IMAGE}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT}" \
  --service-account="${ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT}" \
  --set-cloudsql-instances="${GCP_CLOUD_SQL_INSTANCE}" \
  --env-vars-file="${API_ENV_FILE}" \
  --set-secrets="${API_SECRET_BINDINGS}" \
  --command=python \
  --args="scripts/deployment/cloud_run_job_entrypoint.py,migrate" \
  --tasks=1 \
  --max-retries=0 \
  --task-timeout=1800s \
  --labels="oday-release-sha=${ODAY_RELEASE_SHA},oday-runtime=migration,oday-data-binding=live" \
  --quiet

# This is the release gate: no API, worker, scheduler, or web runtime is
# deployed until the exact release image has migrated the shared database.
execute_job "migration" "${MIGRATION_JOB}"

echo "Deploying API service..."
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
  --allow-unauthenticated \
  --quiet

API_URL="$(gcloud run services describe "${API_SERVICE}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT}" \
  --format='value(status.url)')"
if [ -z "${API_URL}" ]; then
  echo "Error: failed to resolve the deployed API URL." >&2
  exit 1
fi

echo "Deploying scheduler Cloud Run Job..."
gcloud run jobs deploy "${SCHEDULER_JOB}" \
  --image="${SCHEDULER_IMAGE}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT}" \
  --service-account="${ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT}" \
  --set-cloudsql-instances="${GCP_CLOUD_SQL_INSTANCE}" \
  --env-vars-file="${API_ENV_FILE}" \
  --set-secrets="${API_SECRET_BINDINGS}" \
  --command=python \
  --args="scripts/deployment/cloud_run_job_entrypoint.py,scheduler" \
  --tasks=1 \
  --max-retries=0 \
  --task-timeout=600s \
  --labels="oday-release-sha=${ODAY_RELEASE_SHA},oday-runtime=scheduler,oday-data-binding=live" \
  --quiet

echo "Deploying worker Cloud Run Job..."
gcloud run jobs deploy "${WORKER_JOB}" \
  --image="${WORKER_IMAGE}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT}" \
  --service-account="${ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT}" \
  --set-cloudsql-instances="${GCP_CLOUD_SQL_INSTANCE}" \
  --env-vars-file="${API_ENV_FILE}" \
  --set-secrets="${API_SECRET_BINDINGS}" \
  --command=python \
  --args="scripts/deployment/cloud_run_job_entrypoint.py,worker,--max-jobs,100" \
  --tasks=1 \
  --max-retries=3 \
  --task-timeout=900s \
  --labels="oday-release-sha=${ODAY_RELEASE_SHA},oday-runtime=worker,oday-data-binding=live" \
  --quiet

for job in "${SCHEDULER_JOB}" "${WORKER_JOB}"; do
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
  gcloud scheduler jobs "${action}" http "${trigger_name}" \
    --location="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --schedule="${cron}" \
    --time-zone="${ODP_SCHEDULER_TIME_ZONE}" \
    --uri="${target_uri}" \
    --http-method=POST \
    --message-body="{}" \
    --headers="Content-Type=application/json" \
    --oauth-service-account-email="${ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT}" \
    --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
    --quiet
}

upsert_scheduler_trigger "${SCHEDULER_SCHEDULE_NAME}" "${SCHEDULER_JOB}" "${ODP_SCHEDULER_CRON}"
upsert_scheduler_trigger "${WORKER_SCHEDULE_NAME}" "${WORKER_JOB}" "${ODP_WORKER_CRON}"

# The scheduler must persist an enqueue receipt. The worker must either leave a
# terminal success receipt or prove that the durable queue is drained; a
# same-minute scheduler idempotency replay may legitimately leave no new work.
# Wrapper exit codes make retry-queued work retryable by Cloud Run and make
# FAILED/CANCELLED/DLQ non-zero.
execute_job "scheduler" "${SCHEDULER_JOB}"
execute_job "worker" "${WORKER_JOB}" \
  --args="scripts/deployment/cloud_run_job_entrypoint.py,worker,--max-jobs,1"

python3 - "${WEB_ENV_FILE}" "${API_URL}" <<'PY'
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
    "NEXT_PUBLIC_ODP_API_BASE_URL": sys.argv[2],
    "ODP_WEB_OIDC_ISSUER": os.environ["ODP_WEB_OIDC_ISSUER"],
    "ODP_WEB_OIDC_CLIENT_ID": os.environ["ODP_WEB_OIDC_CLIENT_ID"],
    "ODP_WEB_OIDC_ALLOWED_ALGS": "RS256",
}
json.dump(payload, open(sys.argv[1], "w", encoding="utf-8"), sort_keys=True)
PY

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
CI=true ./scripts/security/sign_images.sh verify "${WEB_IMAGE}"

echo "Deploying Web service..."
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
  --allow-unauthenticated \
  --quiet

WEB_URL="$(gcloud run services describe "${WEB_SERVICE}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT}" \
  --format='value(status.url)')"
if [ -z "${WEB_URL}" ]; then
  echo "Error: failed to resolve the deployed Web URL." >&2
  exit 1
fi

echo "Running release-aware, live-data smoke checks..."
python3 scripts/deployment/validate_cloud_run_live_deployment.py smoke \
  --api-url "${API_URL}" \
  --web-url "${WEB_URL}" \
  --expected-sha "${ODAY_RELEASE_SHA}" \
  --correlation-id "corr-cloud-run-${ODP_DEPLOY_ENV}-${ODAY_RELEASE_SHA}" \
  --output "${SMOKE_REPORT}"

echo "=== Cloud Run deployment passed all live-data gates ==="
echo "API Endpoint: ${API_URL}"
echo "Web Endpoint: ${WEB_URL}"
echo "Migration Job: ${MIGRATION_JOB}"
echo "Worker Job: ${WORKER_JOB} (${WORKER_SCHEDULE_NAME})"
echo "Scheduler Job: ${SCHEDULER_JOB} (${SCHEDULER_SCHEDULE_NAME})"
