# ODP-PV-006 Operations / Intervention / PriceOps / AdLift Product E2E Evidence

## Scope

This evidence covers the product-grade E2E scenario for:

- ForecastOps four-light forecast, red alert, and intervention handoff
- InterventionOps approval, execution, observation maturity, effect evaluation, and label writeback
- PriceOps constrained optimization, approval, pre-execution rollback plan, activation, observation, and rollback-triggering evaluation
- AdLift incrementality with matched controls, passing pre-trend, causal-claim guard, and recommendation
- Correlated audit trail across the product flow
- User-visible OpsBoard surfaces for forecast handoff, intervention conflict guard, PriceOps hard-constraint guard, rollback evidence, and AdLift causal-claim guard

## Executable Coverage

Primary spec:

- `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts`

Product runner inclusion:

- `scripts/e2e/run_product_e2e.sh` now includes the PV-006 spec in the Docker compose product E2E suite.

Backend support added for executable PriceOps lifecycle validation:

- `apps/api/app/routes/priceops.py`
- `apps/api/oday_api/main.py`
- `shared/infrastructure/persistence/factory.py`
- `shared/infrastructure/persistence/repositories.py`
- `shared/infrastructure/persistence/__init__.py`

## Verification

Commands run in `/tmp/pantheon-worker-worktrees/oday-plus/odp-pv-006`:

```bash
uv run ruff check apps/api/app/routes/priceops.py apps/api/oday_api/main.py shared/infrastructure/persistence/factory.py shared/infrastructure/persistence/repositories.py shared/infrastructure/persistence/__init__.py
python3 -m py_compile apps/api/app/routes/priceops.py apps/api/oday_api/main.py shared/infrastructure/persistence/factory.py shared/infrastructure/persistence/repositories.py shared/infrastructure/persistence/__init__.py
npm ci
npm run typecheck --workspace=@oday-plus/web
npx playwright test tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts --project=chromium
npx playwright test tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts tests/e2e/e2e-api-bound-ui.spec.ts tests/e2e/e2e-map.spec.ts --project=chromium
npx playwright test tests/e2e/e2e-map.spec.ts --project=chromium
scripts/e2e/run_product_e2e.sh
```

Result:

- Focused backend ruff: passed
- Focused Python compile: passed
- Web typecheck: passed
- PV-006 Playwright spec: 1 passed
- Production web build: passed
- PV-006 + API-bound + map subset: PV-006 and API-bound passed; first parallel map run had one transient timeout waiting for the legend text while the second map test passed
- Focused map rerun: 2 passed
- Full Docker product E2E runner: 7 passed, including PV-006, API-bound UI, map, and product environment checks

## Correlation And Audit

The E2E scenario uses correlation id:

- `corr-pv006-ops-intervention-price-ad`

The spec verifies audit events for:

- `forecastops.forecasted.v1`
- `priceops.optimized.v1`
- `priceops.activated.v1`
- `priceops.evaluated.v1`
- `adlift.incrementality_evaluated.v1`

The InterventionOps workflow records audit events through the intervention workflow engine and also verifies a mature label writeback in the API response.
