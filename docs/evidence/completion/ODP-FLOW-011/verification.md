# ODP-FLOW-011 — Verification

**Owner:** Claude · **Reviewer:** Antigravity
Commands run from the task worktree with `PYTHONPATH=.`.

## 1. Cross-flow gate + runtime state machine

```
$ python3 -m pytest tests/reliability/test_cross_flow_gate.py \
                    tests/integration/test_worker_scheduler_runtime.py
9 passed, 1 warning in 1.71s
```

Covers (ODP-SD-03 §4 / ODP-SD-08 §3):
- `test_registry_composes_without_monolithic_switch` — ≥2 handlers, duplicate
  registration rejected (acceptance 1).
- `test_service_boundaries_declare_runtime_units` — core-api/worker/scheduler
  declared (acceptance 2).
- `test_cross_flow_gate_migrations_seed_api_worker_scheduler` — migrations +
  seed + api + worker + scheduler on one durable DB; external-fetch + forecast
  drained to `SUCCEEDED`; watermark advances; forecast persists; `job.enqueue`
  audit event recorded; idempotent replay; watermark survives DB reopen
  (acceptance 2/3/4 + recovery).
- The 6 pre-existing ODP-GAP-RUNTIME-001 runtime tests still pass — the registry
  refactor is behavior-preserving.

## 2. Broader affected suites

```
$ python3 -m pytest tests/reliability tests/integration/test_worker_scheduler_runtime.py \
                    tests/ops tests/smoke
42 passed, 1 failed, 1 warning
```

The single failure is `tests/smoke/test_foundation_smoke.py::test_production_dependency_stack_imports`
(`ModuleNotFoundError: No module named 'duckdb'`) — an environmental missing
optional dependency. Verified **pre-existing**: it fails identically with this
task's files stashed, and this task does not touch the dependency stack.

## 3. Lint

```
$ python3 -m ruff check apps/api/server.py apps/worker/oday_worker \
        shared/jobs/registry.py apps/scheduler/oday_scheduler \
        tests/reliability/test_cross_flow_gate.py
All checks passed!

$ git diff --check         # no whitespace/conflict errors
(clean)
```

## 4. Runtime bootstrap smoke (durable)

```
$ ODP_PERSISTENCE=durable ODP_DB_PATH=/tmp/odp-boot-test/runtime.sqlite3 \
  python3 -c "from apps.api.server import bootstrap_runtime; \
              b=bootstrap_runtime(prime_scheduled_jobs=True); \
              print(b.mode, b.is_durable); b.engine.close()"
durable True
```

Confirms `bootstrap_runtime` applies migrations (engine bootstrap) and primes the
baseline scheduled job — the `migrate` compose step.

## 5. Compose composition

```
$ docker compose -f docker-compose.yml config      # exit 0
compose valid
```

`migrate` (one-shot) → `api` → `worker` + `scheduler` → `web`, all on the shared
`odp-db` durable volume. Docker build/run of the stack is a follow-up on a host
with the daemon; the composition is validated and the entrypoints
(`python -m apps.worker.oday_worker`, `python -m apps.scheduler.oday_scheduler`,
`python -m apps.api.server`) import and run in-process (covered by §1/§4).
