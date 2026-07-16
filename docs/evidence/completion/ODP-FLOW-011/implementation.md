# ODP-FLOW-011 — Platform runtime workers deployment and cross-flow gate

**Task:** Complete platform runtime workers deployment and cross flow gate
**Owner:** Claude · **Reviewer:** Codex (review reassigned from Antigravity on 2026-07-12; Codex is the reviewer-of-record who approved with no blocking findings) · **Phase:** Product Flow Implementation

## Goal

Compose the first-version deployment units from **ODP-SD-03 §4**
(`core-api`, `worker`, `scheduler`, migrations/seed) into one runtime that
shares a single durable persistence bundle and drives durable jobs through the
shared job state machine of **ODP-SD-08 §3**, then gate the whole thing with a
cross-flow simulation.

## Acceptance → delivery

| Acceptance | Delivered by |
|---|---|
| domain routes and jobs compose without monolithic behavior | `shared/jobs/registry.py` (`JobRegistry`) + `apps/worker/oday_worker/handlers.py` replace the worker's `if/elif` switch; the API already composes domain routers modularly in `apps/api/oday_api/main.py`. |
| migrations seed web API worker scheduler run together | `apps/api/server.py` composition root (`bootstrap_runtime` / `build_server` / `build_worker` / `build_scheduler`) + rewritten `docker-compose.yml` (`migrate` → `api` → `worker` + `scheduler` + `web`, one `odp-db` volume, durable backend). |
| workers execute durable jobs beyond heartbeat | `ODayWorker` (from ODP-GAP-RUNTIME-001) now dispatches through the registry; `python -m apps.worker.oday_worker` runs the durable loop. |
| compose observability recovery and cross flow simulation gate pass | `tests/reliability/test_cross_flow_gate.py` boots migrations→seed→api→worker→scheduler on one durable DB, drives two flows across service boundaries, asserts audit + recovery. |

## Changes

### Composition root — `apps/api/server.py` (new)
Single wiring point for the SD-03 §4 deployment units.
- `SERVICE_BOUNDARIES` — declarative map of `opsboard-web` / `core-api` /
  `worker` / `scheduler` used by docs and asserted by the gate.
- `bootstrap_runtime(prime_scheduled_jobs=…)` — builds the shared
  `PersistenceBundle`; in durable mode the SQLite engine applies
  `infra/db/migrations` on open (the *migrations* step) and, when priming, runs
  the scheduler once to enqueue the baseline recurring jobs (the *seed* step).
- `build_server` / `build_worker` / `build_scheduler` — construct api/worker/
  scheduler bound to the **same** bundle.
- `main()` — `python -m apps.api.server` uvicorn entry for `core-api`.

### Modular job dispatch — no monolithic switch
- `shared/jobs/registry.py` (new) — `JobRegistry` maps `job_type` → handler,
  rejects duplicate registration, lists registered types.
- `apps/worker/oday_worker/handlers.py` (new) — `handle_forecast` and
  `handle_external_fetch` (extracted verbatim from the old `execute_job`
  body) + `build_default_registry()`.
- `apps/worker/oday_worker/main.py` — `ODayWorker.execute_job` now delegates to
  `self.registry.handle(job, persistence)`; the claim/retry/dead-letter loop is
  unchanged. Behavior preserved (existing runtime tests stay green).

### Runtime entrypoints (new)
- `apps/worker/oday_worker/__main__.py` — `python -m apps.worker.oday_worker`.
- `apps/scheduler/oday_scheduler/__main__.py` — `python -m apps.scheduler.oday_scheduler`.

### Compose — `docker-compose.yml`
Now runs migrations + seed + api + worker + scheduler + web against one durable
`odp-db` volume: `migrate` (one-shot, `service_completed_successfully` gate) →
`api` → `worker` + `scheduler` (`service_healthy` gate) → `web`.

### Cross-flow gate — `tests/reliability/test_cross_flow_gate.py` (new)
Boots the runtime on a durable temp DB and asserts:
1. Registry composes ≥2 independently-registered handlers (non-monolithic).
2. `SERVICE_BOUNDARIES` declares core-api/worker/scheduler.
3. Scheduler primes `external-fetch`; a `forecast` job posted through the
   core-api `/jobs` boundary + the primed job are both drained by the worker to
   `SUCCEEDED`; watermark advances, a forecast persists, a `job.enqueue` audit
   event is recorded, and idempotent replay returns the same job.
4. Recovery: reopening the DB at the same path preserves the watermark.

### Source docs unpacked
`docs_archive/04_platform_sd/ODP-SD-03_SERVICE_AND_COMPONENT_BOUNDARIES.md`
and `ODP-SD-08_WORKFLOW_JOB_AND_STATE_MACHINE_DESIGN.md` extracted from the
batch-04 archive as the canonical references this task implements against.

## Boundaries (what this task did NOT change)
- No change to the domain modules or their routers (owned by ODP-FLOW-001..010).
- No change to the durable persistence schema, observability, or runbooks
  (delivered by ODP-PV-009 / ODP-R7-001 / ODP-GAP-* and reused here).
- Handler bodies are byte-for-byte the prior worker behavior — this is a
  composition/refactor task, not a domain-logic change.
