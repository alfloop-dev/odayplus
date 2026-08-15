---
doc_id: ODP-UXD-FRONTEND-EXECUTION-MATRIX
title: "ODay Plus Design to Frontend Execution Matrix"
version: 0.1.0
status: draft
document_class: execution-handoff
project: ODay Plus
language: zh-TW
updated_at: 2026-06-29
owner: "Product Design / Frontend"
reviewers: "Frontend Lead / QA Lead / Human/Ops"
content_format: markdown
source_documents:
  - docs/design/ODAY_PLUS_VISUAL_DESIGN_SYSTEM.md
  - docs/design/ODAY_PLUS_DESIGN_TOKENS.md
  - docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md
  - docs/design/ODAY_PLUS_OPSBOARD_SHELL_BLUEPRINT.md
  - docs/design/ODAY_PLUS_NAVIGATION_AND_WORKFLOW_SPEC.md
  - docs/design/ODAY_PLUS_R0_SCREEN_INVENTORY.md
  - docs/design/ODAY_PLUS_EXPANSION_WORKFLOW_BLUEPRINT.md
  - docs/design/ODAY_PLUS_HEATZONE_MAP_VISUAL_SPEC.md
  - docs/design/ODAY_PLUS_SITESCORE_REPORT_UI_SPEC.md
  - docs/design/ODAY_PLUS_OPERATIONS_ALERT_UI_SPEC.md
  - docs/design/ODAY_PLUS_INTERVENTION_WORKFLOW_UI_SPEC.md
  - docs/design/ODAY_PLUS_PRICING_AND_ADLIFT_UI_SPEC.md
  - docs/design/ODAY_PLUS_ASSET_AND_NETPLAN_UI_SPEC.md
  - docs/design/ODAY_PLUS_LEARNING_HUB_UI_SPEC.md
  - docs/design/ODAY_PLUS_AUDIT_EVIDENCE_UI_SPEC.md
---

# ODay Plus Design to Frontend Execution Matrix

## 1. Purpose

This file converts the completed ODay Plus UXD design specifications into frontend execution tasks that can be handed to implementation fleets. It is not a replacement for the design specs. It is a dispatch index that tells workers what to build, which source spec is authoritative, what API or state contract is expected, and which E2E proof must exist before the work is considered product-grade.

Use this file when creating implementation issues, worker task briefs, PR scopes, and Playwright acceptance suites.

Hard rules:

- Build the operational workbench, not a landing page.
- Use `ODAY_PLUS_DESIGN_TOKENS.md` and `ODAY_PLUS_COMPONENT_CONTRACTS.md`; do not invent colors, spacing, status names, or approval patterns.
- Every data surface must cover loading, empty, error, stale-data where applicable, and permission-limited states.
- Every high-risk action must show evidence, require reason capture where applicable, avoid optimistic update, and write/reflect audit evidence.
- Every map/chart/report must show confidence, freshness, model version, evidence level, and audit affordances when relevant.

## 2. Dispatch Readiness Gates

Before any frontend task starts, the worker must confirm:

| Gate | Required proof |
|---|---|
| Design source | Relevant `docs/design/*.md` source documents are linked in the task brief |
| Component reuse | Required core/domain components exist in `ODAY_PLUS_COMPONENT_CONTRACTS.md` or the task explicitly extends the contract first |
| Token use | Visual values are mapped to semantic tokens, not hard-coded primitive values |
| API binding | API routes, fixtures, generated clients, or product E2E seed data are named |
| Permission model | Read/write/hidden/readonly behavior is listed |
| State coverage | loading, empty, error, stale or low-confidence, permission-limited, and audit-relevant states are enumerated |
| E2E acceptance | Playwright scenario and observable runtime proof are named |

## 3. Execution Task Matrix

### FE-R0-001 OpsBoard App Shell

| Field | Requirement |
|---|---|
| Source specs | `ODAY_PLUS_OPSBOARD_SHELL_BLUEPRINT.md`, `ODAY_PLUS_NAVIGATION_AND_WORKFLOW_SPEC.md`, `ODAY_PLUS_R0_SCREEN_INVENTORY.md` |
| Routes | `/`, `/home`, `/w/:workspace`, `/tasks`, `/notifications`, `/search`, `/settings/:section`, `/admin/:section`, `/403`, `/404`, `/500`, `/offline`, `/maintenance` |
| Components | `AppShell`, `GlobalHeader`, `Sidebar`, `PageHeader`, `Toolbar`, `Drawer`, `CommandPalette`, `EmptyState`, `Toast` |
| States | shell loading, degraded mode, permission-limited navigation, mobile task-only mode, drawer deep link, command palette open/closed |
| E2E proof | shell renders with role-aware sidebar, workspace switch changes navigation, global search opens, task/notification badges are visible, keyboard `Cmd/Ctrl+K` works |

### FE-R0-002 Task Center and Notification Center

| Field | Requirement |
|---|---|
| Source specs | `ODAY_PLUS_R0_SCREEN_INVENTORY.md`, `ODAY_PLUS_NAVIGATION_AND_WORKFLOW_SPEC.md` |
| Routes | `/tasks`, `/tasks/:id`, `/notifications` |
| Components | `Table`, `Drawer`, `ApprovalPanel`, `AlertChip`, `FourLightBadge`, `DataStatusBadge` |
| States | assigned, waiting approval, completed, unread/read, job failed, critical alert, empty queue, permission-limited task |
| E2E proof | task row opens drawer without losing list state, high-risk approval requires reason/risk acknowledgement, notification links to source detail |

### FE-EXP-001 HeatZone Map and Ranking

| Field | Requirement |
|---|---|
| Source specs | `ODAY_PLUS_EXPANSION_WORKFLOW_BLUEPRINT.md`, `ODAY_PLUS_HEATZONE_MAP_VISUAL_SPEC.md` |
| Routes | `/w/expansion/heatzone` |
| Components | `MapShell`, `HeatZoneLayer`, `MapLegend`, `HeatZoneScoreCard`, `DataStatusBadge`, `Drawer`, ranking `Table` |
| API/data | HeatZone map features, H3 cells, source fixture seed data, map layer registry |
| States | map loading, layer loading, no zones, stale heat score, low confidence, selected H3, readonly user |
| E2E proof | MapLibre canvas nonblank, deck overlay rendered, layer toggles work, selecting an H3 opens drawer and syncs ranking selection |

### FE-EXP-002 Listing to Candidate Site Workflow

| Field | Requirement |
|---|---|
| Source specs | `ODAY_PLUS_EXPANSION_WORKFLOW_BLUEPRINT.md`, `ODAY_PLUS_HEATZONE_MAP_VISUAL_SPEC.md` |
| Routes | `/w/expansion/listings`, `/w/expansion/candidates` |
| Components | compact `Table`, `CandidateSiteCard`, `Drawer`, import `Form`, `DataStatusBadge`, `AuditMetadata` |
| API/data | listing source fixtures, geocode status, dedup status, hard-rule status, candidate conversion |
| States | raw, parsed, geocoded, duplicate, failed hard rule, candidate, scored, rejected, failed import, permission-limited conversion |
| E2E proof | source listing appears from deterministic fixture, row drawer shows geocode/dedup evidence, conversion to candidate is reflected in candidate list |

### FE-EXP-003 SiteScore Report and Opening Approval

| Field | Requirement |
|---|---|
| Source specs | `ODAY_PLUS_SITESCORE_REPORT_UI_SPEC.md`, `ODAY_PLUS_EXPANSION_WORKFLOW_BLUEPRINT.md` |
| Routes | `/w/expansion/sitescore`, `/sitescore/reports/:reportId` |
| Components | `SiteScoreReportSummary`, fan chart, comparable table, `EvidencePanel`, `ApprovalPanel`, `AuditMetadata` |
| API/data | report recommendation, P10/P50/P90 revenue path, payback, comparable stores, model version, feature snapshot |
| States | GO, WAIT, REJECT, INVESTIGATE, stale report, deprecated model, hard-rule failure, low confidence, submitted review, approved/rejected/override |
| E2E proof | report shows intervals and model metadata, approval requires reason/risk acknowledgement, audit decision id appears after submit |

### FE-OPS-001 Operations Alert Workbench

| Field | Requirement |
|---|---|
| Source specs | `ODAY_PLUS_OPERATIONS_ALERT_UI_SPEC.md` |
| Routes | `/w/operations/forecast`, `/w/operations/alerts`, `/stores/:storeId` |
| Components | `ForecastBandChart`, `FourLightBadge`, `RootCauseEvidenceCard`, `Table`, `Drawer`, `EvidencePanel` |
| API/data | store forecast band, actuals, SiteScore baseline, four-light state, root-cause evidence |
| States | GREEN/YELLOW/ORANGE/RED, forecast running, stale observations, missing root cause, acknowledged alert, handed-off alert |
| E2E proof | alert row opens store detail, four-light badge includes text/icon, forecast chart shows P10/P50/P90 and intervention markers |

### FE-INT-001 Intervention Lifecycle

| Field | Requirement |
|---|---|
| Source specs | `ODAY_PLUS_INTERVENTION_WORKFLOW_UI_SPEC.md` |
| Routes | `/w/operations/interventions`, `/interventions/:interventionId` |
| Components | `InterventionTimeline`, `ApprovalPanel`, conflict section, observation window, outcome evidence card, `AuditMetadata` |
| API/data | intervention proposal, eligibility, conflict checks, approval chain, execution, observation, outcome |
| States | DRAFT, ELIGIBILITY_CHECKED, CONFLICT_CHECKED, PENDING_APPROVAL, APPROVED, EXECUTING, OBSERVING, OUTCOME_READY, EVALUATED, CLOSED, CANCELLED |
| E2E proof | intervention moves through approval/execution/observation states, reason capture is required, audit events are visible |

### FE-PRICE-001 PriceOps Simulation, Approval, and Rollback

| Field | Requirement |
|---|---|
| Source specs | `ODAY_PLUS_PRICING_AND_ADLIFT_UI_SPEC.md` |
| Routes | `/w/pricing/plans`, `/pricing/plans/:planId` |
| Components | `PricingPlanComparison`, demand curve, hard-constraint panel, rollback panel, `ApprovalPanel` |
| API/data | current/candidate price, demand and gross margin simulation, hard constraints, approval status, rollback target |
| States | draft, simulated, constraint violation, pending approval, approved, activated, evaluated, rollback pending, rolled back |
| E2E proof | hard constraint violation blocks approval, approved plan shows audit id, rollback action requires reason and updates status |

### FE-AD-001 AdLift Candidate, Control Matching, and Lift Report

| Field | Requirement |
|---|---|
| Source specs | `ODAY_PLUS_PRICING_AND_ADLIFT_UI_SPEC.md` |
| Routes | `/w/pricing/adlift`, `/adlift/reports/:reportId` |
| Components | `AdLiftReportCard`, treatment/control trend, incrementality waterfall, iROMI card, evidence ladder |
| API/data | treatment stores, controls, pre-trend status, incremental revenue/gross margin, iROMI, evidence level |
| States | no controls, pre-trend failed, contamination risk, sufficient evidence, continue, stop, needs review |
| E2E proof | lift report does not claim causality without controls, pre-trend failure is visible, continue/stop decision writes audit evidence |

### FE-AVM-001 Asset Valuation and DataRoom

| Field | Requirement |
|---|---|
| Source specs | `ODAY_PLUS_ASSET_AND_NETPLAN_UI_SPEC.md` |
| Routes | `/w/dealroom/valuations`, `/avm/reports/:valuationId` |
| Components | `ValuationRangeChart`, valuation lens comparison, DataRoom completeness, finance `ApprovalPanel`, `AuditMetadata` |
| API/data | fair value P10/P50/P90, reserve/asking price, income/asset/market lenses, DataRoom artifacts, finance approval |
| States | valuation queued, generated, missing DataRoom artifact, sensitive price masked, pending finance approval, approved, exported |
| E2E proof | valuation card shows intervals and masked sensitive values by permission, DataRoom export writes evidence id |

### FE-NET-001 NetPlan Scenario Builder and Solver Result

| Field | Requirement |
|---|---|
| Source specs | `ODAY_PLUS_ASSET_AND_NETPLAN_UI_SPEC.md` |
| Routes | `/w/network/scenarios`, `/netplan/scenarios/:scenarioId` |
| Components | scenario builder form, `NetPlanScenarioCard`, network scenario map, quarterly timeline, constraint utilization, infeasibility diagnosis |
| API/data | planning horizon, constraints, candidate actions, solver run, alternatives, binding constraints, approval |
| States | draft, solver queued, running, feasible, infeasible, alternative available, pending approval, executed, outcome observed |
| E2E proof | solver result shows OPEN/KEEP/IMPROVE/MOVE/EXIT counts, infeasible run shows diagnosis without auto-relaxing constraints, approval writes audit id |

### FE-LEARN-001 Learning Hub Model Governance

| Field | Requirement |
|---|---|
| Source specs | `ODAY_PLUS_LEARNING_HUB_UI_SPEC.md` |
| Routes | `/w/ai/models`, `/learning/models/:modelId/versions/:version`, `/w/ai/releases` |
| Components | `ModelReleaseCard`, model card, release controller, rollback console, drift and data quality panels, `AuditMetadata` |
| API/data | model registry, metric summary, segment regression, data quality gate, drift gate, release stage, rollback target |
| States | experimental, candidate, shadow, canary, production, blocked, deprecated, rolled back, release rejected |
| E2E proof | release cannot proceed with failed gates, canary/promote/rollback actions require approval and produce audit evidence |

### FE-AUDIT-001 Audit Decision Log and Evidence Export

| Field | Requirement |
|---|---|
| Source specs | `ODAY_PLUS_AUDIT_EVIDENCE_UI_SPEC.md` |
| Routes | `/w/audit/decisions`, `/audit/decisions/:decisionId`, `/w/audit/evidence` |
| Components | audit `Table`, `DecisionAuditTimeline`, `AuditMetadata`, evidence export panel, sensitive export modal |
| API/data | decision id, entity, recommendation, human decision, actor, model version, policy version, feature snapshot, outcome, export bundle checksum |
| States | filter empty, evidence retained, export queued, export ready, sensitive export blocked, readonly auditor |
| E2E proof | decision log filters by correlation/model/version, detail timeline shows prediction→outcome, export creates retained evidence checksum |

## 4. Cross-Cutting Engineering Tasks

| Task ID | Scope | Required output | E2E / test proof |
|---|---|---|---|
| FE-XCUT-001 | Design token package | CSS variables and TS token object generated from semantic tokens | visual smoke renders light/dark/high-contrast/presentation tokens |
| FE-XCUT-002 | Core UI package | Shell, cards, tables, drawers, modals, forms, badges, tooltips, toast, command palette | component tests plus axe scan for keyboard/focus behavior |
| FE-XCUT-003 | Domain UI package | HeatZone, SiteScore, Forecast, Intervention, Pricing, AdLift, AVM, NetPlan, Model, Audit components | Storybook or fixture pages for all domain cards and charts |
| FE-XCUT-004 | Permission and masking layer | PermissionGuard, readonly notices, field masking helpers | tests prove hidden actions are not rendered and masked fields do not leak |
| FE-XCUT-005 | Job and audit UX | Job progress, correlation id error display, audit metadata, export progress | Playwright verifies failed job error includes code/correlation id and retry affordance |
| FE-XCUT-006 | Map and chart fallback | Map list fallback, chart data table fallback, export controls | E2E verifies map failure still shows list and chart exposes data table alternative |

## 5. Product-Grade E2E Coverage Map

| Product scenario | Frontend tasks | Required proof |
|---|---|---|
| HeatZone to SiteScore opening decision | FE-EXP-001, FE-EXP-002, FE-EXP-003 | map canvas/deck nonblank, listing source visible, SiteScore approval writes audit |
| Store alert to intervention outcome | FE-OPS-001, FE-INT-001 | four-light alert visible, intervention approved/executed/observed, outcome evidence visible |
| Pricing approval and rollback | FE-PRICE-001 | simulation and constraints visible, approval reason captured, rollback audit visible |
| AdLift incrementality | FE-AD-001 | treatment/control and pre-trend shown, no-causality warning when evidence is insufficient, continue/stop decision audited |
| AVM valuation and DataRoom | FE-AVM-001 | valuation interval visible, sensitive values masked by permission, DataRoom evidence export retained |
| NetPlan solve and approval | FE-NET-001 | feasible and infeasible solver states, alternative comparison, approval audit |
| Model release and rollback | FE-LEARN-001 | failed gates block release, canary/promote/rollback produce audit evidence |
| Decision audit export | FE-AUDIT-001, FE-XCUT-005 | decision timeline complete, retained bundle checksum visible |

## 6. Worker Task Brief Template

Use this structure for every fleet dispatch:

```yaml
task_id:
title:
owner_role:
reviewer_role:
source_specs:
  - docs/design/...
routes:
  - /...
components:
  - ComponentName
api_dependencies:
  - GET /...
permissions:
  - permission.name
states:
  - loading
  - empty
  - error
  - stale
  - permission_limited
  - audit_relevant
acceptance_criteria:
  - ...
e2e_tests:
  - tests/e2e/...
audit_evidence:
  - correlation_id or decision_id required
do_not:
  - invent visual tokens
  - optimistic-update high-risk action
  - hide uncertainty/model/data freshness
```

## 7. Review Checklist

- [ ] Every task references at least one design source spec.
- [ ] Every task states route, component, API/data, permission, states, and E2E proof.
- [ ] Every high-risk action requires reason/evidence/audit and avoids optimistic update.
- [ ] Every prediction, valuation, lift, forecast, or solver output shows uncertainty, confidence, data freshness, and model version where applicable.
- [ ] Every map has a list fallback; every chart has a data table fallback.
- [ ] Every implementation PR updates or adds Playwright coverage for the product scenario it touches.
- [ ] Any missing component first updates `ODAY_PLUS_COMPONENT_CONTRACTS.md`; any missing visual value first updates `ODAY_PLUS_DESIGN_TOKENS.md`.

