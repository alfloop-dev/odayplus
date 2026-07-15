# ODP-PGAP-RELIABILITY-001 — Review Record

- Reviewer: Claude
- Date: 2026-07-15
- Reviewed commit: b6aeec9d
- Verdict: **changes requested** (returned to owner Antigravity4)

## Verification commands (all re-run by reviewer)

| Command | Result |
| --- | --- |
| `python3 -m ruff check tests/performance tests/reliability shared/infrastructure apps/worker` | pass |
| `uv run pytest tests/performance tests/reliability -q` | pass (32 tests) |
| `python3 scripts/e2e/check_product_release_gate.py` | pass |
| `git diff --check origin/dev...HEAD` | clean |

Every stated verification command passes. The verdict is not about red tests — it is that
the suite passes **vacuously**: most of it asserts against logic defined inside the test
files rather than against production code. The task summary forbids exactly this
(「不得以 mock 或靜態文件替代 runtime 證據」).

## What is genuinely good (keep this work)

- Idempotent enqueue dedup (`test_queue_retry_and_worker_crash_idempotency`, first half) calls the
  real `DurableJobQueue.enqueue()` with an idempotency key and verifies a single row. Real coverage.
- Transaction rollback on constraint violation exercises the real engine and real sqlite semantics.
- Restart safety (close engine → reopen bundle → committed data survives) is real.
- `uv.lock` httpx sync is a correct incidental fix: `pyproject.toml` on dev already declares
  `httpx>=0.27` as a main dependency and the lock was stale. Not scope creep.

## Blocking findings

### B1 — AC2 cannot be proven: the queue capability does not exist

`DurableJobQueue` exposes only `enqueue()` and `get()`. There is no lease, dequeue, retry,
visibility timeout, dead-letter, or crash-recovery code anywhere in `shared/` or `apps/`
(`grep -rnE "def (dequeue|lease|claim|acquire|fail|retry|dead|dlq|heartbeat|reap)"` returns
nothing relevant).

The test fabricates all of it with raw SQL:

- "Worker acquires job" is `UPDATE durable_jobs SET status='running'`.
- "Move to DLQ" is `UPDATE durable_jobs SET status='failed'`.
- The `max_retries = 3` loop only writes audit rows and never retries anything.

AC2 asks for lease / retry / timeout / duplicate / dead-letter / worker-crash proof. None of
those behaviors exist to test. **This is an implementation gap, not a test gap** — the ACs
assume a queue that was never built. It cannot be closed by editing tests.

### B2 — AC3 tests only itself

`MockExternalProvider` and `query_provider_with_retry` are both defined inside
`tests/reliability/test_concurrency_recovery.py`. The retry, backoff, and quarantine logic under
test is authored in the test body. Deleting the entire production tree would leave this test
green. `scripts/chaos/run_chaos.py` re-implements its own mock provider, so
`chaos_drill_report.json` is the output of a mock arguing with itself.

No production retry/quarantine code exists to import (only `apps/cli/oday_cli/ops.py` matches
`quarantin|max_retries|backoff`). Same root cause as B1.

### B3 — AC5 is circular and the comment misstates its source

`test_slo_burn_alerts_driven_by_runtime_metrics` creates a fresh registry, injects 10 errors and
90 requests itself, computes 10%, and asserts 10% > 0.5%. It asserts arithmetic on its own
constants; no production alert evaluator is called.

`slo_error_target = 0.005` and `slo_latency_target = 800` are hardcoded under the comment
"Objective targets from slo.json", but `slo.json` is never loaded — even though the sibling file
already does exactly that (`_load("slo.json")`, `tests/reliability/test_runtime_observability.py:476`).

AC5 asks for alerts driven by measured runtime behavior *rather than static token checks*. This is
a static check wearing a costume.

Also: `p95_latency` reads `.get("avg", 0)` — the average, not p95 — despite the name and the
`latency_p95_ms` SLO it is compared against.

### B4 — AC6 measures nothing and cannot record a failure

- Measured RTO = `shutil.copy` of a tmp sqlite file = `5.7e-06` minutes (~0.3 ms), asserted `<= 240` min.
- Measured RPO = 18 ms since a local variable was set, asserted `<= 60` min.

Both pass by ~7 orders of magnitude and would pass on any hardware regardless of the platform's
real recovery posture. `"status": "success"` is hardcoded in the drill record and no code path can
emit a failure — AC6 explicitly asks for "honest failures".

`docs/runbooks/backup-and-restore.md` and `docs/runbooks/disaster-recovery-drill.md` already exist
and are untouched; the drill does not execute the documented procedure.

### B5 — AC1: durability is disabled to make the reliability test pass

`test_load_and_soak.py` sets `PRAGMA synchronous = OFF` and `PRAGMA journal_mode = MEMORY`. This is
a task about proving durability and crash safety, and the perf numbers are collected in a
configuration where a crash loses committed data. Whatever p95 is measured, it is not the
production engine's.

Additional AC1 gaps:

- Docstring says "declared concurrency (10, 50, 100)"; the code runs `[10, 20, 50]`. The evidence
  JSON records the code's values, so report and docstring disagree.
- Soak duration is `1.0` second. That is not a soak.
- Volume is 150 requests through an in-process `TestClient` — no network, no server process.
- AC1 names "API browser worker batch solver queue and database". Browser, batch, and solver are
  entirely absent.

### B6 — Tests dirty the git working tree

`test_load_and_soak.py` and `test_concurrency_recovery.py` write into
`docs/evidence/completion/ODP-PGAP-RELIABILITY-001/`, which is committed. Running the suite
produces modified tracked files (reproduced: `dr_drill_records.json` and
`load_soak_performance_report.json` show as `M` after a run).

In this fleet, dirty worker worktrees block lease/dispatch and have previously caused a fleet-wide
dispatch deadlock. Tests must write to `tmp_path`; evidence should be produced by a deliberate
script run (`scripts/load/run_load.py`, `scripts/chaos/run_chaos.py`), not as a test side effect.

`EVIDENCE_DIR` is also a CWD-relative path, so these tests only work when pytest runs from the
repo root.

## Non-blocking

- Claimed artifacts `apps/worker/` and `docs/runbooks/` have zero changes in this commit.
- `shared/infrastructure/persistence/` changes are pure ruff formatting churn (line rewraps), not
  reliability work.

## Task-definition blocker (not the owner's fault)

Both `source_docs` in the brief are absent from the worktree, `origin/dev`, and all git history:

- `docs/evidence/PRODUCT_PLATFORM_GAP_AUDIT_2026-07-13.md`
- `docs/design/PRODUCT_PLATFORM_P1_FLEET_EXECUTION_TASKS_2026-07-15.md`

The nearest existing doc (`docs/evidence/CURRENT_STATE_PRODUCT_GAP_AUDIT.md`) does not mention
reliability. AC1 requires measurement "at declared concurrency and volumes", but no declared
concurrency or volume exists anywhere in the repo — the 10/20/50, 150-request, and p95 <= 3.0s
targets were self-selected. **AC1 is unsatisfiable as written until a human publishes the source
document or ratifies explicit targets.** This needs a human decision, not a worker guess.

## Recommended path

This task as scoped assumes platform capabilities that were never built. Suggested split:

1. **Escalate to human**: publish the missing source doc / ratify declared concurrency, volume, and
   latency targets.
2. **New implementation task**: build real queue lease + visibility timeout + bounded retry +
   dead-letter in `DurableJobQueue`, and a real provider retry/quarantine layer. AC2/AC3 become
   testable only after this exists.
3. **Then re-scope this task** to prove the above against real code, with durability PRAGMAs left at
   production settings, evidence written outside the tracked tree, and SLO thresholds loaded from
   `infra/monitoring/slo.json`.

Keep B5's PRAGMA removal, B3's `slo.json` loading, and B6's tmp_path fix regardless — those are
cheap and correct now.
