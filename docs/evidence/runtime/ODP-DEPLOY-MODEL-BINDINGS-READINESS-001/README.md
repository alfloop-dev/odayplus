# ODP-DEPLOY-MODEL-BINDINGS-READINESS-001 — Runtime Evidence

Task: Restore verified production model bindings readiness
Owner: Antigravity7 · Reviewer: Claude · Date: 2026-07-29

## Overview

This directory preserves the runtime evidence and candidate smoke receipts for task `ODP-DEPLOY-MODEL-BINDINGS-READINESS-001`.

## Artifacts

- `candidate-cloud-run-smoke-run-30436771086.json`: Exact candidate Cloud Run smoke receipt captured from Deploy Dev run [30436771086](https://github.com/alfloop-dev/odayplus/actions/runs/30436771086).
- `model-bindings-readiness-verification.md`: Summary of readiness evaluation, fail-closed verification, and job queue contract disambiguation.

## Key Findings

1. **Model Readiness Fail-Closed Behavior**:
   - `/platform/health` and `/readiness` return `503 Service Unavailable` with `blockingReasons: ["PRODUCTION_MODEL_BINDINGS_UNVERIFIED"]` when `forecastops` lacks a `production` alias in MLflow.
   - `avm`, `heatzone`, and `sitescore` expose governed-disabled evidence (`DATA_CONTRACT_NOT_MATURE`), which is valid evidence and does not fabricate aliases.
   - `forecastops` requires a mature production alias. Until `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` completes backfill and `ODP-PRODUCTION-MODEL-REGISTRY-001` trains/releases a production alias, Deploy Dev correctly fails closed with 503 and rolls back traffic.

2. **Job Queue Disambiguation**:
   - `apps/api/oday_api/main.py` updated `queue_details` from `"healthy"` to `"healthy (durable postgresql job queue)"` when `bundle.is_durable` is True.
   - Smoke validator `validate_cloud_run_live_deployment.py` confirms durable PostgreSQL queue without misclassifying it as missing or in-memory.
