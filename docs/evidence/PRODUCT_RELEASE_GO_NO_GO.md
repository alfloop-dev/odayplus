# Product Release Go/No-Go

Task: ODP-PV-008  
Decision status: conditional go for deterministic product E2E and E2E backup/restore proof; remote staging rollout remains conditional on environment configuration  
Decision date: 2026-06-29  
Decision owner: Human/Ops  
Prepared by: Codex2 manual implementation by Codex

## Release Candidate Context & Canonical Governance

- **Historical Context (2026-06-29)**: The initial deterministic product E2E readiness baseline was evaluated against draft release PR #82 head commit. That reference evaluation verified GitHub `ci`, `product-e2e-gate`, `e2e-operational-evidence`, API/web image builds, and `deploy` checks after frontend evidence refresh PRs #87, #88, #89, #90, #91, and fleet handback evidence PR #127. It established deterministic product E2E readiness but did not constitute live staging or production deployment authorization.
- **Current Canonical Release Gate (2026-10-02 / 2026-10-03)**: Active candidate tracking, staged admission, and production gates are governed exclusively by the canonical release gate registry [`docs/evidence/gates/RELEASE_GATE_REGISTRY.json`](gates/RELEASE_GATE_REGISTRY.json) and release manifest [`docs/evidence/gates/RELEASE_MANIFEST.json`](gates/RELEASE_MANIFEST.json).
- **Current Gate Posture & Historical Manifest State**: The last recorded historical manifest state in the repository was bound to dev candidate `6140d0ef` (Runtime Release build `37006345960`, Gate 0 passed, Gate 4 dev deviation `HUMANOPS-DEV-MIGRATION-20261002T130206Z`). As established in `EXECUTION.md`, prior build 6140 approval and leases are spent; the candidate-rebind lane remains blocked and a new exact candidate, build verification, and deployment authorization are pending. Staged admission and production gates (Gates 2, 3, 5, 6) remain **BLOCKED** pending source repairs, exact-candidate build, live deployment, runtime NFR observation receipts, and formal stakeholder sign-offs.
- Final Human/Ops sign-off must verify the GitHub checks and receipts attached to the exact target release commit before promoting the release.

## Decision

| Gate | Status | Evidence |
|---|---|---|
| Code/security CI | passed for reference baseline; must pass on target release commit | `make ci` in GitHub `CI`, security high/critical dependency gate |
| Product E2E static release gate | passed when `python3 delivery_toolchain/e2e/check_product_release_gate.py` passes | checks required specs, evidence docs, deterministic env, source/external-data gates, map coverage, and closeout queue |
| Product Docker E2E | passed when `delivery_toolchain/e2e/run_product_e2e.sh` passes | Docker API/web/worker/source-stub stack, 9 Playwright tests after PV-014 |
| Map gate | passed | `tests/e2e/e2e-map.spec.ts`, `e2e-map-live-boundary.spec.ts`, `e2e-map-resilience.spec.ts`, `e2e-map-tooltip-evidence.spec.ts`, and `e2e-map-a11y.spec.ts` |
| External/source gate | passed for deterministic and mock-live E2E | `tests/fixtures/source_data/external/*.valid.json`, source stub readiness, live adapter tests, scheduled fetch tests, quota/freshness/licensing gates, and external source product E2E |
| Audit evidence gate | passed for deterministic E2E | retained bundle checksum and audit correlations in product specs |
| Deployment/backup/rollback gate | passed for deterministic E2E reference baseline; must pass on target release commit | `docs/evidence/DEPLOYMENT_HEALTH_BACKUP_ROLLBACK_EVIDENCE.md`, `python3 delivery_toolchain/e2e/verify_deployment_health_backup_rollback.py`, GitHub `Deploy Dev` |
| Shared frontend contract gate | passed on historical draft release PR #82 head baseline; must pass on target release commit | PR #87 domain type contracts, PR #88 `packages/ui-domain`, PR #89 `packages/ui`, PR #90 evidence refresh, PR #91 release-candidate evidence refresh, PR #127 fleet handback evidence refresh, and contract tests under `tests/contract/` |

## Go Criteria

- `make product-release-gate` passes on the release commit.
- GitHub `CI` workflow passes both jobs: `ci` and `product-e2e-gate`.
- `docs/evidence/PRODUCT_E2E_READINESS_REPORT.md` links every P0 product scenario to executable tests, source data, screenshot/trace evidence, and audit IDs.
- No high/critical dependency or security finding is open.
- Human/Ops accepts the residual risk that this is deterministic product-E2E readiness, not staging/production deployment readiness.

## No-Go Criteria

Release is blocked if any of these are true:

- `delivery_toolchain/e2e/run_product_e2e.sh` omits map, API-bound UI, deterministic environment, PV-006, or PV-007 specs.
- the external source fixtures under `tests/fixtures/source_data/external/*.valid.json` or the Docker source-stub stack are missing.
- map E2E/a11y specs fail canvas/deck, live boundary, resilience, tooltip/evidence, direct picking, layer persistence, or axe/keyboard checks.
- `tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts` fails to export retained audit evidence or loses `corr-pv007-avm-netplan-learning-audit`.
- `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts` loses `corr-pv006-ops-intervention-price-ad`.
- Any P0 scenario in `tests/e2e/test_acceptance_coverage.py` lacks executable automation, deterministic data, or audit evidence.
- remote staging host/url/secret owner variables are required and still unset for live staging rollout, or `/platform/version.release_sha` does not match the candidate SHA.

## Human/Ops Checklist

| Check | Required review action | Status |
|---|---|---|
| Product E2E report reviewed | Confirm every P0 row in `PRODUCT_E2E_READINESS_REPORT.md` has a test, data source, screenshot/trace, and audit/evidence id | pending-human |
| CI release gate reviewed | Confirm GitHub `product-e2e-gate` ran `make product-release-gate` | pending-human |
| Deterministic environment accepted | Confirm deterministic source stub is acceptable for PV readiness | pending-human |
| Remote staging & rollout governance | Confirm ephemeral staging rehearsal and production blue-green criteria in `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md` | pending-human |
| External provider activation policy | Confirm third-party data providers remain runtime disabled with default-deny egress until formal provider activation receipts are approved | pending-human |
| Final decision recorded | Human/Ops writes approved / approved-with-actions / rejected | pending-human |

## External Provider & Staging Rollout Controls

External source activation and staging rehearsals are governed by `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`. All 16 third-party data sources remain disabled until per-source human authorization receipts are recorded.

## Historical Recommendation & Current Canonical Status

- **Historical Recommendation (2026-06-29, PR #82)**: Recommend Approve for PV product-E2E readiness and deterministic E2E deployment/backup/restore proof for the initial reference baseline with explicit action to configure staging/production environments, deploy with immutable release manifest digests, and follow `docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`. Formal approval remains pending Human/Ops sign-off.
- **Current Canonical Status (2026-10-02 / 2026-10-03)**: Canonical release gates and candidate manifests are managed exclusively in [`docs/evidence/gates/RELEASE_GATE_REGISTRY.json`](gates/RELEASE_GATE_REGISTRY.json) and [`docs/evidence/gates/RELEASE_MANIFEST.json`](gates/RELEASE_MANIFEST.json). Staging and production gates remain **BLOCKED** pending source repairs, exact-candidate build, live deployment, live NFR observation receipts, and completion of the Human/Ops checklist above. No new GO decision or release tuple is fabricated here.
