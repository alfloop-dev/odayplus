# Frontend Fleet Completion Audit

Task family: ODP-FE
Generated: 2026-06-29
Reviewer: Human/Ops
Release candidate: `dev@22c802ebc9b4e22b0914b8f3047c858a1a20faeb`

## Purpose

This audit maps the frontend fleet execution lanes to concrete runtime
evidence. It is intentionally stricter than a dispatch list: a lane is marked
`evidence-ready` only when current repository artifacts and product-grade E2E
checks prove the lane's stated acceptance conditions.

This file does not replace `ai-status.json`. It gives reviewers a stable
repository artifact for deciding whether a lane can move from implementation to
review/closeout.

## Evidence Sources

| Evidence | Scope |
|---|---|
| `docs/design/ODAY_PLUS_DESIGN_TO_FRONTEND_EXECUTION_MATRIX.md` | Source task matrix, routes, components, states, and E2E proof expectations |
| `docs/evidence/PRODUCT_VALIDATION_FLEET_DISPATCH.md` | Durable owner/reviewer lanes and dispatch rules |
| `scripts/e2e/run_product_e2e.sh` | Product E2E runner and required Playwright specs |
| `scripts/e2e/check_product_release_gate.py` | Static release gate for product E2E evidence packet |
| `tests/e2e/test_frontend_execution_matrix_coverage.py` | Drift guard tying dispatch, matrix, runner, and release gate together |
| `docs/evidence/PRODUCT_E2E_READINESS_REPORT.md` | Release-level product E2E scenario registry and evidence summary |
| GitHub PR #82 | Draft release PR with current clean checks and Human/Ops gate |

## Lane Completion Matrix

| Lane | Runtime evidence | Status | Reviewer action |
|---|---|---|---|
| `ODP-FE-R0-001` OpsBoard shell and global surfaces | `tests/e2e/opsboard-shell.spec.ts` covers shell rendering, design tokens, 14 work-area routes, role-aware navigation, and sidebar header updates. `tests/e2e/e2e-api-bound-ui.spec.ts` covers live API-bound admin audit and AVM surfaces. | `evidence-ready` | Reviewer may verify R0 against shell specs, then move task to review if no extra UX scope is requested. |
| `ODP-FE-EXP-001` Expansion HeatZone to SiteScore workflow | `tests/e2e/e2e-map.spec.ts` covers MapLibre/deck.gl/H3 nonblank map and list fallback behavior. `tests/e2e/e2e-expansion-product.spec.ts` covers HeatZone scoring, listing import/dedup/hard-rule rejection, candidate creation, SiteScore versioning, approval reason enforcement, realized site registration, audit events, UI map/list sync, listing/candidate drawers, evidence panel, and decision id. | `evidence-ready` | Reviewer may compare against `ODAY_PLUS_EXPANSION_WORKFLOW_BLUEPRINT.md`, `ODAY_PLUS_HEATZONE_MAP_VISUAL_SPEC.md`, and `ODAY_PLUS_SITESCORE_REPORT_UI_SPEC.md`. |
| `ODP-FE-OPS-001` Operations alerts and intervention lifecycle | `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts` covers ForecastOps red alert creation, intervention eligibility/action/conflict/submit/approve/execute/outcome/evaluate, mature label registration, audit events, operations store detail, handoff panel, intervention conflict block, and approval panel. | `evidence-ready` | Reviewer may verify four-light evidence and intervention lifecycle UI against Operations and Intervention specs. |
| `ODP-FE-PRICE-001` PriceOps and AdLift workbenches | `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts` covers PriceOps optimizer, submit/approve/activate/observation/evaluate, rollback recommendation, rollback plan, correlation id, label registration, AdLift incrementality job, controls, pre-trend, causal-claim guard, UI hard constraint disabled approval, and rollback panel. | `evidence-ready` | Reviewer may verify the PriceOps/AdLift specs and confirm whether additional UI affordance polish is required before closeout. |
| `ODP-FE-ASSET-001` Asset valuation and NetPlan workbenches | `tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts` covers AVM case creation/normalize/value/finance approval/DataRoom/export, NetPlan scenario create/solve/submit/approve/execute/outcome, retained audit evidence, UI valuation range chart, approval panels, DataRoom, NetPlan binding constraints, execution variance, and never-optimistic approval copy. | `evidence-ready` | Reviewer may verify AVM and NetPlan UI spec coverage, including infeasible solver coverage from `tests/e2e/e2e-avm-netplan.spec.ts`. |
| `ODP-FE-LEARN-001` Learning Hub and Audit Evidence surfaces | `tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts` covers dataset/model registration, model release to CANARY/FULL/ROLLBACK, alias rollback, artifact digest, model UI validation/release/rollback/audit panels, audit decision detail, override comparison, evidence export panel, retained bundle checksum, and missing requirement checks. | `evidence-ready` | Reviewer may verify Learning Hub and Audit Evidence specs and decide whether route-level manual review is enough to close. |
| `ODP-FE-XCUT-001` Cross-cutting UI contracts and product E2E gate expansion | `tests/e2e/test_frontend_execution_matrix_coverage.py` verifies the dispatch evidence, design matrix, product E2E runner, and static release gate stay aligned. `make ci` and PR #85 prove the drift guard is active in CI. `packages/design-tokens`, `packages/ui`, and `packages/domain-types` typecheck/build in `make ci`. | `partial-evidence-ready` | Drift guard and token/package checks are done. Broader component contract completeness still needs reviewer closeout before marking the whole lane complete. |

## Remaining Non-Code Gates

- `ODP-PV-008` remains in Human/Ops review.
- PR #82 remains draft until Human/Ops records go/no-go in
  `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md`.
- Remote staging rollout remains conditional on rollout target configuration.
- Worker runtime dispatch had prior supervisor failures in local task briefs;
  durable repository evidence now supersedes those transient startup failures
  for review, but individual lanes still need owner/reviewer status updates in
  `ai-status.json`.

## Closeout Recommendation

- Move `ODP-FE-EXP-001`, `ODP-FE-PRICE-001`, and `ODP-FE-LEARN-001` to review
  after owners confirm no additional implementation scope beyond the cited E2E
  evidence is required.
- Ask Claude/Claude2 owners to review `ODP-FE-R0-001`, `ODP-FE-OPS-001`,
  `ODP-FE-ASSET-001`, and `ODP-FE-XCUT-001` against this audit before final
  task closeout.
- Keep #82 draft until Human/Ops accepts the product E2E evidence and rollout
  limitation.
