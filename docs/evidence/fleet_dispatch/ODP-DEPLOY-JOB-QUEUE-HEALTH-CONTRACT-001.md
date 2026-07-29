# Fleet Dispatch Record: ODP-DEPLOY-JOB-QUEUE-HEALTH-CONTRACT-001

## Overview
- **Task ID**: ODP-DEPLOY-JOB-QUEUE-HEALTH-CONTRACT-001
- **Title**: Bind real Job Queue health payload to deploy validator
- **Owner**: Claude3 (reassigned 2026-07-29; rounds 1–4 owned by Antigravity6)
- **Reviewer**: Claude
- **Status**: in_progress → review_requested (round 5)

## Summary of Changes

### 1. Application Health Probe Payload Correction (`apps/api/oday_api/main.py`)
Updated the `/health` and `/platform/health` endpoints to derive the `job_queue` health text from `bundle.mode`. The health text is now:

| `bundle.mode` | Health text emitted | Validator outcome |
|---|---|---|
| `"postgresql"` | `"healthy (durable postgresql job queue)"` | **PASS** — contains `"durable"` (required marker), no forbidden markers |
| `"durable"` (SQLite) | `"healthy (durable sqlite job queue)"` | **FAIL CLOSED** — contains `"sqlite"` (forbidden marker) |
| `"memory"` | `"healthy (in-memory job queue)"` | **FAIL CLOSED** — contains `"in-memory"` (forbidden marker) |

#### Baseline this replaces (corrected in round 5)

There are two distinct predecessors, and an earlier revision of this document conflated them:

1. **The real `dev` baseline** (`git show 88dae2e1:apps/api/oday_api/main.py`) emitted a bare
   `queue_details = "healthy"` for every bundle mode. It carries **no** required marker
   (`"worker"` / `"cloud"` / `"durable"`), so the deploy validator's `job_queue` gate could not
   distinguish a real PostgreSQL queue from an in-memory one — every mode reported the same
   opaque string.
2. **This task's own round-1 code** (`c07bbcb8`) introduced a two-branch `bundle.is_durable`
   text that emitted `"healthy (durable postgresql job queue)"` for *both* PostgreSQL and
   SQLite durable bundles, which would have let SQLite-mode deployments falsely pass the gate.
   That revision **never reached `dev`**; it was corrected inside this task by `6b0850a6`.

The shipped `bundle.mode` derivation fixes both.

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

> **Regression guarantee (corrected in round 5)**: the test is load-bearing against *both*
> predecessors described above, verified by mutation testing rather than by assertion.
>
> - **Primary — revert to the true `dev` baseline** (`queue_details = "healthy"`, as in
>   `88dae2e1`): **Case 1 fails.** Every mode collapses to the bare string, which contains no
>   required marker, so
>   `any(marker in pg_queue_text for marker in ("worker", "cloud", "durable"))` is `False`.
> - **Secondary — revert to this task's round-1 `bundle.is_durable` two-branch text**
>   (`c07bbcb8`): **Case 2 fails.** `_durable_bundle()` has `is_durable=True`, so it would emit
>   `"healthy (durable postgresql job queue)"` and the assertion `"sqlite" in sqlite_queue_text`
>   would fail.
>
> Both mutants were executed, not reasoned about; see the mutation transcript in
> § Round 5 Verification.

## Verification

| Check | Command | Result |
|---|---|---|
| Regression test | `pytest tests/ops/test_cloud_run_live_deployment.py::test_real_app_platform_health_job_queue_contract -v` | **1 passed** |
| Ops test suite | `pytest tests/ops/test_cloud_run_live_deployment.py --tb=no -q` | **356 passed, 1 failed** (see note) |
| Ruff check | `ruff check apps/api/oday_api/main.py tests/ops/test_cloud_run_live_deployment.py scripts/deployment/validate_cloud_run_live_deployment.py` | **All checks passed** |
| Ruff format | `ruff format --diff apps/api/oday_api/main.py tests/ops/test_cloud_run_live_deployment.py scripts/deployment/validate_cloud_run_live_deployment.py` | **3 files already formatted** |
| Git diff whitespace | `git diff origin/dev...HEAD --check` | **No output (exit 0)** |

**Known failure in ops suite**: `test_deploy_preflight_imports_runtime_dependencies_via_locked_python` — requires `uv` binary which is absent in the sandbox. Pre-existing failure before this task; unrelated to queue-health changes.

## Round 5 Verification

Round 5 changed **no application or test code**. It resolves the two round-4 blockers:
B1 (task branch was `BEHIND` `dev`) and B2 (this document misstated the baseline).

| Check | Command | Result |
|---|---|---|
| Base refresh (B1) | `git merge --no-ff origin/dev` into `task/ODP-DEPLOY-JOB-QUEUE-HEALTH-CONTRACT-001` | Conflict-free merge of `origin/dev` @ `4b329493`; no rebase, no force-push |
| Task commits preserved | `git merge-base --is-ancestor <c> HEAD` for `c07bbcb8 24250dc7 6b0850a6 2f65692e` | All 4 still ancestors of HEAD |
| Cumulative diff unchanged | `git diff --stat origin/dev...HEAD` | Same 5 files / +173 −6 as round 4 — the dev merge added nothing to the task diff |
| Focused tests | `pytest tests/ops/test_cloud_run_live_deployment.py::test_real_app_platform_health_job_queue_contract tests/reliability/test_health_endpoints.py -p no:warnings` | **7 passed** |
| Mutation re-run | see `docs/evidence/runtime/ODP-DEPLOY-JOB-QUEUE-HEALTH-CONTRACT-001/mutation-transcript-round5.txt` | **2 mutants executed, 0 survived** (re-executed from scratch on head `61766a45` after a worktree reset — see the RE-VERIFICATION block in that transcript) |
| Ruff check | `python3 -m ruff check apps/api/oday_api/main.py tests/ops/test_cloud_run_live_deployment.py tests/reliability/test_health_endpoints.py` | **All checks passed** |
| Ruff format | `python3 -m ruff format --diff <same 3 files>` | **3 files already formatted** |
| Git diff whitespace | `git diff origin/dev...HEAD --check` | **No output (exit 0)** |

### Open follow-up (non-blocking, N1 from round 4)

`test_real_app_platform_health_job_queue_contract` re-implements the validator's `job_queue`
gate expression inline (`any(marker in text for marker in ("worker", "cloud", "durable"))`)
instead of calling the validator. If the required-marker tuple in
`scripts/deployment/validate_cloud_run_live_deployment.py` (the `smoke:/platform/health:job_queue`
check) changes, this test would stay green while the real deploy gate diverges. The preferred
fix is to extract that gate into a named predicate the test can call.

**Deliberately not done in round 5.** The round-4 review scoped this round to "cumulative diff
unchanged apart from the dev merge"; extracting the predicate would edit the validator and the
test, invalidating the approved diff. Tracked here as a follow-up rather than folded in.

Round-4 finding N2 (the `mode="postgresql"` case is a `dataclasses.replace` spoof over a SQLite
engine, so no real PostgreSQL is exercised) was accepted as-is by the reviewer for a text-contract
test; it is documented in the test docstring and the evidence JSON. No change required.

## Acceptance Alignment
- [x] Based on current `origin/dev` (merged `4b329493` in round 5) carrying only the authoritative queue-health fix
- [x] Added regression coverage invoking real application `/platform/health` composition (reverting `main.py` to the true `dev` baseline fails Case 1; reverting to the round-1 `is_durable` text fails Case 2 — both verified by mutation run)
- [x] Proved durable PostgreSQL queue (`mode="postgresql"`) passes while SQLite durable (`mode="durable"`), in-memory, and bare healthy payloads all fail closed
- [x] Preserved model readiness, provider, secret, migration, worker scheduler, and rollback gates without weakening
- [x] Does not claim production model bindings or candidate health are ready
- [x] Ran focused ops tests, reliability tests, ruff check, ruff format, and git diff whitespace check
- [x] EOF blank line removed; `ruff format --diff` reports all files already formatted
