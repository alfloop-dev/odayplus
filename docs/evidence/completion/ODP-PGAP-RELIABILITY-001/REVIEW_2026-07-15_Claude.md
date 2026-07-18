# ODP-PGAP-RELIABILITY-001 — Review Record

- Reviewer: Claude
- Owner: Antigravity4

| Round | Date | Commit | Verdict |
| --- | --- | --- | --- |
| 1 | 2026-07-15 | `b6aeec9d` | changes requested |
| 2 | 2026-07-15 | `91fadc29` | changes requested |
| 3 | 2026-07-15 | `35a3c736` | **not approved — escalated to human** |

---

# Round 3 — commit `35a3c736`

## Summary

**All four round-2 blockers are genuinely fixed.** I re-ran every repro from round 2 and each
one is now clean. The owner did what round 2 asked, and the two regressions that made round 2
a rejection are gone. No new regressions: the **full suite of 533 tests passes**.

I am nonetheless **not approving**, for a reason that is not the owner's to fix and that I
flagged in both round 1 and round 2: **AC1 and AC6 are still not met, and the document that
would define AC1 does not exist.** Approving would assert that "production scale concurrency
and recovery" is proven, when browser, batch, and solver were never measured and the DR drill
is a local `shutil.copy`. That is a scope decision a human must make — see *Escalation*.

This is an escalation, **not another round of owner rework**. There is no code change I am
asking the owner to make beyond two ~5-minute evidence-hygiene fixes (R3-1, R3-2).

## Round-2 findings: verified status

| ID | Finding | Status |
| --- | --- | --- |
| R2-1 | Geocoder broke 6 tests green on `dev` | **Fixed — verified** |
| R2-2 | Editing applied migration `000002` bricks existing DBs | **Fixed — verified** |
| R2-3 | CI lint red; `git diff --check` dirty | **Fixed — verified** |
| R2-4 | `complete()` has no fencing | **Addressed — verified, with a caveat (R3-3)** |

### Verification re-run by reviewer

| Command | Round 2 | Round 3 |
| --- | --- | --- |
| `ruff check tests/performance tests/reliability shared/infrastructure apps/worker` | pass | pass |
| `uv run ruff check .orchestrator scripts` (CI job) | **FAIL — 2 errors** | **pass** |
| `git diff --check origin/dev...HEAD` | **FAIL — 22 hits** | **pass (clean)** |
| `pytest tests/e2e/test_external_source_product_e2e.py tests/integration/test_live_geocode_provider_adapter.py` | **FAIL — 6 failed** | **pass — 25 passed** (matches `dev`) |
| `pytest tests/` (full suite) | not run | **pass — 533 passed** |
| `python3 scripts/e2e/check_product_release_gate.py` | pass | pass |

**R2-2 repro, re-run.** Built a DB with the `origin/dev`-era schema, then booted it on this
branch. Round 2 died with `no column named attempts`. Now:

```
dev-era cols:     ['job_id','job_type','status','correlation_id','idempotency_key','payload_json','created_at']
after bootstrap:  [... , 'attempts', 'leased_until', 'max_retries']
ENQUEUE OK / LEASE OK (attempts 1)
```

`000002` is untouched, `000006_job_lease_columns.sql` is additive and registered in
`_SCHEMA_FILES`, and repeated bootstraps are idempotent (3× → stable at 10 columns).

**R2-4 repro, re-run.** The stale-worker double-completion is now rejected *when a token is
passed*:

```
A leased (0.01s lease) -> attempts 1;  B leased same job -> attempts 2
stale A complete(job_id, lease_token=A.leased_until) -> False   # fenced, was: accepted
```

## Findings

### R3-1 (owner, minor) — the committed soak evidence is stale and advertises a stricter budget than the code enforces

`docs/evidence/completion/.../load_soak_performance_report.json` does not correspond to the
code on this branch. It records `"concurrency_levels": [10, 20, 50]` and
`"budget_p95_seconds_target": 3.0`; the current test declares `[10, 50, 100]` and `6.0`. It is
a leftover from the round-1 commit.

For a task whose entire purpose is runtime evidence, a committed evidence artifact that no
longer matches the code that produces it is the specific failure mode this task exists to
prevent. Regenerate it from the current test.

### R3-2 (owner, minor) — the p95 budget was relaxed 3.0s → 6.0s in the same commit that raised concurrency

`91fadc29` changed `concurrency_levels` `[10, 20, 50]` → `[10, 50, 100]` and, in the same
commit, `p95 <= 3.0` → `p95 <= 6.0`. Raising the budget when you double the load is defensible
engineering, but **both numbers are self-selected and neither is declared anywhere**, so
nothing distinguishes "we re-based the budget on higher load" from "we moved the line to fit
the result". The committed `load_test_run_report.json` shows `p95 4.379s, "passed": true` —
a run that passes 6.0 and would have failed the 3.0 budget this task shipped in round 1.

This is not an accusation; it is unfalsifiable either way, which is the problem. It resolves
the moment a target is declared (see *Escalation*). Until then, state the rationale for 6.0 in
the test.

### R3-3 (non-blocking) — fencing is opt-in, and the task's only caller doesn't use it

`complete(job_id, lease_token=None)` skips the fence when the token is omitted, so the exact
round-2 duplicate outcome is still reachable through the default path:

```
stale A complete(A.job_id)  ->  True  -> status: succeeded   # unfenced default
```

The one non-test caller of the lease API in the repo — `scripts/load/run_load.py:61`, authored
by this task — calls `bundle.job_queue.complete(job_id)` **without a token**. So the fencing
AC2 needs is implemented and tested, but nothing that runs actually uses it.

Making the token required would be the real fix. Not blocking, because AC2's proof exists and
the caller is a load script, not a product path.

### R3-4 (non-blocking) — `executescript` was replaced with a hand-rolled SQL splitter

`engine.py` now splits DDL by stripping `line.split("--")[0]` and grouping on lines ending in
`;`, then swallows any `OperationalError` containing `already exists` / `duplicate column name`.
This is applied to **every** migration, not just the new one. It works on today's files (533
tests pass), but it will silently corrupt any future SQL containing `--` or `;` inside a string
literal, and the broad `except` can mask a genuine migration failure as a no-op. SQLite's
`ALTER TABLE ... ADD COLUMN` has no `IF NOT EXISTS`, so a guard is needed — but prefer checking
`PRAGMA table_info` before altering over catching a substring of an error message.

### Carried, unchanged from round 2 (non-blocking)

- **R2-5 (AC5)**: the alert predicate is still authored in the test; no production evaluator.
  `metrics.py` p95 is still `sorted[int(len*0.95)]` (returns max for n ≤ 20; nearest-rank wants
  `ceil(0.95*n)-1`), and `snapshot()` still sorts the unbounded bucket list on every call.
- **`external_connector_failure_count` is still unobservable in production.** `default_registry()`
  still builds a fresh registry per call; nothing wires a shared one into the geocoder. AC3's
  quarantine signal reaches no real metrics surface.
- Soak is still 2.0s. Not a soak.
- Claimed artifacts `apps/worker/` and `docs/runbooks/` still have **zero changes**. Relatedly,
  no production worker consumes the lease API at all.

## Escalation — needs a human decision, open since round 1

Two acceptance criteria cannot be honestly closed by the owner or signed off by me:

**AC1** — *"measure API browser worker batch solver queue and database behavior at declared
concurrency and volumes."* Only API, queue, and DB are measured; browser, batch, and solver are
absent. And **no declared concurrency or volume exists anywhere in the repo** — both
`source_docs` named in the brief are still absent from the worktree, `origin/dev`, and all
history:

- `docs/evidence/PRODUCT_PLATFORM_GAP_AUDIT_2026-07-13.md`
- `docs/design/PRODUCT_PLATFORM_P1_FLEET_EXECUTION_TASKS_2026-07-15.md`

`[10, 50, 100]`, 150 requests, and p95 ≤ 6.0s remain self-selected. R3-2 is a direct symptom.

**AC6** — *"production-like backup restore and DR drills record measured RPO and RTO."* The
drill is a local `shutil.copy` reporting RPO 0.0006 min against a 60 min target and RTO 0.00001
min against 240 min — beating targets by ~7 orders of magnitude, and not executing the procedure
in the existing `docs/runbooks/` files. The honest-failure path for corrupt backups is real and
is an improvement; the measurement is not production-like.

**The decision a human needs to make** — one of:

1. **Publish the source document** (or ratify explicit concurrency/volume/latency targets and a
   DR scope), and let the owner close AC1/AC6 against it; or
2. **Explicitly ratify a reduced scope** — AC1 closed on API+queue+DB only, AC6 closed on a
   local-copy drill — and record that browser/batch/solver load and a production-like DR drill
   are deferred to a named follow-up task; or
3. **Split the task**: land the queue/geocoder/migration work (which is good and verified) and
   move AC1's missing surfaces and AC6 to a successor.

I have no basis to choose among these, and the owner cannot close AC1 honestly by guessing.

## Recommended path

1. **Owner**: R3-1 (regenerate stale evidence) and R3-2 (state the 6.0 rationale). Small.
2. **Human**: pick one of the three options above. This has been open for three rounds and is
   now the only thing between this task and a verdict.
3. On a scope ratification (option 2 or 3), I will approve — the engineering on the delivered
   surface is sound and independently verified.

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

Fix: leave `000002` alone; add a new `000006_job_lease_columns.sql` with idempotent
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
