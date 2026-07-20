---
doc_id: ODP-INTAKE-UX-FLEET-20260720
title: ODay Plus Assisted Listing Intake UI Fleet Execution Tasks
version: 1.0.0
status: approved-for-dispatch
owner: Product Platform Engineering
target_branch: dev
baseline_ref: origin/dev
umbrella_task: ODP-INTAKE-UX-001
canonical_design_tool: Claude Design
design_package: operator-console-r7-20260720-package-10
design_review: ODP-UXD-003-ADD-002-REVIEW-003
updated_at: 2026-07-20
---

# ODay Plus Assisted Listing Intake UI Fleet Execution Tasks

## 1. Dispatch Decision

The Package 10 Claude Design baseline is approved for engineering execution
with binding conditions. This packet decomposes the former monolithic
`ODP-INTAKE-UX-001` into eight independently testable implementation tasks.
`ODP-INTAKE-UX-001` remains the integration umbrella and cannot complete until
all eight tasks are independently approved and merged.

The machine-readable authority is:

`docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_FLEET_EXECUTION_TASKS_2026-07-20.json`

## 2. Binding Sources

Workers must read these together before editing:

1. Package 10 archive and `docs_archive/00_source_zips/operator_console/LATEST.json`.
2. `ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_HANDOFF_REQUIREMENTS.md`.
3. `ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE_REVIEW_003.md`.
4. Approved System Design state, authorization, OpenAPI, reliability, and
   privacy contracts.

Review 003 conditions override conflicting prototype behavior. In particular,
workers must not copy the reversed Transfer/Pause controls or treat session
storage as the durable inbox URL contract.

## 3. Task Waves

| Wave | Task | Owner | Depends on | Primary scope |
|---|---|---|---|---|
| 1 | `ODP-INTAKE-UX-FND-001` | Antigravity7 | States, API, Auth | Routes, generated client, typed shell, URL state |
| 2 | `ODP-INTAKE-UX-INBOX-001` | Claude2 | Foundation | Inbox integration and Add URL |
| 2 | `ODP-INTAKE-UX-DETAIL-001` | Antigravity3 | Foundation | Detail, timeline, evidence, errors, receipts |
| 3 | `ODP-INTAKE-UX-REVIEW-001` | Antigravity4 | Foundation, Detail | Assisted entry, field lineage, corrections |
| 3 | `ODP-INTAKE-UX-MATCH-001` | Antigravity5 | Identity, Foundation, Detail | Compare and identity decisions |
| 3 | `ODP-INTAKE-UX-ASSIGN-001` | Antigravity6 | Foundation, Detail | Assignment, SLA, Transfer/Pause |
| 3 | `ODP-INTAKE-UX-PROMOTION-001` | Antigravity2 | Promotion, Foundation, Detail | Candidate promotion and SiteScore jobs |
| 4 | `ODP-INTAKE-UX-QA-001` | Codex2 | All UI slices | Real-API E2E, responsive, a11y, role/error matrix |

## 4. Mandatory Visual Conditions

- `VDC-001`: Transfer renders target + handoff note only. Pause renders reason
  + editable required resume time only. Both preserve input on conflict and
  produce versioned receipts.
- `VDC-002`: No page-level horizontal overflow at 390, 1024, or 1440 px.
- `VDC-003`: Stable dialog focus return, WCAG 2.2 AA contrast, coherent
  landmarks, keyboard completion, and screen-reader summaries.
- `VDC-004`: Filters, sort, view, selection, active section, compare task, and
  receipt state are restorable from the URL, not only session storage.
- `VDC-005`: Product, System Design, Frontend, Accessibility, and QA outcomes
  are recorded against exact implementation commits before release.

## 5. Fleet Rules

- Start from freshly fetched `origin/dev` on `task/<task-id>`.
- One worker owns one task and only its listed paths. Shared changes require an
  explicit handoff; workers never revert another task's edits.
- Generated OpenAPI client/types are mandatory. No direct mock-provider or
  fixture fallback may appear as production behavior.
- High-impact mutations are non-optimistic and use approved concurrency,
  idempotency, authorization, audit, and durable receipt contracts.
- Every task opens a PR to `dev`, records exact-command evidence under
  `docs/evidence/completion/<task-id>/`, and requires a different reviewer.
- A task remains blocked when dependencies are not terminal; the supervisor may
  not bypass dependency state to create visible progress.

## 6. Completion Gate

The umbrella `ODP-INTAKE-UX-001` can close only after:

1. all eight child tasks are merged and independently approved;
2. Package 10 hashes and Review 003 are recorded in completion evidence;
3. actual application E2E covers the six required flows and role/error matrix;
4. Playwright and axe pass at 390, 1024, and 1440 px;
5. the API dependency is merged and no production UI uses silent fixtures;
6. all five visual conditions have exact-commit proof.
