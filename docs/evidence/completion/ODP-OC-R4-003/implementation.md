# ODP-OC-R4-003 Implementation

Task: Implement Store Ops R4 four-light summary and durable issue lifecycle  
Owner: Codex  
Reviewer: Claude2  
Date: 2026-07-14

## Delivered Scope

- Added Store Ops application service with in-memory and durable SQLite-backed repositories.
- Added `/api/v1/operator/store-ops/*` FastAPI routes for:
  - four-light summary and issue queue filtering
  - issue detail and evidence reads
  - lifecycle writes: triage, assign, actions, field-report, outcome, escalation, reply review, transfer
  - camera purpose recording before camera evidence unlock
- Wired Store Ops persistence into `build_persistence()` and `create_app()`.
- Updated the Store Ops workspace to load queue/evidence/audit/store state from the Store Ops API with fixture fallback.
- Added four-light quick filter chips that send deterministic query params:
  - `light=<demand|operations|staffing|margin>`
  - `lightStatus=<red|yellow>`
- Updated Store Ops dialogs to submit workflow writes to the Store Ops API with `Idempotency-Key` and `X-Correlation-ID`, then trigger a local workspace refresh.

## Intentionally Not Changed

- `OperatorConsole.tsx` was left untouched per task do-not-touch rules.
- Existing generic `/api/v1/operator/*` shell endpoints remain available for other Operator Console workspaces.
- Growth, Network, Govern, pricing, and intervention workspaces were not modified.

## Primary Files

- `modules/opsboard/application/store_ops.py`
- `apps/api/app/routes/operator_modules/store_ops.py`
- `apps/api/oday_api/main.py`
- `shared/infrastructure/persistence/factory.py`
- `apps/web/features/operator/DesignAlignedWorkspaces.tsx`
- `apps/web/features/operator/StoreOpsWorkflowDialogs.tsx`
- `tests/contract/test_operator_api.py`
- `tests/e2e/operator-store-ops.spec.ts`
