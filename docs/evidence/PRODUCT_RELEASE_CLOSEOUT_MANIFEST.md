# Product Release Closeout Manifest

Task: ODP-PV-008  
Generated: 2026-06-29  
Owner: Codex  
Status: active closeout manifest

## Purpose

This manifest separates repository evidence from remaining workflow gates for
the ODay Plus product-grade E2E release candidate. It is intentionally narrow:
it does not approve release and does not replace `ai-status.json`; it gives
fleets and Human/Ops a stable closeout map so evidence-ready lanes are not
mistaken for unfinished implementation.

The authoritative release target is draft release PR #82. Use PR #82
`headRefOid` and attached checks as the release candidate; do not hard code a
`dev@...` hash in release evidence documents.

## Repository Evidence Already Proven

| Area | Evidence | Status |
|---|---|---|
| Execution task matrix | `docs/design/ODAY_PLUS_DESIGN_TO_FRONTEND_EXECUTION_MATRIX.md` maps FE-R0, Expansion, Ops/Intervention, Price/AdLift, Asset/NetPlan, Learning/Audit, and cross-cutting tasks to source specs and product E2E proof | proven |
| Fleet dispatch | `docs/evidence/PRODUCT_VALIDATION_FLEET_DISPATCH.md` maps ODP-FE lanes to owners/reviewers, source specs, and required E2E proof | proven |
| Runtime evidence audit | `docs/evidence/FRONTEND_FLEET_COMPLETION_AUDIT.md` marks FE lanes evidence-ready and cites executable tests | proven |
| Product E2E readiness | `docs/evidence/PRODUCT_E2E_READINESS_REPORT.md` links P0 scenarios to executable tests, deterministic data, screenshots/traces, and audit/evidence ids | proven for deterministic product-E2E environment |
| External provider & staging governance | `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md` defines the unified build-once, ephemeral staging, and production blue-green rollout model | governed by single-path release |
| Static release gate | `python3 delivery_toolchain/e2e/check_product_release_gate.py` validates required specs, evidence docs, runner coverage, deterministic source fixtures, and correlation ids | proven |
| Product E2E runner | `delivery_toolchain/e2e/run_product_e2e.sh` runs API-bound UI, map, expansion, PV-006, PV-007, and product environment Playwright specs | proven by PR #82 checks |
| Dynamic release target guard | `tests/e2e/test_frontend_execution_matrix_coverage.py` rejects hard-coded `dev@...` release refs and requires PR #82 `headRefOid`/checks language | proven |
| Shared frontend contract PRs | PR #87 added domain type contracts, PR #88 added `packages/ui-domain`, PR #89 added `packages/ui`, PR #90 refreshed durable fleet evidence, and PR #91 refreshed release-candidate evidence | proven |

## Deterministic E2E Scope Boundaries

| Topic | Current proof | Boundary |
|---|---|---|
| External data sources | Source fixtures, source-stub service, connector contract tests, live-provider adapter tests, scheduled fetch worker tests, quota/rate-limit/freshness/licensing gates, and `tests/e2e/test_external_source_product_e2e.py` prove deterministic and mock-live source behavior | This is not provider-specific production credential rotation or provider-specific production licensing approval |
| Maps | `tests/e2e/e2e-map.spec.ts`, `tests/e2e/e2e-map-live-boundary.spec.ts`, `tests/e2e/e2e-map-resilience.spec.ts`, `tests/e2e/e2e-map-tooltip-evidence.spec.ts`, and `tests/e2e/e2e-map-a11y.spec.ts` prove MapLibre/deck/H3 rendering, live boundary config, URL layer persistence, direct picking, semantic pixels, resilience states, tooltip/evidence detail, and full keyboard accessibility | This is not a remote-staging rollout against actual live tile/geocoder endpoints |
| Deployment/rollback | `docs/evidence/DEPLOYMENT_HEALTH_BACKUP_ROLLBACK_EVIDENCE.md` and GitHub `Deploy Dev` prove deterministic E2E deployment, backup, restore, and rollback evidence | Remote staging remains conditional on target configuration and `docs/evidence/REMOTE_STAGING_PROOF_RUNBOOK.md` |

## Remaining Closeout Actions

| Task | Current state | Required actor | Required action | Blocking type |
|---|---|---|---|---|
| `ODP-PV-008` | `review` | Human/Ops | Review `PRODUCT_E2E_READINESS_REPORT.md`, `PRODUCT_RELEASE_GO_NO_GO.md`, PR #82 checks, deterministic source-stub boundary, and rollout limitation; record go/no-go | human_signoff |
| `ODP-FE-XCUT-001` | `in_progress` | Antigravity3 | Move parent lane to review after accepting PR #87/#88/#89/#90/#91/#92 evidence and no remaining XCUT repo gap | owner_status_closeout |
| `ODP-FE-XCUT-001` | `waiting_for_review_after_handoff` | Antigravity2 | Approve after owner moves it to `review`; current reviewer check found no repository evidence gap | reviewer_status_closeout |
| `ODP-FE-R0-001` | `review_approved` | Claude | Finalize owner closeout to `done` if no extra UX scope is requested | owner_status_closeout |
| `ODP-FE-EXP-001` | `review` | Claude | Review Expansion evidence against Expansion workflow, HeatZone map, and SiteScore specs | reviewer_status_closeout |
| `ODP-FE-ASSET-001` | `waiting_for_review_after_handoff` | Codex2 | Review Asset/NetPlan evidence after Claude owner handoff | reviewer_status_closeout |
| `ODP-FE-XCUT-DOMAIN-001` | `review_approved` | Claude | Finalize owner closeout to `done` after accepted `packages/ui-domain` export evidence | owner_status_closeout |
| PR #82 | draft/open | Human/Ops and release owner | Keep draft until Human/Ops signoff and rollout target decision are recorded | release workflow |
| External provider & staging readiness | `external_blocked` | Platform/Ops, Data Partnerships, Legal, Product Validation | Fulfill source activation receipts and ephemeral staging validation per `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md` before live provider or staging readiness | external proof closeout |

## Completed Closeouts

| Task | Final state | Evidence | Note |
|---|---|---|---|
| `ODP-FE-XCUT-UI-001` | `done` | `docs/evidence/ODP_FE_XCUT_UI_001_CLOSEOUT.md`, `tests/contract/test_ui_core_component_exports.py` | Archived as done after UI core closeout evidence merged. |
| `ODP-FE-OPS-001` | `done` | `docs/evidence/ODP_FE_OPS_001_CLOSEOUT.md`, `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts` | Archived as done after Ops/Intervention closeout evidence merged. |
| `ODP-FE-PRICE-001` | `done` | `docs/evidence/ODP_FE_PRICE_001_CLOSEOUT.md`, `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts` | Archived as done after PriceOps/AdLift review and owner finalization. |
| `ODP-FE-LEARN-001` | `done` | `tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts`, `docs/evidence/FRONTEND_FLEET_COMPLETION_AUDIT.md` | Archived as done; Learning/Audit surfaces remain covered by product E2E evidence. |
| `ODP-FE-XCUT-TYPES-001` | `done` | `packages/domain-types/src/frontend-contracts.ts`, `tests/contract/test_frontend_domain_type_coverage.py` | Archived as done after frontend type contract evidence merged. |

Note: table blocking types use canonical queue values. The older prose labels
"owner status closeout" and "reviewer status closeout" map to
`owner_status_closeout` and `reviewer_status_closeout`.

## Closeout Invariants

- Do not mark the release complete while PR #82 is draft.
- Do not claim live external provider integration from deterministic/mock-live
  source proof.
- Do not claim provider-specific production credential rotation or production
  licensing approval from deterministic/mock-live source proof.
- Remaining external-source terms must stay explicit in the release packet:
  provider credential/OAuth, scheduled external fetch, quota/rate-limit, and
  production licensing.
- Do not claim live remote staging rollout until staging host/url/secret
  configuration is provided and verified with
  `delivery_toolchain/e2e/check_remote_staging_proof.py`.
- Do not claim live provider, live map, or remote staging completion until the
  relevant provider approval receipts and ephemeral staging rehearsals are executed per
  `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`.
- Run `PANTHEON_STATUS_ROOT=/home/lupin/oday-plus python3 delivery_toolchain/e2e/check_product_closeout_action_matrix.py`
  before owner/reviewer/Human-Ops lifecycle commands so fleets can see which
  closeout actions are ready, waiting for handoff, PR-blocked, or stale.
- Run `PANTHEON_STATUS_ROOT=/home/lupin/oday-plus python3 delivery_toolchain/e2e/sync_product_closeout_fleet_comment.py --release-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)" --apply`
  after PR #82 receives a new `headRefOid`; this refreshes the PR #82 fleet
  comment from the closeout queue and current action matrix.
- Run `python3 delivery_toolchain/e2e/check_product_closeout_fleet_notification.py`
  before owner/reviewer/Human-Ops lifecycle commands; PR #82 must have a
  product closeout fleet update for the current release target.
- Run `python3 delivery_toolchain/e2e/check_release_fleet_dispatch_status.py` after
  refreshing issue and PR comments; this is the aggregate proof that the
  current release candidate has been dispatched to external-proof and closeout
  fleets with live GitHub surfaces synchronized.
- Do not close reviewer-owned lanes by changing `ai-status.json` from an
  unassigned actor; use the named owner/reviewer lifecycle.
- Do not run final `done` closeout from a thin or stale `main` checkout. Owner
  finalization must run from a worktree/branch whose commit, PR merge state, and
  task trailers satisfy `scripts/ai_status.py` delivery gates.
- Keep product E2E proof release-blocking through PR #82 checks and
  `make product-release-gate`.
