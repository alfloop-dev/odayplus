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
- `docs/evidence/EXTERNAL_PROOF_FLEET_PICKUP_BOARD.md`

## Required Preflight

Run these before any closeout action:

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeStateStatus,statusCheckRollup,url
python3 scripts/e2e/check_product_release_gate.py
python3 scripts/e2e/check_product_closeout_queue.py --report
python3 -m pytest tests/e2e/test_frontend_execution_matrix_coverage.py
```

For Human/Ops go/no-go, also verify the external proof issue routing remains
live and assigned:

```bash
python3 scripts/e2e/check_external_proof_issue_sync.py --require-assignees
python3 scripts/e2e/check_product_go_no_go.py
python3 scripts/e2e/check_external_proof_handback_status_board.py
python3 scripts/e2e/update_external_proof_handback_status_board.py --help
python3 scripts/e2e/check_external_proof_live_blockers.py --require-assignees
python3 scripts/e2e/check_external_proof_handback_bundle.py <handback-dir-or-files> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"
```

## Pickup Table

| Task | Queue status | Actor | Action type | Blocking type | Required command | Evidence refs |
|---|---|---|---|---|---|---|
| `ODP-PV-008` | `review` | Human/Ops | `go_no_go` | `human_signoff` | `gh pr view 82 --json headRefOid,isDraft,state,mergeStateStatus,statusCheckRollup,url` and `python3 scripts/e2e/check_product_release_gate.py` | `docs/evidence/PRODUCT_E2E_READINESS_REPORT.md`, `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md`, `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_MANIFEST.md`, `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PLAYBOOK.md` |
| `ODP-FE-XCUT-001` | `in_progress` | Claude2 | `owner_handoff` | `owner_status_closeout` | `AI_NAME=Claude2 python3 scripts/ai_status.py handoff ODP-FE-XCUT-001 Codex "<handoff message>"` | `docs/evidence/FRONTEND_FLEET_COMPLETION_AUDIT.md`, `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_MANIFEST.md`, `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PLAYBOOK.md` |
| `ODP-FE-XCUT-001` | `waiting_for_review_after_handoff` | Codex | `reviewer_approve_or_reopen` | `reviewer_status_closeout` | `AI_NAME=Codex REVIEW_NOTES_ZH="<review summary>" python3 scripts/ai_status.py approve ODP-FE-XCUT-001 "<approval message>"` or `AI_NAME=Codex python3 scripts/ai_status.py reopen ODP-FE-XCUT-001 "<missing evidence>"` | `docs/evidence/FRONTEND_FLEET_COMPLETION_AUDIT.md`, `tests/e2e/test_frontend_execution_matrix_coverage.py`, `tests/contract/test_frontend_domain_type_coverage.py`, `tests/contract/test_ui_core_component_exports.py` |
| `ODP-FE-R0-001` | `review_approved` | Claude | `owner_done` | `owner_status_closeout` | `AI_NAME=Claude python3 scripts/ai_status.py done ODP-FE-R0-001 "<finalization message>"` | `tests/e2e/opsboard-shell.spec.ts`, `tests/e2e/e2e-api-bound-ui.spec.ts`, `docs/evidence/FRONTEND_FLEET_COMPLETION_AUDIT.md` |
| `ODP-FE-XCUT-UI-001` | `review_approved` | Claude2 | `owner_done` | `owner_status_closeout` | `AI_NAME=Claude2 python3 scripts/ai_status.py done ODP-FE-XCUT-UI-001 "<finalization message>"` | `packages/ui`, `tests/contract/test_ui_core_component_exports.py`, `docs/evidence/FRONTEND_FLEET_COMPLETION_AUDIT.md` |
| `ODP-FE-EXP-001` | `review` | Claude | `reviewer_approve_or_reopen` | `reviewer_status_closeout` | `AI_NAME=Claude REVIEW_NOTES_ZH="<review summary>" python3 scripts/ai_status.py approve ODP-FE-EXP-001 "<approval message>"` or `AI_NAME=Claude python3 scripts/ai_status.py reopen ODP-FE-EXP-001 "<missing evidence>"` | `tests/e2e/e2e-map.spec.ts`, `tests/e2e/e2e-expansion-product.spec.ts`, `docs/design/ODAY_PLUS_EXPANSION_WORKFLOW_BLUEPRINT.md`, `docs/design/ODAY_PLUS_HEATZONE_MAP_VISUAL_SPEC.md`, `docs/design/ODAY_PLUS_SITESCORE_REPORT_UI_SPEC.md` |
| `ODP-FE-OPS-001` | `review_approved` | Claude2 | `owner_done` | `owner_status_closeout` | `AI_NAME=Claude2 python3 scripts/ai_status.py done ODP-FE-OPS-001 "<finalization message>"` | `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts`, `docs/design/ODAY_PLUS_OPERATIONS_ALERT_UI_SPEC.md`, `docs/design/ODAY_PLUS_INTERVENTION_WORKFLOW_UI_SPEC.md` |
| `ODP-FE-PRICE-001` | `review` | Claude2 | `reviewer_approve_or_reopen` | `reviewer_status_closeout` | `AI_NAME=Claude2 REVIEW_NOTES_ZH="<review summary>" python3 scripts/ai_status.py approve ODP-FE-PRICE-001 "<approval message>"` or `AI_NAME=Claude2 python3 scripts/ai_status.py reopen ODP-FE-PRICE-001 "<missing evidence>"` | `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts`, `docs/design/ODAY_PLUS_PRICING_AND_ADLIFT_UI_SPEC.md` |
| `ODP-FE-ASSET-001` | `review` | Codex2 | `reviewer_approve_or_reopen` | `reviewer_status_closeout` | `AI_NAME=Codex2 REVIEW_NOTES_ZH="<review summary>" python3 scripts/ai_status.py approve ODP-FE-ASSET-001 "<approval message>"` or `AI_NAME=Codex2 python3 scripts/ai_status.py reopen ODP-FE-ASSET-001 "<missing evidence>"` | `apps/web/features/avm/AvmWorkspace.tsx`, `tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts`, `tests/e2e/e2e-avm-netplan.spec.ts`, `docs/design/ODAY_PLUS_ASSET_AND_NETPLAN_UI_SPEC.md` |
| `ODP-FE-LEARN-001` | `review` | Claude2 | `reviewer_approve_or_reopen` | `reviewer_status_closeout` | `AI_NAME=Claude2 REVIEW_NOTES_ZH="<review summary>" python3 scripts/ai_status.py approve ODP-FE-LEARN-001 "<approval message>"` or `AI_NAME=Claude2 python3 scripts/ai_status.py reopen ODP-FE-LEARN-001 "<missing evidence>"` | `tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts`, `docs/design/ODAY_PLUS_LEARNING_HUB_UI_SPEC.md`, `docs/design/ODAY_PLUS_AUDIT_EVIDENCE_UI_SPEC.md` |
| `ODP-FE-XCUT-DOMAIN-001` | `review_approved` | Claude | `owner_done` | `owner_status_closeout` | `AI_NAME=Claude python3 scripts/ai_status.py done ODP-FE-XCUT-DOMAIN-001 "<finalization message>"` | `packages/ui-domain`, `tests/contract/test_frontend_domain_type_coverage.py`, `docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md` |
| `ODP-FE-XCUT-TYPES-001` | `review` | Claude2 | `reviewer_approve_or_reopen` | `reviewer_status_closeout` | `AI_NAME=Claude2 REVIEW_NOTES_ZH="<review summary>" python3 scripts/ai_status.py approve ODP-FE-XCUT-TYPES-001 "<approval message>"` or `AI_NAME=Claude2 python3 scripts/ai_status.py reopen ODP-FE-XCUT-TYPES-001 "<missing evidence>"` | `packages/domain-types/src/frontend-contracts.ts`, `tests/contract/test_frontend_domain_type_coverage.py`, `docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md` |

## Actor Lanes

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
- external proof issues #132-#138 have accepted handbacks that pass
  `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"`;
- the full external proof handback set passes
  `python3 scripts/e2e/check_external_proof_handback_bundle.py <handback-dir-or-files> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"`;
- `python3 scripts/e2e/check_external_proof_issue_sync.py --require-assignees`
  still passes.
- `python3 scripts/e2e/check_external_proof_live_blockers.py --require-assignees`
  still passes so unaccepted #132-#138 tasks cannot be silently closed or lose
  release-blocker routing.
- `python3 scripts/e2e/check_product_go_no_go.py` still passes so
  `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md` cannot mark live provider,
  live map, or remote staging proof complete before #132-#138 are accepted.
- `python3 scripts/e2e/check_external_proof_handback_status_board.py` still
  passes so Product Validation handback intake state remains synchronized with
  `docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json`.
- Product Validation uses
  `python3 scripts/e2e/update_external_proof_handback_status_board.py` rather
  than manual JSON edits when a #132-#138 handback is submitted, rejected for
  revision, or accepted.
