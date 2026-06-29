#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env.e2e ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env.e2e
  set +a
elif [[ -f .env.e2e.example ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env.e2e.example
  set +a
fi

PROJECT="${ODP_E2E_PROJECT:-oday-plus-e2e}"
WEB_PORT="${ODP_E2E_WEB_PORT:-3100}"
API_PORT="${ODP_E2E_API_PORT:-8099}"
SOURCE_STUB_PORT="${ODP_E2E_SOURCE_STUB_PORT:-8077}"
DIAGNOSTICS_DIR="${ODP_E2E_DIAGNOSTICS_DIR:-.odp_data/e2e-diagnostics}"
COMPOSE=(docker compose -p "$PROJECT" -f infra/docker/docker-compose.e2e.yml)

mkdir -p "$DIAGNOSTICS_DIR"

cleanup() {
  if [[ "${ODP_E2E_KEEP_STACK:-0}" != "1" ]]; then
    "${COMPOSE[@]}" down --remove-orphans
  fi
}
trap cleanup EXIT

"${COMPOSE[@]}" up -d --build

python3 scripts/e2e/seed_product_e2e_data.py \
  --wait \
  --api-url "http://127.0.0.1:${API_PORT}" \
  --source-stub-url "http://127.0.0.1:${SOURCE_STUB_PORT}" \
  --diagnostics-dir "$DIAGNOSTICS_DIR"

set +e
ODP_API_BASE_URL="http://127.0.0.1:${API_PORT}" \
OPSBOARD_PORT="$WEB_PORT" \
ODP_PLAYWRIGHT_REUSE_EXISTING=1 \
npx playwright test \
  tests/e2e/e2e-api-bound-ui.spec.ts \
  tests/e2e/e2e-map.spec.ts \
  tests/e2e/e2e-expansion-product.spec.ts \
  tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts \
  tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts \
  tests/e2e/product-e2e-env.spec.ts \
  --project=chromium
test_status=$?
set -e

"${COMPOSE[@]}" ps >"${DIAGNOSTICS_DIR}/compose-ps.txt"
"${COMPOSE[@]}" logs --no-color --tail=200 >"${DIAGNOSTICS_DIR}/compose-tail.log"

printf "Product E2E diagnostics written to %s\n" "$DIAGNOSTICS_DIR"
exit "$test_status"
