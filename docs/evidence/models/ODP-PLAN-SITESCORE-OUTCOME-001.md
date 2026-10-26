# Gate 2 Receipt: SiteScore Opening Outcome Calibration Benchmark (ODP-PLAN-SITESCORE-OUTCOME-001)

- **Task ID**: `ODP-PLAN-SITESCORE-OUTCOME-001`
- **Observed At**: `2026-08-31T16:06:05.300715Z`
- **Gate Status**: `REJECTED_GOVERNED_DISABLED`
- **Data Provenance**: `no_source`
- **Is Governed Disabled**: `True`
- **Integrity Content SHA256**: `f557abb7e6058d6716d6636c17d2b2c9011ddec7f4c5392c3e2babdae73fed43`

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
- **Discovery Inventory Query**: `SELECT c.entity_id, c.store_id, c.target_format_code, c.opened_on, c.is_training_eligible, c.realized_90d_net_revenue, c.realized_180d_net_revenue, c.realized_365d_net_revenue, (CURRENT_DATE - c.opened_on)::integer AS store_age_days, p.prediction_as_of, p.model_version, p.horizon_code, p.predicted_revenue, p.p10, p.p90, p.p50, p.dataset_snapshot_id, p.artifact_lineage_id FROM model_ready.candidate_site_view c LEFT JOIN model_ready.sitescore_predictions p ON (c.entity_id = p.entity_id OR c.store_id = p.store_id) AND c.opened_on = p.prediction_as_of AND p.model_version = 'candidate-site-view-v2';`
- **Backfill Receipt Required**: `True`
- **Audit Reasons**:
  - No database connection or candidate site records were provided
- **Handback Action**: Provide authoritative outcome backfill receipt (ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001) and prediction-source receipt (ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001) with true M6/M12 realized net revenue, interval bounds, and lineage.

## Verification
```bash
pytest -q tests -k "sitescore or opening_outcome or model_ready" && git diff --check
```
