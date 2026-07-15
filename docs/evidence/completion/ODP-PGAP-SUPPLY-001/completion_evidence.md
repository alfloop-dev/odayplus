# ODP-PGAP-SUPPLY-001: Enforce Supply-Chain Security Gates - Completion Evidence

This document serves as the formal completion evidence and runtime proof for the supply-chain security gates task `ODP-PGAP-SUPPLY-001`.

## 1. Resolved PostCSS Advisory & Node dependency scan

The moderate vulnerability in transitive dependency `postcss` has been fully resolved by updating `postcss` to `8.5.19` (which contains the fix for the CSS Stringify XSS vulnerability).

Run check:
```bash
$ npm audit
found 0 vulnerabilities
```

## 2. Python Dependency Scan

Python dependencies are audited fail-closed using `pip-audit` locally in the virtual environment.

Run check:
```bash
$ uv run --with pip-audit pip-audit --local
No known vulnerabilities found
```

## 3. Secret and SAST Scanning

- **Secret Scanner**: Implemented under [secret_scan.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-pgap-supply-001/scripts/security/secret_scan.py). It detects private keys, high-entropy generic tokens/secrets, and blocks build violations. Any bypass in test paths requires an explicit `# pragma: allowlist-secret` justification; legacy or general bypass words like `approved` or `example` are rejected.
- **SAST Scanner**: Implemented under [sast_scan.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-pgap-supply-001/scripts/security/sast_scan.py). It runs `bandit` with pre-approved skips for existing mock hashing.

## 4. Software Bill of Materials (SBOM) & Signed Provenance

Builds produce a CycloneDX 1.5 JSON SBOM containing both Python and Node dependencies, dynamically generated under [sbom.json](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-pgap-supply-001/docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json).

It records:
- Git Commit SHA
- Components specification (`purl:pkg:npm/*`, `purl:pkg:pypi/*`)
- `sbom-content-digest` hash of the components to verify content integrity.

To ensure this committed SBOM never goes stale, a dedicated regression test (`test_sbom_and_provenance_present_and_valid`) dynamically generates the SBOM from the active `package-lock.json` and `uv.lock` and asserts that it matches the committed copy exactly, failing closed if any dependency drifts.

## 5. Image Signing & Verification Policy

The [sign_images.sh](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-pgap-supply-001/scripts/security/sign_images.sh) script provides the runtime procedures for image signing (using Cosign keyless OIDC or local keys) and signature verification.
- Verification now invokes `cosign verify` for real, enforcing validation of image signatures and certificates.
- Image signing is simplified to a single signature on the built image digest.
- Image signatures are verified immediately *before* rollout in both the dev (`deploy-dev.yml`) and staging/release (`deploy-staging.yml`) deployment pipelines.

## 6. Verification Results

All 48 security tests pass successfully (42 regression tests + 6 negative tests verifying fail-closed rejections):
```bash
$ uv run pytest tests/security
................................................                         [100%]
48 passed in 23.42s
```

Negative tests specifically assert fail-closed rejection for:
1. Stale lockfiles (`test_stale_lockfiles_rejected_negative`)
2. Generated-client drift (`test_generated_client_drift_rejected_negative`)
3. Vulnerable fixtures (`test_vulnerable_fixtures_rejected_negative`)
4. Unsigned images (`test_unsigned_images_rejected_negative`)
5. Invalid provenance (`test_invalid_provenance_rejected_negative`)
6. Leaked test secrets (`test_leaked_test_secrets_rejected_negative`)

All E2E release gate static checks pass:
```bash
$ python3 scripts/e2e/check_product_release_gate.py
Product release gate static checks passed.
```
