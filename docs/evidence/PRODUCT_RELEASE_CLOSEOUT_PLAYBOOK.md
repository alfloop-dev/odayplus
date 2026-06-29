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
| `ODP-FE-OPS-001` | Codex2 | `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts`, Operations and Intervention specs |
| `ODP-FE-PRICE-001` | Claude2 | `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts`, Pricing and AdLift spec |
| `ODP-FE-ASSET-001` | Codex2 | `tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts`, AVM/NetPlan specs |
| `ODP-FE-LEARN-001` | Claude2 | `tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts`, Learning Hub and Audit Evidence specs |
| `ODP-FE-XCUT-DOMAIN-001` | Codex2 | `packages/ui-domain`, `tests/contract/test_frontend_domain_type_coverage.py`, component contracts |
| `ODP-FE-XCUT-TYPES-001` | Claude2 | `packages/domain-types/src/frontend-contracts.ts`, `tests/contract/test_frontend_domain_type_coverage.py`, component contracts |
| `ODP-FE-XCUT-001` | Codex | Only after Claude2 owner hands off parent lane to review; inspect PR #87/#88/#89/#90/#91/#92/#93 evidence and cross-cutting acceptance |

## Owner Finalization Commands

Owners can finalize only after their task is `review_approved`. Use a worktree
that satisfies delivery gates; do not finalize from a thin or stale `main`
checkout.

```bash
git fetch origin dev
gh pr view 82 --json headRefOid,isDraft,state,mergeStateStatus,statusCheckRollup,url
AI_NAME=<Owner> python3 scripts/ai_status.py done <task-id> "<finalization message>"
```

Owner closeout currently applies to:

| Task | Owner | Current closeout need |
|---|---|---|
| `ODP-FE-R0-001` | Claude | Finalize after existing review approval |
| `ODP-FE-XCUT-UI-001` | Claude2 | Finalize after existing review approval |
| `ODP-FE-XCUT-001` | Claude2 | First hand off parent lane to Codex for review after accepting child-lane evidence |

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

- External data proof is deterministic source-stub/fixture coverage, not live
  provider credential/OAuth, scheduled fetch, quota/rate-limit, freshness, or
  production licensing proof.
- Map proof is deterministic local MapLibre/deck/H3 coverage, not live tile,
  geocoder, full keyboard accessibility, layer-toggle, or direct map-picking
  rollout proof.
- Remote staging rollout remains conditional until target host/url/secret
  configuration is provided and verified.

## Completion Rule

Do not mark the active objective complete until:

- PR #82 is no longer draft or Human/Ops has explicitly recorded that draft
  status is intentionally retained.
- `ODP-PV-008` has a Human/Ops go/no-go.
- FE reviewer and owner closeouts listed in
  `PRODUCT_RELEASE_CLOSEOUT_MANIFEST.md` are either complete or explicitly
  superseded by Human/Ops.
- PR #82 attached checks are still successful at the decision head.
