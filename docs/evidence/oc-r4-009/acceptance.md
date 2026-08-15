# ODP-OC-R4-009 — Acceptance mapping

Design source: canonical package 6 (`r4-20260707-package-6`), data-screen-label
`Govern 治理稽核`. Screenshots in this directory are captured from the live
API-bound workspace at desktop (1440w) and constrained (768w) widths.

| # | Acceptance criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Desktop and constrained-width screenshots compared with the archived interactive HTML for every changed R4 surface | **Met** | `govern-{approvals,decisions,audit,evidence-package,status-board}-{desktop,constrained}.png` — layout, tab set (`核准中心/Decision Log/Audit Trail/Evidence Package 匯出/系統狀態盤`), Approval Center queue+detail, reason gate, Decision Log columns and Evidence Package builder all match the package-6 Govern section. No unresolved visual differences. |
| 2 | Evidence export records scope, range, format, actor, correlation and retention policy | **Met** | `api-proof.json` (POST /evidence-package) — scope (modules+contents), range, format, actor, `correlationId`, `retentionPolicy` all recorded; export writes an Audit Trail event. Contract: `test_evidence_package_export_records_full_metadata`. |
| 3 | Implementation and review evidence identify canonical package 6 and the relevant data-screen-label values | **Met** | This packet, `implementation.md`, contract/e2e docstrings and `GovernanceWorkspace` all cite `r4-20260707-package-6` + `Govern 治理稽核`. Preflight sha256 match recorded in `verification.md`. |
| 4 | No governance value builder remains unreachable from UI navigation | **Met** | Five tabs reachable; status board now exposes **Data Quality / Model Registry / Connector / SLA / Users** (SLA + Users were previously absent). `govern-status-board-*.png`; e2e `Govern workspace exposes all five tabs and the DQ/Model/Connector/SLA/Users board`; contract `test_governance_snapshot_exposes_every_value_builder`. |
| 5 | Return/reject require reason and approval policies are enforced server-side | **Met** | `POST /decisions` with a short reason → `422` (`api-proof.json`); contract `test_return_without_reason_is_rejected_server_side`, `test_reject_with_short_reason_is_rejected_server_side`; double-decision → `409`; unknown id → `404`; write route fail-closed → `403`. |
| 6 | Store and Growth decisions plus pending Network approvals appear consistently after reload | **Met** | Snapshot approvals span Store Ops/Growth/**Network** and decisions span Store Ops/Growth/Network; a decision persists and re-reads consistently. Contract `test_governance_snapshot_has_store_growth_network_rows`, `test_decision_persists_and_is_consistent_after_reload`; e2e `Govern surfaces Store/Growth decisions and a pending Network approval`. |

## Design-parity note

The changed surfaces reproduce the package-6 Govern layout: the two-column
Approval Center (queue + detail with SLA, approver role, evidence chips and the
`退回／駁回必填` reason gate), the Decision Log table
(`系統建議 / 最終決策 / 理由 / 決策人 / 模型 / 資料快照 / 關聯核准`), the
Audit Trail with category filters, the Evidence Package builder
(date range, module scope, content selection, PDF/CSV, history) and the system
status board. The constrained-width captures show the same content reflowing
without loss of any value builder.
