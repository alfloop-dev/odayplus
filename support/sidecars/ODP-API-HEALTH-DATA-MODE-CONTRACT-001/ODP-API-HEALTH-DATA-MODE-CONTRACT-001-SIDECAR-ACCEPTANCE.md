# Support Sidecar Acceptance Packet: ODP-API-HEALTH-DATA-MODE-CONTRACT-001

- **Task ID**: `ODP-API-HEALTH-DATA-MODE-CONTRACT-001-SIDECAR-ACCEPTANCE`
- **Parent Task**: `ODP-API-HEALTH-DATA-MODE-CONTRACT-001`
- **Helper Kind**: `acceptance_packet`
- **Owner**: `Antigravity7`
- **Reviewer**: `Antigravity`
- **Created At**: `2026-08-02`
- **Scope Restriction**: Support artifacts under `support/sidecars/ODP-API-HEALTH-DATA-MODE-CONTRACT-001/` only. Zero L1 canonical documents, model capability gates, or core contracts modified.

---

## 1. Executive Summary & Context

This sidecar acceptance packet provides the independent support documentation, acceptance checklist, dependency map, and verification summary for parent task **`ODP-API-HEALTH-DATA-MODE-CONTRACT-001`** ("Repair live health data-mode contract without weakening fail-closed").

### Background & Problem Statement
During automated deployment validation of Cloud Run candidate revisions (e.g. Deploy Dev runs `30726115566` and `30727732267`), immutable API/Web candidate containers were deployed at 0% traffic for smoke verification. The candidate smoke test failed because `/platform/health` and `/readiness` endpoints returned responses where `data_mode` was missing at the top level or formatted in a nested envelope shape (`modes.data.mode` / `details.data.mode`) that the deployment validator did not recognize. As a result, Cloud Run deployment validation failed and rolled back candidate traffic, blocking **`ODP-P10-DEV-REDEPLOY-VERIFY-001`**.

---

## 2. Parent Task Deliverables & Scope

Parent task `ODP-API-HEALTH-DATA-MODE-CONTRACT-001` addressed the root cause across three layers without weakening security or fail-closed guarantees:

1. **API Endpoint Contract (`apps/api/oday_api/main.py`)**:
   - Updated `/platform/health` and `/readiness` route handlers to explicitly expose a top-level `data_mode` string field matching `modes['data']['mode']`.

2. **Deployment Validator Envelope Resolution (`scripts/deployment/validate_cloud_run_live_deployment.py`)**:
   - Enhanced `validator._declared_data_mode` to inspect canonical top-level `data_mode`, nested `modes.data.mode`, `details.data.mode`, and `meta` envelope shapes.
   - Retained strict rejection of missing, empty, fixture, or mock data mode values.

3. **Test Suite & Integration Coverage (`tests/ops/test_cloud_run_live_deployment.py`)**:
   - Added `test_declared_data_mode_handles_all_envelope_shapes` and integrated test cases for live, unavailable, and invalid data modes.

---

## 3. Acceptance Checklist

| Criteria / Requirement | Parent Implementation | Verification Method | Status |
| --- | --- | --- | --- |
| **Top-Level `data_mode` in Health Endpoints** | Exposed in `/platform/health` and `/readiness` in `apps/api/oday_api/main.py` | `pytest tests/reliability/test_health_endpoints.py` | **PASSED** |
| **Validator Envelope Flexibility** | `validator._declared_data_mode` handles top-level, `modes.data.mode`, `details.data.mode`, and `meta` | `pytest tests/ops/test_cloud_run_live_deployment.py` | **PASSED** |
| **Fail-Closed Guarantee** | Missing, mock, or fixture data modes trigger deployment rejection | `test_declared_data_mode_handles_all_envelope_shapes` | **PASSED** |
| **Code Hygiene & Standards** | Zero linting errors or formatting issues | `ruff check` & `git diff --check` | **PASSED** |
| **L1 Canonical & Contract Preservations** | Untouched canonical files | Scope audit | **PASSED** |

---

## 4. Upstream & Downstream Dependency Map

```
                  ┌─────────────────────────────────────────────────────────┐
                  │       ODP-API-HEALTH-DATA-MODE-CONTRACT-001            │
                  │   (Repair health data-mode contract & validator)       │
                  └───────────────────────────┬─────────────────────────────┘
                                              │
                    ┌─────────────────────────┴─────────────────────────┐
                    ▼                                                   ▼
┌───────────────────────────────────────┐           ┌───────────────────────────────────────┐
│     ODP-P10-DEV-REDEPLOY-VERIFY-001   │           │     Cloud Run Deploy Dev Pipeline     │
│ (Redeploy dev & verify Package 10)    │           │   (.github/workflows/deploy-dev.yml)   │
└───────────────────────────────────────┘           └───────────────────────────────────────┘
```

- **Upstream Dependencies**: None.
- **Downstream Blocked Tasks**:
  - `ODP-P10-DEV-REDEPLOY-VERIFY-001`: Deploy Dev verification was blocked until live candidate smoke check passes on `/platform/health` and `/readiness`.
- **Related Governance Tasks**:
  - `ODP-ORCH-TASK-PR-DISCOVERY-001`: Immutable task ref PR discovery in Supervisor.
  - `ODP-ORCH-DONE-DELIVERY-PROVENANCE-001`: Delivery provenance tracking.

---

## 5. Scope & Boundary Conformance Matrix

| File / Component Path | Primary Layer | Sidecar Slice Role | Disposition |
| --- | --- | --- | --- |
| `apps/api/oday_api/main.py` | API Runtime | Parent Task (`ODP-API-HEALTH-DATA-MODE-CONTRACT-001`) | **Preserved / Intact** |
| `scripts/deployment/validate_cloud_run_live_deployment.py` | Deployment Ops | Parent Task (`ODP-API-HEALTH-DATA-MODE-CONTRACT-001`) | **Preserved / Intact** |
| `tests/ops/test_cloud_run_live_deployment.py` | Ops Test Suite | Parent Task (`ODP-API-HEALTH-DATA-MODE-CONTRACT-001`) | **Preserved / Intact** |
| `support/sidecars/ODP-API-HEALTH-DATA-MODE-CONTRACT-001/ODP-API-HEALTH-DATA-MODE-CONTRACT-001-SIDECAR-ACCEPTANCE.md` | Sidecar Support | This Task (`ODP-API-HEALTH-DATA-MODE-CONTRACT-001-SIDECAR-ACCEPTANCE`) | **ADDED** |
| L1 Canonical Architecture Documents | Platform Policy | None | **STRICTLY UNTOUCHED** |

---

## 6. Verification Summary & Commands

The parent task fixes and this sidecar support artifact were verified with the following execution commands:

```bash
# 1. Focused Ops & Deployment Validator Test Suite
/home/lupin/oday-plus/.venv/bin/pytest -q tests/ops/test_cloud_run_live_deployment.py

# 2. Comprehensive Health & Reliability Test Suite
/home/lupin/oday-plus/.venv/bin/pytest -q tests/reliability/test_health_endpoints.py tests/integration/test_operator_live_provenance_health.py

# 3. Static Code Quality & Formatting Audit
/home/lupin/oday-plus/.venv/bin/ruff check apps/api/oday_api/main.py scripts/deployment/validate_cloud_run_live_deployment.py tests/ops/test_cloud_run_live_deployment.py tests/reliability/test_health_endpoints.py
git diff --check
```

**Verification Results**:
- `test_cloud_run_live_deployment.py`: 12 passed.
- `test_health_endpoints.py` & `test_operator_live_provenance_health.py`: 365 passed.
- `ruff check`: All checks passed!
- `git diff --check`: Clean (0 whitespace errors).

---

## 7. Handoff & Reviewer Summary

- **Artifact Path**: `support/sidecars/ODP-API-HEALTH-DATA-MODE-CONTRACT-001/ODP-API-HEALTH-DATA-MODE-CONTRACT-001-SIDECAR-ACCEPTANCE.md`
- **Assigned Reviewer**: `Antigravity`
- **Handoff Note**: This acceptance packet documents the background, deliverables, acceptance matrix, dependency map, and test verification suite for `ODP-API-HEALTH-DATA-MODE-CONTRACT-001`. No canonical documents or core contracts were modified in this sidecar slice. Ready for review and handoff.
