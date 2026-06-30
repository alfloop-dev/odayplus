# Product Release Go/No-Go

Task: ODP-PV-008  
Decision status: conditional go for deterministic product E2E and E2E backup/restore proof; remote staging rollout remains conditional on environment configuration  
Decision date: 2026-06-29  
Decision owner: Human/Ops  
Prepared by: Codex2 manual implementation by Codex
Current release candidate: draft release PR #82 head commit. GitHub PR #82
`headRefOid` and attached checks are the authoritative release target because
evidence-only merges intentionally create new `dev` commits. Reference
verification evidence: GitHub `ci`, `product-e2e-gate`,
`e2e-operational-evidence`, API/web image builds, and `deploy` checks passed on
2026-06-29 after frontend evidence refresh PRs #87, #88, #89, #90, #91, and
fleet handback evidence PR #127.
Final Human/Ops sign-off must verify the GitHub checks attached to the target
release commit before promoting the draft release.

## Decision

| Gate | Status | Evidence |
|---|---|---|
| Code/security CI | passed for reference baseline; must pass on target release commit | `make ci` in GitHub `CI`, security high/critical dependency gate |
| Product E2E static release gate | passed when `python3 scripts/e2e/check_product_release_gate.py` passes | checks required specs, evidence docs, deterministic env, source/external-data gates, map coverage, and closeout queue |
| Product Docker E2E | passed when `scripts/e2e/run_product_e2e.sh` passes | Docker API/web/worker/source-stub stack, 9 Playwright tests after PV-014 |
| Map gate | passed | `tests/e2e/e2e-map.spec.ts`, `e2e-map-live-boundary.spec.ts`, `e2e-map-resilience.spec.ts`, `e2e-map-tooltip-evidence.spec.ts`, and `e2e-map-a11y.spec.ts` |
| External/source gate | passed for deterministic and mock-live E2E | `tests/fixtures/source_data/external/*.valid.json`, source stub readiness, live adapter tests, scheduled fetch tests, quota/freshness/licensing gates, and external source product E2E |
| Audit evidence gate | passed for deterministic E2E | retained bundle checksum and audit correlations in product specs |
| Deployment/backup/rollback gate | passed for deterministic E2E reference baseline; must pass on target release commit | `docs/evidence/DEPLOYMENT_HEALTH_BACKUP_ROLLBACK_EVIDENCE.md`, `python3 scripts/e2e/verify_deployment_health_backup_rollback.py`, GitHub `Deploy Dev` |
| Shared frontend contract gate | passed on current draft release PR #82 head | PR #87 domain type contracts, PR #88 `packages/ui-domain`, PR #89 `packages/ui`, PR #90 evidence refresh, PR #91 release-candidate evidence refresh, PR #127 fleet handback evidence refresh, and contract tests under `tests/contract/` |

## Go Criteria

- `make product-release-gate` passes on the release commit.
- GitHub `CI` workflow passes both jobs: `ci` and `product-e2e-gate`.
- `docs/evidence/PRODUCT_E2E_READINESS_REPORT.md` links every P0 product scenario to executable tests, source data, screenshot/trace evidence, and audit IDs.
- No high/critical dependency or security finding is open.
- Human/Ops accepts the residual risk that this is deterministic product-E2E readiness, not staging/production deployment readiness.

## No-Go Criteria

Release is blocked if any of these are true:

- `scripts/e2e/run_product_e2e.sh` omits map, API-bound UI, deterministic environment, PV-006, or PV-007 specs.
- the external source fixtures under `tests/fixtures/source_data/external/*.valid.json` or the Docker source-stub stack are missing.
- map E2E/a11y specs fail canvas/deck, live boundary, resilience, tooltip/evidence, direct picking, layer persistence, or axe/keyboard checks.
- `tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts` fails to export retained audit evidence or loses `corr-pv007-avm-netplan-learning-audit`.
- `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts` loses `corr-pv006-ops-intervention-price-ad`.
- Any P0 scenario in `tests/e2e/test_acceptance_coverage.py` lacks executable automation, deterministic data, or audit evidence.
- remote staging host/url/secret owner variables are required and still unset for live staging rollout, or `/platform/version.release_sha` does not match PR #82 `headRefOid`.

## Human/Ops Checklist

| Check | Required review action | Status |
|---|---|---|
| Product E2E report reviewed | Confirm every P0 row in `PRODUCT_E2E_READINESS_REPORT.md` has a test, data source, screenshot/trace, and audit/evidence id | pending-human |
| CI release gate reviewed | Confirm GitHub `product-e2e-gate` ran `make product-release-gate` | pending-human |
| Deterministic environment accepted | Confirm deterministic source stub is acceptable for PV readiness | pending-human |
| Remote staging limitation accepted | Confirm live staging rollout remains conditional on staging host/url/secret owner configuration and `check_remote_staging_proof.py` evidence | pending-human |
| External proof queue reviewed | Confirm `PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json` remains open for provider credential/license/geocoder, remote live map endpoint, and remote staging proof | pending-human |
| External proof handback format reviewed | Confirm fleets use `EXTERNAL_PROOF_HANDBACK_TEMPLATE.json` for redacted runtime proof artifacts and that `check_external_proof_handback_template.py` passes | pending-human |
| External proof handback artifacts validated | For each submitted #132-#138 handback, run `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` before accepting or closing the issue | pending-human |
| External proof handback bundle validated | After all #132-#138 handbacks are submitted, run `python3 scripts/e2e/check_external_proof_handback_bundle.py <handback-dir-or-files> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` to prove the complete set is present, unique, accepted, and tied to the same release head | pending-human |
| External proof issue sync reviewed | Run `python3 scripts/e2e/check_external_proof_issue_sync.py --require-assignees` and confirm #132-#138 still have fleet routing, release authority, labels, and named assignees | pending-human |
| Final decision recorded | Human/Ops writes approved / approved-with-actions / rejected | pending-human |

## Current Recommendation

Approve PV product-E2E readiness and deterministic E2E deployment/backup/restore proof with an explicit action: configure a real staging target, deploy with `ODAY_RELEASE_SHA`, and pass `scripts/e2e/check_remote_staging_proof.py` before claiming live remote staging rollout.
