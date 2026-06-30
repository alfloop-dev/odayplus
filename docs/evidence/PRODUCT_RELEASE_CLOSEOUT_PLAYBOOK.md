# Product Release Closeout Playbook

Task: ODP-PV-008  
Generated: 2026-06-29  
Audience: Human/Ops, FE reviewers, FE owners, release owner

## Purpose

This playbook turns the remaining release closeout work into concrete actions.
Use it with `PRODUCT_RELEASE_CLOSEOUT_MANIFEST.md`; the manifest says what
remains, while this playbook says exactly how each actor should close or reject
their gate.

The release target is draft PR #82. Always verify PR #82 `headRefOid` and
attached checks at the time of decision.

Shared frontend evidence lineage for reviewer context:

- PR #87: domain type contracts and frontend contract coverage.
- PR #88: `packages/ui-domain` domain component scaffolds.
- PR #89: `packages/ui` core component scaffolds.
- PR #90: durable frontend fleet completion evidence refresh.
- PR #91: release-candidate evidence refresh and stale-ref guard.

## Reviewer Closeout Commands

Reviewers should approve only after comparing the evidence to the named design
specs and checking that PR #82 is still clean. Use `REVIEW_NOTES_ZH` when
recording a meaningful review summary.

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeStateStatus,statusCheckRollup,url
python3 -m pytest tests/e2e/test_frontend_execution_matrix_coverage.py
python3 scripts/e2e/check_product_release_gate.py
PANTHEON_STATUS_ROOT=/home/lupin/oday-plus python3 scripts/e2e/check_product_closeout_action_matrix.py
PANTHEON_STATUS_ROOT=/home/lupin/oday-plus python3 scripts/e2e/sync_product_closeout_fleet_comment.py --release-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)" --apply
python3 scripts/e2e/check_product_closeout_fleet_notification.py
python3 scripts/e2e/check_release_fleet_dispatch_status.py
PANTHEON_STATUS_ROOT=/home/lupin/oday-plus python3 scripts/e2e/check_product_closeout_action.py --task <task-id> --actor <Reviewer> --action-type reviewer_approve_or_reopen
python3 scripts/e2e/check_external_proof_closeout_queue.py
python3 scripts/e2e/check_external_proof_handback_template.py
```

If satisfied:

```bash
AI_NAME=<Reviewer> REVIEW_NOTES_ZH="<review summary>" \
  python3 scripts/ai_status.py approve <task-id> "<approval message>"
```

If not satisfied:

```bash
AI_NAME=<Reviewer> python3 scripts/ai_status.py reopen <task-id> "<specific missing evidence or required change>"
```

## Reviewer Task Map

| Task | Reviewer | Required evidence to inspect |
|---|---|---|
| `ODP-FE-EXP-001` | Claude | `tests/e2e/e2e-map.spec.ts`, `tests/e2e/e2e-expansion-product.spec.ts`, Expansion/HeatZone/SiteScore specs |
| `ODP-FE-ASSET-001` | Codex2 | Only after Claude owner hands off Asset/NetPlan lane; inspect `tests/e2e/e2e-avm-netplan.spec.ts`, `tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts`, AVM/NetPlan specs |
| `ODP-FE-XCUT-001` | Codex | Only after Claude2 owner hands off parent lane to review; inspect PR #87/#88/#89/#90/#91/#92/#93 evidence and cross-cutting acceptance |

## Owner Finalization Commands

Owners can finalize only after their task is `review_approved`. Use a worktree
that satisfies delivery gates; do not finalize from a thin or stale `main`
checkout.

```bash
git fetch origin dev
gh pr view 82 --json headRefOid,isDraft,state,mergeStateStatus,statusCheckRollup,url
PANTHEON_STATUS_ROOT=/home/lupin/oday-plus python3 scripts/e2e/check_product_closeout_action_matrix.py
python3 scripts/e2e/check_product_closeout_fleet_notification.py
python3 scripts/e2e/check_release_fleet_dispatch_status.py
PANTHEON_STATUS_ROOT=/home/lupin/oday-plus python3 scripts/e2e/check_product_closeout_action.py --task <task-id> --actor <Owner> --action-type owner_done
AI_NAME=<Owner> python3 scripts/ai_status.py done <task-id> "<finalization message>"
```

For owner handoff lanes, replace `owner_done` with `owner_handoff` in the
preflight command before running `scripts/ai_status.py handoff`.

Owner closeout currently applies to:

| Task | Owner | Current closeout need |
|---|---|---|
| `ODP-FE-R0-001` | Claude | Finalize after existing review approval |
| `ODP-FE-XCUT-DOMAIN-001` | Claude | Finalize after existing review approval |
| `ODP-FE-XCUT-001` | Claude2 | First hand off parent lane to Codex for review after accepting child-lane evidence |
| `ODP-FE-ASSET-001` | Claude | Hand off Asset/NetPlan lane to Codex2 for review |

## Completed Closeouts

These lanes are no longer active closeout work. They remain on the manifest as
completed evidence so release reviewers can verify lineage without reassigning
finished tasks.

| Task | Status | Evidence |
|---|---|---|
| `ODP-FE-XCUT-UI-001` | done | `docs/evidence/ODP_FE_XCUT_UI_001_CLOSEOUT.md`, `tests/contract/test_ui_core_component_exports.py` |
| `ODP-FE-OPS-001` | done | `docs/evidence/ODP_FE_OPS_001_CLOSEOUT.md`, `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts` |
| `ODP-FE-PRICE-001` | done | `docs/evidence/ODP_FE_PRICE_001_CLOSEOUT.md`, `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts` |
| `ODP-FE-LEARN-001` | done | `tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts`, `docs/evidence/FRONTEND_FLEET_COMPLETION_AUDIT.md` |
| `ODP-FE-XCUT-TYPES-001` | done | `packages/domain-types/src/frontend-contracts.ts`, `tests/contract/test_frontend_domain_type_coverage.py` |

## Human/Ops Go/No-Go

Human/Ops should record one of three decisions in
`PRODUCT_RELEASE_GO_NO_GO.md` and `ai-status.json`:

| Decision | Meaning |
|---|---|
| `approved` | Deterministic product-E2E readiness and deterministic deployment/backup/rollback proof are accepted; release workflow may proceed subject to PR #82 policy |
| `approved-with-actions` | Deterministic readiness is accepted, but named follow-up actions such as live staging target configuration are required before broader rollout claims |
| `rejected` | Release remains blocked; Human/Ops must list the missing evidence or failed acceptance gate |

Before deciding, Human/Ops must verify:

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeStateStatus,statusCheckRollup,url
python3 scripts/e2e/check_product_release_gate.py
```

Human/Ops must also explicitly acknowledge these boundaries:

- External data proof now includes deterministic source-stub/fixture coverage,
  live-provider adapter tests, scheduled fetch worker tests, quota/rate-limit
  handling, freshness/data-quality gates, licensing gates, and product E2E mock
  proof. It still does not prove provider-specific production credential
  rotation or provider-specific production licensing approval.
- External data proof is deterministic source-stub/fixture coverage plus the
  mock-live/source operational gates named above; it is not provider-specific
  production credential proof.
- Map proof now includes deterministic local MapLibre/deck/H3 coverage, live
  tile/geocoder boundary checks, layer-toggle persistence, direct map picking,
  semantic deck pixel checks, resilience states, tooltip/evidence detail, and
  full keyboard accessibility. It still does not prove remote-staging rollout
  against actual live tile/geocoder endpoints.
- Map proof is deterministic local MapLibre/deck/H3 coverage plus the local
  map follow-up gates named above; it is not remote-staging live map rollout.
- Remote staging rollout remains conditional until target host/url/secret
  configuration is provided and verified with
  `docs/evidence/REMOTE_STAGING_PROOF_RUNBOOK.md` and
  `python3 scripts/e2e/check_remote_staging_proof.py`.
- External proof closeout remains open until
  `docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json` tasks have
  redacted runtime evidence for provider credentials/licensing/geocoder,
  remote live map endpoints, and remote staging smoke/drill proof.
- External proof handbacks must use
  `docs/evidence/EXTERNAL_PROOF_HANDBACK_TEMPLATE.json` and must pass
  `python3 scripts/e2e/check_external_proof_handback_template.py` before
  Product Validation accepts #132-#138 evidence.
- Product Validation must keep handback intake state in
  `docs/evidence/EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json` and run
  `python3 scripts/e2e/check_external_proof_handback_status_board.py` so the
  board cannot drift from the external proof queue. The status board is not
  runtime proof; it only tracks pending/submitted/needs-revision/accepted state.
- Update that board with
  `python3 scripts/e2e/update_external_proof_handback_status_board.py`, not by
  manual JSON edits. Use `--status handback_submitted` when a handback arrives,
  `--status needs_revision --next-action "<specific correction>"` when Product
  Validation rejects it, and `--status accepted --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"`
  only after the artifact checker passes.
- A completed external-proof handback must also pass
  `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"`
  before Product Validation accepts or closes the corresponding issue. This
  verifies the actual handback artifact, not only the template.
- When all #132-#138 handbacks are claimed complete, Product Validation must
  run `python3 scripts/e2e/check_external_proof_handback_bundle.py <handback-dir-or-files> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"`.
  This verifies that every external proof task has exactly one accepted
  handback and that all handbacks cite the same PR #82 `headRefOid`.
- External proof GitHub issue handoff must remain synced with queue routing.
  If PR #82 changes `headRefOid`, first run
  `python3 scripts/e2e/sync_external_proof_fleet_issues.py --release-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)" --apply`
  to refresh #132-#138 issue bodies and pickup comments from
  `docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json`.
  Run `python3 scripts/e2e/check_external_proof_issue_sync.py --require-assignees`
  before Human/Ops go/no-go so #132-#138 cannot silently lose labels,
  release authority, pickup commands, or named assignees.
- External proof live blocker state must match Product Validation intake
  status. Run `python3 scripts/e2e/check_external_proof_live_blockers.py --require-assignees`
  before closing any #132-#138 issue and before Human/Ops go/no-go so an
  unaccepted handback cannot have a closed or unrouted release-blocker issue.
- External proof fleet pickup comments must track the current release target.
  Run `python3 scripts/e2e/check_external_proof_fleet_notifications.py` after
  PR #82 changes head so #132-#138 assignees have task-specific instructions
  for the active `headRefOid`.
- Product closeout fleet pickup comments must track the current release target.
  After PR #82 changes `headRefOid`, run
  `PANTHEON_STATUS_ROOT=/home/lupin/oday-plus python3 scripts/e2e/sync_product_closeout_fleet_comment.py --release-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)" --apply`
  and then `python3 scripts/e2e/check_product_closeout_fleet_notification.py`
  so PR #82 itself names the ready/waiting lifecycle lanes for the active
  release candidate.
- Release-owner fleet dispatch status must be checked as one live surface. Run
  `python3 scripts/e2e/check_release_fleet_dispatch_status.py` after refreshing
  issue/PR comments so PR #82 checks, #132-#138 issue handoff, external
  blocker state, handback board, product closeout PR comment, and closeout
  action matrix are verified together.
- Product go/no-go external-proof wording must remain guarded. Run
  `python3 scripts/e2e/check_product_go_no_go.py` before Human/Ops go/no-go so
  `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md` cannot mark live provider,
  live map, or remote staging proof complete before #132-#138 are accepted.

## Completion Rule

Do not mark the active objective complete until:

- PR #82 is no longer draft or Human/Ops has explicitly recorded that draft
  status is intentionally retained.
- `ODP-PV-008` has a Human/Ops go/no-go.
- FE reviewer and owner closeouts listed in
  `PRODUCT_RELEASE_CLOSEOUT_MANIFEST.md` are either complete or explicitly
  superseded by Human/Ops.
- `python3 scripts/e2e/check_external_proof_issue_sync.py --require-assignees`
  passes against live GitHub issues #132-#138.
- `python3 scripts/e2e/check_external_proof_live_blockers.py --require-assignees`
  passes against live GitHub issues #132-#138 and
  `docs/evidence/EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json`.
- `python3 scripts/e2e/check_external_proof_fleet_notifications.py` passes
  against live GitHub issue comments for #132-#138.
- `python3 scripts/e2e/check_product_closeout_fleet_notification.py` passes
  against live PR #82 comments for the current `headRefOid`.
- `python3 scripts/e2e/check_release_fleet_dispatch_status.py` passes against
  live PR #82, #132-#138 issue handoff, and product closeout notification
  state.
- `python3 scripts/e2e/check_product_go_no_go.py` passes against
  `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md`.
- `python3 scripts/e2e/check_external_proof_handback_template.py` passes and
  accepted external proof artifacts cite the template fields.
- `python3 scripts/e2e/check_external_proof_handback_status_board.py` passes
  against `docs/evidence/EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json`.
- `python3 scripts/e2e/update_external_proof_handback_status_board.py --help`
  is available for guarded intake updates.
- Each accepted external-proof issue handback has passed
  `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"`.
- The complete external-proof handback set has passed
  `python3 scripts/e2e/check_external_proof_handback_bundle.py <handback-dir-or-files> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"`.
- PR #82 attached checks are still successful at the decision head.
