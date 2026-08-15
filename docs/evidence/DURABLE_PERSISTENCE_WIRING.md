# Durable Repository and Persistence Wiring (ODP-PV-009)

Phase: PV Product-Grade E2E Validation · Owner: Claude · Reviewer: Codex

## Goal

Take the product-critical repositories off in-memory storage and onto a
**durable, restart-survivable** backend, so Product-Grade E2E can exercise a
data path that survives a process restart and retains audit/correlation
metadata — without standing up a live database server in CI.

## Approach

The canonical production storage target is **PostgreSQL + PostGIS**
(`infra/db/migrations/000001_baseline_canonical_schema.sql`, per ODP-SD-05).
For the E2E lane we add a parallel, engine-neutral durable store built on the
Python **stdlib `sqlite3`** (WAL journaling), so the same default code path can
run either in-memory or against a real on-disk database with zero new runtime
dependencies (the project only depends on `fastapi`).

Rich domain aggregates (frozen dataclasses with nested dataclasses, `datetime`,
and `StrEnum` fields) are persisted as full-fidelity `pickle` blobs so **no
domain type needed a hand-written `from_dict`** — the repository interfaces are
unchanged and remain compatible with existing domain/application tests. Audit
events and jobs are persisted **columnar** so `correlation_id` and the
idempotency key stay real, indexed query columns.

## What landed

| Layer | File |
| --- | --- |
| SQLite engine (WAL, thread-safe, schema bootstrap) | `shared/infrastructure/persistence/engine.py` |
| Generic durable aggregate store (pickle + index columns) | `shared/infrastructure/persistence/document_store.py` |
| Durable audit log (columnar, correlation-indexed) | `shared/infrastructure/persistence/audit_log.py` |
| Durable job queue (columnar, idempotency index) | `shared/infrastructure/persistence/job_queue.py` |
| Durable module repositories (AVM, ForecastOps, SiteScore, AdLift, Intervention, LabelRegistry) | `shared/infrastructure/persistence/repositories.py` |
| Backend selection factory + bundle | `shared/infrastructure/persistence/factory.py` |
| Durable-persistence DDL (executed verbatim on bootstrap) | `infra/db/migrations/000002_durable_e2e_persistence.sql` |
| API wiring (env-gated defaults) | `apps/api/oday_api/main.py` |
| Integration tests | `tests/integration/test_durable_repository_wiring.py` |

The migration SQL is read and executed by the engine at bootstrap, so the
migration artifact and the runtime schema cannot drift.

## Configuration

`create_app()` resolves its repository/audit/job defaults from
`build_persistence()`, which selects the backend from the environment:

| Variable | Values | Effect |
| --- | --- | --- |
| `ODP_PERSISTENCE` | `memory` (default), `durable` / `sqlite` | backend selection |
| `ODP_DB_PATH` | filesystem path (default `.odp_data/durable.sqlite3`) | durable database file |

Default behaviour is **unchanged** (in-memory). Explicit `create_app(...)`
arguments still override the factory, so tests can inject their own doubles. A
`persistence=` bundle argument allows injecting a pre-built durable bundle.

To run the API on durable storage for E2E:

```bash
ODP_PERSISTENCE=durable ODP_DB_PATH=/data/odp-e2e.sqlite3 \
  uv run uvicorn apps.api.oday_api.main:app
```

## Acceptance evidence

All four acceptance criteria are covered by
`tests/integration/test_durable_repository_wiring.py`:

1. **Product API defaults can run against durable E2E database storage** —
   `test_api_jobs_and_audit_persist_across_restart` builds the real FastAPI app
   on a durable bundle and drives it over HTTP via `TestClient`;
   `test_factory_selects_durable_from_env` proves env selection.
2. **Repository interfaces remain compatible with domain/application code** —
   `test_forecast_service_writes_survive_restart` runs the unmodified
   `ForecastOpsService` against the durable repo with identical versioning
   semantics; the full existing `tests/integration` + `tests/contract` suite
   (163 tests) stays green.
3. **API/workflow writes survive process restart in E2E** — every restart test
   closes the engine and rebuilds a fresh bundle on the same on-disk file, then
   reads the data back through the public interfaces (jobs, forecasts, document
   versions, audit events).
4. **Core decision entities persist audit/correlation metadata** —
   `test_durable_audit_log_filters_by_correlation` and the API restart test
   confirm audit events are retrievable and filterable by `correlation_id`
   after a restart, and that idempotent job replay returns the original job.

## Verification

```bash
uv run pytest tests/integration/test_durable_repository_wiring.py -q     # 7 passed
uv run pytest tests/integration tests/contract -p no:warnings -q         # 163 passed
uv run ruff check shared/infrastructure/persistence apps/api/oday_api/main.py \
  tests/integration/test_durable_repository_wiring.py                     # clean
```

(Pre-existing, unrelated failures in `.orchestrator/test_supervisor.py` and
`scripts/test_ai_status.py` stem from git-remote operations unavailable in the
sandbox; they fail identically with this task's changes stashed.)

## Notes / follow-ups

- The durable backend is the E2E/local durability path. Production durability
  against the canonical Postgres schema is a separate wiring step; the
  repository interfaces and factory seam make that swap mechanical.
- Pickle blobs are written and read only by our own process; the usual
  untrusted-pickle concern does not apply. Columnar storage is used wherever a
  field must stay queryable (audit correlation, job idempotency).
