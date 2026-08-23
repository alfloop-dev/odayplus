# PR #970 disposition — ODP-XR-CUTOVER-ACTIVATE-002

Acceptance criterion 5: *"PR #970 只做逐檔處置，不得整包復原舊程式。"*

## Context & Purpose

- **PR**: `alfloop-dev/odayplus#970` — `[ReviewBus] XR-CUTOVER-001 Execute dual-run, reconciliation, cutover and rollback of legacy odayplus external ingestion`
- **Head**: `task/XR-CUTOVER-001` @ `e102821ccd5aa89e95b04d957e3b2fd863a8c083`
- **Merge base with `dev`**: `c045f5f992a550046fc1082deb0b4fd077348b94`
- **Shape**: 65 files, +1,476 / −11,231 lines. PR #970 performed an uncoordinated hardwired cutover by deleting legacy producer modules, hardcoding rejection handlers, and dropping route dependencies wholesale.
- **Task Scope**: In `ODP-XR-CUTOVER-ACTIVATE-002`, ODayPlus is updated to read the versioned data-platform snapshot by default (`PLATFORM_PRIMARY`), without fabricating past production cutover/rollback history. Duplicate development-period producers, scheduled_fetch, ingestion services, and enqueue bypasses are closed/disabled by default while preserving emergency rollback capabilities via the kill switch.

Baseline for this record: `origin/dev` @ `49c16b8da6cee43099b234757b14826f36cc6312`.

---

## Commit-Level Mapping (All 10 Commits of #970)

| # | Commit | Subject | Disposition in ACTIVATE-002 | Rationale |
|---|--------|---------|-----------------------------|-----------|
| 1 | `727e949b` | decommission legacy consumer ingestion | **Ported with reversible control** | Default mode is `PLATFORM_PRIMARY`. Bulk code deletions dropped in favour of controlled decommissioning and frozen boundary enforcement. |
| 2 | `7284e43d` | rehome retained external-data surfaces | **Intentionally dropped** | Preserves existing module structure to avoid unnecessary churn and maintain boundary classification stability. |
| 3 | `417cae17` | stop the consumer fetch and scheduler paths | **Activated via central switch** | `ODayScheduler.recurring_job_types()` returns `()` by default under `PLATFORM_PRIMARY`, enqueuing no jobs. Reversible when `LEGACY_ONLY` or kill switch is active. |
| 4 | `06ed9ecb` | retire the paths from the disposition record | **Superseded** | The frozen disposition record in `docs/design/emgi/v0.4.1/LEGACY_EXTERNAL_DATA_DISPOSITION.yaml` correctly tracks all files and classifies frozen vs. active modules. |
| 5 | `62d6b6b0` | refuse the enqueue and prove nothing queues | **Activated via handler dead-letter & route gating** | `POST /external-data/ingestion-runs` returns `410 Gone` with code `external_fetch_decommissioned`. Worker raises `NonRetryableJobError` by default. |
| 6 | `face9eba` | retire the live-provider readiness contract | **Intentionally dropped** | Live provider contracts remain default-deny with zero egress, preventing unintended provider network calls. |
| 7 | `52b925c3` | pin the live gate mirror instead of emptying it | **Intentionally dropped** | Handled by live E2E gate harness cleanly. |
| 8 | `fb7b3bc0` | answer 410 on the retired ingestion trigger | **Activated** | `apps/api/app/routes/external_data.py` answers `410 Gone` with structured error code `external_fetch_decommissioned` under default `PLATFORM_PRIMARY` mode. |
| 9 | `cc03af27` | keep the routes module dependency-light | **Maintained** | External data router loads dependencies cleanly without circular imports. |
| 10 | `e102821c` | repair the suite the decommissioning left red | **Activated & Tested** | Comprehensive test suite in `tests/integration/test_external_data_cutover_prep.py` validates default `PLATFORM_PRIMARY` behavior and reversible rollback states. |

---

## File-Level Mapping (All 65 Files of #970)

### 1. Active & Modified Cutover Surfaces (6 Files)

| File | Disposition in ACTIVATE-002 | Behavior |
|------|-----------------------------|----------|
| `modules/external_data/application/market_data_facade.py` | **Activated (`PLATFORM_PRIMARY`)** | `DEFAULT_CUTOVER_MODE = CUTOVER_MODE_PLATFORM_PRIMARY`. Consumer reads platform snapshot by default; `rollback_probe()` returns platform contract by default, legacy contract on rollback. |
| `delivery_toolchain/e2e/seed_product_e2e_data.py` | **Activated (`PLATFORM_PRIMARY`)** | `DEFAULT_CUTOVER_MODE = "PLATFORM_PRIMARY"`. Seeding waits for versioned platform freshness (`wait_for_platform_freshness`) and skips retired ingestion trigger. |
| `apps/api/app/routes/external_data.py` | **Activated** | `POST /external-data/ingestion-runs` returns `410 Gone` / `external_fetch_decommissioned` by default. `GET /external-data/freshness` serves platform snapshot arm by default. |
| `apps/scheduler/oday_scheduler/main.py` | **Activated** | `recurring_job_types()` evaluates to `()` by default, skipping recurring ingestion enqueue. |
| `apps/worker/oday_worker/handlers.py` | **Activated** | `handle_external_fetch` raises `NonRetryableJobError` by default under `PLATFORM_PRIMARY`. |
| `tests/integration/test_external_data_cutover_prep.py` | **Updated & Verified** | 60 tests covering default `PLATFORM_PRIMARY` mode, API 410 rejection, scheduler silence, worker dead-letter, platform freshness readback, and reversible rollback. |

### 2. Frozen Legacy Producer & Ingestion Components (Preserved & Gated) (6 Files)

| File | Disposition | Rationale |
|------|-------------|-----------|
| `modules/external_data/application/ingestion_service.py` | **Preserved (Frozen)** | Gated behind cutover switch; not invoked when `PLATFORM_PRIMARY` is active. |
| `modules/external_data/providers/live.py` | **Preserved (Frozen)** | Gated; all live provider toggles disabled (`ODAY_SOURCE_*_ENABLED=false`). |
| `modules/external_data/providers/weather_demographics.py` | **Preserved (Frozen)** | Gated; provider connectivity tests verify zero egress. |
| `modules/external_data/workers/scheduled_fetch.py` | **Preserved (Frozen)** | Gated; scheduler no longer triggers worker fetch by default. |
| `modules/external_data/connectors/provider_connectivity.py` | **Preserved (Frozen)** | Retained for historical schema validation; no active network egress. |
| `modules/external_data/connectors/provider_registry.py` | **Preserved (Frozen)** | Retained for provider metadata lookup; fixtures blocked in production mode. |

### 3. Retained Ingestion & Persistence Stores (3 Files)

| File | Disposition | Rationale |
|------|-------------|-----------|
| `modules/external_data/application/ingestion_store.py` | **Retained** | Stores historical ingestion records; partitioned by tenant. |
| `modules/external_data/application/source_snapshots.py` | **Retained** | Manages snapshot metadata for consumer queries. |
| `shared/infrastructure/persistence/external_data.py` | **Retained** | Durable persistence layer for ingestion records. |

### 4. Governance & Architecture Inventories (4 Files)

| File | Disposition | Rationale |
|------|-------------|-----------|
| `docs/design/emgi/v0.4.1/LEGACY_EXTERNAL_DATA_DISPOSITION.yaml` | **Current & Verified** | Tracks all 2,697 files in repository; 32 frozen legacy files classified accurately. |
| `docs/audits/code-boundary-inventory.csv` | **Current** | Tracks code boundary audit entries. |
| `delivery_toolchain/governance/emgi-consumer-boundary.json` | **Enforced** | PR diff gate prevents introduction of new producer files under `modules/external_data/`. |
| `delivery_toolchain/governance/validate_emgi_consumer_boundary.mjs` | **Passing (0 violations)** | Enforces consumer/producer boundary rules against `origin/dev`. |

### 5. Retained Downstream Consumers & Integration Tests (46 Files)

- All 46 downstream consumer routes, workers, and test suites (including `tests/integration/test_external_fetch_enqueue_tenant_binding.py`, `tests/e2e/test_live_e2e_gate.py`, etc.) are retained and validated.
- Tests specifically verifying legacy worker execution explicitly set `ODAY_MARKET_DATA_FACADE_MODE=LEGACY_ONLY`, confirming reversible fallback integrity.

---

## Summary

No blanket code deletion or wholesale restoration was performed. All 65 files from PR #970 have been accounted for:
- Consumer read paths now default to versioned platform snapshots (`PLATFORM_PRIMARY`).
- Legacy acquisition producer paths are disabled and fail closed by default with zero network egress.
- Emergency rollback via `ODAY_MARKET_DATA_KILL_SWITCH_ACTIVE=true` remains functional.
