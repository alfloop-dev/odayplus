# Completion Evidence: ODP-CAP-INTERVENTION-WORKSPACE-001

## Overview
- **Task ID**: `ODP-CAP-INTERVENTION-WORKSPACE-001`
- **Title**: Complete Intervention Inbox and Detail workflow
- **Owner**: `Antigravity4`
- **Reviewer**: `Claude`
- **Phase**: P1 Product Capability
- **Summary**: Implemented Intervention Inbox and Detail assignment state transitions, RBAC authorization, optimistic concurrency stale update checks, deep-linkable query filtering, and audit evidence tracking.

---

## Acceptance Criteria Verification Matrix

| Acceptance Criterion | Implementation & Location | Verification Method | Status |
| :--- | :--- | :--- | :--- |
| **1. Inbox and detail are deep-linkable** | `active_workflow.list_cases(...)` in [`apps/api/app/routes/interventions.py`](file:///tmp/pantheon-worker-worktrees/oday-plus-supervisor-live/odp-cap-intervention-workspace-001/apps/api/app/routes/interventions.py#L265-L275) supports filtering by `store_id`, `assigned_to`, `status`, and `kind`. `GET /interventions/{id}` returns complete aggregate. | `test_api_assignment_rbac_and_inbox_deep_link_filtering` in [`tests/integration/test_intervention_workflow.py`](file:///tmp/pantheon-worker-worktrees/oday-plus-supervisor-live/odp-cap-intervention-workspace-001/tests/integration/test_intervention_workflow.py) | **PASS** |
| **2. State transitions are server-authoritative** | All state changes (open, assign, unassign, eligibility, action, conflict, submit, approve/reject, execute, outcome, evaluate, close) enforced server-side by [`InterventionWorkflow`](file:///tmp/pantheon-worker-worktrees/oday-plus-supervisor-live/odp-cap-intervention-workspace-001/modules/intervention/application/workflow.py). | `test_full_lifecycle_reaches_completed_with_causal_evidence_and_label`, `test_approval_and_execution_are_separated_and_guarded` | **PASS** |
| **3. Unauthorized actions are rejected** | `require_permission("intervention", ...)` dependency attached to all API endpoints in [`interventions.py`](file:///tmp/pantheon-worker-worktrees/oday-plus-supervisor-live/odp-cap-intervention-workspace-001/apps/api/app/routes/interventions.py). | `test_domain_route_denies_anonymous_and_writes_security_audit`, `test_api_drives_full_lifecycle_with_conflict_and_label` | **PASS** |
| **4. Stale or conflicting updates are visible** | `version: int` tracking on `Intervention` aggregate in [`lifecycle.py`](file:///tmp/pantheon-worker-worktrees/oday-plus-supervisor-live/odp-cap-intervention-workspace-001/modules/intervention/domain/lifecycle.py) + `expected_version` checks in [`workflow.py`](file:///tmp/pantheon-worker-worktrees/oday-plus-supervisor-live/odp-cap-intervention-workspace-001/modules/intervention/application/workflow.py). Mismatch surfaces HTTP 409 Conflict (`STALE_UPDATE_CONFLICT`). | `test_stale_update_concurrency_conflict_detected`, `test_api_assignment_rbac_and_inbox_deep_link_filtering` | **PASS** |
| **5. Every decision writes audit evidence** | `self._audit(...)` called for `create`, `assign`, `unassign`, `check_eligibility`, `propose_action`, `check_conflict`, `submit_for_approval`, `approve`, `reject`, `execute`, `collect_outcome`, `evaluate_effect`, `close`. | `test_assignment_lifecycle_and_audit`, `test_api_close_case_with_follow_up_and_audit` | **PASS** |

---

## Domain Model & API Enhancements

### 1. `Intervention` Aggregate (`modules/intervention/domain/lifecycle.py`)
- Added fields: `assigned_to`, `assigned_at`, `assigned_by`, `assignment_role`, `version`.
- Updated `to_dict()` and `with_transition()` to maintain state versioning and include assignment fields.

### 2. `InterventionWorkflow` Engine (`modules/intervention/application/workflow.py`)
- Added `assign_case(...)` and `unassign_case(...)` methods with terminal state guard and version checks.
- Added `list_cases(...)` query method supporting inbox filter combinations (`store_id`, `assigned_to`, `status`, `kind`).
- Integrated `_check_version(...)` across state transitions.

### 3. API Routes (`apps/api/app/routes/interventions.py`)
- Added `POST /interventions/{id}/assign` and `POST /interventions/{id}/unassign`.
- Enhanced `GET /interventions` with query filters for deep linking.
- Added 409 Conflict error handler (`STALE_UPDATE_CONFLICT`) for concurrency version collisions.

---

## Reviewer Feedback Remediation & Verification

### Fixes Applied (Round 2 Re-review)
1. **OpenAPI Artifact & Client Drift Fix**:
   - Regenerated `packages/openapi-client/openapi.json` via `scripts/openapi/export_openapi.py` (includes `/api/v1/interventions/{id}/assign` and `/unassign` paths and DTO schemas).
   - Regenerated `packages/openapi-client/src/generated/types.ts` via `scripts/openapi/generate_client.py`.
   - Verified contract test `tests/contract/test_openapi_artifact_and_client.py` passes completely.
2. **Inbox Query Input Validation (HTTP 422 vs HTTP 500)**:
   - Wrapped `active_workflow.list_cases(...)` in `apps/api/app/routes/interventions.py` with `try ... except (InterventionError, ValueError)` block.
   - Deep links with invalid status/kind values (e.g. `GET /interventions?status=NOT_A_STATUS`) now return a readable HTTP 422 Unprocessable Entity instead of an unhandled HTTP 500 Internal Server Error.
3. **Negative RBAC & Deep Link Validation Coverage**:
   - Added negative RBAC tests to `test_api_assignment_rbac_and_inbox_deep_link_filtering` in `tests/integration/test_intervention_workflow.py`, verifying unauthorized callers receive HTTP 403 Forbidden on `/assign` and `/unassign`.
   - Added negative query filter tests verifying invalid `status` and `kind` parameters return HTTP 422.

---

## Verification Executed

```bash
/tmp/pantheon-round10-clean/.venv/bin/pytest tests/contract/test_openapi_artifact_and_client.py tests/integration/test_intervention_workflow.py
```
Output:
```text
17 passed in 39.77s (contract tests)
16 passed in 1.85s (integration tests)
```
