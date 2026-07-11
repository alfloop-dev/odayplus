# Frontend Fleet Completion Audit

Task family: ODP-FE
Generated: 2026-06-29
Reviewer: Human/Ops
Release target: draft release PR #82 head commit; use GitHub PR #82
`headRefOid` and attached checks as the authoritative release candidate.

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
| `ODP-FE-XCUT-001` Cross-cutting UI contracts and product E2E gate expansion | `tests/e2e/test_frontend_execution_matrix_coverage.py` verifies dispatch evidence, design matrix, product E2E runner, and static release gate alignment. PR #87 added `packages/domain-types/src/frontend-contracts.ts` plus `tests/contract/test_frontend_domain_type_coverage.py` for Candidate/SiteScore/Forecast/Intervention/Pricing/AdLift/AVM/NetPlan/ModelRelease/Audit UI contracts. PR #88 added `packages/ui-domain` reusable scaffolds for all 13 documented domain components. PR #89 added core `packages/ui` scaffolds and `tests/contract/test_ui_core_component_exports.py` for Toolbar/FilterBar, Drawer, Button, Card, Table, Form, Modal, Tabs, Timeline, Toast, Tooltip, CommandPalette, EmptyState, DataStatusBadge, ModelVersionBadge, ApprovalPanel, AuditMetadata, AlertChip, and EvidencePanel. PR #90 refreshed the durable fleet evidence. PR #91 refreshed release evidence and added stale release-ref guards. `make ci`, GitHub `ci`, and `product-e2e-gate` passed for all three implementation follow-up PRs and for draft release PR #82 head checks. | `evidence-ready` | Reviewer may verify shared UI/domain/type coverage against `ODAY_PLUS_COMPONENT_CONTRACTS.md`. Parent lane closeout still requires assigned owner/reviewer status updates in `ai-status.json`; no remaining code/evidence gap is known for the shared contract slice. |

## Remaining Non-Code Gates

- `ODP-PV-008` remains in Human/Ops review.
- PR #82 remains draft until Human/Ops records go/no-go in
  `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md`.
- `ODP-FE-XCUT-001` remains owner-held by Antigravity3; Antigravity2 reviewer closeout is
  queued only after Antigravity3 hands off the parent cross-cutting lane.
- `ODP-FE-R0-001` and `ODP-FE-XCUT-DOMAIN-001` are `review_approved` and need
  Claude owner finalization.
- `ODP-FE-EXP-001` is in Claude reviewer closeout.
- `ODP-FE-ASSET-001` remains owner-held by Claude; Codex2 reviewer closeout is
  queued only after Claude hands off the Asset/NetPlan lane.
- `ODP-FE-XCUT-UI-001`, `ODP-FE-OPS-001`, `ODP-FE-PRICE-001`,
  `ODP-FE-LEARN-001`, and `ODP-FE-XCUT-TYPES-001` are completed closeouts and
  must not be re-dispatched as active work.
- Remote staging rollout remains conditional on rollout target configuration
  and accepted #137/#138 handbacks.
- Live provider credential/licensing/geocoder and remote live map proof remain
  conditional on accepted #132-#136 handbacks.

## Closeout Recommendation

- Use `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_QUEUE.json` and
  `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PICKUP_BOARD.md` as the active task
  source of truth.
- Do not move completed lanes back into active review. Their evidence is kept
  under `completed_closeouts` for lineage only.
- Ask assigned active owners/reviewers to finish only the queued lifecycle
  actions: Human/Ops go/no-go for `ODP-PV-008`, Antigravity3 handoff and Antigravity2
  review for `ODP-FE-XCUT-001`, Claude finalization for `ODP-FE-R0-001`,
  Claude review for `ODP-FE-EXP-001`, Claude handoff and Codex2 review for
  `ODP-FE-ASSET-001`, and Claude finalization for
  `ODP-FE-XCUT-DOMAIN-001`.
- Keep #82 draft until Human/Ops accepts the product E2E evidence and rollout
  limitation, and until #132-#138 external runtime handbacks are accepted or
  explicitly deferred by Human/Ops policy.
