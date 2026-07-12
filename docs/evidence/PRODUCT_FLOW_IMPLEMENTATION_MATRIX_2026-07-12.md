# Product Flow Implementation Matrix — 2026-07-12

Tracks the "Product Flow Implementation" wave (`ODP-FLOW-0xx`): turning each
vertical from wired-but-in-memory into a persisted, API/UI-readable, idempotent,
audited closed loop. One row per flow; owners fill their row on completion.
Status values: `todo` → `in-progress` → `done`.

| Flow | Task | Owner | Status | Closed-loop deliverable |
| --- | --- | --- | --- | --- |
| Integration & External Data | ODP-FLOW-001 | Claude2 | **done** | see below |
| Expansion HeatZone→SiteScore | ODP-FLOW-002 | Antigravity | todo | — |
| ForecastOps alert & handoff | ODP-FLOW-003 | Codex | todo | — |
| InterventionOps lifecycle | ODP-FLOW-004 | Claude | todo | — |
| PriceOps sim/approval/rollback | ODP-FLOW-005 | Antigravity2 | todo | — |
| AdLift campaign & incrementality | ODP-FLOW-006 | Antigravity3 | todo | — |
| DealRoom AVM valuation | ODP-FLOW-007 | Antigravity4 | todo | — |
| NetPlan solver & publish | ODP-FLOW-008 | Antigravity5 | todo | — |
| Learning Hub release/rollback | ODP-FLOW-009 | Antigravity6 | todo | — |
| OpsBoard & Governance operator | ODP-FLOW-010 | Antigravity7 | todo | — |
| Platform runtime & cross-flow gate | ODP-FLOW-011 | Codex2 | todo | — |

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
