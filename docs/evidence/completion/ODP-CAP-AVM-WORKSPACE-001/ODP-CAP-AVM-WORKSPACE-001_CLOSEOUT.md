# ODP-CAP-AVM-WORKSPACE-001 Closeout Evidence: AVM Queue Valuation Card and Data Room

- **Task ID**: ODP-CAP-AVM-WORKSPACE-001
- **Title**: Complete AVM Queue Valuation Card and Data Room
- **Owner**: Antigravity5
- **Reviewer**: Claude2
- **Phase**: P1 Product Capability
- **Generated At**: 2026-08-08T09:47:35Z

---

## Executive Summary

Task `ODP-CAP-AVM-WORKSPACE-001` delivers the AVM (Automated Valuation Model) workspace capabilities, covering valuation request creation, multi-lens estimation (income, asset, market, blended), finance-legal approval state machine, Data Room assembly & audit-tracked exports, and explicit `GOVERNED_DISABLED` capability bindings for live release safety without waiting for raw transaction outcomes or model training.

---

## Acceptance Criteria Verification

### 1. Governed-Disabled State Is Explicit
- **Requirement**: `governed-disabled state is explicit`
- **Implementation**:
  - `PRODUCTION_MODEL_CONTRACTS["avm"]` in `models/shared_ml/production_runtime.py` explicitly marks `is_governed_disabled = True` with `GovernedDisabledBinding`.
  - Service metadata endpoints (`/api/v1/operator/models/capabilities`) publish `governedDisabled: true` and `governedDisabledEvidence` payloads.
  - Runtime live gate (`tests/e2e/test_live_e2e_gate.py`) enforces that AVM cannot claim an unapproved production model alias while in `governed_disabled` status.
- **Verification**: `tests/e2e/test_live_e2e_gate.py` (38 passed).

### 2. No Unlined Estimate Is Presented As Production Ready
- **Requirement**: `no unlined estimate is presented as production ready`
- **Implementation**:
  - All AVM valuation reports (`ValuationReport` in `modules/avm/domain/valuation.py`) require explicit lens breakdowns (`income`, `asset`, `market`, `blended`) with exact line-item evidence (`source_snapshot_ids`, `normalized_gm`, asset book values, equipment fair values, lease liabilities, market comp multiples, liquidity discounts).
  - Unapproved/unlined estimations are explicitly typed with evidence status (e.g. `missing_default_multiple`) and require explicit normalization and valuation steps.
- **Verification**: `modules/avm/tests/test_avm_production_execution.py` and `tests/integration/test_avm_valuation.py`.

### 3. Sensitive Fields and Exports Obey Scope
- **Requirement**: `sensitive fields and exports obey scope`
- **Implementation**:
  - `APIRouter` endpoints in `apps/api/app/routes/avm.py` enforce RBAC permission checks via `require_permission("avm", Action.EXPORT, engine=authz_engine)`.
  - Data room export (`POST /avm/cases/{case_id}/dataroom/export`) strictly requires `Action.EXPORT` and tenant scoping (`tenant_id`).
  - Export actions log actor, reason, correlation ID, and timestamp into immutable audit logs.
- **Verification**: `test_finance_approval_state_gates_versions_and_dataroom_export` in `tests/integration/test_avm_valuation.py`.

### 4. Incomplete Data Room Cannot Be Approved
- **Requirement**: `incomplete data room cannot be approved`
- **Implementation**:
  - `build_dataroom` strictly gates on `ValuationCaseStatus.APPROVED` and `report.finance_approval is not None`.
  - Attempting to build or export a Data Room prior to finance approval yields HTTP 422 `AVMError("finance approval required before data room")`.
  - Creator self-approval is rejected (`AVMError("case creator cannot approve their own valuation case")`).
  - Data room completeness is computed dynamically (`completeness == 1.0`, `is_complete == True`).
- **Verification**: `test_finance_approval_state_gates_versions_and_dataroom_export` in `tests/integration/test_avm_valuation.py`.

### 5. All Decisions Are Audited
- **Requirement**: `all decisions are audited`
- **Implementation**:
  - Every state transition emits structured `AuditEvent` records into `InMemoryAuditLog`:
    - `avm.case_created.v1`
    - `avm.normalized.v1`
    - `avm.valued.v1`
    - `avm.finance_approved.v1`
    - `avm.dataroom_ready.v1`
    - `avm.dataroom_exported.v1`
  - Audit events record correlation IDs, actor identity, action resource, decision reasons, and execution metadata.
- **Verification**: `test_avm_api_runs_e2e_valuation_dataroom_export_and_audit` in `tests/integration/test_avm_valuation.py`.

---

## Test Execution Summary

The following test suites were executed to verify the deliverable:

1. **AVM Module Unit Tests**:
   - `modules/avm/tests/test_avm_production_execution.py`
   - `modules/avm/tests/test_lifelines_liquidity_survival.py`
   - *Result*: 2 passed.

2. **AVM Valuation & Integration Tests**:
   - `tests/integration/test_avm_valuation.py`
   - *Result*: 10 passed (including API e2e, durable restart, versioning, RBAC, and audit verification).

3. **Live E2E & Governance Gate Tests**:
   - `tests/e2e/test_live_e2e_gate.py`
   - *Result*: 38 passed (verifying AVM governed-disabled capability bindings and fail-closed checks).

---

## Key Code Artifacts

- `apps/api/app/routes/avm.py`: AVM FastAPI router (`/avm/cases`, `/value`, `/finance-approval`, `/dataroom`, `/dataroom/export`).
- `modules/avm/domain/valuation.py`: Domain models (`ValuationCase`, `ValuationReport`, `LensValuation`, `ApprovalDecision`, `DataRoom`).
- `modules/avm/application/valuation.py`: Application service (`AVMService`) managing state transitions, versioning, approvals, and data room generation.
- `tests/integration/test_avm_valuation.py`: Integration test suite for end-to-end AVM flows.
