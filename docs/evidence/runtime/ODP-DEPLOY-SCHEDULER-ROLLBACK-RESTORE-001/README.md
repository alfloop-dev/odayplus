# ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001: Cloud Scheduler Trigger Restoration Evidence

Owner: Antigravity6 · Reviewer: Codex3 · Phase: Live Runtime Remediation · 2026-08-02

Reproduction, diagnosis, and remediation of the Cloud Scheduler trigger restoration defect during Deploy Dev rollback.

## 1. Reproduction & Diagnosis

Deploy Dev runs `30745285034` and `30747676117` failed during rollback when attempting to restore the two Cloud Scheduler triggers (`oday-worker-trigger` and `oday-scheduler-trigger`).

### Observed Root Cause:
1. **OIDC Token Schema Incompatibility in Validator:**
   `scripts/deployment/cloud_scheduler_trigger.py` enforced `REQUIRED_FIELDS` containing strictly:
   - `httpTarget.oauthToken.serviceAccountEmail`
   - `httpTarget.oauthToken.scope`

   However, production Cloud Run Job triggers in Cloud Scheduler use **`oidcToken`** (`httpTarget.oidcToken.serviceAccountEmail` and `httpTarget.oidcToken.audience`). When `gcloud scheduler jobs describe` returned an OIDC trigger snapshot, `cloud_scheduler_trigger.py` threw `ValueError: scheduler snapshot is missing httpTarget.oauthToken.serviceAccountEmail` during validation and field extraction.

2. **Hardcoded OAuth Flags & Loss of Contract Fields:**
   `scripts/deployment/cloud_run_release_traffic.sh` hardcoded `--oauth-service-account-email` and `--oauth-token-scope` in `restore_scheduler_trigger`, causing `gcloud scheduler jobs update http` to fail when updating OIDC-configured triggers. Furthermore, the previous script ignored custom HTTP methods, body payloads, headers, retry policy (`maxRetryAttempts`, backoff bounds, doublings), and job state (`PAUSED` vs `ENABLED`).

3. **Absence & Multi-Trigger Error Isolation:**
   When a trigger was absent before deployment (`exists: false`), restoration did not safely delete candidate triggers created during deploy. Additionally, a failure restoring one trigger could abort execution before attempting the second trigger.

## 2. Remediation Architecture

### A. Python Helper (`scripts/deployment/cloud_scheduler_trigger.py`):
- **Flexible Auth Token Contract:** Supports both `oidcToken` (`serviceAccountEmail`, `audience`) and `oauthToken` (`serviceAccountEmail`, `scope`).
- **Full Contract Argument Generator (`restore-args`):** Generates null-terminated gcloud arguments matching the exact captured pre-deploy trigger contract (`--location`, `--project`, `--schedule`, `--time-zone`, `--uri`, `--http-method`, `--message-body`, `--headers`, OIDC/OAuth flags, `--max-retry-attempts`, `--max-retry-duration`, `--min-backoff-duration`, `--max-backoff-duration`, `--max-doublings`).
- **Redacted Readback Comparison (`compare` & `redact`):** Compares pre-deploy snapshot and post-rollback describe readback after normalizing dynamic timestamps (`userUpdateTime`, `lastAttemptTime`, `status`). Returns 0 on exact redacted equality or 1 on configuration drift.

### B. Deploy Shell Helper (`scripts/deployment/cloud_run_release_traffic.sh`):
- **Idempotent Restoration (`restore_scheduler_trigger`):**
  - If snapshot indicates `exists: false`, executes `gcloud scheduler jobs delete` (silently succeeding if absent).
  - Dynamically selects `update` or `create` based on current trigger existence.
  - Safely reads null-delimited restore flags via `mapfile -d ''`.
  - Restores paused state (`gcloud scheduler jobs pause` if pre-deploy state was `PAUSED`).
  - Performs post-restore describe readback equality check via `cloud_scheduler_trigger.py compare`.
  - Emits per-trigger diagnostic log messages and isolates failures between triggers.

## 3. Verification & Live Rollback Drill

### Unit & Integration Suite:
`.venv/bin/pytest -q tests/ops/test_cloud_run_live_deployment.py -k "scheduler_trigger"`
Passed 6/6 tests:
1. `test_scheduler_trigger_restore_uses_recorded_target_and_schedule` (OAuth restoration)
2. `test_scheduler_trigger_restore_supports_oidc_token` (OIDC restoration & retry bounds)
3. `test_scheduler_trigger_restore_handles_paused_state` (`PAUSED` job state restoration)
4. `test_scheduler_trigger_restore_deletes_absent_pre_deploy_trigger` (`exists: false` cleanup)
5. `test_scheduler_trigger_restore_partial_failure_continues_and_reports_diagnostics` (per-trigger isolation)
6. `test_scheduler_trigger_compare_verifies_redacted_equality_and_detects_drift` (readback equality assertion)

### Static Checks:
- `.venv/bin/ruff check scripts/deployment/cloud_scheduler_trigger.py tests/ops/test_cloud_run_live_deployment.py` — Passed cleanly.
- `bash -n scripts/deployment/cloud_run_release_traffic.sh` — Clean.
- `bash -n scripts/deploy_cloud_run_waji.sh` — Clean.

### Live Drill Evidence Receipts:
- Pre-deploy snapshots: `pre-deploy-worker-trigger.json`, `pre-deploy-scheduler-trigger.json`
- Post-rollback readback: `post-rollback-readback-worker.json`, `post-rollback-readback-scheduler.json`
- Readback equality verification: `readback-equality-verification.json` (`PASSED_ZERO_DRIFT`)
