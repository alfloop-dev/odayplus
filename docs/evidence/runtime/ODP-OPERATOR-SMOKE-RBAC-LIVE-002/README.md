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

- **Replacement Deploy Dev Run**: `30809922826` on exact current `origin/dev` SHA `5a1aee5b0a9d6fdd2311b4cbd3569527c5f89837`.
- **Superseded Run**: Prior run `30809256501` (on `b147631c`) was cancelled by dev merge PR #602 (`5a1aee5b0a9d6fdd2311b4cbd3569527c5f89837`).
- **Verified Composite Roles & Endpoints**:
  - `ODP_OPERATOR_SMOKE_ROLE`: `operations_manager,model_owner,data_owner`
  - Secret Manager (`oday-plus-dev-auth-principal-map` v3): `operations_manager,model_owner,data_owner` for subject `110296401444439097904` and email `oday-dev-smoke-operator@alfaloop-data-project.iam.gserviceaccount.com`.
  - `GET /api/v1/operator/bootstrap`: `200 OK` (`operator_console:view`)
  - `GET /api/v1/learninghub/models`: `200 OK` (`model:view`, former 403 resolved)
  - `GET /api/v1/external-data/ingestion-runs`: `200 OK` (`integration:view`, former 403 resolved)
  - `GET /api/v1/audit/events`: `200 OK` (`audit:view`)
  - Anonymous / unauthenticated access: `401 Unauthorized` / `403 Forbidden` retained.
- **Local Suite Verification**:
  - `python3 -m pytest tests/integration/test_auth_boundary_authz.py tests/e2e/test_live_e2e_gate.py tests/ops/test_cloud_run_live_deployment.py -q` passed (147 passed).
  - `python3 -m ruff check tests/integration/test_auth_boundary_authz.py tests/e2e/test_live_e2e_gate.py tests/ops/test_cloud_run_live_deployment.py docs/evidence/runtime/ODP-OPERATOR-SMOKE-RBAC-LIVE-002/` passed (0 errors).
  - `git diff --stat origin/dev...HEAD` clean evidence-only diff.
