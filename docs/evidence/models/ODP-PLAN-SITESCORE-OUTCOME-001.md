# Gate 2 Receipt: SiteScore Opening Outcome Calibration Benchmark (ODP-PLAN-SITESCORE-OUTCOME-001)

- **Task ID**: `ODP-PLAN-SITESCORE-OUTCOME-001`
- **Observed At**: `2026-07-30T22:43:56.396382Z`
- **Gate Status**: `REJECTED_GOVERNED_DISABLED`
- **Data Provenance**: `no_source`
- **Is Governed Disabled**: `True`
- **Integrity Content SHA256**: `330e61a5a1b215a59ae2ed66dd7e0bf3805395309edaeb5e7918db30003e99a7`

## Benchmark Inventory & Coverage Summary

| Metric | Observed | Threshold / Required | Status |
| --- | --- | --- | --- |
| Mature Labels | 0 | >= 200 | FAIL (GOVERNED_DISABLED) |
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
- **Backfill Query**: `SELECT entity_id, store_id, target_format_code, opened_on, is_training_eligible, realized_90d_net_revenue, realized_180d_net_revenue AS realized_m6_net_revenue, realized_365d_net_revenue AS realized_m12_net_revenue, predicted_revenue, p10, p90, dataset_snapshot_id, model_version, artifact_lineage_id, (CURRENT_DATE - opened_on)::integer AS m6_days, (CURRENT_DATE - opened_on)::integer AS m12_days FROM model_ready.candidate_site_view;`
- **Backfill Receipt Required**: `True`
- **Audit Reasons**:
  - No database connection or candidate site records were provided
- **Handback Action**: Provide a valid PostgreSQL database URL (ODAY_DATABASE_URL / --db-url) or candidate site records.

## Verification
```bash
pytest -q tests -k "sitescore or opening_outcome or model_ready" && git diff --check
```
