# AVM Outcome Inventory Benchmark & Gate 1 Receipt

- **Task ID**: `ODP-PLAN-AVM-OUTCOME-001`
- **Evaluation Date**: `2026-07-31T17:10:24.354570+00:00`
- **Verdict**: **❌ FAIL CLOSED**
- **Model Version**: `dealroom-avm-baseline-v1`
- **Dataset Snapshot ID**: `empty-snapshot-unpopulated`
- **Dataset Snapshot SHA256**: `86d4b28ff48929469cc683ca9b2ce209c0831c266a6124b68c6ee701fe56ed96`
- **Model Artifact SHA256**: `a2a47bcb23d13b714cda38544c1f1ba5d8b9fc2df0e770a1b20379f6c5197cfd`
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
- **Permitted Accesses**: `0` (Roles: `NONE`)
- **Denied Accesses**: `4` (Roles: `FINANCE_LEGAL`, `FRANCHISEE`, `PLATFORM_ADMIN`, `REGIONAL_SUPERVISOR`)
- **Zero Confidential Leak Verified**: `True`
- **Audit Receipt SHA256**: `dc5c4b756da864a04940c26b74380fff6ed3dabe5997cd9d93906f518ba78e54`

---

## 4. Fail-Closed Governance & Safety Enforcements

1. **Governed-Disabled Status**: Production binding for `avm` model (`dealroom_avm`) remains **governed-disabled** with canonical reason code `DATA_CONTRACT_NOT_MATURE`.
2. **Zero Synthetic Data Policy**: Synthetic outcomes, mock transactions, auto-seeded entries, or copied predictions are strictly prohibited.
3. **Activation Threshold**: Requires at least **120** mature real transaction outcomes with independent label-authority approval.
