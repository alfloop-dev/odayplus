# ODP-DEPLOY-MODEL-BINDINGS-READINESS-001 — Runtime Evidence

Task: Restore verified production model bindings readiness
Owner: Antigravity2 · Reviewer: Claude · Date: 2026-08-19

## Overview

This directory preserves the runtime evidence, candidate smoke receipts, and readiness contract verification for task `ODP-DEPLOY-MODEL-BINDINGS-READINESS-001`.

## Artifacts

- `candidate-cloud-run-smoke-run-30436771086.json`: Exact candidate Cloud Run smoke receipt captured from Deploy Dev run [30436771086](https://github.com/alfloop-dev/odayplus/actions/runs/30436771086).
- `model-bindings-readiness-verification.md`: Complete breakdown of all 8 candidate smoke failing checks, root causes, task ownership/delegation, rollback evidence, and job queue contract remediation.

## Key Findings & Receipt Analysis

1. **Model Readiness Fail-Closed Behavior (503 & live_data_mode)**:
   - `/platform/health` and `/readiness` return `503 Service Unavailable` with `blockingReasons: ["PRODUCTION_MODEL_BINDINGS_UNVERIFIED"]` when `forecastops` lacks a `production` alias in MLflow.
   - `avm`, `heatzone`, and `sitescore` expose governed-disabled evidence (`DATA_CONTRACT_NOT_MATURE`), which is valid evidence and does not fabricate aliases.
   - `forecastops` requires a mature production alias. Until `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` completes backfill and `ODP-PRODUCTION-MODEL-REGISTRY-001` trains/releases a production alias, Deploy Dev correctly fails closed with 503 and rolls back candidate traffic.

2. **Job Queue Disambiguation**:
   - `apps/api/oday_api/main.py` updated `queue_details` from `"healthy"` to `"healthy (durable postgresql job queue)"` when `bundle.is_durable` is True.
   - Smoke validator `validate_cloud_run_live_deployment.py` confirms durable PostgreSQL queue without misclassifying it as missing or in-memory. Regression test added to `tests/ops/test_cloud_run_live_deployment.py`.

3. **Operator Bootstrap Provenance (data_mode=degraded)**:
   - `smoke:/api/v1/operator/bootstrap:provenance` and `smoke:/api/v1/operator/bootstrap:read_provenance` report `data_mode=degraded` due to `_meta.dataMode` in `modules/opsboard/application/operator_state.py` when model readiness is unverified.
   - Owned by `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` & `ODP-PRODUCTION-MODEL-REGISTRY-001` / `ODP-OPERATOR-LIVE-PREFLIGHT-001`.

4. **Web Protected Redirect (307)**:
   - `smoke:web:/operator` status 307 delegated to `ODP-DEPLOY-WEB-PROTECTED-REDIRECT-001`.

5. **Traffic Rollback Evidence**:
   - Deploy Dev run 30436771086 candidate smoke failure triggered automatic traffic rollback in `scripts/deploy_cloud_run_waji.sh`. Live traffic remained 100% on the stable revision and candidate revision received 0% traffic.
