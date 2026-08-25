# Product Release Closeout Pickup Board

Generated: 2026-06-30  
Release target: PR #82 `headRefOid` and attached checks  
Source of truth: `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_QUEUE.json`

## Purpose

This board is the fleet-facing pickup surface for the remaining product release
closeout work. It does not approve release and it does not replace
`ai-status.json`. It turns the machine-readable closeout queue into one
operator table so Human/Ops, owners, and reviewers can pick the correct task,
inspect the named evidence, run the preflight, and record a lifecycle action.

Use this board together with:

- `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_QUEUE.json`
- `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_MANIFEST.md`
- `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PLAYBOOK.md`
- `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`

## Required Preflight

Run these before any closeout action:

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeStateStatus,statusCheckRollup,url
python3 delivery_toolchain/e2e/check_product_release_gate.py
PANTHEON_STATUS_ROOT=/home/lupin/oday-plus python3 delivery_toolchain/e2e/check_product_closeout_action_matrix.py
PANTHEON_STATUS_ROOT=/home/lupin/oday-plus python3 delivery_toolchain/e2e/sync_product_closeout_fleet_comment.py --release-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)" --apply
python3 delivery_toolchain/e2e/check_product_release_gate.py
python3 delivery_toolchain/e2e/check_product_closeout_action.py --task <task-id> --actor <actor> --action-type <action-type>
python3 -m pytest tests/e2e/test_frontend_execution_matrix_coverage.py
```

For staging and production deployment, follow `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md` and the unified `Runtime Release` workflow.

## Pickup Table

| Task | Queue status | Actor | Action type | Blocking type | Required command | Evidence refs |
|---|---|---|---|---|---|---|
| `ODP-PV-008` | `review` | Human/Ops | `go_no_go` | `human_signoff` | `gh pr view 82 --json headRefOid,isDraft,state,mergeStateStatus,statusCheckRollup,url` and `python3 delivery_toolchain/e2e/check_product_release_gate.py` | `docs/evidence/PRODUCT_E2E_READINESS_REPORT.md`, `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md`, `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_MANIFEST.md`, `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PLAYBOOK.md` |
| `ODP-FE-XCUT-001` | `in_progress` | Antigravity3 | `owner_handoff` | `owner_status_closeout` | `AI_NAME=Antigravity3 python3 scripts/ai_status.py handoff ODP-FE-XCUT-001 Antigravity2 "<handoff message>"` | `docs/evidence/FRONTEND_FLEET_COMPLETION_AUDIT.md`, `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_MANIFEST.md`, `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PLAYBOOK.md` |
| `ODP-FE-XCUT-001` | `waiting_for_review_after_handoff` | Antigravity2 | `reviewer_approve_or_reopen` | `reviewer_status_closeout` | `AI_NAME=Antigravity2 REVIEW_NOTES_ZH="<review summary>" python3 scripts/ai_status.py approve ODP-FE-XCUT-001 "<approval message>"` or `AI_NAME=Antigravity2 python3 scripts/ai_status.py reopen ODP-FE-XCUT-001 "<missing evidence>"` | `docs/evidence/FRONTEND_FLEET_COMPLETION_AUDIT.md`, `tests/e2e/test_frontend_execution_matrix_coverage.py`, `tests/contract/test_frontend_domain_type_coverage.py`, `tests/contract/test_ui_core_component_exports.py` |
| `ODP-FE-R0-001` | `review_approved` | Claude | `owner_done` | `owner_status_closeout` | `AI_NAME=Claude python3 scripts/ai_status.py done ODP-FE-R0-001 "<finalization message>"` | `tests/e2e/e2e-operator-console.spec.ts`, `tests/e2e/shell-resource-binding.spec.ts`, `docs/evidence/fleet_dispatch/package10_20260726/ODP-P10-LEGACY-VISUAL-RETIREMENT-VERIFICATION.md`, `docs/evidence/FRONTEND_FLEET_COMPLETION_AUDIT.md` |
| `ODP-FE-EXP-001` | `review` | Claude | `reviewer_approve_or_reopen` | `reviewer_status_closeout` | `AI_NAME=Claude REVIEW_NOTES_ZH="<review summary>" python3 scripts/ai_status.py approve ODP-FE-EXP-001 "<approval message>"` or `AI_NAME=Claude python3 scripts/ai_status.py reopen ODP-FE-EXP-001 "<missing evidence>"` | `tests/e2e/operator-network-listings.spec.ts`, `tests/e2e/operator-network-scoring.spec.ts`, `docs/evidence/fleet_dispatch/package10_20260726/ODP-P10-LEGACY-VISUAL-RETIREMENT-VERIFICATION.md`, `docs/design/ODAY_PLUS_EXPANSION_WORKFLOW_BLUEPRINT.md`, `docs/design/ODAY_PLUS_HEATZONE_MAP_VISUAL_SPEC.md`, `docs/design/ODAY_PLUS_SITESCORE_REPORT_UI_SPEC.md` |
| `ODP-FE-ASSET-001` | `in_progress` | Claude | `owner_handoff` | `owner_status_closeout` | `AI_NAME=Claude python3 scripts/ai_status.py handoff ODP-FE-ASSET-001 Codex2 "<handoff message>"` | `apps/web/features/operator/NetworkFindAreasWorkspace.tsx`, `tests/e2e/e2e-network-find-areas-api-binding.spec.ts`, `docs/evidence/fleet_dispatch/package10_20260726/ODP-P10-LEGACY-VISUAL-RETIREMENT-VERIFICATION.md`, `docs/design/ODAY_PLUS_ASSET_AND_NETPLAN_UI_SPEC.md` |
| `ODP-FE-ASSET-001` | `waiting_for_review_after_handoff` | Codex2 | `reviewer_approve_or_reopen` | `reviewer_status_closeout` | `AI_NAME=Codex2 REVIEW_NOTES_ZH="<review summary>" python3 scripts/ai_status.py approve ODP-FE-ASSET-001 "<approval message>"` or `AI_NAME=Codex2 python3 scripts/ai_status.py reopen ODP-FE-ASSET-001 "<missing evidence>"` | `apps/web/features/operator/NetworkFindAreasWorkspace.tsx`, `tests/e2e/e2e-network-find-areas-api-binding.spec.ts`, `docs/evidence/fleet_dispatch/package10_20260726/ODP-P10-LEGACY-VISUAL-RETIREMENT-VERIFICATION.md`, `docs/design/ODAY_PLUS_ASSET_AND_NETPLAN_UI_SPEC.md` |
| `ODP-FE-XCUT-DOMAIN-001` | `review_approved` | Claude | `owner_done` | `owner_status_closeout` | `AI_NAME=Claude python3 scripts/ai_status.py done ODP-FE-XCUT-DOMAIN-001 "<finalization message>"` | `packages/ui-domain`, `tests/contract/test_frontend_domain_type_coverage.py`, `docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md` |

Before running any required command above, run the single-action preflight with
the same task, actor, and action type:

```bash
PANTHEON_STATUS_ROOT=/home/lupin/oday-plus python3 delivery_toolchain/e2e/check_product_closeout_action.py --task ODP-FE-XCUT-001 --actor Antigravity3 --action-type owner_handoff
```

Use the matrix report to see all currently ready and waiting lanes:

```bash
PANTHEON_STATUS_ROOT=/home/lupin/oday-plus python3 delivery_toolchain/e2e/check_product_closeout_action_matrix.py
```

After PR #82 receives a new `headRefOid`, the release owner must post the
current matrix to PR #82 and verify the comment before any owner/reviewer or
Human/Ops lifecycle action:

```bash
PANTHEON_STATUS_ROOT=/home/lupin/oday-plus python3 delivery_toolchain/e2e/sync_product_closeout_fleet_comment.py --release-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)" --apply
python3 delivery_toolchain/e2e/check_product_closeout_fleet_notification.py
python3 delivery_toolchain/e2e/check_product_release_gate.py
```

## Completed Closeouts

| Task | Final state | Evidence refs |
|---|---|---|
| `ODP-FE-XCUT-UI-001` | `done` | `docs/evidence/ODP_FE_XCUT_UI_001_CLOSEOUT.md`, `tests/contract/test_ui_core_component_exports.py`, `docs/evidence/FRONTEND_FLEET_COMPLETION_AUDIT.md` |
| `ODP-FE-OPS-001` | `done` | `docs/evidence/ODP_FE_OPS_001_CLOSEOUT.md`, `tests/e2e/operator-store-ops.spec.ts`, `docs/evidence/fleet_dispatch/package10_20260726/ODP-P10-LEGACY-VISUAL-RETIREMENT-VERIFICATION.md`, `docs/evidence/FRONTEND_FLEET_COMPLETION_AUDIT.md` |
| `ODP-FE-PRICE-001` | `done` | `docs/evidence/ODP_FE_PRICE_001_CLOSEOUT.md`, `tests/e2e/operator-growth.spec.ts`, `docs/evidence/fleet_dispatch/package10_20260726/ODP-P10-LEGACY-VISUAL-RETIREMENT-VERIFICATION.md`, `docs/evidence/FRONTEND_FLEET_COMPLETION_AUDIT.md` |
| `ODP-FE-LEARN-001` | `done` | `tests/e2e/operator-governance.spec.ts`, `docs/evidence/fleet_dispatch/package10_20260726/ODP-P10-LEGACY-VISUAL-RETIREMENT-VERIFICATION.md`, `docs/evidence/FRONTEND_FLEET_COMPLETION_AUDIT.md` |
| `ODP-FE-XCUT-TYPES-001` | `done` | `packages/domain-types/src/frontend-contracts.ts`, `tests/contract/test_frontend_domain_type_coverage.py`, `docs/evidence/FRONTEND_FLEET_COMPLETION_AUDIT.md` |

## Actor Lanes

- Default actors in early phases include Claude, Claude2, Codex, Codex2.

### Human/Ops

- Pick up `ODP-PV-008`.
- Confirm PR #82 `headRefOid`, draft state, merge state, and attached checks.
- Record `approved`, `approved-with-actions`, or `rejected` in
  `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md`.
- Explicitly preserve the current boundaries: deterministic external data proof
  is not production credential/licensing proof; deterministic map proof is not
  remote-staging live tile/geocoder rollout; deterministic deployment proof is
  not remote staging host/url/secret proof.

### Owners

- Use `owner_handoff` when the parent lane must move from owner work to
  reviewer validation.
- Use `owner_done` only after the task is already `review_approved`.
- Do not finalize from a thin or stale `main` checkout.

### Reviewers

- Use `reviewer_approve_or_reopen` only after inspecting the listed evidence
  refs against the design specs and product E2E proof.
- Approve with `REVIEW_NOTES_ZH` when the evidence is sufficient.
- Reopen with a specific missing evidence message when runtime, visual,
  accessibility, permission, audit, or masking proof is incomplete.

## Scope Boundaries To Preserve

- External data sources currently have deterministic fixtures/source-stub,
  connector contracts, live-provider adapter tests, scheduled fetch worker
  tests, quota/rate-limit handling, freshness/licensing gates, and product E2E
  mock proof. They do not yet have provider-specific production credential
  rotation or provider-specific production licensing approval.
- Maps currently have deterministic local MapLibre/deck/H3 E2E, live
  tile/geocoder boundary checks, layer-toggle behavior, direct map picking,
  deck.gl pixel content, tooltip/evidence detail, resilience states, and full keyboard accessibility.
  They do not yet have remote-staging live tile or live geocoder rollout proof.
- Remote staging currently has deterministic deployment, backup, restore, and
  rollback evidence. It does not yet have remote staging host/url/secret
  configuration, live staging rollout, health/version proof matching PR #82
  `headRefOid`, or a remote staging smoke and backup/restore/rollback drill.

## Close Rule

Do not mark the product release objective complete from this board alone.
Completion still requires:

- PR #82 attached checks are green at the decision `headRefOid`;
- `ODP-PV-008` has Human/Ops go/no-go;
- owner and reviewer closeouts are done or explicitly superseded by Human/Ops;
- `python3 delivery_toolchain/e2e/check_product_release_gate.py` passes;
- ephemeral staging rehearsal and production rollout follow `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`.
