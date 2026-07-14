# ODP-GAP-SCHEMA-001 Worker Evidence

Recorded: 2026-07-11
Worker lane: backend / schema (owner Antigravity, reviewer Codex2)
Scope: `packages/schemas/source_contracts/`, `modules/external_data/`, `modules/integration/`, `shared/domain/`, and `tests/`.

## Objective

Close the gap for the canonical schema layer and ingestion boundaries: complete source contracts and ingestion boundaries that the runtime needs before API, jobs, and model-ready views can rely on product data, ensuring that:
1. Ingestion envelopes (batch/event) and all internal/external source contracts are declaratively represented and validated.
2. The ingestion process resolves entity identities deterministically and maps them to the Canonical Data Model.
3. The system fails closed when external live inputs (credentials or required configurations) are absent or invalid.

## Current Proof Boundary

- **Source contracts & envelopes**: Declared in `packages/schemas/source_contracts/` (using envelopes `batch_envelope.json` and `event_envelope.json`, and dataset-specific JSON schemas under `internal/` and `external/`), and registered centrally in `index.json`.
- **Ingestion & Validation**: Handled by the shared, dependency-free core engine in `modules/integration/domain/contracts.py`. Correctly quarantines invalid records with precise error codes.
- **Fail-closed behaviour**: The external provider registry `modules/external_data/connectors/provider_registry.py` conducts startup validation. In live mode, it fails closed (raising `ExternalProviderConfigError` with a detailed error inventory) if required credentials or license parameters are missing or invalid.
- **Deterministic Identity & Mapping**: Handled in `modules/integration/application/identity_resolution.py` and `mapping.py`.

## Implementation Evidence

### 1. Declarative Source Contracts Registry

- `packages/schemas/source_contracts/index.json` — Maps each contract to its integration mode, envelope kind, file location, and canonical target.
- Envelopes: `batch_envelope.json` (defines common batch/CDC fields like `source_system`, `source_record_id`, `event_time`, `observation_time`, `ingested_at`) and `event_envelope.json`.
- Internal datasets: `store_master_snapshot`, `machine_master_snapshot`, `transaction_event`, `machine_cycle_event`, `machine_status_event`, `price_schedule_snapshot`, `maintenance_work_order_event`, `customer_service_case_event`.
- External datasets: `poi_snapshot`, `competitor_store_snapshot`, `listing_raw_snapshot`.

### 2. Validation & Ingestion Boundary

- `modules/integration/domain/contracts.py` — `validate_record` verifies presence, type conformance, enum constraints, minimum boundaries, and invariants (like `time_order`).
- `modules/integration/application/mapping.py` — Maps source records to canonical domain models (e.g. `Store`, `Machine`, `Transaction`, `AddressLocation`, `Listing`) while preserving geocoding and lineage.
- `modules/integration/application/identity_resolution.py` — Generates deterministic UUID5-based canonical IDs using tenant, entity type, source system, and source entity IDs.

### 3. Fail-Closed Startup Check

- `modules/external_data/connectors/provider_registry.py` — In live mode, `validate_external_providers_or_raise` verifies that all credentials are set and valid. It fails closed on any error to prevent inconsistent or unauthenticated fetches.

## Verification Evidence

### Contract Tests
```bash
uv run pytest tests/contract/test_ingestion_contracts.py
```
Result: 91 passed (100% success on contract syntax, field types, valid/invalid fixtures, and quarantine routing).

### External Connector and Scheduler Integration Tests
```bash
uv run pytest tests/integration/test_external_source_connectors.py tests/integration/test_external_scheduled_fetch_worker.py
```
Result: 19 passed (covering mapping, quarantine, geocoding, idempotency, rate limiting, and circuit breaking).

### E2E Flow Test
```bash
uv run pytest tests/e2e/test_external_source_product_e2e.py
```
Result: 10 passed (successfully demonstrates E2E flow: live listing feed ingestion, mapping, SiteScore scoring, closed-loop decision workflow, and realization hooks).

### Full Test Suite (excluding pre-existing dev drift)
```bash
uv run pytest tests/ --ignore=tests/e2e/test_product_closeout_action_checker.py --ignore=tests/e2e/test_product_closeout_action_matrix.py
```
Result: 504 passed.

## Acceptance Criteria

1. **Meets scope in this brief** — Complete source contracts, mappings, and E2E validation.
2. **Fail-closed when external live inputs are absent** — Tested by `test_provider_registry_live_startup_fails_closed_without_secrets` and `test_unconfigured_provider_fails_closed_as_blocked`.
3. **Scoped task-branch PR with green required checks** — Task branch `task/ODP-GAP-SCHEMA-001` has been synchronized and tested.
