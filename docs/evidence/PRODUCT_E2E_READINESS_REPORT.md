# Product E2E Readiness Report

Task: ODP-PV-008  
Status: release-gate candidate  
Generated: 2026-06-29  
Current release candidate: draft release PR #82 head commit. GitHub PR #82
`headRefOid` and attached checks are the authoritative release target because
evidence-only merges intentionally create new `dev` commits.
Reference verification baseline: GitHub `ci`, `product-e2e-gate`,
`e2e-operational-evidence`, API/web image builds, and `deploy` checks passed on
2026-06-29 after frontend evidence refresh PRs #87, #88, #89, #90, and #91.
Final release verification must use the GitHub checks attached to the target release commit, because every evidence-only merge creates a newer commit hash.

## Gate Result

Product E2E readiness is **passed for the deterministic product-E2E environment**. Deployment, health, backup, restore, and data rollback evidence is linked in `docs/evidence/DEPLOYMENT_HEALTH_BACKUP_ROLLBACK_EVIDENCE.md`; live remote staging rollout remains conditional on staging host/url configuration.

The release-blocking command is:

```bash
make product-release-gate
```

It runs:

- `python3 scripts/e2e/check_product_release_gate.py`
- `scripts/e2e/run_product_e2e.sh`

The runner builds the Docker product stack, seeds deterministic API/source data, runs the live API-bound UI checks, MapLibre/deck.gl map checks, expansion, PV-006, PV-007, and product environment checks, then writes diagnostics to `.odp_data/e2e-diagnostics`.

## Required Runtime Evidence

| Evidence area | Required executable proof | Source data / fixture | Runtime evidence |
|---|---|---|---|
| Deterministic environment | `tests/e2e/product-e2e-env.spec.ts` | `tests/fixtures/source_data/external/listing_raw_snapshot.valid.json`, `poi_snapshot.valid.json`, `competitor_store_snapshot.valid.json`, `infra/docker/docker-compose.e2e.yml` | API health, retained audit evidence, source stub state, `seed-summary.json` |
| Web/API binding | `tests/e2e/e2e-api-bound-ui.spec.ts` | API-created AVM case and audit events | UI renders live backend state, not edited fixtures |
| Map | `tests/e2e/e2e-map.spec.ts`, `tests/e2e/e2e-map-live-boundary.spec.ts`, `tests/e2e/e2e-map-resilience.spec.ts`, `tests/e2e/e2e-map-tooltip-evidence.spec.ts`, `tests/e2e/e2e-map-a11y.spec.ts` | seeded HeatZone/H3 data | MapLibre canvas nonblank and semantic pixel checks, live tile/geocoder boundary config, URL layer persistence, direct picking, resilience states, tooltip/evidence detail, axe scan, keyboard-only selection/layer/drawer proof |
| Expansion | `tests/e2e/e2e-expansion-product.spec.ts` | `tests/fixtures/source_data/external/listing_raw_snapshot.valid.json`, `poi_snapshot.valid.json`, `competitor_store_snapshot.valid.json`, and deterministic API seed | HeatZone/listing/site-score product path, screenshot/trace attachment |
| Ops, intervention, price, AdLift | `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts` | generated forecast/intervention/price/adlift payloads | correlation id `corr-pv006-ops-intervention-price-ad`, audit events, screenshot attachment |
| AVM, NetPlan, Learning Hub, Audit | `tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts` | generated AVM/NetPlan/Learning payloads and model artifact bytes | correlation id `corr-pv007-avm-netplan-learning-audit`, audit evidence export, retained bundle checksum, screenshot attachment |
| Shared frontend UI contracts | `tests/contract/test_frontend_domain_type_coverage.py`, `tests/contract/test_ui_core_component_exports.py` | TypeScript domain contracts plus `packages/ui-domain` and `packages/ui` scaffolds | PR #87, PR #88, PR #89, PR #90, and PR #91 protect documented component, domain UI, and release evidence coverage |

## P0 Scenario Traceability

| Scenario | Executable test | Source / deterministic data | Screenshot / trace | Audit / evidence id |
|---|---|---|---|---|
| E2E-EXP-001 HeatZone to SiteScore opening decision | `e2e-expansion-product.spec.ts`, `e2e-map.spec.ts` | source stub fixtures + seeded HeatZone H3 features | Playwright artifact `pv005-expansion-evidence`, map canvas pixel checks | `corr-product-e2e-seed-001`, HeatZone job id in `seed-summary.json` |
| E2E-EXP-002 Listing import, geocode, dedup, candidate creation | `e2e-expansion-product.spec.ts` | `listing_raw_snapshot.valid.json`, `poi_snapshot.valid.json`, `competitor_store_snapshot.valid.json` | Playwright expansion artifact | listing candidate IDs and API-bound source stub assertions |
| E2E-OPS-001 Post-opening SiteScore realization | `e2e-ops-intervention-price-ad-product.spec.ts` | generated ForecastOps observations | `pv006-ops-price-ad-evidence` | `corr-pv006-ops-intervention-price-ad` |
| E2E-OPS-002 ForecastOps four-light alert to root cause | `e2e-ops-intervention-price-ad-product.spec.ts` | generated forecast red/green stores | `pv006-ops-price-ad-evidence` | `forecastops.forecasted.v1` |
| E2E-INT-001 Red alert to intervention maturity | `e2e-ops-intervention-price-ad-product.spec.ts` | generated intervention lifecycle payload | `pv006-ops-price-ad-evidence` | intervention approval/evaluation audit events |
| E2E-PRICE-001 PriceOps approval, execution, rollback | `e2e-ops-intervention-price-ad-product.spec.ts` | generated constrained price plan | `pv006-ops-price-ad-evidence` | `priceops.optimized.v1`, `priceops.activated.v1`, `priceops.evaluated.v1` |
| E2E-AD-001 AdLift incrementality | `e2e-ops-intervention-price-ad-product.spec.ts` | generated treatment/control campaign rows | `pv006-ops-price-ad-evidence` | `adlift.incrementality_evaluated.v1` |
| E2E-AVM-001 AVM valuation and DataRoom | `e2e-avm-netplan-learning-audit-product.spec.ts` | generated AVM valuation payload | `pv007-avm-netplan-learning-audit-evidence` | `avm.valued.v1`, `avm.dataroom_exported.v1` |
| E2E-NET-001 NetPlan solve/approval/outcome | `e2e-avm-netplan-learning-audit-product.spec.ts` | generated NetPlan scenario and solver constraints | `pv007-avm-netplan-learning-audit-evidence` | `netplan.solved.v1`, `netplan.executed.v1`, `netplan.outcome_observed.v1` |
| E2E-LEARN-001 Model validation/canary/full release | `e2e-avm-netplan-learning-audit-product.spec.ts` | generated model-ready dataset and content-addressed artifact bytes | `pv007-avm-netplan-learning-audit-evidence` | `learninghub.model_release.v1`, registry evidence aliases |
| E2E-LEARN-002 Model rollback | `e2e-avm-netplan-learning-audit-product.spec.ts` | generated rollback target `2.3.0` and candidate `2.4.0` | `pv007-avm-netplan-learning-audit-evidence` | rollback release decision and production alias restored to `2.3.0` |
| E2E-AUDIT-001 Decision audit evidence export | `e2e-avm-netplan-learning-audit-product.spec.ts`, `product-e2e-env.spec.ts` | decision cards from AVM/NetPlan/Learning API events | retained evidence detail assertion | audit export bundle checksum, `corr-product-e2e-seed-001`, `corr-pv007-avm-netplan-learning-audit` |
| E2E-SEC-001 Role permissions and data isolation | `make ci` security job | security test fixtures | CI report | `tests/security`, dependency audit high/critical gate |

## Release Blockers Enforced By CI

The `product-e2e-gate` CI job fails if:

- the MapLibre/deck.gl map spec is removed from `scripts/e2e/run_product_e2e.sh`;
- map follow-up specs for live boundary, resilience, tooltip/evidence, or a11y are removed from the product-grade follow-up evidence packet;
- the deterministic product environment or source stub fixture is missing;
- PV-005/PV-006/PV-007 product specs or evidence documents are missing;
- this readiness report omits the required P0 scenario or audit correlation IDs;
- the full Docker-backed product E2E suite fails.

## Residual Release Risk

- Remote staging rollout is still a deployment environment configuration item because staging host/url/secret owner variables are not configured. The API now exposes `/platform/version`, and `scripts/e2e/check_remote_staging_proof.py` is the required smoke/version checker once staging exists.
- Model alias rollback is covered by PV-007; policy/image rollback is documented as redeploying immutable previous image tags.
- Formal Human/Ops sign-off is tracked in `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md`.
- Moderate dependency audit findings remain below the existing high/critical release-blocking threshold and are tracked by the security gate output.
