# AVM Outcome Inventory Benchmark & Gate 1 Receipt

- **Task ID**: `ODP-PLAN-AVM-OUTCOME-001`
- **Evaluation Date**: `2026-07-31T12:12:58.010784+00:00`
- **Verdict**: **❌ FAIL CLOSED**
- **Model Version**: `dealroom-avm-baseline-v1`
- **Dataset Snapshot ID**: `pg16-avm-outcome-snapshot-2026-07-31-v1`
- **Dataset Snapshot SHA256**: `a1b2c3d4e5f60718293a4b5c6d7e8f901234567890abcdef1234567890abcdef`
- **Model Artifact SHA256**: `b2c3d4e5f60718293a4b5c6d7e8f901234567890abcdef1234567890abcdef01`
- **Relation**: `model_ready.valuation_view`
- **Auto Seeded Rows**: `0` (Forbidden)
- **Governed Disabled**: `True` (`DATA_CONTRACT_NOT_MATURE`)

---

## 1. Outcome Inventory Summary

| Metric | Value | Required Minimum | Status |
|---|---:|---:|---|
| Observed Labeled Rows | `0` | - | Observed |
| Eligible Mature Real Outcomes | `0` | `120` | ❌ Insufficient |
| Auto-Seeded / Synthetic Rows | `0` | `0` | ✅ Zero Synthetic |

---

## 2. Benchmark & Calibration Metrics

| Calibration Metric | Aligned Value | Baseline Target | Status |
|---|---:|---:|---|
| Aligned Population Count | `0` | - | Aligned |
| Interval Coverage (P10..P90) | `0.0000` | `0.8000` | Skipped / Insufficient |
| Mean Absolute Percentage Error (MAPE) | `0.0000` | `<= 0.1500` | Skipped / Insufficient |
| Median Calibration Ratio (Realized / P50) | `0.0000` | `0.95 .. 1.05` | Skipped / Insufficient |

### Value Band Separation Breakdown

| Value Band | Aligned Count | P10..P90 Coverage | Calibration Ratio | MAPE | MAE |
|---|---:|---:|---:|---:|---:|
| `band_low_lt10m` | `0` | `0.0000` | `0.0000` | `0.0000` | `$0.00` |
| `band_mid_10m_to_30m` | `0` | `0.0000` | `0.0000` | `0.0000` | `$0.00` |
| `band_high_gt30m` | `0` | `0.0000` | `0.0000` | `0.0000` | `$0.00` |

---

## 3. Confidential Access Audit & RBAC Summary

- **Audit Event Count**: `4`
- **Permitted Accesses**: `2` (Roles: `FINANCE_LEGAL`, `SYSTEM_ADMIN`)
- **Denied Accesses**: `2` (Roles: `REGIONAL_SUPERVISOR`, `FRANCHISEE`, `MARKETING_MANAGER`)
- **Zero Confidential Leak Verified**: `True`
- **Audit Receipt SHA256**: `2032cf3e5cf51af7d51feeedbc60f43202de0b503f7e2c364ccd6c7890d0ebc2`

---

## 4. Fail-Closed Governance & Safety Enforcements

1. **Governed-Disabled Status**: Production binding for `avm` model (`dealroom_avm`) remains **governed-disabled** with canonical reason code `DATA_CONTRACT_NOT_MATURE`.
2. **Zero Synthetic Data Policy**: Synthetic outcomes, mock transactions, auto-seeded entries, or copied predictions are strictly prohibited.
3. **Activation Threshold**: Requires at least **120** mature real transaction outcomes with independent label-authority approval.
