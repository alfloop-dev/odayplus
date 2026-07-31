# Gate 2 Receipt: SiteScore Opening Outcome Calibration Benchmark (ODP-PLAN-SITESCORE-OUTCOME-001)

- **Task ID**: `ODP-PLAN-SITESCORE-OUTCOME-001`
- **Observed At**: `2026-07-31T07:58:12.507423Z`
- **Gate Status**: `REJECTED_GOVERNED_DISABLED`
- **Data Provenance**: `no_source`
- **Is Governed Disabled**: `True`
- **Integrity Content SHA256**: `6e9aa113522bc491c0f2294e8952b904d3c8cbad01f82dee0edf1f229659bf7f`

## Benchmark Inventory & Coverage Summary

| Metric | Observed | Threshold / Required | Status |
| --- | --- | --- | --- |
| Mature Labels | 0 | >= 200 | FAIL (GOVERNED_DISABLED) |
| Matched Predictions | 0 | N/A | INFO |
| Prediction Coverage | 0.0% | >= 70.0% | FAIL |
| Interval Bounds Coverage | 0.0% | >= 70.0% | FAIL |
| M6 Horizon Coverage | 0.0% | >= 70.0% | FAIL |
| M12 Horizon Coverage | 0.0% | >= 70.0% | FAIL |
| P80 Coverage Ratio | 0.0% | >= 70.0% | FAIL |
| Normalized MAE | 0.000 | <= 0.250 | FAIL (GOVERNED_DISABLED) |

## Handback & Governance Receipt

- **Handback Required**: `True`
- **Reason Code**: `NO_SOURCE_INVENTORY`
- **Backfill Owner**: `Human/Ops`
- **Backfill Task ID**: `ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001`
- **Prediction Source Task ID**: `ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001`
- **Backfill Query**: `SELECT entity_id, store_id, target_format_code, opened_on, is_training_eligible, realized_90d_net_revenue, (CURRENT_DATE - opened_on)::integer AS store_age_days FROM model_ready.candidate_site_view;`
- **Backfill Receipt Required**: `True`
- **Audit Reasons**:
  - No database connection or candidate site records were provided
- **Handback Action**: Provide a valid PostgreSQL database URL (ODAY_DATABASE_URL / --db-url) or candidate site records.

## Verification
```bash
pytest -q tests -k "sitescore or opening_outcome or model_ready" && git diff --check
```
