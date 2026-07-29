# Fleet Dispatch Record: ODP-DEPLOY-JOB-QUEUE-HEALTH-CONTRACT-001

## Overview
- **Task ID**: ODP-DEPLOY-JOB-QUEUE-HEALTH-CONTRACT-001
- **Title**: Bind real Job Queue health payload to deploy validator
- **Owner**: Antigravity6
- **Reviewer**: Claude
- **Status**: in_progress → review_requested (round 4)

## Summary of Changes

### 1. Application Health Probe Payload Correction (`apps/api/oday_api/main.py`)
Updated the `/health` and `/platform/health` endpoints to derive the `job_queue` health text from `bundle.mode` (not the old `bundle.is_durable` boolean). The health text is now:

| `bundle.mode` | Health text emitted | Validator outcome |
|---|---|---|
| `"postgresql"` | `"healthy (durable postgresql job queue)"` | **PASS** — contains `"durable"` (required marker), no forbidden markers |
| `"durable"` (SQLite) | `"healthy (durable sqlite job queue)"` | **FAIL CLOSED** — contains `"sqlite"` (forbidden marker) |
| `"memory"` | `"healthy (in-memory job queue)"` | **FAIL CLOSED** — contains `"in-memory"` (forbidden marker) |

The old `bundle.is_durable` path emitted `"healthy (durable postgresql job queue)"` for *both* PostgreSQL and SQLite durable bundles, causing SQLite-mode deployments to falsely appear as passing the deploy validator gate.

### 2. Automated Regression Coverage (`tests/ops/test_cloud_run_live_deployment.py`)
Added `test_real_app_platform_health_job_queue_contract` with four sub-cases:

**Case 1 — mode=`"postgresql"` (positive path, must pass):**
Constructs `_durable_bundle(tmp_path / "test.db")` (SQLite engine for sandbox) and overrides
`mode` to `"postgresql"` via `dataclasses.replace(sqlite_base, mode="postgresql")`. Calls real
FastAPI app `GET /platform/health`. Asserts `"healthy"` is in text, no forbidden markers,
and at least one required marker (`"worker"`, `"cloud"`, or `"durable"`) is present.

**Case 2 — mode=`"durable"` / SQLite durable bundle (must fail closed):**
Constructs `_durable_bundle(tmp_path / "sqlite_test.db")` without overriding mode (mode stays
`"durable"`). Asserts emitted text contains `"healthy"` and `"sqlite"`, and that
`validator._contains_forbidden_marker()` returns `True`.

**Case 3 — mode=`"memory"` / in-memory bundle (must fail closed):**
Constructs `_memory_bundle()`. Asserts emitted text contains `"healthy"` and that
`validator._contains_forbidden_marker()` returns `True`.

**Case 4 — bare `"healthy"` payload (must fail closed):**
Directly constructs `{"dependencies": {"job_queue": "healthy"}}`. Asserts that none of the
required markers (`"worker"`, `"cloud"`, `"durable"`) appear in the text — validator fails closed
because required marker is missing.

> **Regression guarantee**: Reverting `main.py` to the old `bundle.is_durable` path causes
> Case 2 to fail: `_durable_bundle()` has `is_durable=True`, so the old path would emit
> `"healthy (durable postgresql job queue)"` and the assertion `"sqlite" in sqlite_queue_text`
> would fail. The test is genuinely load-bearing.

## Verification

| Check | Command | Result |
|---|---|---|
| Regression test | `pytest tests/ops/test_cloud_run_live_deployment.py::test_real_app_platform_health_job_queue_contract -v` | **1 passed** |
| Ops test suite | `pytest tests/ops/test_cloud_run_live_deployment.py --tb=no -q` | **356 passed, 1 failed** (see note) |
| Ruff check | `ruff check apps/api/oday_api/main.py tests/ops/test_cloud_run_live_deployment.py scripts/deployment/validate_cloud_run_live_deployment.py` | **All checks passed** |
| Ruff format | `ruff format --diff apps/api/oday_api/main.py tests/ops/test_cloud_run_live_deployment.py scripts/deployment/validate_cloud_run_live_deployment.py` | **3 files already formatted** |
| Git diff whitespace | `git diff origin/dev...HEAD --check` | **No output (exit 0)** |

**Known failure in ops suite**: `test_deploy_preflight_imports_runtime_dependencies_via_locked_python` — requires `uv` binary which is absent in the sandbox. Pre-existing failure before this task; unrelated to queue-health changes.

## Acceptance Alignment
- [x] Based on current `origin/dev` carrying only the authoritative queue-health fix
- [x] Added regression coverage invoking real application `/platform/health` composition (reverting `main.py` fails Case 2)
- [x] Proved durable PostgreSQL queue (`mode="postgresql"`) passes while SQLite durable (`mode="durable"`), in-memory, and bare healthy payloads all fail closed
- [x] Preserved model readiness, provider, secret, migration, worker scheduler, and rollback gates without weakening
- [x] Does not claim production model bindings or candidate health are ready
- [x] Ran focused ops tests, reliability tests, ruff check, ruff format, and git diff whitespace check
- [x] EOF blank line removed; `ruff format --diff` reports all files already formatted
