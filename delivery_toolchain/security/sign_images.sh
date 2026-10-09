#!/usr/bin/env bash
# Container Image Signing & Verification policy and procedures.
# This script serves as a deployment gate helper and documents rotation / revocation.

set -euo pipefail

# Print help/usage
usage() {
  echo "Usage: $0 [sign|attest|verify|rotate-keys|revoke-key] [image-reference] [SBOM-path]"
  echo ""
  echo "Commands:"
  echo "  sign <image>         Sign container image using Cosign keyless/OIDC or local key"
  echo "  attest <image> <SBOM-path>  Attach a CycloneDX SBOM using Cosign keyless/OIDC"
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

# Retry only credential acquisition failures known to precede publication.
# Fixed three attempts, with 2s/4s backoff; no dispatcher-controlled policy.
# Never print Cosign diagnostics: OIDC responses may contain bearer credentials.
# Verification and registry publication are deliberately NOT retried here.
cosign_with_oidc_retry() (
  umask 077
  diagnostic="$(mktemp)" || return 1
  trap 'rm -f "${diagnostic}"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' HUP TERM
  local attempt rc
  for attempt in 1 2 3; do
    if cosign "$@" >"${diagnostic}" 2>&1; then
      return 0
    else
      rc=$?
    fi
    # A permanent authorization/trust failure wins even if another line looks
    # transient. Unknown errors fail closed, preserving the actual exit code.
    if grep -Eiq 'unauthorized|unauthenticated|forbidden|denied|invalid.*(token|audience|issuer)|expired token|audience mismatch|issuer mismatch|trust|certificate|x509|signature verification|(^|[^0-9])(400|401|403)([^0-9]|$)' "${diagnostic}" ||
       ! grep -Eq "fetching ambient OIDC credentials: (invalid character 'u' looking for beginning of value|unexpected EOF|.*(429 Too Many Requests|500 Internal Server Error|502 Bad Gateway|503 Service Unavailable|504 Gateway Timeout))" "${diagnostic}"; then
      echo "Error: cosign $1 failed (attempt ${attempt}/3, exit ${rc}); non-retryable diagnostics withheld." >&2
      return "${rc}"
    fi
    if [ "${attempt}" -eq 3 ]; then
      echo "Error: cosign $1 transient OIDC failure exhausted 3 attempts (exit ${rc}); diagnostics withheld." >&2
      return "${rc}"
    fi
    echo "Cosign $1 transient OIDC failure; retrying after $((attempt * 2))s (attempt ${attempt}/3)." >&2
    sleep "$((attempt * 2))" || return $?
  done
)

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
    cosign_with_oidc_retry sign --yes "${IMAGE}"
    echo "Signature generated and attached successfully."
    ;;

  attest)
    if [ $# -ne 3 ] || [ ! -f "$3" ]; then
      echo "Error: attest requires an image and an existing SBOM file." >&2
      usage
    fi
    require_cosign
    cosign_with_oidc_retry attest --yes --type cyclonedx --predicate "$3" "$2"
    echo "SBOM attestation generated and attached successfully."
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
