# Gate 2 Receipt: SiteScore Opening Outcome Calibration Benchmark (ODP-PLAN-SITESCORE-OUTCOME-001)

- **Task ID**: `ODP-PLAN-SITESCORE-OUTCOME-001`
- **Observed At**: `2026-07-30T17:02:04.211760Z`
- **Gate Status**: `REJECTED_GOVERNED_DISABLED`
- **Is Governed Disabled**: `True`
- **Integrity Content SHA256**: `dc4dafe85f03a76fee9e11b41d92505b23ed5d01494c1a6518444ab1205a7de6`

## Benchmark Inventory & Coverage Summary

| Metric | Observed | Threshold / Required | Status |
| --- | --- | --- | --- |
| Mature Labels | 0 | >= 200 | FAIL (GOVERNED_DISABLED) |
| M6 Horizon Coverage | 0.0% | >= 70.0% | FAIL |
| M12 Horizon Coverage | 0.0% | >= 70.0% | FAIL |
| P80 Coverage Ratio | 0.0% | >= 70.0% | FAIL |
| Normalized MAE | 0.000 | <= 0.250 | PASS |

## Handback & Governance Receipt

- **Handback Required**: `True`
- **Reason Code**: `MATURE_LABELS_BELOW_THRESHOLD`
- **Audit Reasons**:
  - Mature label count (0) is below threshold (200)
  - M6 horizon coverage (0.0%) is below threshold (70.0%)
  - M12 horizon coverage (0.0%) is below threshold (70.0%)
  - P80 coverage (0.0%) is below threshold (70.0%)
- **Handback Action**: Provide >= 200 mature opening outcome labels with complete M6 (180d) and M12 (365d) post-opening transaction history.

## Verification
```bash
pytest -q tests -k "sitescore or opening_outcome or model_ready" && git diff --check
```
