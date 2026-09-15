#!/bin/bash
set -euo pipefail
status_root="$(cd "$(dirname "$0")/.." && pwd)"
export PANTHEON_STATUS_ROOT="${PANTHEON_STATUS_ROOT:-$status_root}"
exec python3 /home/lupin/oday-plus-supervisor-runtime-current/scripts/ai_status.py "$@"
