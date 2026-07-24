#!/usr/bin/env bash
#
# Deploy ODay Plus API and Web services to GCP Cloud Run.
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

PREFLIGHT_REPORT="${PREFLIGHT_REPORT:-.odp_data/deployment/cloud-run-preflight.json}"
SMOKE_REPORT="${SMOKE_REPORT:-.odp_data/deployment/cloud-run-smoke.json}"

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

echo "Deployment details:"
echo "  Environment:      ${ODP_DEPLOY_ENV}"
echo "  Release SHA:      ${ODAY_RELEASE_SHA}"
echo "  GCP Project:      ${GCP_PROJECT}"
echo "  GCP Region:       ${GCP_REGION}"
echo "  Artifact Repo:    ${GCP_AR_REPO}"
echo "  API Service:      ${API_SERVICE}"
echo "  Web Service:      ${WEB_SERVICE}"
echo "  Runtime Mode:     live / production / PostgreSQL"
echo "----------------------------------------------"

API_ENV_FILE="$(mktemp)"
WEB_ENV_FILE="$(mktemp)"
cleanup() {
  rm -f "${API_ENV_FILE}" "${WEB_ENV_FILE}"
}
trap cleanup EXIT

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
)
payload = {key: os.environ[key] for key in keys}
payload["ODAY_ENV"] = os.environ["ODP_DEPLOY_ENV"]
payload["ODP_ENV"] = os.environ["ODP_DEPLOY_ENV"]
json.dump(payload, open(sys.argv[1], "w", encoding="utf-8"), sort_keys=True)
PY

gcloud auth configure-docker "${REGISTRY_HOST}" --quiet

echo "Building and publishing API image..."
docker build \
  --platform linux/amd64 \
  --label "org.opencontainers.image.revision=${ODAY_RELEASE_SHA}" \
  --label "com.oday-plus.data-binding=live" \
  -t "${API_IMAGE}" \
  -f infra/docker/api.Dockerfile \
  .
docker push "${API_IMAGE}"

if command -v cosign >/dev/null 2>&1; then
  cosign sign --yes "${API_IMAGE}"
  CI=true ./scripts/security/sign_images.sh verify "${API_IMAGE}"
else
  echo "Warning: cosign is not installed; workflow policy must install it before deployment." >&2
fi

API_SECRET_BINDINGS="ODAY_DATABASE_URL=${ODAY_DATABASE_URL_SECRET}"
API_SECRET_BINDINGS+=",ODP_LISTING_PROVIDER_API_KEY=${ODP_LISTING_PROVIDER_API_KEY_SECRET}"
API_SECRET_BINDINGS+=",ODP_POI_PROVIDER_API_KEY=${ODP_POI_PROVIDER_API_KEY_SECRET}"
API_SECRET_BINDINGS+=",ODP_GEOCODE_PROVIDER_API_KEY=${ODP_GEOCODE_PROVIDER_API_KEY_SECRET}"
API_SECRET_BINDINGS+=",ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN=${ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN_SECRET}"
API_SECRET_BINDINGS+=",ODP_AUTH_HS256_KEYS=${ODP_AUTH_HS256_KEYS_SECRET}"

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

if command -v cosign >/dev/null 2>&1; then
  cosign sign --yes "${WEB_IMAGE}"
  CI=true ./scripts/security/sign_images.sh verify "${WEB_IMAGE}"
else
  echo "Warning: cosign is not installed; workflow policy must install it before deployment." >&2
fi

echo "Deploying Web service..."
gcloud run deploy "${WEB_SERVICE}" \
  --image="${WEB_IMAGE}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT}" \
  --platform=managed \
  --port=3000 \
  --service-account="${ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT}" \
  --env-vars-file="${WEB_ENV_FILE}" \
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
