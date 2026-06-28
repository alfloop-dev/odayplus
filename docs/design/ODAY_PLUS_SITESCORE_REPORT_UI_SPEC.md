---
doc_id: ODP-UXD-003-SITESCORE-REPORT-UI-SPEC
title: "ODay Plus SiteScore Report UI Spec"
version: 0.1.0
status: draft
document_class: ux-blueprint
project: ODay Plus
language: zh-TW
updated_at: 2026-06-28
owner: "Product Design / Frontend"
approvers: "Product Lead / Frontend Lead"
content_format: markdown
source_documents:
  - docs/design/ODAY_PLUS_EXPANSION_WORKFLOW_BLUEPRINT.md
  - docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md
  - docs/design/ODAY_PLUS_VISUAL_DESIGN_SYSTEM.md
related_documents:
  - shared/domain/models.py
---

# ODay Plus SiteScore Report UI Spec

## 1. Purpose & Boundary

本文件定義 `/w/expansion/sitescore/:reportId` 的完整 SiteScore 報告與開店核准體驗。這是展店決策的高風險頁面，必須讓使用者看清楚模型預測、不確定性、證據、系統建議、人工決策、後續執行與 audit。

列表頁與上游流程見 `ODAY_PLUS_EXPANSION_WORKFLOW_BLUEPRINT.md`；本文件專注 detail report。

## 2. Required Report Data

Report UI 必須能呈現以下資料；若後端 endpoint 尚未齊備，前端 contract/mock 也必須保留這些欄位位置。

| Field | Source model / contract | UI requirement |
|---|---|---|
| `candidate_site_id` | `SiteScoreRun.candidate_site_id` | breadcrumb/entity id |
| `target_format_code` | `SiteScoreRun.target_format_code` | format badge |
| `prediction_run_id` | `SiteScoreRun.prediction_run_id` | version/audit link |
| `m1/m3/m6/m12 p10/p50/p90` | `SiteScoreRun` | interval table + chart; never only P50 |
| `payback_p50_months` plus interval when available | SiteScore report | payback block |
| `decision_recommendation` | `go/wait/reject/investigate` | uppercase `GO|WAIT|REJECT|INVESTIGATE` recommendation |
| `report_uri` | `SiteScoreRun.report_uri` | immutable report/export link |
| Candidate fields | `CandidateSiteCard` contract | address, rent, area, frontage, floor, geocode |
| Evidence | `EvidencePanel` contract | positive/negative factors, comparables, confidence, limitations |
| Decision | `Decision` + `Approval` | approval status, actor, reason, audit |

## 3. Page Header

- Breadcrumb：`展店 Expansion > SiteScore Reports > <candidate address/report id>`。
- Title：candidate address or business name。
- Summary：`系統建議 <GO|WAIT|REJECT|INVESTIGATE>，M12 P50 <value>，confidence <level>，需 <role> 核准。`
- Status badges：DecisionStatus、DataStatus、ModelStatus、format code。
- Primary action：根據權限與狀態顯示 `送審`、`核准`、`退回補件`、`建立開店任務`。
- Secondary actions：compare candidates、export report、open candidate drawer、view audit。
- Last updated：feature snapshot time + report generated time。

## 4. Information Architecture

Detail page 固定七層順序：

1. Summary
2. Status
3. Evidence
4. Recommendation
5. Decision
6. Execution/Result
7. Version/Audit

可以用 anchor tabs 快速跳轉，但頁面本體順序不得重排。

## 5. Summary Section

Use `SiteScoreReportSummary` and include:

- Recommendation：`GO|WAIT|REJECT|INVESTIGATE` with text reason。
- Forecast intervals：M1、M3、M6、M12 each P10/P50/P90。
- Payback：P50 plus uncertainty if available。
- Rent reasonableness。
- Cannibalization risk。
- Comparable stores count and quality。
- Key positive and negative factors。
- Model version and feature snapshot time。

Low confidence must show warning at top of section, not hidden in tooltip.

## 6. Status Section

Status row:

| Status | Display |
|---|---|
| Decision | `DRAFT|SYSTEM_RECOMMENDED|PENDING_REVIEW|APPROVED|REJECTED|OVERRIDDEN|CLOSED` |
| Data | `FRESH|STALE|PARTIAL|MISSING|LOW_CONFIDENCE|FAILED_QA|BLOCKED` |
| Model | model version + `PRODUCTION|CANDIDATE|CHALLENGER|...` |
| SLA | reviewer due time and overdue state |
| Permissions | read-only or approval capability |

No status may be color-only; each uses text and icon/pattern.

### 6.1 DecisionStatus Mapping

UI `DecisionStatus` is a presentation vocabulary over the backend `Decision`
and `Approval` records. Do not persist UI-only status strings back to the
domain model.

| UI DecisionStatus | Backend source | Mapping rule |
|---|---|---|
| `DRAFT` | no persisted `Decision` yet, or local draft only | Report exists but no system decision record is ready for review. |
| `SYSTEM_RECOMMENDED` | `Decision.decision_status=proposed`, no `Approval` yet | System created `Decision.recommendation=go|wait|reject|investigate`; human review has not started. |
| `PENDING_REVIEW` | `Decision.decision_status=proposed` + latest `Approval.approval_status=pending|escalated` | Approval workflow is active and waiting for the required role. |
| `APPROVED` | `Decision.decision_status=approved` + latest `Approval.approval_status=approved` | Human approval accepted the recommended or selected action. |
| `REJECTED` | `Decision.decision_status=rejected` or latest `Approval.approval_status=rejected` | Review rejected the decision; show reason code/comment and close or appeal path. |
| `OVERRIDDEN` | `Decision.decision_status=overridden` + approval trail | Human decision differs from the system recommendation; show original recommendation, override decision, reason, risk acknowledgement, and approval id. |
| `CLOSED` | `Decision.decision_status=executed|cancelled|expired` or terminal workflow policy | Decision is no longer reviewable; execution/result timeline explains the terminal reason. |

Returned approval is not a terminal UI status: `Approval.approval_status=returned`
renders as `PENDING_REVIEW` with a revision-required banner and requested
fields/tasks.

## 7. Evidence Section

Use `EvidencePanel` with these blocks:

- Positive factors：demand gap, format fit, rent feasibility, traffic/POI, listing availability。
- Negative factors：cannibalization risk, low geocode confidence, comparable scarcity, rent over threshold, competitor density。
- Comparable stores：name/id masked by permission, distance, format match, maturity, performance band。
- Trend / forecast chart：P10-P90 band with M1/M3/M6/M12 markers。
- Limitations：missing data, stale snapshots, low sample size, policy constraints。

Charts require data-table alternative and export of visible data only.

## 8. Recommendation Section

Recommendation card must explicitly say:

- Generated by system, not a human decision。
- `modelVersion`
- `policyVersion`
- `featureSnapshotTime`
- `generatedAt`
- required approval role。
- reason summary with links to evidence anchors。

Recommendation meanings:

| Recommendation | Meaning | Required next step |
|---|---|---|
| `GO` | Model/policy supports opening workflow | Human approval before execution |
| `WAIT` | Potential exists but timing/evidence not ready | revisit date or missing evidence task |
| `REJECT` | Not viable under current policy/evidence | reason code and close/appeal path |
| `INVESTIGATE` | Material uncertainty or conflict | create site visit/data task |

## 9. Decision & Approval Section

Use `ApprovalPanel`. SiteScore approval is high risk:

- No optimistic update。
- Submit button loading blocks duplicate submit。
- Reason required for every decision。
- Override requires original recommendation, override decision, reason min length, risk acknowledgement。
- Success response must display `decision_id` and approval id when available。
- Failure keeps form values and shows field/global errors with correlation_id。

### 9.1 Decision Actions

| Action | Visible when | Required fields |
|---|---|---|
| Submit for review | report is `DRAFT` or `SYSTEM_RECOMMENDED`; user can submit | summary, reviewer group |
| Approve GO | recommendation/action allows; user has approval permission | reason, risk acknowledgement |
| Reject | user has approval permission | reason code, comment |
| Request revision | missing evidence or low confidence | requested fields/tasks |
| Override | user has override permission | override decision, reason, risk acknowledgement |

### 9.2 Segregation Rules

- Creator cannot approve own override unless policy explicitly permits emergency mode。
- Read-only users see decision status and audit but no action buttons。
- If data status is `FAILED_QA|BLOCKED`, approval buttons disabled with reason。

## 10. Execution / Result Section

After approval:

- `GO` shows next steps: create opening project, assign owner, target open date, required tasks。
- `WAIT` shows revisit date and monitoring task。
- `REJECT` shows closure reason and reopen conditions。
- `INVESTIGATE` shows created site visit/data collection tasks。

Before actual outcome maturity, UI must not claim success. It may show `OBSERVING` and expected outcome windows.

## 11. Version / Audit Section

Required fields:

- report id / uri
- model version
- feature view version
- feature snapshot time
- prediction origin time
- policy version
- candidate site id
- source listing id and import snapshot
- actor, reviewer, timestamp
- reason / override reason
- decision id / approval id
- correlation id for failed or submitted actions

Audit timeline uses `Timeline` / `AuditMetadata`; export includes watermark when sensitive fields are visible.

## 12. Responsive Behavior

- `lg+`：full report with side summary rail and sticky decision panel。
- `md`：single-column sections, sticky bottom action bar for approval。
- `sm`：summary, status, top evidence, and task response only. Full report review shows notice `完整模型審查請使用桌機版` unless policy allows mobile approval。

Text must not overlap charts or buttons; long addresses wrap cleanly.

## 13. Empty / Loading / Error

| State | Required UI |
|---|---|
| Loading | skeleton for header, summary, evidence, approval; no fake score |
| Empty | report not generated: action `執行 SiteScore` if permitted |
| Error | error summary + code + correlation_id + retry + timestamp |
| Stale | report renders with stale warning and rerun action |
| Permission | read-only or 403 according to route permission |

## 14. Export / Print

Export is high risk when sensitive fields are visible:

- Requires permission, reason, audit event, watermark。
- Exported report must include model version, policy version, feature snapshot time, generatedAt, actor。
- Print-safe view uses `print_safe` map/chart theme and preserves text labels for all statuses。

## 15. Implementation Checklist

- [ ] Report shows M1/M3/M6/M12 P10/P50/P90 and never collapses to only P50。
- [ ] Evidence includes positive/negative factors, comparables, confidence reasons, limitations。
- [ ] Recommendation is clearly system-generated and separate from human decision。
- [ ] ApprovalPanel blocks optimistic update, validates reason/risk acknowledgement, and displays decision_id on success。
- [ ] Data/model/policy/version/audit metadata are visible on the report。
- [ ] Stale, low-confidence, FAILED_QA, and BLOCKED states disable or constrain approval with explicit reasons。
- [ ] Responsive layouts support desktop full review and mobile summary/task handling without overlap。
