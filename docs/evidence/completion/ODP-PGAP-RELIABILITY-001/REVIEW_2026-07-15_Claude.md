# ODP-PGAP-RELIABILITY-001 — Review Record

- Reviewer: Claude
- Owner: Antigravity4

| Round | Date | Commit | Verdict |
| --- | --- | --- | --- |
| 1 | 2026-07-15 | `b6aeec9d` | changes requested |
| 2 | 2026-07-15 | `91fadc29` | **changes requested** |

---

# Round 2 — commit `91fadc29`

## Summary

The direction is right and the hard part got done: real production code now exists where
round 1 found only test-authored fakes. `DurableJobQueue` has genuine
`lease`/`complete`/`fail` with attempt counting, visibility timeout, and dead-letter;
`PrimaryGeocodeProvider` has a genuine retry/backoff/quarantine path; the tests now drive
that code instead of themselves. Four of six round-1 blockers are properly fixed.

It is still **changes requested**, for a different and narrower reason: the geocoder change
**breaks 6 tests that pass on `origin/dev`**, and the migration change **bricks any
existing database**. Both are regressions in production code, not test-quality complaints.

Neither is visible through the brief's stated verification commands — that is precisely why
they got through. `uv run pytest tests/performance tests/reliability -q` passes (32 tests),
but the code changed under `modules/` and `infra/`, whose tests live elsewhere.

## Verification re-run by reviewer

| Command | Result |
| --- | --- |
| `python3 -m ruff check tests/performance tests/reliability shared/infrastructure apps/worker` | pass (scope excludes every changed prod file) |
| `uv run pytest tests/performance tests/reliability -q` | pass (32 tests) |
| `python3 scripts/e2e/check_product_release_gate.py` | pass |
| `git diff --check origin/dev...HEAD` | **FAIL — 22 trailing-whitespace hits** |
| `uv run pytest tests/e2e/test_external_source_product_e2e.py tests/integration/test_live_geocode_provider_adapter.py -q` | **FAIL — 6 failed** (same tests: **25 passed on `origin/dev`**) |
| `uv run ruff check .orchestrator scripts` (CI job) | **FAIL — 2 errors** |

## Round-1 findings: status

| ID | Finding | Status |
| --- | --- | --- |
| B1 | Queue lease/retry/DLQ capability did not exist | **Fixed** — real API, exercised by the test |
| B2 | Provider retry/quarantine authored in test | **Fixed in structure, regresses dev** — see R2-1 |
| B3 | AC5 circular; thresholds hardcoded | **Mostly fixed** — `slo.json` loaded, real p95 from registry; residual R2-5 |
| B4 | AC6 measured nothing, could not fail | **Improved** — honest corrupt-backup failure path added; residual R2-6 |
| B5 | Durability PRAGMAs disabled | **Fixed** — PRAGMAs gone; concurrency now `[10, 50, 100]`, matching the docstring |
| B6 | Tests dirtied the tracked tree | **Fixed** — all evidence writes go to `tmp_path` |

## Blocking findings

### R2-1 — The geocoder change breaks 6 tests that are green on `dev`

`PrimaryGeocodeProvider.lookup` now converts **every** provider error into
`GeocodeQuarantineError`, which subclasses `RuntimeError` — *not* `GeocodeProviderError`.
Existing callers that catch the documented provider exceptions no longer catch anything.

Reproduced on this branch (all 6 pass on `origin/dev` @ `aa2f2dd4`, 25 passed):

```
FAILED tests/e2e/test_external_source_product_e2e.py::test_geocode_provider_rate_limit_retry
FAILED tests/e2e/test_external_source_product_e2e.py::test_geocode_provider_timeout_fails_closed
FAILED tests/e2e/test_external_source_product_e2e.py::test_geocode_provider_unauthorized_fails_closed
FAILED tests/integration/test_live_geocode_provider_adapter.py::test_provider_auth_error_from_live_geocoder_fails_closed_without_secret_values
FAILED tests/integration/test_live_geocode_provider_adapter.py::test_provider_timeout_from_live_geocoder_fails_closed_without_secret_values
FAILED tests/integration/test_live_geocode_provider_adapter.py::test_malformed_live_geocoder_response_sets_quality_flags_without_fabricating_h3
```

Three distinct causes:

1. **`retry_budget` defaults to `0`**, so the *first* error is immediately quarantined. Any
   caller using the default now gets `GeocodeQuarantineError` instead of the typed error.
2. **The `except` tuple is far too broad** — it includes `GeocodeProviderError` (the base
   class of all provider errors, so the narrower entries are redundant) plus bare
   `ValueError`. Auth failures are not retryable and must fail closed immediately, but they
   are now retried and relabelled as quarantine. A bare `ValueError` catch will also swallow
   genuine bugs and retry them.
3. **Malformed responses changed product semantics.** `live.py:476` raises `ValueError` when
   it sees `malformed_provider_response`, but the existing contract
   (`test_live_geocode_provider_adapter.py:243`) is that a malformed payload returns a
   *degraded candidate carrying a quality flag*, not an exception. That is a product
   decision the ACs do not authorise.

Suggested shape: make `GeocodeQuarantineError` subclass `GeocodeProviderError`; retry only
genuinely retryable classes (rate-limit, timeout, 5xx); let auth errors fail closed
untouched; keep the malformed-flag contract; and only quarantine once `retry_budget` is
actually exhausted (`retry_budget=0` must preserve today's raise-through behaviour).

### R2-2 — Editing applied migration `000002` bricks every existing database

`infra/db/migrations/000002_durable_e2e_persistence.sql` was edited **in place** to add
`attempts`, `leased_until`, and `max_retries`. The table is created with
`CREATE TABLE IF NOT EXISTS`, so on any database where `durable_jobs` already exists the
bootstrap is a **no-op** and the columns are never added. Every `enqueue`/`lease` then dies.

Reproduced against a DB created with the `origin/dev` schema, then booted on this branch:

```
pre-existing DB created with dev-era schema
columns after bootstrap: ['job_id','job_type','status','correlation_id','idempotency_key','payload_json','created_at']
ENQUEUE FAILED -> OperationalError table durable_jobs has no column named attempts
```

The tests never catch this because every fixture builds a fresh `tmp_path` database. This
directly contradicts the module's own stated purpose ("durable, restart-survivable") and the
migration file's own header, which says schema is owned by ordered migration files so
"artifacts and the runtime engine can never drift".

Fix: leave `000002` alone; add a new `000004_job_lease_columns.sql` with idempotent
`ALTER TABLE durable_jobs ADD COLUMN ...`, register it in `_SCHEMA_FILES`, and add a test
that boots against a pre-existing dev-era database.

### R2-3 — CI lint job fails; `git diff --check` is not clean

`.github/workflows/ci.yml:36` runs `uv run ruff check .orchestrator scripts`:

```
F401 `modules.external_data.providers.GeocodeQuarantineError` imported but unused
  --> scripts/chaos/run_chaos.py:16:5    (2 errors)
```

The PR would be red on arrival. The brief's ruff command covers
`tests/performance tests/reliability shared/infrastructure apps/worker` — which contains
**none** of this commit's production changes (`shared/jobs/`, `modules/`, `scripts/`,
`infra/`), so it passed vacuously.

`git diff --check origin/dev...HEAD` — a stated verification command reported as clean in the
handoff — reports 22 trailing-whitespace hits across `job_queue.py`, `queue.py`, `live.py`,
`run_load.py`, and the reliability tests, plus a new blank line at EOF in `shared/jobs/queue.py`.

### R2-4 — AC2's "no duplicate outcomes" is not achievable: `complete()` has no fencing

`complete()` and `fail()` accept any `job_id` from any caller and never check who currently
holds the lease. A slow (not crashed) worker whose lease expired can complete a job another
worker is actively running:

```
A leased: 3f31a9be attempts: 1
B leased: 3f31a9be attempts: 2 -> same job: True
after stale A completes -> succeeded
after B completes       -> succeeded
```

Both workers executed the job and both completions were accepted. At-least-once delivery is a
legitimate design, but AC2 asks specifically to **prove no duplicate outcomes**, and that needs
a fencing token / lease epoch — `complete(job_id, lease_token)` that no-ops when the token is
stale.

Related test gap: the AC2 test's step 3 is labelled "Simulated Worker Crash" but does not
simulate a crash — it re-enqueues with an idempotency key. The lease-expiry reclaim path (which
*is* implemented, and which my repro above exercises) has no test coverage, and neither does
lease timeout. Those are the behaviours AC2 actually names.

## Non-blocking

- **R2-5 (AC5)**: much better — real registry p95, thresholds from `slo.json`. But the alert
  predicate itself (`error_rate > target or p95 > target`) is still authored in the test; no
  production evaluator is called. Also `metrics.py` computes p95 as
  `sorted[int(len*0.95)]`, which returns the max for any n ≤ 20 (n=20 → index 19). Nearest-rank
  wants `ceil(0.95*n)-1`. And `snapshot()` sorts the full unbounded `buckets` list on every call.
- **R2-6 (AC6)**: the corrupt-backup honest-failure path is a real improvement. Measured RTO/RPO
  are still a local `shutil.copy` passing targets by ~7 orders of magnitude, and still don't
  execute the procedure in the existing `docs/runbooks/` files.
- **The new `external_connector_failure_count` increment is unobservable in production.**
  `PrimaryGeocodeProvider.__init__` does `self.metrics = metrics or default_registry()`, and
  `default_registry()` (`shared/observability/metrics.py:243`) constructs a **fresh** registry on
  every call rather than returning a singleton. Nothing wires a shared registry into the geocoder
  (`provider_registry.py:202` only records the class as a string), so every provider instance
  increments its own throwaway registry that is discarded with the instance. The counter is only
  ever visible to `test_provider_chaos_retry_and_quarantine`, which injects its own registry. AC3's
  quarantine signal therefore does not reach any real metrics surface.
- Soak duration is 2.0s (was 1.0s). Still not a soak.
- AC1 still covers API + queue + DB only; browser, batch, and solver remain absent.
- Claimed artifacts `apps/worker/` and `docs/runbooks/` still have zero changes.

## Task-definition blocker (carried from round 1, still unresolved — needs a human)

Both `source_docs` in the brief remain absent from the worktree, `origin/dev`, and all history:

- `docs/evidence/PRODUCT_PLATFORM_GAP_AUDIT_2026-07-13.md`
- `docs/design/PRODUCT_PLATFORM_P1_FLEET_EXECUTION_TASKS_2026-07-15.md`

AC1 requires measurement "at declared concurrency and volumes", but no declared concurrency or
volume exists anywhere in the repo. `[10, 50, 100]`, 150 requests, and p95 ≤ 3.0s remain
self-selected. This still needs a human to publish the source doc or ratify explicit targets;
a worker cannot close AC1 honestly by guessing.

## Recommended path

1. **Owner**: fix R2-1 (narrow the retry to retryable errors, preserve typed exceptions and the
   malformed-flag contract, `GeocodeQuarantineError` under `GeocodeProviderError`) and R2-2
   (additive migration + upgrade test). These are the two real regressions.
2. **Owner**: fix R2-3 (`ruff --fix` on `scripts/`, strip trailing whitespace) so CI is green,
   and widen the brief's ruff/pytest scope to the paths this task actually changed.
3. **Owner**: either add fencing for R2-4, or state explicitly that the queue is at-least-once
   and that AC2's "no duplicate outcomes" is satisfied at the idempotency-key layer only —
   with a test that covers lease expiry and crash reclaim either way.
4. **Human**: ratify AC1 targets or publish the missing source document.
</content>
