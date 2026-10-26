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
export ODP_SCORE_BROWSER_API_URL="http://127.0.0.1:${ODP_SCORE_BROWSER_API_PORT:-38299}"
export ODP_SCORE_BROWSER_WEB_URL="http://127.0.0.1:${ODP_SCORE_BROWSER_WEB_PORT:-38300}"
export ODP_SCORE_BROWSER_CONTROL_KEY="synthetic-browser-control-20261013-local-only"
DIAGNOSTICS_DIR="${ODP_E2E_DIAGNOSTICS_DIR:-.odp_data/e2e-diagnostics}"
COMPOSE=(docker compose -p "$PROJECT" -f infra/docker/docker-compose.e2e.yml)
PLAYWRIGHT_PAYLOAD="$(mktemp "${TMPDIR:-/tmp}/odp-playwright-payload.XXXXXX.json")"
PLAYWRIGHT_ARTIFACT="docs/evidence/e2e/raw_playwright_results.json"
COMPOSE_STARTED=0
DIAGNOSTICS_CAPTURED=0
export PATH="/home/lupin/.local/bin:$PATH"

if command -v uv >/dev/null 2>&1; then
  PYTHON_COMMAND=(uv run --frozen python)
elif [[ -f /home/lupin/oday-plus/.venv/bin/python3 ]]; then
  PYTHON_COMMAND=(/home/lupin/oday-plus/.venv/bin/python3)
else
  PYTHON_COMMAND=(python3)
fi



capture_diagnostics() {
  if [[ "$COMPOSE_STARTED" == "1" && "$DIAGNOSTICS_CAPTURED" != "1" ]]; then
    mkdir -p "$DIAGNOSTICS_DIR" || true
    "${COMPOSE[@]}" ps >"${DIAGNOSTICS_DIR}/compose-ps.txt" 2>&1 || true
    "${COMPOSE[@]}" logs --no-color --tail=200 >"${DIAGNOSTICS_DIR}/compose-tail.log" 2>&1 || true
    DIAGNOSTICS_CAPTURED=1
  fi
}

cleanup() {
  local runner_exit=$?
  trap - EXIT
  # Startup, fixture seeding and readiness failures must retain their logs
  # before disposable volumes and containers disappear. Never replace the
  # original test/startup exit status with a diagnostics/teardown error.
  capture_diagnostics
  rm -f "$PLAYWRIGHT_PAYLOAD" || true
  if [[ "$COMPOSE_STARTED" == "1" && "${ODP_E2E_KEEP_STACK:-0}" != "1" ]]; then
    "${COMPOSE[@]}" down --remove-orphans --volumes || true
  fi
  exit "$runner_exit"
}
trap cleanup EXIT

mkdir -p "$DIAGNOSTICS_DIR"

TESTED_SOURCE_SHA="$(git rev-parse HEAD)"
TESTED_TREE_SHA="$(git rev-parse HEAD^{tree})"
export ODP_E2E_TESTED_SOURCE_SHA="$TESTED_SOURCE_SHA"
export ODP_E2E_TESTED_TREE_SHA="$TESTED_TREE_SHA"

while IFS= read -r dirty_path; do
  case "$dirty_path" in
    docs/evidence/e2e/raw_playwright_results.json|\
    docs/evidence/e2e/raw_pytest_results.json|\
    docs/evidence/e2e/PRODUCT_E2E_EXECUTION_RECEIPT.json)
      ;;
    "")
      ;;
    *)
      printf "Refusing E2E run with non-evidence tracked change: %s\n" "$dirty_path" >&2
      exit 2
      ;;
  esac
done < <(
  {
    git diff --name-only HEAD
    git diff --cached --name-only HEAD
  } | sort -u
)

"${PYTHON_COMMAND[@]}" delivery_toolchain/e2e/check_chromium_prerequisites.py

python3 - "$DIAGNOSTICS_DIR/source-identity.json" "$TESTED_SOURCE_SHA" "$TESTED_TREE_SHA" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

Path(sys.argv[1]).write_text(json.dumps({
    "diagnostic_only": True,
    "canonical_receipt": False,
    "source_sha": sys.argv[2],
    "tree_sha": sys.argv[3],
    "created_at": datetime.now(timezone.utc).isoformat(),
}, indent=2) + "\n", encoding="utf-8")
PY

COMPOSE_STARTED=1
"${COMPOSE[@]}" down --remove-orphans --volumes
"${COMPOSE[@]}" up -d --build

python3 delivery_toolchain/e2e/seed_product_e2e_data.py \
  --wait \
  --api-url "http://127.0.0.1:${API_PORT}" \
  --source-stub-url "http://127.0.0.1:${SOURCE_STUB_PORT}" \
  --web-url "http://127.0.0.1:${WEB_PORT}" \
  --diagnostics-dir "$DIAGNOSTICS_DIR"

python3 - <<'PY'
import os
import time
import urllib.error
import urllib.request

for url in (os.environ["ODP_SCORE_BROWSER_API_URL"] + "/_synthetic/health", os.environ["ODP_SCORE_BROWSER_WEB_URL"]):
    deadline = time.monotonic() + 120
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    break
        except (OSError, urllib.error.URLError):
            pass
        if time.monotonic() >= deadline:
            raise SystemExit("Synthetic PostgreSQL browser composition did not become ready: " + url)
        time.sleep(1)
PY

PLAYWRIGHT_COMMAND=(
  npx playwright test
  tests/e2e/e2e-network-find-areas-api-binding.spec.ts
  tests/e2e/e2e-operator-console.spec.ts
  tests/e2e/market_intelligence.spec.ts
  tests/e2e/operator-assisted-listing-intake-a11y.spec.ts
  tests/e2e/operator-assisted-listing-intake-mobile.spec.ts
  tests/e2e/operator-assisted-listing-intake.spec.ts
  tests/e2e/operator-governance.spec.ts
  tests/e2e/operator-growth.spec.ts
  tests/e2e/operator-network-assisted-intake.spec.ts
  tests/e2e/operator-network-listings.spec.ts
  tests/e2e/operator-network-rebalance.spec.ts
  tests/e2e/operator-network-review.spec.ts
  tests/e2e/operator-network-scoring.spec.ts
  tests/e2e/operator-shell-layout.spec.ts
  tests/e2e/operator-shell-today.spec.ts
  tests/e2e/operator-store-ops.spec.ts
  tests/e2e/product-e2e-env.spec.ts
  tests/e2e/shell-resource-binding.spec.ts
  --workers=1
  --retries=0
  --trace=retain-on-failure
  --project=chromium
  --reporter=json
)
printf -v PLAYWRIGHT_COMMAND_TEXT '%q ' "${PLAYWRIGHT_COMMAND[@]}"
PLAYWRIGHT_COMMAND_TEXT="${PLAYWRIGHT_COMMAND_TEXT% }"
PLAYWRIGHT_VERSION="$(npx playwright --version)"
PLAYWRIGHT_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

set +e
ODP_API_BASE_URL="http://127.0.0.1:${API_PORT}" \
OPSBOARD_PORT="$WEB_PORT" \
ODP_PLAYWRIGHT_REUSE_EXISTING=1 \
ODP_OPERATOR_PRODUCT_GATE=1 \
PLAYWRIGHT_JSON_OUTPUT_NAME="$PLAYWRIGHT_PAYLOAD" \
"${PLAYWRIGHT_COMMAND[@]}"
playwright_status=$?
PLAYWRIGHT_ENDED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

python3 delivery_toolchain/e2e/record_playwright_results.py \
  --payload "$PLAYWRIGHT_PAYLOAD" \
  --output "$PLAYWRIGHT_ARTIFACT" \
  --source-sha "$TESTED_SOURCE_SHA" \
  --tree-sha "$TESTED_TREE_SHA" \
  --command "$PLAYWRIGHT_COMMAND_TEXT" \
  --version "$PLAYWRIGHT_VERSION" \
  --started-at "$PLAYWRIGHT_STARTED_AT" \
  --ended-at "$PLAYWRIGHT_ENDED_AT" \
  --exit-code "$playwright_status" \
  --project chromium \
  --workers 1 \
  --retries 0
playwright_record_status=$?

"${PYTHON_COMMAND[@]}" delivery_toolchain/e2e/run_python_e2e_tests.py
pytest_status=$?

"${PYTHON_COMMAND[@]}" delivery_toolchain/e2e/generate_product_e2e_receipt.py
receipt_status=$?

capture_diagnostics
set -e

printf "Product E2E diagnostics written to %s\n" "$DIAGNOSTICS_DIR"
printf "Runner status: playwright=%s recorder=%s pytest=%s receipt=%s\n" \
  "$playwright_status" "$playwright_record_status" "$pytest_status" "$receipt_status"

for status in \
  "$playwright_status" \
  "$playwright_record_status" \
  "$pytest_status" \
  "$receipt_status"; do
  if [[ "$status" -ne 0 ]]; then
    exit "$status"
  fi
done
exit 0
