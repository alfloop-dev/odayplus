#!/usr/bin/env bash
# Container Image Signing & Verification policy and procedures.
# This script serves as a deployment gate helper and documents rotation / revocation.

set -euo pipefail

# Print help/usage
usage() {
  echo "Usage: $0 [sign|verify|rotate-keys|revoke-key] [image-reference]"
  echo ""
  echo "Commands:"
  echo "  sign <image>         Sign container image using Cosign keyless/OIDC or local key"
  echo "  verify <image>       Verify container image signature and provenance"
  echo "  rotate-keys          Show key rotation policy and CLI steps"
  echo "  revoke-key           Show key revocation and remediation policy"
  exit 1
}

if [ $# -lt 1 ]; then
  usage
fi

COMMAND="$1"

require_cosign() {
  if ! command -v cosign >/dev/null 2>&1; then
    echo "Error: cosign is required for image signing/verification; refusing to simulate success." >&2
    return 1
  fi
}

case "$COMMAND" in
  sign)
    if [ $# -lt 2 ]; then
      echo "Error: Missing image reference to sign."
      usage
    fi
    IMAGE="$2"
    require_cosign
    echo "Signing image: ${IMAGE}..."
    # Keyless signing via GitHub Actions OIDC (or an explicitly configured
    # cosign mode). Missing cosign must fail before any success text is
    # emitted; a local simulation is not release evidence.
    echo "Running: cosign sign --yes ${IMAGE}"
    cosign sign --yes "${IMAGE}"
    echo "Signature generated and attached successfully."
    ;;

  verify)
    if [ $# -lt 2 ]; then
      echo "Error: Missing image reference to verify."
      usage
    fi
    IMAGE="$2"
    require_cosign
    echo "Verifying image signature: ${IMAGE}..."
    echo "Running: cosign verify --certificate-identity-regexp 'https://github.com/alfloop-dev/.*' --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' ${IMAGE}"
    cosign verify --certificate-identity-regexp 'https://github.com/alfloop-dev/.*' --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' "${IMAGE}"
    echo "Verification PASSED."
    ;;

  rotate-keys)
    cat << 'EOF'
================================================================================
CONTAINER SIGNING KEY ROTATION POLICY & PROCEDURES
================================================================================
Release policy dictates container signing keys must be rotated every 90 days.

Steps to rotate local Cosign keypairs:
1. Generate new keypair:
   $ cosign generate-key-pair
2. Backup the new private key to the secure Vault:
   $ vault kv put secret/ci/cosign cosign.key=@cosign.key
3. Update GitHub Action Repository Secrets:
   - Go to Settings -> Secrets and variables -> Actions
   - Update COSIGN_PRIVATE_KEY with the contents of cosign.key
4. Publish new public key to the environments verification config.
EOF
    ;;

  revoke-key)
    cat << 'EOF'
================================================================================
CONTAINER SIGNING KEY REVOCATION & COMPROMISE RUNBOOK
================================================================================
In the event of a signing key compromise:

1. Mark key as compromised:
   - Revoke the compromised public key in Sigstore Rekor transparency log.
   - Delete the compromised secret from GitHub Actions Secrets immediately.
2. Alert Security Response Team:
   - Initiate immediate audit of all images deployed in the last 72 hours.
3. Redeploy and Re-sign:
   - Run key rotation procedure to generate a clean keypair.
   - Rebuild all active service containers from the verified source commits.
   - Sign new container images using the new key.
EOF
    ;;

  *)
    usage
    ;;
esac
