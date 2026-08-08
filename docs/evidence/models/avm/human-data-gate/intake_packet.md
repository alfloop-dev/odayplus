# AVM Outcome Backfill Human Data Gate Intake Packet (ODP-PLAN-AVM-OUTCOME-BACKFILL-001)

- **Task ID**: `ODP-PLAN-AVM-OUTCOME-BACKFILL-001`
- **Gap ID**: `GAP-P1-003-DATA`
- **Class**: `human_gate`
- **Owner**: `Human/Ops`
- **Reviewer**: `Codex2`
- **Phase**: P1 Human Data Gate
- **Generated At**: 2026-08-03T00:50:00Z

---

## 1. Governance & Scope Overview

This task governs the intake and validation of authoritative mature transaction outcomes required for DealRoom AVM (Asset Valuation Model) model calibration and operational readiness.

Per platform policy (`PAPER_CANARY_LIVE_POLICY.md` & `DEVELOPMENT_PLAN_GAP_EXECUTION_TASKS_2026-07-30.md`), AI agents are strictly forbidden from fabricating, seeding, or auto-generating synthetic transaction records or fake access control receipts. AVM capability must remain **governed-disabled** (`BUSINESS_UAT_UNVERIFIED` / `GOVERNED_DISABLED`) until Human/Ops supplies authentic, audited transaction data and RBAC access receipts.

---

## 2. Current Gate Status

| Metric / Check | Value | Threshold | Status |
| :--- | :--- | :--- | :--- |
| **Observed Labeled Count** | 0 | >= 120 | FAIL_CLOSED |
| **Eligible Mature Count** | 0 | >= 120 | FAIL_CLOSED |
| **Shortfall** | 120 | 0 | PENDING |
| **Dataset Snapshot Hash** | `empty-snapshot-unpopulated` | Valid SHA-256 | UNVERIFIED |
| **Authentic Evidence Available** | `false` | `true` | FAIL_CLOSED |
| **Capability Binding** | `BUSINESS_UAT_UNVERIFIED` / `GOVERNED_DISABLED` | Active | SAFE_DEFAULT |

---

## 3. Deliverables & Acceptance Criteria

To unlock and pass `ODP-PLAN-AVM-OUTCOME-BACKFILL-001`, Human/Ops must provide the following:

1. **Eligible Mature Transaction Dataset**:
   - At least 120 authoritative mature transaction outcomes.
   - Stable prediction join keys (`prediction_id`, `property_id`, `transaction_date`, `realized_price`, `appraisal_value`).
   - Zero synthetic, fixture, auto-seeded, duplicate, or immature transactions.

2. **Dataset Governance Metadata**:
   - Redacted dataset snapshot SHA-256 hash.
   - Named data owner (`Human/Ops & Finance Legal Team`).
   - Lineage source and freshness/cutoff date.
   - Confidentiality level classification (`HIGH` / `CONFIDENTIAL`).

3. **Confidential Access & Security Receipts**:
   - RBAC/ABAC audit receipts proving access controls are enforced.
   - Zero exposure of unmasked confidential transaction values in evidence logs.

4. **Production Query Readback**:
   - Verifiable readback logs from production PostgreSQL `model_ready.valuation_view`.

---

## 4. Fail-Closed Rules

Execution will immediately fail closed under any of the following conditions:
- Presence of synthetic, fixture, mock, auto-seeded, or copied transaction outcomes.
- Missing or unstable prediction join keys.
- AI-authored source or access receipts without authentic production system readback.
- Exposure of raw confidential property transaction values in public evidence artifacts.

---

## 5. Required Action Items for Human/Ops Handback

| ID | Action Item | Responsible Party |
| :--- | :--- | :--- |
| **REQ-AVM-BF-001** | Backfill >= 120 authentic mature transaction outcomes with join keys into `model_ready.valuation_view`. | Human/Ops & Finance Legal |
| **REQ-AVM-BF-002** | Verify zero synthetic, auto-seeded, or mock rows exist in valuation view. | Data Engineering |
| **REQ-AVM-BF-003** | Provide dataset snapshot hash, lineage, owner attestation, and cutoff date. | Human/Ops Data Owner |
| **REQ-AVM-BF-004** | Export redacted RBAC/ABAC access audit receipts without leaking confidential values. | Security & Compliance |
