# ODP-AVM-QUALITY-NULLABLE-001

## Disposition

`ValuationInput.quality_score` now represents an optional measurement. Omitted
and explicit `null` values remain `None` through API parsing, domain mapping,
durable case serialization, and API responses. The normalize/value boundary
rejects an unmeasured input with the named reason
`quality_score is required before AVM valuation; input quality is unmeasured`.
No valuation report or valuation card is persisted on that path.

## Field lineage

| Layer | Owner symbol / artifact | Missing-value behavior |
|---|---|---|
| API request | `apps/api/app/routes/avm.py::AVMCasePayload.quality_score` | Optional `float | None`, default `None`; omission and JSON `null` are retained |
| Domain parser | `modules/avm/domain/valuation.py::ValuationInput.from_mapping` | `quality_score` and legacy `data_quality_score` alias are bounded only when present; no perfect fallback |
| Domain case | `modules/avm/domain/valuation.py::ValuationInput.to_dict` | Emits `quality_score: null` so absence remains observable |
| Service boundary | `modules/avm/application/valuation.py::AVMService.normalize` | Rejects before changing case state or writing a margin |
| Domain consumers | `normalize_margin`, `value_store`, `build_model_valuation_report` | Reject missing quality before deriving confidence or a report; idempotently downgrade opaque legacy margins |
| Durable case | `shared/infrastructure/persistence/repositories.py::DurableAVMRepository` | Pickle persistence preserves `None` without coercion; only cases whose payload predates the status field are persisted as `legacy_unknown` |
| Historical reports | `ValuationReport.with_legacy_quality_disposition`, `latest_report`, `report_history` | Legacy reports are persisted as `legacy_unknown_downgraded`, expose only `low` confidence, and cannot carry an actionable old approval |
| Historical data rooms | `DataRoom.with_legacy_quality_disposition`, `get_dataroom` | Legacy rooms retain historical prices for audit but expose a named low-confidence downgrade; rebuild and export are rejected |
| PostgreSQL | `infra/db/migrations/000021_avm_quality_score_nullable.sql` and Alembic `0016` | Drops `NOT NULL` and `DEFAULT`; old rows retain values and receive `legacy_unknown` status |
| SQLite | `infra/db/migrations/000021_avm_quality_score_nullable_sqlite.sql` | Rebuilds table with nullable score; commits before/after PRAGMAs to retain `foreign_keys=1` and reject orphan child inserts on fresh and restarted engines |
| API contract | `packages/openapi-client/openapi.json` | `quality_score` is `number | null` with no default and is not required |
| TypeScript | `packages/openapi-client/src/generated/types.ts`, `src/index.ts` | `quality_score?: number | null` |
| Governance | `delivery_toolchain/governance/measurement_default_exemptions.json` | AVM exemption removed in the same change |

## Legacy data handling

The forward migration does not update historical `quality_score = 1.00` rows to
`NULL`: the old schema cannot distinguish measured perfect quality from an
omitted score. Existing rows keep their value and are marked
`quality_score_status = 'legacy_unknown'`. New writers (such as `LineageManifest.to_audit_snapshot_row()`)
explicitly emit `measured` or `unmeasured` status. In `DurableAVMRepository`, legacy
pickled cases lacking an explicit status marker are migrated on retrieval to
`quality_score_status = 'legacy_unknown'`, and that marker is written back to the
case.

"Lacking an explicit status marker" means the stored payload predates the field,
not that a caller omitted it. `ValuationInput.is_pre_status_payload` reports
whether `quality_score_status` is absent from the instance dict, which can only
happen when unpickling a record written by the previous release: `__init__`
always writes every field. A freshly built input that carries a measured
`quality_score` and leaves the status out therefore resolves to `measured` and
keeps its confidence and price on both the in-memory and durable paths;
`DurableAVMRepository._migrate_legacy_case` rewrites only pre-status payloads.
`test_fresh_input_with_omitted_status_is_measured_not_legacy` covers the domain,
in-memory, and durable entry points, and the legacy tests now simulate old
records by removing the field from the payload rather than by passing `None`.

When valued, legacy cases with `legacy_unknown` status receive a named
`legacy_quality_unknown_discount` and conservative `low` confidence even when a
high-confidence margin was already persisted; the service saves that downgraded
margin before entering the formula or approved production executor, and marks the
report before returning it. This prevents the existing-margin fast path from
bypassing the legacy disposition. Historical reports and data rooms are also migrated
on read: their prices remain available for audit, but the report and valuation card are
marked `legacy_unknown_downgraded`, all exposed confidence is `low`, and any old finance
approval is retained only under `legacy_finance_approval`. New finance approval, data-room
rebuild, and data-room export require recomputation with measured quality.

## Search boundary

The task-scoped search covered `modules/avm`, `apps/api/app/routes/avm.py`,
`infra/db/migrations`, `packages/openapi-client`, `shared/infrastructure/persistence`,
`shared/infrastructure/persistence/model_ready.py`, and tests. The AVM quality paths
no longer contain `quality_score`/`data_quality_score` fallbacks to `1.0`; the existing
margin production-entry path is covered for persisted legacy data. The remaining
`1.0` values in the searched tree belong to unrelated bounded defaults or historical
migrations. The governance checker was run to ensure the AVM dataclass exemption is no
longer live.

## Verification

All commands below were run from the task worktree on the current head. The
project virtualenv is CPython 3.12; the repository's default CPython 3.14
environment cannot install the pinned `pgserver==0.1.4` wheel, so every command
is routed through `uv run --frozen`.

### Current head (fresh-vs-legacy status discrimination)

- `uv run --frozen pytest -x -q tests/integration/test_avm_valuation.py` — 13 passed, including `test_fresh_input_with_omitted_status_is_measured_not_legacy` (a fresh measured input with an omitted status keeps `measured` status, `high` confidence, and an undiscounted margin on the domain, in-memory, and durable paths) and the legacy read-path tests, which now simulate old records by removing the field from the payload.
- `uv run --frozen pytest -q modules/avm/tests/test_deal_outcome_and_calibration.py tests/integration/test_avm_deal_outcome.py tests/integration/test_operator_canonical_wiring.py tests/ops/test_avm_quality_nullable_migration.py tests/integration/test_model_ready_materialization.py modules/avm/tests/test_avm_production_execution.py tests/ops/test_migration_backfill.py` — 72 passed, including `test_sqlite_engine_enforces_foreign_keys_and_rejects_orphan_child_inserts_on_fresh_and_restart` and the migration-plan contract.
- `uv run --frozen pytest -q tests/contract/test_openapi_artifact_and_client.py` — 23 passed.
- `uv run --frozen ruff check modules/avm/domain/valuation.py shared/infrastructure/persistence/repositories.py tests/integration/test_avm_valuation.py` — passed.
- `uv run --frozen python delivery_toolchain/governance/check_measurement_defaults.py` — passed: 15 known (dataclass 6, mapper 4, sql 5), 15 exempted with an owner; next expiry 2026-10-31.
- `git diff --check` and `git diff --check origin/dev...HEAD` — passed; no whitespace or end-of-file findings.

### Earlier heads on this branch

- `uv run pytest -q tests/integration/test_avm_valuation.py -k 'persisted_legacy_margin'` — passed; durable persisted-margin value-entry regression.
- `uv run ruff check modules/avm/application/valuation.py modules/avm/domain/__init__.py modules/avm/domain/valuation.py tests/integration/test_avm_valuation.py tests/integration/test_model_ready_materialization.py` — passed.
- `make api-contract` and `uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling` — passed on the OpenAPI breaking-change approval head.
