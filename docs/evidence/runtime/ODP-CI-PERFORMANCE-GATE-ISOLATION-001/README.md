# ODP-CI-PERFORMANCE-GATE-ISOLATION-001 — evidence

Isolate the deterministic load/soak P95 gate from the shared full product suite.

The SLO was never weakened. The 3.0 s P95 budget, the zero-failure assertion,
the concurrency waves (10 / 20 / 50), the 150-request volume, and the soak phase
are byte-for-byte unchanged. What changed is *which runner measures them*.

## 1. The failure being explained

Run [30380735899](https://github.com/alfloop-dev/odayplus/actions/runs/30380735899),
head sha `ef048b0fd0e4ac1b9e9ccb6c7eaa052aa984951e`, PR #484.

| Attempt | Job | Conclusion | Measured P95 | Budget | Suite wall clock |
| --- | --- | --- | --- | --- | --- |
| 1 | `product` (90347667367) | failure | **7.518 s** | 3.0 s | 790.31 s (1 failed, 1968 passed) |
| 2 — exact-head rerun | `product` (90351774215) | failure | **6.956 s** | 3.0 s | 862.53 s (1 failed, 1968 passed) |

Both attempts ran the identical head sha. The rerun was not a different
experiment, so "rerun until green" was never an available fix — and the second
measurement (6.956 s) is still 2.3× over budget.

Raw log lines: [`shared-runner-failure-receipts.txt`](./shared-runner-failure-receipts.txt).

## 2. Why the shared runner produced those numbers

`tests/performance/test_load_and_soak.py::test_concurrency_and_soak_execution`
is a wall-clock measurement, not a functional assertion. On the `product` job it
ran:

- in the same process as ~1968 other tests, roughly 13 minutes deep into the run,
  carrying all of their accumulated imports, engines, pools, and heap;
- on a runner also hosting a `postgis/postgis:16-3.5` service container;
- while driving 50-way thread concurrency through a single GIL.

Under those conditions the number it reports is dominated by the runner's
residual load, not by the system under test. The measurement was measuring CI.

## 3. What a clean runner actually measures

New `performance-gate` job, first CI run
[30383983330](https://github.com/alfloop-dev/odayplus/actions/runs/30383983330)
(job 90358528895), head `0541391b` — **passed**, three consecutive attempts in
separate processes:

| Attempt | P95 | P50 | P99 | Failures | Throughput | Budget | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | **0.4730 s** | 0.2099 s | 0.5126 s | 0 / 150 | 79.96 req/s | 3.0 s | pass |
| 2 | **0.4716 s** | 0.2134 s | 0.5014 s | 0 / 150 | 82.29 req/s | 3.0 s | pass |
| 3 | **0.4743 s** | 0.2044 s | 0.4873 s | 0 / 150 | 81.41 req/s | 3.0 s | pass |

Per-attempt reports:
[attempt 1](./ci-gate-run-30383983330-attempt-1.json),
[attempt 2](./ci-gate-run-30383983330-attempt-2.json),
[attempt 3](./ci-gate-run-30383983330-attempt-3.json).

Two things follow:

- **The budget is correct, and generous.** Measured P95 sits 6.3× under the
  3.0 s threshold, so there was never a case for relaxing it.
- **The measurement is deterministic once isolated.** The spread across three
  runs is 2.7 ms (0.4716–0.4743 s). Compare with the 562 ms swing between the
  two shared-runner attempts (7.518 → 6.956 s), both of which were ~15× the
  isolated value.

Local corroboration on an unloaded process (6-core dev host, system Python):
P95 1.1415 s, 150/150 success, `passed: true` —
[`local-isolated-run.json`](./local-isolated-run.json). Same direction, same
conclusion: the test comfortably meets its SLO when it owns its process.

## 4. Change surface

| File | Change |
| --- | --- |
| `pyproject.toml` | Register the `performance` marker. |
| `tests/performance/test_load_and_soak.py` | Add `@pytest.mark.performance` plus a comment recording why. No threshold, concurrency level, volume, or assertion touched. |
| `.github/workflows/ci.yml` | `product` job selects `-m "not requires_live_env and not performance"`; new blocking `performance-gate` job. |

### The product suite excludes exactly one test

Collected counts under `tests` on this branch:

```
[not requires_live_env]                        -> 1805 tests
[not requires_live_env and not performance]    -> 1804 tests
[performance]                                  ->    1 test
```

Re-verified at the exact PR head against the path set the `product` job actually
passes to pytest (`tests modules apps shared models`, not `tests` alone), so the
proof matches the CI invocation rather than a narrower subset:

```
[not requires_live_env]                        -> 1945 tests
[not requires_live_env and not performance]    -> 1944 tests
```

Diffing the two collection listings shows a single removed entry,
`tests/performance/test_load_and_soak.py`, and no other file's count changes.
(The 1805/1945 spread is scope, not drift: the wider set adds the `modules`,
`apps`, `shared`, and `models` trees. Either way the delta is exactly 1.)

The delta is 1, and `-m performance` resolves to
`tests/performance/test_load_and_soak.py::test_concurrency_and_soak_execution`.
`tests/performance/test_acceptance_budgets.py` is unmarked and still runs in the
product suite. CI job log confirms the same on the gate side:
`1 passed, 6 deselected`.

### The gate is fail-closed

If the marker is ever dropped, `-m performance tests/performance` collects
nothing and pytest exits 5, so the gate turns red rather than silently passing
an empty selection. Per-attempt JSON reports upload as an artifact on failure as
well as success, so a red gate hands over the measured P95, throughput, and
error list instead of only an assertion string.

## 5. Scope boundaries

- PR #484's files (`scripts/deployment/**`, `tests/ops/test_cloud_run_live_deployment.py`,
  `docs/evidence/runtime/ODP-DEPLOY-JOB-SECRET-BINDING-SELECTION-001/**`) are untouched.
- Package 10 product paths (`apps/**`, `modules/**`) are untouched.
- This should merge into `dev` before #484 is refreshed from `dev`, preserving
  #484's exact-head review chain.

## 6. Known follow-up (not fixed here)

`make test` still resolves `PYTEST_MARK_EXPR ?= not requires_live_env`, so a
local full-suite run continues to execute the measurement in-suite and can
report the same inflated P95. The `Makefile` is outside this task's declared
writable paths, so it is deliberately left alone. Running
`make test PYTEST_MARK_EXPR="not requires_live_env and not performance"` matches
CI in the meantime. Aligning the `Makefile` default is a one-line follow-up.

## 7. Commands run

```bash
# Failure receipts
gh api repos/alfloop-dev/odayplus/actions/jobs/90347667367/logs   # attempt 1: P95 7.518s
gh api repos/alfloop-dev/odayplus/actions/jobs/90351774215/logs   # attempt 2: P95 6.956s

# Selection proof (tests tree only)
python3 -m pytest -m "not requires_live_env" tests --collect-only -q                     # 1805
python3 -m pytest -m "not requires_live_env and not performance" tests --collect-only -q # 1804
python3 -m pytest -m performance tests --collect-only -q                                 # 1

# Selection proof at the exact PR head, using the product job's real path set
uv run pytest -m "not requires_live_env" \
  tests modules apps shared models --collect-only -q                                     # 1945
uv run pytest -m "not requires_live_env and not performance" \
  tests modules apps shared models --collect-only -q                                     # 1944
# diff of the two listings: only `tests/performance/test_load_and_soak.py: 1` removed

# Local isolated measurement
python3 -m pytest tests/performance/test_load_and_soak.py -q   # pass, P95 1.1415s

# Clean-runner enforcement (CI, 3x)
uv run pytest -m performance tests/performance                 # pass x3, P95 <= 0.4743s
```
