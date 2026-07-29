# Fleet Dispatch Record: ODP-DEPLOY-JOB-QUEUE-HEALTH-CONTRACT-001

## Overview
- **Task ID**: ODP-DEPLOY-JOB-QUEUE-HEALTH-CONTRACT-001
- **Title**: Bind real Job Queue health payload to deploy validator
- **Owner**: Antigravity6
- **Reviewer**: Claude
- **Status**: in_progress -> review_requested

## Summary of Changes
1. **Application Health Probe Payload Correction (`apps/api/oday_api/main.py`)**:
   Updated the `/health` and `/platform/health` endpoints to emit `"healthy (durable postgresql job queue)"` when `bundle.is_durable` is true, and `"healthy (in-memory job queue)"` when `bundle.is_durable` is false.
   - For durable PostgreSQL queue deployments: the payload contains `"healthy"` and `"durable"`, satisfying `validate_cloud_run_live_deployment.py` smoke check requirements (`smoke:/platform/health:job_queue`).
   - For in-memory or bare healthy queues: the payload contains `"in-memory"` (a forbidden data marker) or lacks `"durable"`/`"worker"`/`"cloud"`, causing the deploy validator to fail closed.

2. **Automated Regression Coverage (`tests/ops/test_cloud_run_live_deployment.py`)**:
   Added `test_real_app_platform_health_job_queue_contract`:
   - Instantiates real FastAPI app with `_durable_bundle(tmp_path / "test.db")`, calls `GET /platform/health`, and verifies `validator._dependency_text(payload, "job_queue")` satisfies validator assertions (`"healthy"` in text, no forbidden markers, contains `"durable"`).
   - Instantiates real FastAPI app with `_memory_bundle()`, calls `GET /platform/health`, and verifies validator fails closed on forbidden `"in-memory"` marker.
   - Verifies bare `"healthy"` payload fails closed due to missing required queue mode marker.

## Verification
- **Regression Test**: `test_real_app_platform_health_job_queue_contract` PASSED (1 passed).
- **Ops Test Suite**: 356 passed in `tests/ops/test_cloud_run_live_deployment.py`.
- **Linter Check**: `ruff check` passed cleanly on all touched paths.

## Acceptance Alignment
- [x] Based on current `origin/dev` carrying only authoritative queue-health fix
- [x] Added regression coverage invoking real application `/platform/health` composition (reverting `main.py` fails test)
- [x] Proved durable PostgreSQL queue passes while in-memory and bare healthy payloads fail closed
- [x] Preserved model readiness, provider, secret, migration, worker scheduler, and rollback gates without weakening
- [x] Does not claim production model bindings or candidate health are ready
- [x] Ran focused ops tests and Ruff diff check
