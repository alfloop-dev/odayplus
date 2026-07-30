# HeatZone Label Inventory Benchmark & Gate 1 Receipt

- **Task ID**: `ODP-PLAN-HEATZONE-OUTCOME-001`
- **Evaluation Date**: `2026-07-30T21:58:23.733780Z`
- **Verdict**: **❌ FAIL CLOSED**
- **Inventory Lineage Version**: `pg16-production-model-inventory-2026-07-25-v1`
- **Inventory Observed At**: `2026-07-25T15:20:00Z`
- **Inventory SHA256**: `3f1c8ec4baa1e2f06f5c4e93e82a6258315012b46aacfd3f3e578221aa8b5f44`
- **Relation**: `model_ready.heatzone_training_view` (`heatzone-training-view-v2`)
- **Auto Seeded**: `False` (Forbidden)
- **Governed Disabled**: `True` (`DATA_CONTRACT_NOT_MATURE`)

---

## 1. Label Inventory Summary

| Metric | Value | Required Minimum | Status |
|---|---:|---:|---|
| Observed Labeled Rows | `0` | - | Observed |
| Eligible Mature Real Labels | `0` | `200` | ❌ Insufficient |
| Auto-Seeded / Synthetic Rows | `0` | `0` | ✅ Zero Synthetic |

---

## 2. Benchmark Evaluation Criteria

| Benchmark Metric | Observed Value | Baseline Threshold | Status |
|---|---:|---:|---|
| Population Density Ranking NDCG | `N/A` | `0.5` | Skipped (Insufficient Data) |
| Top-K Field Site Survey Rate | `N/A` | `0.3` | Skipped (Insufficient Data) |

### Evaluation Notes
Eligible HeatZone label count (0) is below the activation threshold (200). Capability remains governed-disabled with fail-closed status.

---

## 3. Fail-Closed Governance & Safety Enforcements

1. **Governed-Disabled Status**: Production binding for `heatzone` model (`heatzone_priority`) remains **governed-disabled** with canonical reason code `DATA_CONTRACT_NOT_MATURE`.
2. **Zero Synthetic Data Policy**: Synthetic labels, mock rows, auto-seeded entries, or fabricated opening dates are strictly prohibited from model training and release pathways.
3. **Integrity Envelope**: Receipt content SHA-256 is immutable (`cd9f0c298d0a9ec155c9978e2260027e922f1e4253c6658ba378a0c501d4b749`).

---

## 4. Actionable Data Handback Requirements

To enable future HeatZone model activation, the Expansion Operations / POS Data Platform team must provide:
- At least **200 eligible mature real labels** (with 90 complete prior transaction days and 28 complete forward outcome days per H3 cell origin).
- Approved immutable store opening dates (`opened_on`) and canonical store/geography lineage.
- Audit evidence proving superior performance over population-density sorting and improved field survey rates.
