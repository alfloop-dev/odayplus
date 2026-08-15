# ODP-FLOW-001 — Complete Integration and External Data flow

**Owner:** Claude2 · **Reviewer:** Claude · **Phase:** Product Flow Implementation

## Goal

Close the external-data loop end to end: external data is fetched from a
(mock/replay) provider, mapped to canonical, DQ-gated with quarantine, its
lineage/freshness preserved, **persisted** to a durable store, and read back by
the API and the expansion UI — with idempotent retry rejection and an audit
trail that survive a process restart.

## What already existed (composed, not rebuilt)

- `ExternalFetchScheduler` (`modules/external_data/workers/scheduled_fetch.py`):
  window idempotency, last-success watermark, freshness/staleness, retry
  backoff, provider circuit breaker.
- `ListingPartnerFeedProvider` (`modules/external_data/providers/live.py`):
  fetch → canonical mapping → DQ/duplicate quarantine → per-record lineage.
- Durable persistence toolkit (`shared/infrastructure/persistence/*`) and the
  `shared.audit` event log, both already used by other modules.

These were **not** modified; the gap was that run output lived only in-process
(in-memory dict + loose JSON files), the API `GET /external-data/freshness`
returned a hardcoded fixture (`app.state.external_freshness_evidence` was never
populated), and the expansion UI rendered a static fixture.

## What this task added

### 1. Persisted, queryable run state
- `modules/external_data/application/ingestion_store.py` —
  `IngestionRunRecord` (canonical output summary + `QuarantineRecord` +
  `LineageRecord` + freshness), and `InMemoryIngestionRunStore`
  (get / list / `latest_per_provider` / `freshness` / `quarantine_records`,
  keyed by run id with window- and API-idempotency indices).
- `shared/infrastructure/persistence/external_data.py` —
  `DurableIngestionRunStore`, a drop-in SQLite twin over the existing
  `durable_documents` table (grouped by `provider_id`). No new migration.
- `shared/infrastructure/persistence/factory.py` — `ingestion_run_store`
  added to `PersistenceBundle` for both memory and durable backends.

### 2. Closed-loop service
- `modules/external_data/application/ingestion_service.py` —
  `ExternalIngestionService` wraps the scheduler + provider, **captures** the
  provider result to fold quarantine/lineage into the record, persists it,
  emits a `shared.audit` `external_data.ingested.v1` event
  (`accepted` vs `idempotent_replay`), and on construction **rehydrates** the
  scheduler's watermark/idempotency state from the store, so a restarted
  process rejects duplicate windows and keeps advancing from the persisted
  watermark. `run_scheduled()` and the manual API path use the same code.

### 3. API reads persisted state
- `apps/api/app/routes/external_data.py` —
  - `POST /external-data/ingestion-runs` (manual trigger, `Idempotency-Key`
    header, RBAC `integration:create`) → returns the persisted run plus
    `created` and `audit_event_id`;
  - `GET /external-data/ingestion-runs` (+ `/{run_id}`) — persisted runs;
  - `GET /external-data/quarantine` — DQ quarantine, queryable;
  - `GET /external-data/freshness` — now reads persisted freshness, with the
    documented fixture retained only as a **cold-store** fallback.
- `apps/api/oday_api/main.py` — builds `ExternalIngestionService` from the
  bundle store and injects it into the router (`external_ingestion_service`
  param for test doubles).

### 4. UI reads persisted state
- `packages/openapi-client` — typed `listExternalDataFreshness()`.
- `apps/web/src/app/w/expansion/page.tsx` — server component binds the live
  freshness via `getServerApiClient` + `loadApiBinding` (dynamic route).
- `apps/web/features/expansion/ExpansionWorkspace.tsx` — the freshness/lineage
  evidence panel renders persisted snapshot id / observed / ingested /
  correlation id when the API served them, with a `DataSourceBadge`; falls
  back to the documented fixture otherwise (preserving existing e2e).

## Acceptance mapping

| Acceptance | Where |
| --- | --- |
| scheduled and manual ingestion persist canonical outputs | `ExternalIngestionService.ingest` / `run_scheduled` → store `save`; `test_manual_ingestion_persists_*`, `test_scheduled_ingestion_persists_*` |
| DQ quarantine lineage and freshness are queryable | `GET /external-data/quarantine`, run `lineage`, `GET /external-data/freshness`; `test_quarantine_and_lineage_are_queryable`, `test_freshness_reads_persisted_run_state` |
| API and UI read persisted run state | freshness/run/quarantine GETs + expansion overview binding; integration tests + `tsc` typecheck |
| idempotent retry rejection and audit E2E pass | `Idempotency-Key` + window idempotency → `created=False` + `idempotent_replay` audit, incl. across restart; `test_idempotent_retry_rejection_and_audit`, `test_ingestion_run_survives_restart_and_replays` |
