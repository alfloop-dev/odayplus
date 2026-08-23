# PR #970 disposition — ODP-XR-CUTOVER-PREP-002

Acceptance criterion 5: *"Record an exact mapping of #970 commits/files that were
ported, rewritten, superseded or intentionally dropped."*

## What #970 was, and why it was not merged

- PR: `alfloop-dev/odayplus#970` — `[ReviewBus] XR-CUTOVER-001 Execute dual-run,
  reconciliation, cutover and rollback of legacy odayplus external ingestion`
- Head: `task/XR-CUTOVER-001` @ `e102821ccd5aa89e95b04d957e3b2fd863a8c083`
- Merge base with `dev`: `c045f5f992a550046fc1082deb0b4fd077348b94`
- State at the time of this task: **OPEN**, base stale by many commits.
- Shape: 65 files, +1 476 / −11 231. It **performed** the cutover — it deleted
  `providers/live.py` (2 209 lines), `providers/weather_demographics.py` (816),
  `workers/scheduled_fetch.py` (800) and `application/ingestion_service.py`
  (474), and hardwired the API, scheduler and worker into their refusing form.

This task rebuilds the *consumer* intent from current `dev` and makes it
**reversible**. Four of the files #970 deleted are named in this task's
`forbidden_paths`, so the deletion is not merely deferred by preference — it is
out of scope by contract, and belongs to an explicit later activation task.

Baseline for this record: `origin/dev` @ `49c16b8da6cee43099b234757b14826f36cc6312`.

## Commit-level mapping

| # | Commit | Subject | Disposition |
|---|--------|---------|-------------|
| 1 | `727e949b` | decommission legacy consumer ingestion | **Partly ported** — only the `market_data_facade.py` hunk. Every deletion **intentionally dropped**. |
| 2 | `7284e43d` | rehome retained external-data surfaces | **Intentionally dropped** — a package reshuffle a reversible switch does not need. |
| 3 | `417cae17` | stop the consumer fetch and scheduler paths | **Rewritten** — the scheduler stop is conditional now. Its rehoming and test deletions **dropped**. |
| 4 | `06ed9ecb` | retire the paths from the disposition record | **Superseded** — the frozen surfaces stay, so the v2 record must keep naming them. |
| 5 | `62d6b6b0` | refuse the enqueue and prove nothing queues | **Rewritten** — refusal is conditional and lives in the worker handler. |
| 6 | `face9eba` | retire the live-provider readiness contract | **Intentionally dropped** — retiring the live-provider contract is an activation-time decision, not a prep-time one. |
| 7 | `52b925c3` | pin the live gate mirror instead of emptying it | **Intentionally dropped** — `check_live_e2e_gate.py` is outside this task's owned paths. |
| 8 | `fb7b3bc0` | answer 410 on the retired ingestion trigger | **Rewritten** — 410 and its error code are kept, gated on `PLATFORM_PRIMARY`. |
| 9 | `cc03af27` | keep the routes module dependency-light | **Ported (as intent)** — the cutover imports are function-scoped for this reason. |
| 10 | `e102821c` | repair the suite the decommissioning left red | **Intentionally dropped** — nothing is decommissioned, so nothing is left red. |

## File-level mapping (all 65 files of #970)

### Ported — carried over, with the same names and semantics (1)

| File | What was ported |
|---|---|
| `modules/external_data/application/market_data_facade.py` | `FACADE_MODE_ENV`, `KILL_SWITCH_ENV`, `ROLLBACK_PROBE_SITE_ID`, `rollback_probe()` and `_RollbackProbeClient` — including the docstring reasoning about the stable fallback payload, because a producer-side verifier consumes this contract across repos. Extended, not replaced: a `DUAL_RUN` arm and `get_platform_snapshot()` were added, and the default moved from `PLATFORM_PRIMARY` to `LEGACY_ONLY` so an unconfigured deployment probes as rolled back. |

### Rewritten — same intent, made conditional and reversible (4)

| File | #970 | This task |
|---|---|---|
| `apps/api/app/routes/external_data.py` | Deleted the trigger's implementation; the route unconditionally raises `410 Gone` / `external_fetch_decommissioned`. Dropped all route dependencies. | Same status, same error code, same operator guidance — raised only when the mode is `PLATFORM_PRIMARY`, from a dependency registered *first* so it still answers before the authz and live-provider guards (#970's stated reason for dropping them). `/freshness` gains the platform arm: compared under `dual_run`, authoritative under `PLATFORM_PRIMARY`. |
| `apps/scheduler/oday_scheduler/main.py` | `RECURRING_JOB_TYPES: tuple[str, ...] = ()` class constant; `run_once` deleted the enqueue outright. | `recurring_job_types()` resolves per tick from the switch and returns `("external-fetch",)` or `()`. The enqueue body is untouched and still runs at the default mode. #970's kept-tenant-guard reasoning is preserved verbatim in behaviour: the guard runs first, cut over or not. |
| `apps/worker/oday_worker/handlers.py` | Deleted the handler body; raises `NonRetryableJobError` unconditionally. | Same dead-letter, same reasoning about keeping the job type registered (ported into the docstring), raised only when the switch says fetch is off. The legacy body is intact below it. |
| `apps/api/oday_api/main.py` | Rehomed imports across several commits. | One added keyword argument passing the already-resolved facade into the route, so the snapshot read path is reachable without a second transport. |

### Superseded — `dev` already carries a different or better answer (3, plus one note)

| File | Why superseded |
|---|---|
| *(note — counted under Ported, not double-counted here)* `modules/external_data/application/market_data_facade.py` | #970 created it at 59 lines. `dev` carries the 622-line ODP-LEGACY-FACADE-001 facade with the full authorized read surface. This task extends `dev`'s version. |
| `docs/design/emgi/v0.4.1/LEGACY_EXTERNAL_DATA_DISPOSITION.yaml` | #970 removed the retired paths from the frozen inventory. Those paths still exist here, and the v2 inventory fails in *both* directions, so removing them would break the record it is meant to keep exact. `scripts/validate_external_data_boundary.py` passes unchanged (2 686/2 686 classified). |
| `docs/audits/code-boundary-inventory.csv` | Refreshed on `dev` by ODP-NETPLAN-001 (`8d4fe789`) after #970 was opened. |
| `tests/architecture/test_external_data_boundary.py` | #970 adjusted it to the shrunken tree. `dev`'s version passes against the intact tree (69 passed). |

### Intentionally dropped — the decommission itself, and everything downstream of it (57)

Counts below are against the rename-aware diff
(`git diff --name-status -M c045f5f9 e102821c`), so a file #970 *moved* is
counted once at its new path rather than as a delete plus an add.

**Producer deletions (4).** `modules/external_data/application/ingestion_service.py`,
`providers/live.py`, `providers/weather_demographics.py`,
`workers/scheduled_fetch.py`. All four are named in this task's
`forbidden_paths`. Deleting them *is* the activation, and the acceptance
criteria state it may happen only when an explicit later task authorizes it,
after live readback.

**Package rehoming (11).** Three renames out of the frozen surfaces —
`application/ingestion_store.py` → `application/ingestion_records.py`,
`application/source_snapshots.py` → `assisted/source_snapshots.py`,
`providers/taiwan_real_estate.py` → `official_records/taiwan_real_estate.py` —
plus their four new package files (`assisted/__init__.py`,
`geo/geocode_errors.py`, `geo/geocode_payloads.py`,
`official_records/__init__.py`) and the four `__init__.py` edits that rewire
them (`modules/external_data/`, `application/`, `providers/`, `workers/`).
These moves only pay off once the deleted modules are actually gone; doing them
now would churn import paths for no reversible gain, and two of the three
renames move files that the v2 frozen inventory pins by exact path.

**Frozen-surface edits (2).** `connectors/provider_connectivity.py`,
`connectors/provider_registry.py` — repairs for a decommissioning that is not
happening here.

**Consumer and ops edits outside this task's owned paths (11).**
`apps/data_platform/geography_backfill.py`,
`apps/worker/assisted_listing_intake/worker.py`,
`delivery_toolchain/e2e/check_live_e2e_gate.py`,
`delivery_toolchain/release/assisted_listing_intake/drills.py`,
`modules/opsboard/application/network_listings.py`,
`product_ops/deployment/cloud_run_job_entrypoint.py`,
`product_ops/deployment/validate_cloud_run_live_deployment.py`,
`product_ops/external_data_backfill.py`,
`product_ops/modeling/real_estate_outcomes.py`,
`shared/infrastructure/persistence/external_data.py`,
`shared/infrastructure/persistence/factory.py`.

**Test deletions (10).** `tests/data/test_external_providers.py`,
`tests/e2e/test_external_source_product_e2e.py`,
`tests/integration/test_external_ingestion_multisource.py`,
`test_external_ingestion_persistence.py`,
`test_external_scheduled_fetch_worker.py`,
`test_live_geocode_provider_adapter.py`,
`test_live_listing_provider_adapter.py`, `test_live_snapshot_providers.py`,
`test_scheduled_ingestion_tenant_propagation.py`,
`test_worker_scheduler_runtime.py`. Each covers behaviour that still exists, so
deleting them would remove live coverage. The last two pass unchanged here and
are in this task's verification set.

**Test edits that followed those deletions (19).**
`tests/data/test_geo_pipeline.py`, `test_taiwan_real_estate_outcomes.py`,
`tests/e2e/test_live_e2e_gate.py`,
`tests/integration/test_assisted_listing_snapshots.py`,
`test_external_fetch_enqueue_tenant_binding.py`,
`test_external_listing_live_ingestion.py`,
`test_external_provider_connectivity.py`, `test_external_provider_registry.py`,
`test_official_real_estate_postgresql.py`,
`test_operator_live_provenance_health.py`, `test_operator_live_repository.py`,
`test_place_geography_backfill.py`, `test_production_api_composition.py`,
`tests/ops/test_cloud_run_job_entrypoint.py`,
`test_cloud_run_live_deployment.py`,
`tests/reliability/test_concurrency_recovery.py`, `test_cross_flow_gate.py`,
`test_runtime_observability.py`,
`tests/security/test_assisted_listing_snapshot_residency.py`.

**Total: 4 + 11 + 2 + 11 + 10 + 19 = 57.**
With 1 ported, 4 rewritten and 3 superseded, that accounts for all 65 files.

## What this task added that #970 did not have

| Addition | Why |
|---|---|
| `DUAL_RUN` mode | #970 had only platform-primary and legacy-fallback. A cutover with no comparison state has to be decided blind. |
| `LEGACY_ONLY` as the default | #970 defaulted to `PLATFORM_PRIMARY`, which would cut a deployment over on deploy. Preparing a cutover must not perform one. |
| Kill switch evaluated *before* the mode is validated | The rollback lever must not be blocked by a typo in the variable being rolled back from. |
| Refusal on an unreadable mode | Silently continuing to fetch while an operator believes they cut over is the failure the switch exists to prevent. Each consumer surfaces it in its own idiom (HTTP 500 with a code, a loud skipped tick, a first-attempt dead letter). |
| `MarketDataFacade.get_platform_snapshot()` | The read path the cutover moves `/external-data/freshness` onto: published release provenance in the legacy wire shape, so a dual run compares like with like. |
| One switch, four consumers | A scheduler that enqueues while the worker dead-letters produces nothing but dead letters. All four read the same resolver. |
| Cut-over-aware E2E seeding | Acceptance criterion 3: the retired trigger is not called once the mode selects the cutover. |

## Verification

| Command | Result |
|---|---|
| `uv run pytest tests/integration/test_external_data_cutover_prep.py -q` | **59 passed** |
| `node delivery_toolchain/governance/validate_emgi_consumer_boundary.mjs --base-sha origin/dev --head-sha HEAD` | **0 violations**, exit 0 |
| `uv run python scripts/validate_external_data_boundary.py` (v2 whole-tree) | **OK** — 2 686/2 686 classified, 0 unclassified, 32 frozen files intact |
| `uv run pytest tests/architecture/test_external_data_boundary.py -q` | **69 passed** |
| Adjacent suites: `tests/e2e/test_seed_product_e2e_data.py`, `tests/contract/test_platform_api.py`, `tests/integration/test_external_fetch_enqueue_tenant_binding.py`, `test_scheduled_ingestion_tenant_propagation.py`, `test_worker_scheduler_runtime.py`, `tests/ops/test_cloud_run_job_entrypoint.py`, `tests/reliability/test_runtime_observability.py` | **155 passed** |
| `uv run ruff check .orchestrator delivery_toolchain scripts tests modules apps shared models solver pipelines infra` | clean |
| `uv run python delivery_toolchain/openapi/check_drift.py --base-ref origin/dev` | **PASS** — 0 additive, 0 breaking |
| `make product-e2e-gate` | **Not runnable in this environment.** Fails at `check_product_release_gate.py --dev-merge` with `Cannot find module '@playwright/test'`; `node_modules` is not installed in this worktree or the main checkout. Measured against a clean `origin/dev` worktree (`49c16b8d`, no changes from this task): **identical failure**, so it is a pre-existing environment gap, not a regression. This diff contains no JS/TS changes. CI installs the workspace via `npm ci` and runs the gate there. |

The E2E behaviour the gate would exercise is covered directly instead: the seed
script's cut-over branch, its wait-for-platform-snapshot path and its
anti-drift pin against the facade constants are all unit-tested, and the
pre-existing `tests/e2e/test_seed_product_e2e_data.py` passes unchanged —
including its assertion that the default seed still posts to the trigger.
