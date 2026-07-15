#!/usr/bin/env bash
#
# Deploy ODay Plus API and Web services to GCP Cloud Run.
#
# Required Environment Variables:
#   GCP_PROJECT     - The GCP Project ID (e.g., alfaloop-data-project)
#   GCP_REGION      - The GCP Region (e.g., asia-east1)
#   GCP_AR_REPO     - The GCP Artifact Registry repository name
#
# Optional Environment Variables:
#   IMAGE_TAG       - The docker image tag to push/deploy (defaults to "dev")
#   API_SERVICE     - Cloud Run service name for API (defaults to "oday-api")
#   WEB_SERVICE     - Cloud Run service name for Web (defaults to "oday-web")
#

set -euo pipefail

# --- Configuration & Validation ---

echo "=== Starting ODay Plus Cloud Run Deployment ==="

IMAGE_TAG="${IMAGE_TAG:-dev}"
API_SERVICE="${API_SERVICE:-oday-api}"
WEB_SERVICE="${WEB_SERVICE:-oday-web}"

# Fail-closed validation with clear messages
MISSING_VARS=0
if [ -z "${GCP_PROJECT:-}" ]; then
  echo "Error: GCP_PROJECT environment variable is not set." >&2
  MISSING_VARS=1
fi
if [ -z "${GCP_REGION:-}" ]; then
  echo "Error: GCP_REGION environment variable is not set." >&2
  MISSING_VARS=1
fi
if [ -z "${GCP_AR_REPO:-}" ]; then
  echo "Error: GCP_AR_REPO environment variable is not set." >&2
  MISSING_VARS=1
fi

if [ "$MISSING_VARS" -ne 0 ]; then
  echo "Error: Missing required configuration variables. Deploying aborted (fail-closed)." >&2
  exit 1
fi

# Check required commands
for cmd in gcloud docker curl; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "Error: Required command '$cmd' is not installed." >&2
    exit 1
  fi
done

# Define Image URLs
REGISTRY_HOST="${GCP_REGION}-docker.pkg.dev"
REPO_PATH="${REGISTRY_HOST}/${GCP_PROJECT}/${GCP_AR_REPO}"
API_IMAGE="${REPO_PATH}/${API_SERVICE}:${IMAGE_TAG}"
WEB_IMAGE="${REPO_PATH}/${WEB_SERVICE}:${IMAGE_TAG}"

echo "Deployment Details:"
echo "  GCP Project:      ${GCP_PROJECT}"
echo "  GCP Region:       ${GCP_REGION}"
echo "  Artifact Repo:    ${GCP_AR_REPO}"
echo "  Image Tag:        ${IMAGE_TAG}"
echo "  API Image:        ${API_IMAGE}"
echo "  Web Image:        ${WEB_IMAGE}"
echo "  API Service Name: ${API_SERVICE}"
echo "  Web Service Name: ${WEB_SERVICE}"
echo "----------------------------------------------"

# --- Docker Registry Authentication ---
echo "Authenticating docker with Artifact Registry at ${REGISTRY_HOST}..."
gcloud auth configure-docker "${REGISTRY_HOST}" --quiet

# --- Build & Deploy API Service ---
echo "Building API container image..."
docker build \
  --platform linux/amd64 \
  -t "${API_IMAGE}" \
  -f infra/docker/api.Dockerfile \
  .

echo "Pushing API container image to Artifact Registry..."
docker push "${API_IMAGE}"

echo "Deploying API service to Cloud Run..."
gcloud run deploy "${API_SERVICE}" \
  --image="${API_IMAGE}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT}" \
  --platform=managed \
  --allow-unauthenticated \
  --set-env-vars="ODP_ENV=dev" \
  --quiet

# Retrieve API URL
echo "Retrieving API service URL..."
API_URL=$(gcloud run services describe "${API_SERVICE}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT}" \
  --format='value(status.url)')

if [ -z "${API_URL}" ]; then
  echo "Error: Failed to retrieve API service URL from Cloud Run." >&2
  exit 1
fi
echo "API successfully deployed to: ${API_URL}"

# --- Build & Deploy Web Service ---
# We bake ODP_API_BASE_URL (pointing to the API service URL) at build time.
echo "Building Web container image with baked API URL: ${API_URL}..."
docker build \
  --platform linux/amd64 \
  --build-arg ODP_API_BASE_URL="${API_URL}" \
  -t "${WEB_IMAGE}" \
  -f infra/docker/web.Dockerfile \
  .

echo "Pushing Web container image to Artifact Registry..."
docker push "${WEB_IMAGE}"

echo "Deploying Web service to Cloud Run..."
gcloud run deploy "${WEB_SERVICE}" \
  --image="${WEB_IMAGE}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT}" \
  --platform=managed \
  --allow-unauthenticated \
  --quiet

# Retrieve Web URL
echo "Retrieving Web service URL..."
WEB_URL=$(gcloud run services describe "${WEB_SERVICE}" \
  --region="${GCP_REGION}" \
  --project="${GCP_PROJECT}" \
  --format='value(status.url)')

if [ -z "${WEB_URL}" ]; then
  echo "Error: Failed to retrieve Web service URL from Cloud Run." >&2
  exit 1
fi
echo "Web successfully deployed to: ${WEB_URL}"

# --- Smoke Checks ---
echo "----------------------------------------------"
echo "Running automatic smoke checks..."

# Check API health
API_HEALTH_URL="${API_URL}/health"
echo "Probing API health endpoint: ${API_HEALTH_URL}..."
API_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${API_HEALTH_URL}")
if [ "$API_STATUS" -ne 200 ]; then
  echo "Error: API smoke check failed with HTTP status code ${API_STATUS} (expected 200)." >&2
  exit 1
fi
echo "API smoke check passed!"

# Check Web operator console
WEB_OP_URL="${WEB_URL}/operator"
echo "Probing Web operator console: ${WEB_OP_URL}..."
WEB_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${WEB_OP_URL}")
if [ "$WEB_STATUS" -ne 200 ]; then
  echo "Error: Web operator console smoke check failed with HTTP status code ${WEB_STATUS} (expected 200)." >&2
  exit 1
fi
echo "Web operator console smoke check passed!"

echo "=== All Services Deployed and Verified Successfully ==="
echo "API Endpoint: ${API_URL}"
echo "Web Endpoint: ${WEB_URL}"
