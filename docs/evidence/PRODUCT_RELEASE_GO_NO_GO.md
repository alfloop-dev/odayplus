# Product Release Go/No-Go

Task: ODP-PV-008  
Decision status: conditional go for deterministic product E2E and E2E backup/restore proof; remote staging rollout remains conditional on environment configuration  
Decision date: 2026-06-29  
Decision owner: Human/Ops  
Prepared by: Codex2 manual implementation by Codex
Reference verification evidence: `dev@8834cc819051c2ebda8f531f467a67b07cc547e4` passed GitHub `CI` and `Deploy Dev`; evidence refresh PR #80 passed GitHub `CI` and `product-e2e-gate` on 2026-06-29. Final Human/Ops sign-off must verify the GitHub checks attached to the target release commit.

## Decision

| Gate | Status | Evidence |
|---|---|---|
| Code/security CI | passed for reference baseline; must pass on target release commit | `make ci` in GitHub `CI`, security high/critical dependency gate |
| Product E2E static release gate | passed when `python3 scripts/e2e/check_product_release_gate.py` passes | checks required specs, evidence docs, deterministic env, source stub, map coverage |
| Product Docker E2E | passed when `scripts/e2e/run_product_e2e.sh` passes | Docker API/web/worker/source-stub stack, 9 Playwright tests after PV-014 |
| Map gate | passed | `tests/e2e/e2e-map.spec.ts` nonblank MapLibre canvas and deck overlay assertions |
| External/source stub gate | passed for deterministic E2E | `tests/fixtures/source_data/external/*.valid.json`, source stub readiness in `product-e2e-env.spec.ts` |
| Audit evidence gate | passed for deterministic E2E | retained bundle checksum and audit correlations in product specs |
| Deployment/backup/rollback gate | passed for deterministic E2E reference baseline; must pass on target release commit | `docs/evidence/DEPLOYMENT_HEALTH_BACKUP_ROLLBACK_EVIDENCE.md`, `python3 scripts/e2e/verify_deployment_health_backup_rollback.py`, GitHub `Deploy Dev` |

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
- `tests/e2e/e2e-map.spec.ts` fails its nonblank canvas/deck overlay checks.
- `tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts` fails to export retained audit evidence or loses `corr-pv007-avm-netplan-learning-audit`.
- `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts` loses `corr-pv006-ops-intervention-price-ad`.
- Any P0 scenario in `tests/e2e/test_acceptance_coverage.py` lacks executable automation, deterministic data, or audit evidence.
- remote staging host/url variables are required and still unset for live staging rollout.

## Human/Ops Checklist

| Check | Required review action | Status |
|---|---|---|
| Product E2E report reviewed | Confirm every P0 row in `PRODUCT_E2E_READINESS_REPORT.md` has a test, data source, screenshot/trace, and audit/evidence id | pending-human |
| CI release gate reviewed | Confirm GitHub `product-e2e-gate` ran `make product-release-gate` | pending-human |
| Deterministic environment accepted | Confirm deterministic source stub is acceptable for PV readiness | pending-human |
| Remote staging limitation accepted | Confirm live staging rollout remains conditional on staging host/url configuration | pending-human |
| Final decision recorded | Human/Ops writes approved / approved-with-actions / rejected | pending-human |

## Current Recommendation

Approve PV product-E2E readiness and deterministic E2E deployment/backup/restore proof with an explicit action: configure a real staging target before claiming live remote staging rollout.
