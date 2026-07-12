# Product Flow Implementation Matrix — 2026-07-12

Traceability for the **Product Flow Implementation** wave (ODP-FLOW-001 …
ODP-FLOW-011): each product flow → its module design doc, domain module, API
router, worker/job, web feature, and completion evidence. The capstone
(ODP-FLOW-011) composes these flows into one runtime and gates them end to end.

## Flow → implementation

| Flow | Design doc | Domain module | API router | Worker / Job | Web feature | Evidence |
|---|---|---|---|---|---|---|
| FLOW-001 Integration & External Data | ODP-MOD-00 | `modules/integration`, `modules/external_data` | `external_data_router`, `listing_router` | `external-fetch` (scheduled) | — | `completion/ODP-FLOW-001/` |
| FLOW-002 Expansion HeatZone→SiteScore | ODP-MOD-01/02/03 | `modules/heatzone`, `modules/listing`, `modules/sitescore` | `heatzone_router`, `listings_router`, `sitescore_router` | `heatzone_score`, report | `features/expansion` | `completion/ODP-FLOW-002/` |
| FLOW-003 ForecastOps alert & handoff | ODP-MOD-04 | `modules/forecastops` | `forecastops_router` | `forecast` (daily score) | `features/operations` | `completion/ODP-FLOW-003/` |
| FLOW-004 InterventionOps lifecycle | ODP-MOD-05 | `modules/intervention` | `interventions_router` | eligibility / effect-eval | `features/interventions` | `completion/ODP-FLOW-004/` |
| FLOW-005 PriceOps sim/approval/rollback | ODP-MOD-06 | `modules/priceops` | `priceops_router` | pricing optimizer | `features/pricing` | `completion/ODP-FLOW-005/` |
| FLOW-006 AdLift campaign & incrementality | ODP-MOD-07 | `modules/adlift` | `adlift_router` | control-match / DiD | `features/growth` | `completion/ODP-FLOW-006/` |
| FLOW-007 DealRoom AVM valuation | ODP-MOD-08 | `modules/avm` | `avm_router` | valuation worker | `features/avm` | `completion/ODP-FLOW-007/` |
| FLOW-008 NetPlan scenario solver | ODP-MOD-09 | `modules/netplan` | `netplan_router` | solver job | `features/netplan` | `completion/ODP-FLOW-008/` |
| FLOW-009 Learning Hub validation/release | ODP-MOD-10 | `modules/learninghub`, `models/` | `learninghub_router` | backtest / drift / release | Learning Hub | `completion/ODP-FLOW-009/` |
| FLOW-010 OpsBoard & Governance operator | ODP-MOD-11 | `modules/opsboard` | `operator_router` | notification | OpsBoard | `completion/ODP-FLOW-010/` |
| **FLOW-011 Platform runtime & cross-flow gate** | **ODP-SD-03, ODP-SD-08** | `apps/api/server.py`, `shared/jobs/registry.py` | *(composes all routers)* | `ODayWorker` + `ODayScheduler` | *(compose)* | `completion/ODP-FLOW-011/` |

## Capstone composition (ODP-FLOW-011)

The flows above are composed by the first-version deployment units of
**ODP-SD-03 §4**, all bound to one durable persistence bundle:

| Deployment unit | Runtime | Entry point |
|---|---|---|
| `opsboard-web` | Frontend | `apps/web` (Next.js) |
| `core-api` | API | `python -m apps.api.server` → `apps.api.oday_api.main:app` |
| `worker` | Worker | `python -m apps.worker.oday_worker` (`ODayWorker`) |
| `scheduler` | Scheduler | `python -m apps.scheduler.oday_scheduler` (`ODayScheduler`) |
| migrations + seed | Data | `apps.api.server.bootstrap_runtime(prime_scheduled_jobs=True)` |

- **Job composition (no monolith):** domain jobs register into
  `shared/jobs/registry.py`; the worker dispatches by lookup, not an `if/elif`
  switch (ODP-AC-SD03-003).
- **Shared job state machine:** `queued → running → succeeded | failed` with
  retry/dead-letter (ODP-SD-08 §3.2, ODP-AC-SD08-001).
- **Cross-flow gate:** `tests/reliability/test_cross_flow_gate.py` runs
  migrations + seed + api + worker + scheduler on one durable DB and drives the
  Integration (`external-fetch`) and Operations (`forecast`) flows across the
  core-api boundary to `SUCCEEDED`, with an audit trail (ODP-AC-SD08-003) that
  survives a process restart (ODP-AC-SD03-004).
- **Compose:** `docker-compose.yml` runs `migrate → api → worker + scheduler →
  web` on the shared `odp-db` volume.

## Cross-flow gate status

| Gate | Status | Evidence |
|---|---|---|
| Registry composes domain jobs modularly | ✅ pass | `test_registry_composes_without_monolithic_switch` |
| SD-03 §4 deployment units declared | ✅ pass | `test_service_boundaries_declare_runtime_units` |
| migrations+seed+api+worker+scheduler on one DB | ✅ pass | `test_cross_flow_gate_migrations_seed_api_worker_scheduler` |
| Durable job → SUCCEEDED across API boundary | ✅ pass | same |
| Audit event on job enqueue | ✅ pass | same |
| Recovery: watermark survives restart | ✅ pass | same |

See `docs/evidence/completion/ODP-FLOW-011/verification.md` for command output.

## ODP-FLOW-001 — Integration and External Data (done)

External data now flows fetch → canonical mapping → DQ/quarantine →
lineage/freshness → **durable persistence** → API/UI, idempotent and audited.

- **Persistence:** `IngestionRunRecord` + `InMemoryIngestionRunStore`
  (`modules/external_data/application/ingestion_store.py`) with a durable SQLite
  twin `DurableIngestionRunStore`
  (`shared/infrastructure/persistence/external_data.py`), wired into
  `PersistenceBundle`.
- **Service:** `ExternalIngestionService`
  (`modules/external_data/application/ingestion_service.py`) composes the
  existing scheduler + provider, persists canonical output/quarantine/lineage,
  emits `external_data.ingested.v1` audit events, and rehydrates
  watermark/idempotency on restart. `run_scheduled()` and the manual API path
  share it.
- **API:** `POST /external-data/ingestion-runs` (Idempotency-Key),
  `GET /external-data/ingestion-runs[/{id}]`, `GET /external-data/quarantine`,
  and `GET /external-data/freshness` now reads persisted state (fixture only on
  cold store).
- **UI:** expansion overview freshness/lineage panel binds live via
  `getServerApiClient` + `loadApiBinding` with a `DataSourceBadge`
  (`apps/web/features/expansion/ExpansionWorkspace.tsx`).

Evidence: `docs/evidence/completion/ODP-FLOW-001/implementation.md` and
`verification.md` (focused suite 6 passed; related surface 161 passed; ruff
clean; `tsc --noEmit` clean for web + openapi-client).

## ODP-FLOW-003 — ForecastOps alert and handoff (ready for review)

ForecastOps now closes timeseries snapshot → versioned forecast + uncertainty
→ four-light alert → **acknowledge** → **executable intervention handoff**.

- **State machine:** alert `open → acknowledged` is once-only and rejects closed
  alerts; handoff `proposed → dispatched` records execution metadata and rejects
  double-dispatch.
- **Persistence:** versioned `save_forecast` remains intact; `save_alert` and
  `save_handoff` persist acknowledgement and dispatch state in place.
- **Audit:** lifecycle actions emit `forecastops.alert.acknowledged.v1` and
  `forecastops.handoff.executed.v1` alongside the existing
  `forecastops.forecasted.v1` event.
- **UI/API:** Overview and Alert center bind to live `GET /forecastops/alerts`
  through `loadApiBinding` and `DataSourceBadge`, with fixture fallback for cold
  or unconfigured stores.
- **E2E:** `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts` drives
  forecast → acknowledge → execute handoff → linked InterventionOps case.

Evidence: `docs/evidence/completion/ODP-FLOW-003/implementation.md` and
`verification.md` (integration 8 passed; smoke 2 passed / 1 deselected; ruff
clean; `npm run typecheck` clean; targeted Playwright E2E 1 passed).
