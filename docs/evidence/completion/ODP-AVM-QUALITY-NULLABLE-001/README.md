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
| PostgreSQL | `infra/db/migrations/000023_avm_quality_score_nullable.sql` and Alembic `0017` | Drops `NOT NULL` and `DEFAULT`; old rows retain values and receive `legacy_unknown` status |
| SQLite | `infra/db/migrations/000023_avm_quality_score_nullable_sqlite.sql` | Rebuilds table with nullable score; commits before/after PRAGMAs to retain `foreign_keys=1` and reject orphan child inserts on fresh and restarted engines |
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

## Base advance (2026-09-05)

PR #1149 was ejected from the merge queue as `CONFLICTING` after
ODP-INT-MANUAL-CORRECTION-AUDIT-001 landed on `dev`. Current `dev`
(`6c4a8be8`) was composed into this branch as a merge commit; the approved head
`974c8904` is preserved as the first parent and nothing was rebased or reset.

The two branches had reserved the same migration slots, so this task renumbered
itself off the collision (it is the later arrival):

| Before | After |
|---|---|
| `infra/db/migrations/000021_avm_quality_score_nullable.sql` | `000023_avm_quality_score_nullable.sql` |
| `infra/db/migrations/000021_avm_quality_score_nullable_sqlite.sql` | `000023_avm_quality_score_nullable_sqlite.sql` |
| `infra/db/migrations/versions/0016_avm_quality_score_nullable.py` | `versions/0017_avm_quality_score_nullable.py` |
| Alembic `revision="0016"`, `down_revision="0015"` | `revision="0017"`, `down_revision="0016"` |

`ScriptDirectory.get_heads()` returns the single head `('0017',)`, so the two
tasks form the chain `0015 -> 0016 (manual corrections) -> 0017 (this task)`
rather than two revisions sharing id `0016`.

Cross-layer checks made on the composed tree, not assumed from the pre-merge head:

- `shared/infrastructure/persistence/engine.py` keeps both SQLite bootstrap
  entries, with `000023_..._sqlite.sql` last. The PRAGMA `foreign_keys`
  commit-fencing fix from review round 7 survived the merge verbatim, and dev's
  `000022_durable_manual_corrections.sql` declares no foreign key into
  `data_snapshots`, so running the table rebuild after it is safe.
- `packages/openapi-client/openapi.json` and `src/generated/types.ts` were
  regenerated from the composed app and came out byte-identical to the
  auto-merged files, so neither branch narrowed the other's schema. The
  contract gate reports `0 additive, 1 approved breaking, 0 unapproved
  breaking` — this task's approved `quality_score` nullability change.
- `docs/audits/code-boundary-inventory.csv` was regenerated only after the
  merge index was fully resolved. Regenerating while unmerged paths remain
  produces duplicate rows, because `git ls-files` prints a conflicted path once
  per stage, and the checker still passes on that polluted output.
- `tests/ops/test_migration_backfill.py` needed a hand fix that git did not
  flag: both branches appended a literally identical `"0016",` to the expected
  revision list, so the merge collapsed them into one line instead of
  conflicting. The list is now explicit through `"0017"`.

## Verification

All commands below were run from the task worktree on the current head. The
project virtualenv is CPython 3.12; the repository's default CPython 3.14
environment cannot install the pinned `pgserver==0.1.4` wheel, so every command
is routed through `uv run --frozen`.

### Composed head (base advance onto dev 6c4a8be8)

Re-run in full on the merged tree, not carried over from the pre-merge head:

- `uv run --frozen pytest -q tests/ops/test_migration_backfill.py tests/ops/test_avm_quality_nullable_migration.py tests/integration/test_avm_valuation.py tests/integration/test_avm_deal_outcome.py tests/integration/test_model_ready_materialization.py tests/integration/test_operator_canonical_wiring.py tests/contract/test_openapi_artifact_and_client.py modules/avm/tests/test_deal_outcome_and_calibration.py modules/avm/tests/test_avm_production_execution.py tests/contract/test_manual_correction_contract.py tests/integration/test_manual_correction_persistence.py delivery_toolchain/governance/test_check_measurement_defaults.py` — 208 passed, 0 failed. The last two files are the incoming base's own manual-correction contract and persistence suites, run here to show this task's SQLite table rebuild does not break them.
- `uv run --frozen python delivery_toolchain/openapi/check_drift.py` — API contract gate PASS; artifact and generated client both fresh, `0 additive, 1 approved breaking, 0 unapproved breaking`.
- `uv run --frozen python delivery_toolchain/governance/check_code_boundaries.py` — passed for 1112 files; `cut -d, -f1 docs/audits/code-boundary-inventory.csv | sort | uniq -d` is empty.
- `uv run --frozen python delivery_toolchain/governance/check_measurement_defaults.py` — passed: 15 known (dataclass 6, mapper 4, sql 5), 15 exempted with an owner; next expiry 2026-10-31.
- `uv run --frozen ruff check <the 20 .py files changed vs origin/dev>` — All checks passed. Repository-wide `ruff check .` reports 8 errors, all in five `docs/evidence/runtime/**` scripts owned by other tasks; `git diff origin/dev HEAD -- <each file>` is empty for all five, so this branch neither introduced nor can fix them.
- `git diff --check origin/dev...HEAD` — clean.
- Alembic graph: single head `0017`, chain `0015 -> 0016 -> 0017`.

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
