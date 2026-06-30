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
| Release go/no-go packet | `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md` lists go/no-go criteria and Human/Ops checklist | prepared, pending Human/Ops |
| External proof closeout queue | `docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json` enumerates provider credential/license/geocoder, remote live map endpoint, and remote staging proof tasks with owners, commands, evidence refs, and completion rules | prepared, externally blocked |
| Static release gate | `python3 scripts/e2e/check_product_release_gate.py` validates required specs, evidence docs, runner coverage, deterministic source fixtures, and correlation ids | proven |
| Product E2E runner | `scripts/e2e/run_product_e2e.sh` runs API-bound UI, map, expansion, PV-006, PV-007, and product environment Playwright specs | proven by PR #82 checks |
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
| `ODP-PV-008` | `review` | Human/Ops | Review `PRODUCT_E2E_READINESS_REPORT.md`, `PRODUCT_RELEASE_GO_NO_GO.md`, PR #82 checks, deterministic source-stub boundary, and rollout limitation; record go/no-go | human signoff |
| `ODP-FE-XCUT-001` | `in_progress` | Claude2 owner | Move parent lane to review after accepting PR #87/#88/#89/#90/#91/#92 evidence and no remaining XCUT repo gap | owner status closeout |
| `ODP-FE-XCUT-001` | waiting after owner handoff | Codex reviewer | Approve after owner moves it to `review`; current reviewer check found no repository evidence gap | reviewer status closeout |
| `ODP-FE-R0-001` | `review_approved` | Claude owner | Finalize owner closeout to `done` if no extra UX scope is requested | owner status closeout |
| `ODP-FE-XCUT-UI-001` | `review_approved` | Claude2 owner | Finalize owner closeout to `done` if no extra UI contract scope is requested | owner status closeout |
| `ODP-FE-EXP-001` | `review` | Claude reviewer | Review Expansion evidence against Expansion workflow, HeatZone map, and SiteScore specs | reviewer status closeout |
| `ODP-FE-OPS-001` | `review` | Codex2 reviewer | Review Ops/Intervention evidence against Operations and Intervention specs | reviewer status closeout |
| `ODP-FE-PRICE-001` | `review` | Claude2 reviewer | Review PriceOps/AdLift evidence against Pricing and AdLift specs | reviewer status closeout |
| `ODP-FE-ASSET-001` | `review` | Codex2 reviewer | Review AVM/NetPlan evidence against Asset and NetPlan specs | reviewer status closeout |
| `ODP-FE-LEARN-001` | `review` | Claude2 reviewer | Review Learning/Audit evidence against Learning Hub and Audit Evidence specs | reviewer status closeout |
| `ODP-FE-XCUT-DOMAIN-001` | `review` | Codex2 reviewer | Review `packages/ui-domain` exports and domain contract test evidence | reviewer status closeout |
| `ODP-FE-XCUT-TYPES-001` | `review` | Claude2 reviewer | Review `packages/domain-types` frontend contract coverage and type export evidence | reviewer status closeout |
| PR #82 | draft/open | Human/Ops and release owner | Keep draft until Human/Ops signoff and rollout target decision are recorded | release workflow |
| External proof queue | `external_blocked` | Platform/Ops, Data Partnerships, Legal, Product Validation | Complete `docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json` tasks with redacted runtime proof before claiming live provider, live map, or remote staging readiness | external proof closeout |

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
  `scripts/e2e/check_remote_staging_proof.py`.
- Do not claim live provider, live map, or remote staging completion until the
  relevant task in `docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json`
  has attached external runtime evidence.
- Do not close reviewer-owned lanes by changing `ai-status.json` from an
  unassigned actor; use the named owner/reviewer lifecycle.
- Do not run final `done` closeout from a thin or stale `main` checkout. Owner
  finalization must run from a worktree/branch whose commit, PR merge state, and
  task trailers satisfy `scripts/ai_status.py` delivery gates.
- Keep product E2E proof release-blocking through PR #82 checks and
  `make product-release-gate`.
