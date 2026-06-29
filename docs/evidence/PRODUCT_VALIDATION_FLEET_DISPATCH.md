# Product Validation Fleet Dispatch

Task: ODP-PV-000
Generated: 2026-06-28
Owner: Codex
Reviewer: Claude

## Purpose

This dispatch note turns the ODP-PV-000 current-state audit into bounded
follow-on lanes for the PV Product-Grade E2E Validation phase. It is an
evidence artifact, not a claim that the product is production-ready.

## Dispatch Baseline

The reviewed baseline is:

- `docs/evidence/BRANCH_TRUTH_TABLE.md`
- `docs/evidence/CURRENT_STATE_PRODUCT_GAP_AUDIT.md`
- `docs_archive/EXECUTION_TASKS.md`

The baseline found that `task/ODP-PV-000`, `origin/main`, and `origin/dev`
have identical product trees, while the task branch still needs the normal PR
flow into `dev` because branch topology differs.

## Fleet Lanes

| Lane | Objective | Starting evidence | Done signal |
|---|---|---|---|
| Durable persistence | Replace default in-memory API/module repositories with durable stores behind existing interfaces | `CURRENT_STATE_PRODUCT_GAP_AUDIT.md` P0 repository wiring gap | API and module tests prove persisted state survives process boundaries |
| Fixture-to-API web binding | Move OpsBoard screens from bundled `data.ts` fixtures to API-backed state incrementally | Audit web data binding gap | E2E scenarios exercise live API contracts for converted screens |
| Evidence store and audit retention | Persist audit events and evidence export bundles with hash, retention, and privacy scope | Audit evidence durability gap | Exported evidence can be reproduced from durable storage with recorded hashes |
| External ingestion | Implement provider-backed POI, competitor, and listing ingestion with quarantine and freshness checks | Source contract registry and audit external data gap | Scheduled or manual ingest produces validated snapshots and freshness evidence |
| Map/geocoder readiness | Replace HeatZone preview behavior with production map, geocoder, and spatial lookup path | Branch audit geospatial/map findings | UI renders licensed map/geocoder results backed by approved spatial data |
| Model registry integration | Connect model lifecycle code to artifact storage, model aliases, and release evidence | Audit model lifecycle gap | A signed model card references durable artifacts and rollback metadata |
| Release blocker remediation | Resolve dependency/security findings and fill release metadata before any release-ready claim | `PRODUCTION_READINESS_PACKAGE.md` blockers | Release package has assigned owners, versions, approvals, and passing checks |

## Coordination Notes

- Fleet tasks should treat the current repo as a demo-capable modular product
  skeleton.
- Follow-on work must not claim live external data, production map readiness,
  durable audit retention, formal UAT closure, or release readiness until each
  lane produces its own evidence.
- Cross-lane sequencing should prioritize durable persistence and API contracts
  before broad fixture-to-API screen conversion.
- Any release-readiness task must cite the unresolved blockers in
  `docs/evidence/PRODUCTION_READINESS_PACKAGE.md` before changing approval
  language.

## Closeout Scope

This file reconciles the ODP-PV-000 declared artifact list. It does not change
runtime code, canonical architecture policy, task ownership, or release status.

## Frontend Fleet Dispatch Addendum

Updated: 2026-06-29
Source matrix: `docs/design/ODAY_PLUS_DESIGN_TO_FRONTEND_EXECUTION_MATRIX.md`
Release candidate carrying this addendum: `dev@27f5ba0301b143e3b1ca544d44de3ecac4f97cfa`

This addendum turns the completed UXD specifications and execution matrix into
durable frontend implementation lanes. It complements the runtime `ai-status`
assignments and keeps the fleet handoff reviewable in repository evidence.
Shared frontend contract evidence is current through PR #87, PR #88, PR #89,
and PR #90, which added domain type contracts, `packages/ui-domain`,
`packages/ui`, and this evidence refresh.

### Frontend Fleet Lanes

| Task | Owner | Reviewer | Source design specs | Required product E2E proof |
|---|---|---|---|---|
| `ODP-FE-R0-001` OpsBoard shell and global surfaces (`FE-R0-001`, `FE-R0-002`) | Claude | Codex | `ODAY_PLUS_OPSBOARD_SHELL_BLUEPRINT.md`, `ODAY_PLUS_NAVIGATION_AND_WORKFLOW_SPEC.md`, `ODAY_PLUS_R0_SCREEN_INVENTORY.md` | `tests/e2e/e2e-api-bound-ui.spec.ts`; shell, task, notification, search, and role-aware navigation coverage |
| `ODP-FE-EXP-001` Expansion HeatZone to SiteScore workflow (`FE-EXP-001`, `FE-EXP-002`, `FE-EXP-003`) | Codex | Claude | `ODAY_PLUS_EXPANSION_WORKFLOW_BLUEPRINT.md`, `ODAY_PLUS_HEATZONE_MAP_VISUAL_SPEC.md`, `ODAY_PLUS_SITESCORE_REPORT_UI_SPEC.md` | `tests/e2e/e2e-map.spec.ts`, `tests/e2e/e2e-expansion-product.spec.ts`; map/list sync, listing source, SiteScore approval, audit id |
| `ODP-FE-OPS-001` Operations alerts and intervention lifecycle (`FE-OPS-001`, `FE-INT-001`) | Claude2 | Codex2 | `ODAY_PLUS_OPERATIONS_ALERT_UI_SPEC.md`, `ODAY_PLUS_INTERVENTION_WORKFLOW_UI_SPEC.md` | `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts`; four-light evidence, intervention approval/execution/observation, audit evidence |
| `ODP-FE-PRICE-001` PriceOps and AdLift workbenches (`FE-PRICE-001`, `FE-AD-001`) | Codex2 | Claude2 | `ODAY_PLUS_PRICING_AND_ADLIFT_UI_SPEC.md` | `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts`; pricing constraints/rollback and AdLift controls/pre-trend/iROMI proof |
| `ODP-FE-ASSET-001` Asset valuation and NetPlan workbenches (`FE-AVM-001`, `FE-NET-001`) | Claude | Codex2 | `ODAY_PLUS_ASSET_AND_NETPLAN_UI_SPEC.md` | `tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts`; AVM ranges/DataRoom, NetPlan feasible/infeasible solver, approval evidence |
| `ODP-FE-LEARN-001` Learning Hub and Audit Evidence surfaces (`FE-LEARN-001`, `FE-AUDIT-001`) | Codex | Claude2 | `ODAY_PLUS_LEARNING_HUB_UI_SPEC.md`, `ODAY_PLUS_AUDIT_EVIDENCE_UI_SPEC.md` | `tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts`; model release/rollback gates and audit evidence export |
| `ODP-FE-XCUT-001` Cross-cutting UI contracts and product E2E gate expansion (`FE-XCUT-001`, `FE-XCUT-002`, `FE-XCUT-003`, `FE-XCUT-004`, `FE-XCUT-005`, `FE-XCUT-006`) | Claude2 | Codex | `ODAY_PLUS_VISUAL_DESIGN_SYSTEM.md`, `ODAY_PLUS_DESIGN_TOKENS.md`, `ODAY_PLUS_COMPONENT_CONTRACTS.md`, execution matrix | `tests/e2e/test_frontend_execution_matrix_coverage.py`, `tests/contract/test_frontend_domain_type_coverage.py`, `tests/contract/test_ui_core_component_exports.py`, `scripts/e2e/check_product_release_gate.py`; matrix-to-runner-to-release-gate drift guard plus shared domain type, ui-domain, and core UI export coverage |

### Dispatch Rules

- Every lane must use semantic tokens and component contracts from
  `docs/design/`; workers must not invent colors, status names, approval
  patterns, density modes, or landing-page layouts.
- Every data surface must include loading, empty, error, stale-data or
  low-confidence, permission-limited, and audit-relevant states where
  applicable.
- Every high-risk action must show evidence, require reason capture where
  applicable, avoid optimistic update, and display or write audit evidence.
- Every map or chart lane must provide a list/table fallback and must preserve
  confidence, freshness, model version, evidence level, and audit affordances.
- A lane is not complete until its PR cites the source specs above, includes the
  mapped product E2E proof, and leaves `make ci` plus the product release gate
  green on GitHub.

### Current Status

- The frontend dispatch matrix is tracked in `docs/design/`.
- Runtime fleet tasks have been assigned in `ai-status.json`.
- `tests/e2e/test_frontend_execution_matrix_coverage.py` prevents the matrix,
  runner, and release gate from drifting apart.
- This addendum is not a release approval. The draft release PR remains gated
  by Human/Ops go/no-go and rollout target configuration.
