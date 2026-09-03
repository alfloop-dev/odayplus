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
| Domain consumers | `normalize_margin`, `value_store`, `build_model_valuation_report` | Reject missing quality before deriving confidence or a report |
| Durable case | `shared/infrastructure/persistence/repositories.py::DurableAVMRepository` | Pickle persistence preserves `None` without coercion |
| PostgreSQL | `infra/db/migrations/000017_avm_quality_score_nullable.sql` and Alembic `0011` | Drops `NOT NULL` and `DEFAULT`; old rows retain values and receive `legacy_unknown` status |
| SQLite | `infra/db/migrations/000017_avm_quality_score_nullable_sqlite.sql` | Rebuilds the table with nullable score; copies old values unchanged and preserves status on restart |
| API contract | `packages/openapi-client/openapi.json` | `quality_score` is `number | null` with no default and is not required |
| TypeScript | `packages/openapi-client/src/generated/types.ts`, `src/index.ts` | `quality_score?: number | null` |
| Governance | `delivery_toolchain/governance/measurement_default_exemptions.json` | AVM exemption removed in the same change |

## Legacy data handling

The forward migration does not update historical `quality_score = 1.00` rows to
`NULL`: the old schema cannot distinguish measured perfect quality from an
omitted score. Existing rows keep their value and are marked
`quality_score_status = 'legacy_unknown'`. New writers can explicitly use
`measured` or `unmeasured`; a missing status remains conservative rather than
being treated as a perfect measurement.

## Search boundary

The task-scoped search covered `modules/avm`, `apps/api/app/routes/avm.py`,
`infra/db/migrations`, `packages/openapi-client`, `shared/infrastructure/persistence`,
and tests. The AVM quality paths no longer contain `quality_score`/`data_quality_score`
fallbacks to `1.0`; the remaining `1.0` values in the searched tree belong to
unrelated bounded defaults or historical migrations. The governance checker
was run to ensure the AVM dataclass exemption is no longer live.

## Verification

- `pytest -q modules/avm/tests/test_deal_outcome_and_calibration.py tests/integration/test_avm_valuation.py tests/integration/test_avm_deal_outcome.py tests/integration/test_operator_canonical_wiring.py tests/ops/test_avm_quality_nullable_migration.py` — passed.
- `pytest -q tests/ops/test_avm_quality_nullable_migration.py tests/contract/test_openapi_artifact_and_client.py -k 'avm_quality or generated_client_matches_the_artifact or artifact_is_checked_in_and_matches_the_live_app'` — passed.
- `python delivery_toolchain/governance/check_measurement_defaults.py` — passed; 10 remaining exemptions are unrelated to AVM.

The repository's default CPython 3.14 environment cannot install the pinned
`pgserver==0.1.4` wheel. Verification used the available CPython 3.12
interpreter via `uv run --python /home/lupin/.local/bin/python3.12`.
