#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

export PATH="/home/lupin/.local/bin:$PATH"

if command -v uv >/dev/null 2>&1; then
  echo "==> Syncing Python dependencies (uv sync --frozen)..."
  uv sync --frozen
  PYTHON_CMD=(uv run --frozen python)
elif [[ -f .venv/bin/python3 ]]; then
  PYTHON_CMD=(.venv/bin/python3)
else
  PYTHON_CMD=(python3)
fi

if [[ -f package-lock.json ]]; then
  echo "==> Installing Node dependencies (npm ci)..."
  npm ci
elif [[ -f package.json ]]; then
  echo "==> Installing Node dependencies (npm install)..."
  npm install
fi

echo "==> Installing Playwright Chromium browser and host system dependencies..."
npx playwright install --with-deps chromium

echo "==> Verifying Chromium and Product E2E prerequisites..."
"${PYTHON_CMD[@]}" delivery_toolchain/e2e/check_chromium_prerequisites.py

echo "==> Product E2E bootstrap completed successfully."
