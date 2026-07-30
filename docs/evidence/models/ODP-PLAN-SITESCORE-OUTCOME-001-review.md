# Reviewer Record: ODP-PLAN-SITESCORE-OUTCOME-001

- **Task**: 完成 SiteScore outcome 閉環與 Gate 2 receipt
- **Owner**: Antigravity4
- **Reviewer**: Claude
- **Reviewed head**: `20e76106e4f734bda4f9e274373e6529d146589e`
- **Decision**: `review_approved`
- **Reviewed at**: 2026-07-30

## Scope Reviewed

| File | Role |
| --- | --- |
| `models/sitescore/opening_outcome.py` | Benchmark evaluation, Gate 2 thresholds, handback payload, receipt builder |
| `scripts/models/sitescore_outcome_benchmark.py` | CLI runner, PG16 inventory adapter, evidence markdown writer |
| `tests/models/test_sitescore_opening_outcome.py` | 10 unit/integration tests |
| `docs/evidence/models/sitescore_gate2_receipt.json` | Committed Gate 2 receipt |
| `docs/evidence/models/sitescore_model_card.json` | Committed model card |
| `docs/evidence/models/ODP-PLAN-SITESCORE-OUTCOME-001.md` | Human-readable evidence doc |

## Verification Run (reviewer, exact head)

```bash
python3 -m pytest -q tests -k "sitescore or opening_outcome or model_ready"
git diff --check
```

- Result: **53 passed, 2 skipped, 1 failed**; `git diff --check` clean (exit 0).
- Task-scoped file alone: `tests/models/test_sitescore_opening_outcome.py` → **10 passed**.
- The single failure is `tests/data/test_great_expectations_gate.py::test_great_expectations_gate_accepts_model_ready_rows`,
  raising `OssCapabilityUnavailable: OSS capability 'data_quality' requires missing packages: great_expectations`.
  This is a **pre-existing environment dependency gap**, not a regression from this task: the
  package is absent from the runner (`ModuleNotFoundError: No module named 'great_expectations'`),
  the failing test was last modified by `2b5bb64a` (unrelated task), and this task's diff touches
  no dependency manifest and no `models/shared_ml/oss_capabilities.py` surface.
- Committed receipt integrity re-verified independently: `integrity.content_sha256` matches
  `compute_gate2_receipt_sha256(receipt)`.

## Acceptance Assessment

Acceptance: *至少 200 成熟 labels 且 M6/M12 與 coverage threshold 通過；不足時 governed-disabled 並具體 handback。*

Met. Current committed state is `no_source` → `REJECTED_GOVERNED_DISABLED` with reason
`NO_SOURCE_INVENTORY`, model card `release_status: GOVERNED_DISABLED`, and a concrete
handback action. All four fail-closed paths are covered by regressions:

| Failure mode | Reason code | Test |
| --- | --- | --- |
| < 200 mature labels | `MATURE_LABELS_BELOW_THRESHOLD` | `..._insufficient_labels_fails_closed` |
| M6/M12 window short | `M6_M12_COVERAGE_INSUFFICIENT` | `..._coverage_insufficient_fails_closed` |
| Predictions absent | `PREDICTION_EVIDENCE_MISSING` | `..._no_predictions_fails_closed` |
| DB outage | `DB_INVENTORY_UNREACHABLE` | `..._db_unreachable_fails_closed` |
| No source at all | `NO_SOURCE_INVENTORY` | `..._no_source_fails_closed` |

The prior rework items both check out: absent predictions no longer default `y_pred` to
`y_true` (they fail closed via `prediction_coverage_ratio`), and `get_days_elapsed` fallback
precedence now prefers the explicit `m6_covered`/`m12_covered` flag, then numeric
`m6_days`/`m12_days`, then derives from `opened_on`.

## Follow-up Findings (non-blocking, do not gate this approval)

1. **The `pg16_query` path can never reach `ACTIVE`.**
   `scripts/models/sitescore_outcome_benchmark.py:41-66` selects no `predicted_revenue`,
   `p10`, or `p90`, and `model_ready.candidate_site_view` (see
   `scripts/models/sql/model_ready_views.sql`) exposes no such columns. Any real inventory
   run therefore yields `prediction_coverage_ratio == 0.0` and terminates at
   `PREDICTION_EVIDENCE_MISSING`, regardless of how many mature labels exist. This is the
   safe direction (fail-closed) and the emitted handback action does name model predictions
   as required input, so it does not violate the stated acceptance — but the loop cannot
   actually close from the declared source contract until a prediction source is joined.
   Recommend a follow-up task to bind the benchmark to a prediction/registry source.

2. **P80 accounting on malformed interval bounds** — `models/sitescore/opening_outcome.py:259-268`
   appends the absolute error before parsing `p10`/`p90`, so a `TypeError`/`ValueError` on the
   interval bounds keeps the record in the MAE population while dropping it from
   `in_p80_count`. Fail-closed in direction (depresses `p80_coverage`), minor.

3. **Mixed denominators in `normalized_mae`** — `mae` is averaged over predicted records while
   `mean_y` is averaged over all mature records (`opening_outcome.py:270-272`). Bounded in
   practice because `prediction_coverage_ratio >= 0.70` is required to pass, but the two
   populations are not identical.

## Coordinator Note Addressed

The 2026-07-30T18:14:40Z coordinator note asked the exact-head reviewer to rerun the
task-scoped acceptance suite after the owner's full-repository pytest was killed on the
30-minute worker timeout. That rerun is recorded above and completed well inside the
timeout; the owner's recorded task-focused pass count stands. No bounded full-suite CI
rerun is required for this diff beyond the pre-existing `great_expectations` gap noted above.
