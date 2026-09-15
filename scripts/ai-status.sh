#!/bin/bash
set -euo pipefail
status_root="$(cd "$(dirname "$0")/.." && pwd)"
export PANTHEON_STATUS_ROOT="${PANTHEON_STATUS_ROOT:-$status_root}"
# Derive config from the status root when not explicitly set by a Supervisor.
# This ensures config resolution uses PANTHEON_STATUS_ROOT as the base, not the
# checkout where ai_status.py physically resides (which may differ when exec'd
# from a separate runtime checkout).
export ORCH_CONFIG_PATH="${ORCH_CONFIG_PATH:-${PANTHEON_STATUS_ROOT}/.orchestrator/config.json}"
exec python3 "$status_root/scripts/ai_status.py" "$@"
