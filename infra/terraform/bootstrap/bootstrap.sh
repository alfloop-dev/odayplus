#!/usr/bin/env bash
set -euo pipefail

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

cleanup() {
  local exit_code=$?
  if [ "$BOOTSTRAP_SUCCESS" -ne 1 ]; then
    echo "=== Bootstrap interrupted or failed (exit code: $exit_code) ===" >&2
    if [ -f "$PHASE1_DIR/terraform.tfstate" ]; then
      echo "Preserving phase 1 local state to $SCRIPT_DIR/terraform.tfstate for recovery..." >&2
      cp "$PHASE1_DIR/terraform.tfstate" "$SCRIPT_DIR/terraform.tfstate"
    fi
    if [ -f "$PHASE1_DIR/terraform.tfstate.backup" ]; then
      cp "$PHASE1_DIR/terraform.tfstate.backup" "$SCRIPT_DIR/terraform.tfstate.backup"
    fi
    echo "RECOVERY GUIDANCE:" >&2
    echo "  1. Phase 1 state has been preserved at $SCRIPT_DIR/terraform.tfstate (and in $PHASE1_DIR)." >&2
    echo "  2. Do NOT blindly re-run bootstrap without state; inspect existing resources and state." >&2
    echo "  3. To resume or destroy partially created resources, use:" >&2
    echo "     terraform -chdir=\"$PHASE1_DIR\" plan -var-file=\"$VAR_FILE\"" >&2
    echo "     or resume migration once bucket/connectivity is restored." >&2
    echo "  4. Temporary workdir retained at: $PHASE1_DIR" >&2
  else
    rm -rf "$PHASE1_DIR"
  fi
}
trap cleanup EXIT INT TERM

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
terraform -chdir="$PHASE1_DIR" init -backend=false -reconfigure -input=false

echo "=== Phase 1: Planning and applying bootstrap resources ==="
terraform -chdir="$PHASE1_DIR" plan -input=false -var-file="$VAR_FILE" -out="$PHASE1_DIR/bootstrap.tfplan"
terraform -chdir="$PHASE1_DIR" apply -input=false "$PHASE1_DIR/bootstrap.tfplan"

# Make the just-applied local state available to the canonical configuration
# so phase 2 can migrate exactly this state into the governed prefix.
cp "$PHASE1_DIR/terraform.tfstate" "$SCRIPT_DIR/terraform.tfstate"

BUCKET_NAME="$(terraform -chdir="$PHASE1_DIR" output -raw state_bucket_name)"
ENVIRONMENT="$(terraform -chdir="$PHASE1_DIR" output -raw environment 2>/dev/null || echo "staging")"

echo "=== Phase 2: Migrating bootstrap state to newly created governed bucket ($BUCKET_NAME) ==="
terraform -chdir="$SCRIPT_DIR" init -input=false -migrate-state -backend-config="bucket=$BUCKET_NAME" -backend-config="prefix=oday-plus/bootstrap" -force-copy

# The local bootstrap state contains sensitive generated values. Once the
# remote backend is initialized successfully, remove only the local state
# files; the governed GCS object is the durable source of truth.
BOOTSTRAP_SUCCESS=1
rm -f "$SCRIPT_DIR/terraform.tfstate" "$SCRIPT_DIR/terraform.tfstate.backup"

echo "=== Two-Phase Bootstrap Completed Successfully! ==="
echo "Governed State Bucket: gs://$BUCKET_NAME"
echo "Backend configuration snippet for root Terraform:"
echo "  bucket = \"$BUCKET_NAME\""
echo "  prefix = \"oday-plus/$ENVIRONMENT\""
