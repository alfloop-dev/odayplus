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
VAR_FILE="${1:-}"

if [ -z "$VAR_FILE" ] || [ ! -f "$VAR_FILE" ]; then
  echo "Usage: $0 <path-to-tfvars>"
  echo "Example: $0 $SCRIPT_DIR/staging.tfvars"
  exit 1
fi

echo "=== Phase 1: Initializing bootstrap module with local state (-backend=false) ==="
terraform -chdir="$SCRIPT_DIR" init -backend=false -reconfigure

echo "=== Phase 1: Planning and applying bootstrap resources ==="
terraform -chdir="$SCRIPT_DIR" plan -var-file="$VAR_FILE" -out="$SCRIPT_DIR/bootstrap.tfplan"
terraform -chdir="$SCRIPT_DIR" apply "$SCRIPT_DIR/bootstrap.tfplan"
rm -f "$SCRIPT_DIR/bootstrap.tfplan"

BUCKET_NAME="$(terraform -chdir="$SCRIPT_DIR" output -raw state_bucket_name)"
ENVIRONMENT="$(terraform -chdir="$SCRIPT_DIR" output -raw environment 2>/dev/null || echo "staging")"

echo "=== Phase 2: Migrating bootstrap state to newly created governed bucket ($BUCKET_NAME) ==="
terraform -chdir="$SCRIPT_DIR" init   -migrate-state   -backend-config="bucket=$BUCKET_NAME"   -backend-config="prefix=oday-plus/bootstrap"   -force-copy

echo "=== Two-Phase Bootstrap Completed Successfully! ==="
echo "Governed State Bucket: gs://$BUCKET_NAME"
echo "Backend configuration snippet for root Terraform:"
echo "  bucket = \"$BUCKET_NAME\""
echo "  prefix = \"oday-plus/$ENVIRONMENT\""
