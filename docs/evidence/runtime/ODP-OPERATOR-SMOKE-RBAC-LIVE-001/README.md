# ODP-OPERATOR-SMOKE-RBAC-LIVE-001 — Authenticated Operator Smoke Least-Privilege Role Binding Repair

Owner: Antigravity5 · Reviewer: Codex8 · Date: 2026-08-02

## Root-Cause Analysis

Deploy Dev runs [30745285034](https://github.com/alfloop-dev/odayplus/actions/runs/30745285034) and [30747676117](https://github.com/alfloop-dev/odayplus/actions/runs/30747676117) failed in candidate smoke / post-promotion live E2E gate assertions against the authenticated operator smoke principal (`oday-dev-smoke-operator@alfaloop-data-project.iam.gserviceaccount.com`, OIDC subject `110296401444439097904`).

### Failure Symptoms
The candidate smoke and live E2E gate (`scripts/e2e/check_live_e2e_gate.py`) issue HTTP requests to verify the promoted release:
1. `GET /api/v1/operator/bootstrap` (requires `operator_console:view`) -> Returned `200 OK`
2. `GET /api/v1/learninghub/models` (requires `model:view`) -> Returned `403 Forbidden` (`dependency: auth`)
3. `GET /api/v1/external-data/ingestion-runs` (requires `integration:view`) -> Returned `403 Forbidden` (`dependency: auth`)
4. `GET /api/v1/audit/events` (requires `audit:view`) -> Returned `200 OK`

### Root Cause
Secret `ODP_AUTH_PRINCIPAL_MAP` in Secret Manager mapped subject `110296401444439097904` and email `oday-dev-smoke-operator@...` solely to `{"roles": ["operations_manager"]}`.
Per `shared/auth/rbac.py`:
- `operations_manager` carries `operator_console:view`, `operator_console:update`, `forecastops:*`, `intervention:*`, `audit:view`, and `franchisee_portal:view`.
- `operations_manager` does **not** carry `model:view` or `integration:view`.

Because `X-Operator-Role` selects a console persona and does **not** widen underlying platform grants (`apps/api/oday_api/security/dependencies.py:_select_operator_role`), passing `X-Operator-Role: operations_manager` on a single-role token caused `model:view` and `integration:view` requests to be rejected with HTTP 403.

---

## Least-Privilege Composite Role Binding Repair

### Policy & Scope Guarantees
- The business RBAC matrix in `shared/auth/rbac.py` remains **unchanged**. `operations_manager` is not bloated or granted `model:view` or `integration:view` globally.
- No `platform_admin` wildcard or header bypass is introduced.
- The dedicated smoke principal is bound to the least-privilege composite read roles required by the canonical live E2E gate contract:
  - `operations_manager` -> grants `operator_console:view` (bootstrap) and `audit:view`
  - `model_owner` -> grants `model:view` (learninghub model registry) and `audit:view`
  - `data_owner` -> grants `integration:view` (ingestion runs) and `audit:view`

### Principal Mapping Secret Schema
Secret `oday-plus-dev-auth-principal-map` (Secret Manager) JSON mapping:

```json
{
  "110296401444439097904": {
    "roles": [
      "operations_manager",
      "model_owner",
      "data_owner"
    ],
    "tenant_id": "tenant-a"
  },
  "oday-dev-smoke-operator@alfaloop-data-project.iam.gserviceaccount.com": {
    "roles": [
      "operations_manager",
      "model_owner",
      "data_owner"
    ],
    "tenant_id": "tenant-a"
  }
}
```

### OIDC & API Observed Role Consistency
1. **OIDC Subject**: `110296401444439097904` (`sub`) / `oday-dev-smoke-operator@alfaloop-data-project.iam.gserviceaccount.com` (`email`)
2. **Secret Manager Mapping**: `{"roles": ["operations_manager", "model_owner", "data_owner"]}`
3. **Workflow Expected Roles**: `operations_manager`, `model_owner`, `data_owner`
4. **API Observed System Roles**: `data_owner,model_owner,operations_manager` (`request.state.operator_system_roles`)
5. **API Active Operator Console Persona**: `operations_manager` (`request.state.operator_role_id` via `X-Operator-Role: operations_manager`)

---

## Verification & Test Results

### 1. Focused Integration & Authz Matrix Tests
- `tests/integration/test_auth_boundary_authz.py`:
  - `test_operator_smoke_principal_least_privilege_composite_roles`: **PASSED** (verifies 200/allowed on all 4 gate endpoints for composite principal).
  - `test_operator_smoke_principal_single_role_operations_manager_reproduces_403`: **PASSED** (reproduces 403 on model/integration endpoints when principal holds only `operations_manager`).
  - `test_business_rbac_matrix_operations_manager_remains_unwidened`: **PASSED** (verifies RBAC matrix for `operations_manager` is unmodified).

### 2. Live E2E Gate & Deployment Validator
- `tests/e2e/test_live_e2e_gate.py`: **PASSED**
- `tests/ops/test_cloud_run_live_deployment.py`: **PASSED**

### 3. Verification Command Output
```bash
export PATH="/home/lupin/.local/bin:$PATH"
uv run pytest tests/security/ tests/ops/test_cloud_run_live_deployment.py tests/e2e/test_live_e2e_gate.py tests/integration/test_auth_boundary_authz.py -q
# All passed cleanly!
```
