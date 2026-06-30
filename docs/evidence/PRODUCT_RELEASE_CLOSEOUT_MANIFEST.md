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
| External proof closeout queue | `docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json` enumerates provider credential/license/geocoder, remote live map endpoint, and remote staging proof tasks with owners, fleet routing, required pickup labels, commands, evidence refs, completion rules, and GitHub tracking issues #132-#138 | prepared, externally blocked |
| External proof handback template | `docs/evidence/EXTERNAL_PROOF_HANDBACK_TEMPLATE.json` defines the redacted runtime proof artifact fields fleets must attach for #132-#138, including PR #82 `headRefOid`, correlation ids, artifact refs, redaction summary, and Product Validation attestation; `python3 scripts/e2e/check_external_proof_handback_template.py` keeps the template synchronized with the external proof queue | prepared, handback contract |
| External proof handback status board | `docs/evidence/EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json` tracks Product Validation intake status for #132-#138 handbacks as pending/submitted/needs-revision/accepted; `python3 scripts/e2e/update_external_proof_handback_status_board.py` safely updates entries and `python3 scripts/e2e/check_external_proof_handback_status_board.py` keeps it synchronized with the external proof queue | prepared, intake tracking guard |
| External proof handback artifact validator | `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` validates a completed fleet handback before Product Validation accepts it: task/issue mapping, release SHA, redaction, artifact types, required evidence results, required queue command fragments in `commands_run`, command exits, correlation ids, and accepted attestation | prepared, acceptance guard |
| External proof handback bundle validator | `python3 scripts/e2e/check_external_proof_handback_bundle.py <handback-dir-or-files> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` validates the full #132-#138 handback set: every task present, no duplicates, no mixed release SHAs, and every handback accepted by the artifact checker | prepared, set-level acceptance guard |
| External proof issue/comment syncer | `python3 scripts/e2e/sync_external_proof_fleet_issues.py --release-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)" --apply` refreshes #132-#138 issue bodies and pickup comments from `PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json` whenever PR #82 advances | prepared, live GitHub handoff updater |
| External proof issue sync | `python3 scripts/e2e/check_external_proof_issue_sync.py --require-assignees` verifies live GitHub issues #132-#138 still carry the queue-defined fleet routing labels, pickup commands, release authority, completion boundaries, and named release-coordinator assignees | prepared, live GitHub check |
| External proof live blocker sync | `python3 scripts/e2e/check_external_proof_live_blockers.py --require-assignees` compares live GitHub issue state with `EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json` so unaccepted #132-#138 handbacks must keep open, labeled, assigned release-blocker issues | prepared, live GitHub blocker check |
| External proof fleet notification sync | `python3 scripts/e2e/check_external_proof_fleet_notifications.py` verifies #132-#138 each have a fleet pickup comment tied to the current PR #82 `headRefOid`, so assignees are notified whenever the release target changes | prepared, live GitHub notification check |
| Product go/no-go external proof guard | `python3 scripts/e2e/check_product_go_no_go.py` verifies `PRODUCT_RELEASE_GO_NO_GO.md` remains conditional and keeps #132-#138 pending until accepted handbacks prove live provider, live map, and remote staging evidence | prepared, release wording guard |
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
| `ODP-PV-008` | `review` | Human/Ops | Review `PRODUCT_E2E_READINESS_REPORT.md`, `PRODUCT_RELEASE_GO_NO_GO.md`, PR #82 checks, deterministic source-stub boundary, and rollout limitation; record go/no-go | human_signoff |
| `ODP-FE-XCUT-001` | `in_progress` | Claude2 | Move parent lane to review after accepting PR #87/#88/#89/#90/#91/#92 evidence and no remaining XCUT repo gap | owner_status_closeout |
| `ODP-FE-XCUT-001` | `waiting_for_review_after_handoff` | Codex | Approve after owner moves it to `review`; current reviewer check found no repository evidence gap | reviewer_status_closeout |
| `ODP-FE-R0-001` | `review_approved` | Claude | Finalize owner closeout to `done` if no extra UX scope is requested | owner_status_closeout |
| `ODP-FE-EXP-001` | `review` | Claude | Review Expansion evidence against Expansion workflow, HeatZone map, and SiteScore specs | reviewer_status_closeout |
| `ODP-FE-ASSET-001` | `in_progress` | Claude | Hand off Asset/NetPlan evidence to Codex2 after accepting AVM reserve/asking masking and non-leakage E2E assertions | owner_status_closeout |
| `ODP-FE-ASSET-001` | `waiting_for_review_after_handoff` | Codex2 | Review Asset/NetPlan evidence after Claude owner handoff | reviewer_status_closeout |
| `ODP-FE-XCUT-DOMAIN-001` | `review_approved` | Claude | Finalize owner closeout to `done` after accepted `packages/ui-domain` export evidence | owner_status_closeout |
| PR #82 | draft/open | Human/Ops and release owner | Keep draft until Human/Ops signoff and rollout target decision are recorded | release workflow |
| External proof queue | `external_blocked` | Platform/Ops, Data Partnerships, Legal, Product Validation | Complete `docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json` tasks with redacted runtime proof before claiming live provider, live map, or remote staging readiness | external proof closeout |

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
  `scripts/e2e/check_remote_staging_proof.py`.
- Do not claim live provider, live map, or remote staging completion until the
  relevant task in `docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json`
  has attached external runtime evidence.
- External runtime evidence attached to #132-#138 must follow
  `docs/evidence/EXTERNAL_PROOF_HANDBACK_TEMPLATE.json`; do not accept
  unredacted logs, secrets, or proof that lacks PR #82 `headRefOid`,
  correlation ids, artifact refs, and Product Validation attestation.
- Before closing any external-proof issue, Product Validation must run
  `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"`
  against the attached handback and reject proof that fails this checker.
- Before treating external proof closeout as complete, Product Validation must
  run `python3 scripts/e2e/check_external_proof_handback_bundle.py <handback-dir-or-files> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"`
  against the full #132-#138 handback set and reject missing, duplicate, or
  mixed-release handbacks.
- Keep every external proof GitHub issue routed with `product-e2e`,
  `external-proof`, `release-blocker`, and the owner-lane pickup label
  (`platform-ops` or `data-partnerships`) until Product Validation accepts the
  attached proof.
- Run `python3 scripts/e2e/sync_external_proof_fleet_issues.py --release-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)" --apply`
  after PR #82 receives a new `headRefOid`; this refreshes #132-#138 issue
  bodies and pickup comments from the external proof queue before live checks.
- Run `python3 scripts/e2e/check_external_proof_live_blockers.py --require-assignees`
  before external-proof closeout; an issue cannot be closed while its matching
  handback status is pending, submitted, or needs revision.
- Run `python3 scripts/e2e/check_external_proof_fleet_notifications.py` after
  PR #82 receives a new `headRefOid`; every #132-#138 issue must have a pickup
  comment for the current release target before fleet closeout.
- Do not close reviewer-owned lanes by changing `ai-status.json` from an
  unassigned actor; use the named owner/reviewer lifecycle.
- Do not run final `done` closeout from a thin or stale `main` checkout. Owner
  finalization must run from a worktree/branch whose commit, PR merge state, and
  task trailers satisfy `scripts/ai_status.py` delivery gates.
- Keep product E2E proof release-blocking through PR #82 checks and
  `make product-release-gate`.
