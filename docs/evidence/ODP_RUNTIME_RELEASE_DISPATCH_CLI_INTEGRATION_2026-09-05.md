# ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001 Evidence Document

**Task ID**: `ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001`  
**Owner**: `Antigravity7`  
**Reviewer**: `Claude`  
**Date**: `2026-09-05`  
**Target Branch**: `dev`  

---

## 1. Problem & Context

Two integration gaps were identified in the existing single-path Runtime Release pipeline:

1. **Manifest CLI Import Gap**:
   - `delivery_toolchain/release/release_manifest.py` failed when invoked as a standalone CLI tool without `PYTHONPATH` set (e.g. `env -u PYTHONPATH python3 delivery_toolchain/release/release_manifest.py --manifest ... --structure-only`).
   - The CLI threw `cannot load Terraform egress contract verifier: No module named 'infra'`, preventing isolated subprocess invocations (such as in CI runner environments or local scripts without project root in `PYTHONPATH`) from validating manifest structure.

2. **Workflow Dispatch Ref & Ancestry Validation Gap**:
   - The GitHub Actions `workflow_dispatch` API (`POST repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches`) requires `ref` to be a valid git branch or tag name; passing a raw 40-character commit SHA results in an HTTP 422 Unprocessable Entity error.
   - `.orchestrator/release_lease_integration.py` previously constructed the dispatch payload with `"ref": lease["candidate_sha"]`.
   - Furthermore, using a branch ref such as `dev` requires strict verification that the candidate commit SHA is an ancestor of the target branch tip, and that any intermediate commits between the candidate SHA and the branch tip are strictly evidence-only (`docs/evidence/**`) to prevent code drift from being deployed under an approval issued for an earlier commit.
   - In addition, remote ref tip consistency must be verified via read-only remote queries (`git ls-remote`), the actual `dispatch_ref` and `dispatch_ref_sha` must be recorded in the issuance receipt and activity log, and the hosted admission workflow must re-verify candidate ancestry against the workflow dispatch event `GITHUB_SHA`.

---

## 2. Changes Implemented

### 2.1 Manifest CLI Egress Verifier Import Fix
- **File**: `delivery_toolchain/release/release_manifest.py`
  - Initialized `ROOT = Path(__file__).resolve().parents[2]` at module top-level and ensured `str(ROOT)` is inserted into `sys.path`.
  - In `_sources_off_egress_contract_errors(root: Path)`, ensured `str(root)` is present in `sys.path` before attempting dynamic import of `infra.terraform.verify_terraform_sources_off_egress_contract`.
  - Preserved strict fail-closed validation when egress contract is violated or corrupted.

### 2.2 Dispatch Ref Validation, Ancestry Verification & Ref Resolution
- **File**: `.orchestrator/release_lease_integration.py`
  - Added constants `DEFAULT_DISPATCH_REF = "dev"` and `_REF_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")`.
  - Updated `issuer_settings` to parse and validate `dispatch_ref` (rejecting 40-character SHAs and invalid characters).
  - Implemented `resolve_ref_sha(root: Path, ref: str, repository: str | None = None)`:
    - Queries read-only remote references via `git ls-remote` to ensure remote tip parity.
    - Gracefully falls back to local git commit resolution (`git rev-parse`) in offline/isolated environments.
  - Implemented `check_dispatch_ref_errors(settings, candidate_sha, root, ref_resolver)`:
    - Validates ref format.
    - Resolves the commit SHA.
    - Enforces candidate ancestry and evidence-only drift verification via `check_candidate_ancestry(candidate_sha, ref_sha, root)`.
  - Updated `dispatch_runtime_release`:
    - Constructs payload with `"ref": dispatch_ref` instead of raw candidate SHA.
    - Preserves all mandatory runtime release deploy inputs: `phase="deploy"`, `environment`, `release_sha`, `task_id`, `release_lease`, `manifest_run_id`, `manifest_digest`, and four immutable component images (`api_image`, `web_image`, `worker_image`, `scheduler_image`).
  - Updated `process_release_lease_issuance`:
    - Enforces `check_dispatch_ref_errors` both pre-reservation and post-key acquisition.
    - Records `dispatch_ref` and `dispatch_ref_sha` in the issuance record, receipt, and activity log without exposing secret key material or lease bearer tokens.

### 2.3 Hosted Admission Dispatch Ancestry Gate
- **File**: `.github/workflows/deploy-dev.yml`
  - In `admission` job: Added step `Validate candidate ancestry against dispatch GITHUB_SHA` to verify via `check_candidate_ancestry` that `RELEASE_SHA` (`inputs.release_sha`) is an evidence-only ancestor of the dispatch event `GITHUB_SHA`.

---

## 3. Test Coverage & Verification

### 3.1 Subprocess & Unit Tests Added
- **File**: `tests/release/test_release_manifest_cli.py`
  - `test_sources_off_manifest_structure_only_without_pythonpath`: Executes `delivery_toolchain/release/release_manifest.py` in a clean subprocess with `env -u PYTHONPATH` and asserts exit code 0.
  - `test_sources_off_manifest_corrupted_contract_fails_closed_without_pythonpath`: Verifies fail-closed behavior when the Terraform egress contract is corrupted.
- **File**: `.orchestrator/test_release_lease_integration.py`
  - `test_dispatch_ref_raw_sha_rejected_in_settings`: Verifies 40-char SHA rejection.
  - `test_dispatch_ref_invalid_characters_rejected`: Verifies rejection of spaces and invalid characters.
  - `test_dispatch_ref_custom_branch_is_used_in_payload`: Verifies payload `"ref"` matches custom branch.
  - `test_dispatch_ref_ancestry_real_git_evidence_only_passes`: Real git test verifying that evidence-only commits between candidate SHA and `dev` pass ancestry check, and verifies `dispatch_ref` / `dispatch_ref_sha` in status record and receipt.
  - `test_resolve_ref_sha_remote_and_local`: Verifies `resolve_ref_sha` remote `git ls-remote` query and local fallback.
  - `test_dispatch_ref_ancestry_real_git_non_evidence_drift_blocks`: Verifies product code drift blocks issuance before signing key access.
  - `test_dispatch_ref_non_ancestor_blocks`: Verifies non-ancestor ref blocks issuance.
  - `test_dispatch_ref_unresolvable_blocks`: Verifies unresolvable ref blocks issuance.
  - `test_runtime_release_inputs_missing_components_raises_dispatch_error`: Verifies all component image inputs are validated.

### 3.2 Test Receipts

1. **Release Test Suite**:
   ```
   .venv/bin/pytest .orchestrator/test_release_lease_integration.py tests/release/test_release_manifest_cli.py tests/release/test_release_manifest.py
   ============================= 112 passed in 3.43s ==============================
   ```

2. **Full Release Tests**:
   ```
   .venv/bin/pytest tests/release/
   ============================= 359 passed in 12.52s =============================
   ```

3. **Orchestrator Release Lease Suite**:
   ```
   .venv/bin/pytest .orchestrator/test_release_lease*.py
   ============================== 66 passed in 1.42s ==============================
   ```

4. **Code Boundaries & Lint**:
   ```
   .venv/bin/python delivery_toolchain/governance/check_code_boundaries.py
   Code boundary checks passed for 1110 files.

   .venv/bin/python -m ruff check .orchestrator/release_lease_integration.py .orchestrator/test_release_lease_integration.py delivery_toolchain/release/release_manifest.py tests/release/test_release_manifest_cli.py tests/release/test_release_manifest.py
   All checks passed!
   ```

---

## 4. Invariants & Security Guardrails
- **No Secret Manager reads**: No actual Secret Manager API access occurred.
- **No lease minting or deployment**: Default disabled posture is preserved.
- **No gate registry mutation**: `docs/evidence/gates/` remained untouched.
- **Strict credential hygiene**: Signing keys and bearer tokens are never logged or persisted.
