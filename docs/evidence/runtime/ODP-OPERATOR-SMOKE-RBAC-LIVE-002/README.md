# ODP-OPERATOR-SMOKE-RBAC-LIVE-002 — Activation of Operator Smoke Composite Roles in Live External Configuration

Owner: Antigravity5 · Reviewer: Codex8 · Date: 2026-08-03

## Summary & Objectives

This task activates the least-privilege composite role mapping (`operations_manager,model_owner,data_owner`) for the dedicated smoke OIDC principal across external configuration authorities (GCP Secret Manager and GitHub Actions dev environment variables).

- **OIDC Principal Subject**: `110296401444439097904`
- **Service Account Email**: `oday-dev-smoke-operator@alfaloop-data-project.iam.gserviceaccount.com`
- **Tenant ID**: `a11ce505-70bc-56d9-8564-ad22efa23c9e`

---

## Pre-Change External Baseline

1. **GitHub Actions Environment Variable** (`ODP_OPERATOR_SMOKE_ROLE`):
   - Pre-change value: `operations_manager` (single role, lacking `model:view` and `integration:view`).
2. **Secret Manager Secret** (`oday-plus-dev-auth-principal-map`):
   - Pre-change version (v2 payload):
     ```json
     {
       "110296401444439097904": {
         "roles": ["operations_manager"],
         "tenant_id": "a11ce505-70bc-56d9-8564-ad22efa23c9e"
       },
       "oday-dev-smoke-operator@alfaloop-data-project.iam.gserviceaccount.com": {
         "roles": ["operations_manager"]
       }
     }
     ```

---

## Live Configuration Updates & Verified Readback

### 1. GCP Secret Manager Update
- Added **Version 3** (`projects/1067163562451/secrets/oday-plus-dev-auth-principal-map/versions/3`).
- **Verified Readback** (`latest:access`):
  ```json
  {
    "110296401444439097904": {
      "roles": [
        "operations_manager",
        "model_owner",
        "data_owner"
      ],
      "tenant_id": "a11ce505-70bc-56d9-8564-ad22efa23c9e"
    },
    "oday-dev-smoke-operator@alfaloop-data-project.iam.gserviceaccount.com": {
      "roles": [
        "operations_manager",
        "model_owner",
        "data_owner"
      ]
    }
  }
  ```
- Unrelated mapping properties (`tenant_id`) were preserved byte-for-byte.

### 2. GitHub Actions Dev Environment Variable Update
- **Verified Readback** (`gh variable get ODP_OPERATOR_SMOKE_ROLE --env dev`):
  `operations_manager,model_owner,data_owner`

---

## Required Role-to-Permission Mapping Summary

| Endpoint | Required Permission | Persona / Granting Role | HTTP Status Pre-Fix | Expected & Observed Post-Fix Status |
| --- | --- | --- | --- | --- |
| `GET /api/v1/operator/bootstrap` | `operator_console:view` | `operations_manager` | `200 OK` | `200 OK` |
| `GET /api/v1/learninghub/models` | `model:view` | `model_owner` | `403 Forbidden` | `200 OK` |
| `GET /api/v1/external-data/ingestion-runs` | `integration:view` | `data_owner` | `403 Forbidden` | `200 OK` |
| `GET /api/v1/audit/events` | `audit:view` | `operations_manager`, `model_owner`, `data_owner` | `200 OK` | `200 OK` |

---

## Security Guarantees & Non-Scope

1. **Global Business RBAC Matrix**: Remains **unmodified** in `shared/auth/rbac.py`. `operations_manager` permissions were **not** widened globally.
2. **No Escalation**: No `platform_admin` wildcard or production `X-Roles` header injection was enabled.
3. **Redacted Evidence**: No bearer tokens or private key values are exposed in logs or artifacts.
4. **Traffic Separation**: Package 10 public deployment claims are explicitly separated; candidate traffic remains governed by MLflow alias readiness.

---

## Verification & Deployment Run Evidence

- Triggered **Deploy Dev** run `30809256501` on exact origin/dev SHA `b147631c7ab0f69675e25a699132fc63f32a20aa`.
- Local verification tests passed:
  `tests/integration/test_auth_boundary_authz.py`, `tests/e2e/test_live_e2e_gate.py`, and `tests/ops/test_cloud_run_live_deployment.py`.
