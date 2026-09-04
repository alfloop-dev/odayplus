# ODP-SUPPLY-CHAIN-LOCKFILE-CONSISTENCY-001 Completion Evidence

## Task Summary
- **Task ID**: `ODP-SUPPLY-CHAIN-LOCKFILE-CONSISTENCY-001`
- **Title**: 修正 production npm audit 的無效 package tree
- **Owner**: Antigravity4
- **Reviewer**: Codex
- **Status**: Ready for Review
- **Base Commit**: `470f495d957e` (composed via `origin/dev` base advance)

## Root Cause Analysis
During product CI runs following dependency modifications in PR #1164, `npm audit --omit=dev` encountered `Invalid package tree` errors (HTTP 400 from npm registry audit endpoint). The investigation identified that in npm 10 monorepo workspaces:
1. When workspace manifests (e.g. `apps/web/package.json` or `packages/*`) have dependencies added or updated without full lockfile synchronization, npm's internal tree builder (`@npmcli/arborist`) generates an incomplete or disconnected package tree for production-only audits (`--omit=dev`).
2. Transitive optional dependencies (such as `@img/sharp-wasm32` under `sharp`) and unlinked workspace bindings can cause extraneous package declarations if lockfile entries lack explicit dev/optional scoping.
3. In SBOM consistency checks, `delivery_toolchain/security/generate_sbom.py` relied on standard `importlib.metadata.distributions()`, which inspected system packages when invoked directly with `python3` instead of the project `.venv`. This caused 223 component metadata differences in non-venv environments.

## Consistency & Verification Actions
1. **Manifest & Workspace Alignment**:
   Verified that root `package.json` and all workspace manifests (`apps/web`, `packages/design-tokens`, `packages/domain-types`, `packages/openapi-client`, `packages/schemas`, `packages/testkit`, `packages/ui`, `packages/ui-domain`) are fully consistent with `package-lock.json`. `npm install --package-lock-only` confirmed 0 diff and clean tree resolution.

2. **Clean Installation (`npm ci`)**:
   Executed `npm ci` cleanly:
   - Added 485 packages, audited 494 packages in workspace.
   - Exit code: `0`.
   - Vulnerabilities: `0`.

3. **Production Security Audit (`npm audit --omit=dev --audit-level=high`)**:
   Executed `npm audit --omit=dev --audit-level=high` (and `npm run audit:security`):
   - Command: `npm audit --omit=dev --audit-level=high`
   - Exit code: `0`.
   - Vulnerabilities: `0`.

4. **SBOM & Provenance Validation Fix**:
   Updated `delivery_toolchain/security/generate_sbom.py` to discover installed Python distributions in `.venv/lib/python*/site-packages` automatically when run outside an active virtual environment.
   - Environment 1 (Direct system python): `python3 delivery_toolchain/security/generate_sbom.py --check` -> Exit code: `0` (valid and up to date)
   - Environment 2 (Project venv via uv): `uv run python delivery_toolchain/security/generate_sbom.py --check` -> Exit code: `0` (valid and up to date)
   - Verified that committed `docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json` matches active `package-lock.json` and `uv.lock`.

5. **Supply Chain Security Gate Suite**:
   Executed `uv run pytest -v tests/security/test_supply_chain_security_gate.py`:
   - `test_postcss_advisory_resolved`: PASSED
   - `test_npm_audit_passes`: PASSED
   - `test_pip_audit_passes`: PASSED
   - `test_secrets_scan_passes`: PASSED
   - `test_sast_scan_passes`: PASSED
   - `test_sbom_and_provenance_present_and_valid`: PASSED
   - `test_sign_images_script_executable`: PASSED
   - `test_stale_lockfiles_rejected_negative`: PASSED
   - `test_generated_client_drift_rejected_negative`: PASSED
   - `test_vulnerable_fixtures_rejected_negative`: PASSED
   - `test_unsigned_images_rejected_negative`: PASSED
   - `test_invalid_provenance_rejected_negative`: PASSED
   - `test_leaked_test_secrets_rejected_negative`: PASSED
   - Summary: `13 passed`.

6. **Safety Invariants**:
   No security thresholds were lowered, no waivers or skips were added, and no non-200 responses were treated as successes.
