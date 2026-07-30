# Gate 2 Receipt: SiteScore Opening Outcome Calibration Benchmark (ODP-PLAN-SITESCORE-OUTCOME-001)

- **Task ID**: `ODP-PLAN-SITESCORE-OUTCOME-001`
- **Observed At**: `2026-07-30T17:46:13.086558Z`
- **Gate Status**: `REJECTED_GOVERNED_DISABLED`
- **Data Provenance**: `no_source`
- **Is Governed Disabled**: `True`
- **Integrity Content SHA256**: `cedc046e459dafabadf92aef480f3dc78b3cd0b90e676efa2fec0e2ae4d9769a`

## Benchmark Inventory & Coverage Summary

| Metric | Observed | Threshold / Required | Status |
| --- | --- | --- | --- |
| Mature Labels | 0 | >= 200 | FAIL (GOVERNED_DISABLED) |
| Prediction Coverage | 0.0% | >= 70.0% | FAIL |
| M6 Horizon Coverage | 0.0% | >= 70.0% | FAIL |
| M12 Horizon Coverage | 0.0% | >= 70.0% | FAIL |
| P80 Coverage Ratio | 0.0% | >= 70.0% | FAIL |
| Normalized MAE | 0.000 | <= 0.250 | FAIL (GOVERNED_DISABLED) |

## Handback & Governance Receipt

- **Handback Required**: `True`
- **Reason Code**: `NO_SOURCE_INVENTORY`
- **Audit Reasons**:
  - No database connection or candidate site records were provided
- **Handback Action**: Provide a valid PostgreSQL database URL (ODAY_DATABASE_URL / --db-url) or candidate site records.

## Task Closeout & Finalization
- **Finalized At**: `2026-07-30T19:06:00Z`
- **Owner**: `Antigravity4`
- **Reviewer**: `Claude`
- **PR**: `#525`
- **Review Head**: `20e76106`

## Verification
```bash
pytest -q tests -k "sitescore or opening_outcome or model_ready" && git diff --check
```
