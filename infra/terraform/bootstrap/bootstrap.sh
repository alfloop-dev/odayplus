#!/usr/bin/env bash
set -euo pipefail
umask 077

# -----------------------------------------------------------------------------
# ODay Plus Governed Terraform State Backend Two-Phase Bootstrap Script
#
# Solves the bootstrap chicken-and-egg dilemma deterministically:
# Phase 1: Local state bootstrap (-backend=false) to create CMEK key & state bucket
# Phase 2: Remote state migration (-migrate-state) to store bootstrap state in the bucket
# -----------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAW_VAR_FILE="${1:-}"

if [ -z "$RAW_VAR_FILE" ] || [ ! -f "$RAW_VAR_FILE" ]; then
  echo "Usage: $0 <path-to-tfvars>" >&2
  echo "Example: $0 $SCRIPT_DIR/staging.tfvars" >&2
  exit 1
fi

VAR_FILE="$(cd "$(dirname "$RAW_VAR_FILE")" && pwd)/$(basename "$RAW_VAR_FILE")"
PHASE1_DIR="$(mktemp -d "${TMPDIR:-/tmp}/oday-bootstrap.XXXXXX")"
BOOTSTRAP_SUCCESS=0
PHASE2_STARTED=0
TERRAFORM_PID=""

# A separate job process group lets cancellation reach Terraform providers as
# well as Terraform itself, even when only this shell receives the signal.
set -m

run_terraform() {
  local exit_code=0
  terraform "$@" &
  TERRAFORM_PID=$!
  wait "$TERRAFORM_PID" || exit_code=$?
  TERRAFORM_PID=""
  return "$exit_code"
}

cancel_bootstrap() {
  local signal_name="$1" exit_code="$2"
  # Await Terraform's final state flush before the EXIT handler copies state.
  # A repeated signal must not interrupt this wait and copy a partial file.
  trap '' INT TERM
  if [ -n "$TERRAFORM_PID" ]; then
    kill -s "$signal_name" -- "-$TERRAFORM_PID" 2>/dev/null || true
    wait "$TERRAFORM_PID" || true
    TERRAFORM_PID=""
  fi
  exit "$exit_code"
}

cleanup() {
  local exit_code=$?
  trap - EXIT
  trap '' INT TERM
  if [ "$BOOTSTRAP_SUCCESS" -ne 1 ]; then
    echo "=== Bootstrap interrupted or failed (exit code: $exit_code) ===" >&2
    for state_file in terraform.tfstate terraform.tfstate.backup; do
      if [ "$PHASE2_STARTED" -eq 1 ]; then
        # Migration may have updated the canonical local state. Never replace
        # that newer file with the pre-migration phase-1 copy.
        echo "Retaining migration state, if present: $SCRIPT_DIR/$state_file" >&2
        continue
      fi
      if [ -f "$PHASE1_DIR/$state_file" ]; then
        if cp "$PHASE1_DIR/$state_file" "$SCRIPT_DIR/$state_file"; then
          echo "Preserving phase 1 state at $SCRIPT_DIR/$state_file" >&2
        else
          echo "State copy failed; recover the original $PHASE1_DIR/$state_file" >&2
        fi
      fi
    done
    echo "RECOVERY GUIDANCE:" >&2
    echo "  1. Inspect state/backup files in the retained workspace: $PHASE1_DIR" >&2
    echo "  2. Do NOT blindly re-run bootstrap without state; inspect existing resources and state." >&2
    echo "  3. To resume or destroy partially created resources, use:" >&2
    echo "     terraform -chdir=\"$PHASE1_DIR\" plan -var-file=\"$VAR_FILE\"" >&2
    echo "     or resume migration once bucket/connectivity is restored." >&2
    echo "  4. Temporary workdir retained at: $PHASE1_DIR" >&2
  else
    # Only a successful remote migration authorizes removal of local state.
    if rm -f "$SCRIPT_DIR/terraform.tfstate" "$SCRIPT_DIR/terraform.tfstate.backup"; then
      rm -rf "$PHASE1_DIR" || exit_code=1
    else
      echo "Local state cleanup failed; workspace retained at $PHASE1_DIR" >&2
      exit_code=1
    fi
  fi
  exit "$exit_code"
}
trap cleanup EXIT
trap 'cancel_bootstrap INT 130' INT
trap 'cancel_bootstrap TERM 143' TERM

echo "=== Phase 1: Preparing an ephemeral backend-less bootstrap config ==="
for config_file in "$SCRIPT_DIR"/*.tf "$SCRIPT_DIR"/.terraform.lock.hcl; do
  [ -f "$config_file" ] || continue
  config_name="$(basename "$config_file")"
  if [ "$config_name" = "main.tf" ]; then
    # Terraform requires a backend declaration for phase 2, but refuses plan
    # after init -backend=false when that declaration is present. The phase-1
    # copy is deliberately transient and contains the same resource graph;
    # only the backend declaration is omitted until the bucket exists.
    sed '/^[[:space:]]*backend "gcs" {}/d' "$config_file" > "$PHASE1_DIR/$config_name"
  else
    cp "$config_file" "$PHASE1_DIR/$config_name"
  fi
done

if [ -f "$SCRIPT_DIR/terraform.tfstate" ]; then
  cp "$SCRIPT_DIR/terraform.tfstate" "$PHASE1_DIR/terraform.tfstate"
fi

echo "=== Phase 1: Initializing bootstrap module with local state (-backend=false) ==="
run_terraform -chdir="$PHASE1_DIR" init -backend=false -reconfigure -input=false

echo "=== Phase 1: Planning and applying bootstrap resources ==="
run_terraform -chdir="$PHASE1_DIR" plan -input=false -var-file="$VAR_FILE" -out="$PHASE1_DIR/bootstrap.tfplan"
run_terraform -chdir="$PHASE1_DIR" apply -input=false "$PHASE1_DIR/bootstrap.tfplan"

# Make the just-applied local state available to the canonical configuration
# so phase 2 can migrate exactly this state into the governed prefix.
cp "$PHASE1_DIR/terraform.tfstate" "$SCRIPT_DIR/terraform.tfstate"
if [ -f "$PHASE1_DIR/terraform.tfstate.backup" ]; then
  cp "$PHASE1_DIR/terraform.tfstate.backup" "$SCRIPT_DIR/terraform.tfstate.backup"
fi

# Run in the main shell so signal handlers always see the active Terraform job.
run_terraform -chdir="$PHASE1_DIR" output -raw state_bucket_name > "$PHASE1_DIR/bucket-name"
run_terraform -chdir="$PHASE1_DIR" output -raw backend_config_hcl_example > "$PHASE1_DIR/backend.hcl"
BUCKET_NAME="$(cat "$PHASE1_DIR/bucket-name")"
BACKEND_CONFIG="$(cat "$PHASE1_DIR/backend.hcl")"

echo "=== Phase 2: Migrating bootstrap state to newly created governed bucket ($BUCKET_NAME) ==="
PHASE2_STARTED=1
run_terraform -chdir="$SCRIPT_DIR" init -input=false -migrate-state -backend-config="bucket=$BUCKET_NAME" -backend-config="prefix=oday-plus/bootstrap" -force-copy

# The local bootstrap state contains sensitive generated values. Once the
# remote backend is initialized successfully, remove only the local state
# files; the governed GCS object is the durable source of truth.
BOOTSTRAP_SUCCESS=1

echo "=== Two-Phase Bootstrap Completed Successfully! ==="
echo "Governed State Bucket: gs://$BUCKET_NAME"
echo "Backend configuration snippet for root Terraform:"
printf '%s\n' "$BACKEND_CONFIG"
