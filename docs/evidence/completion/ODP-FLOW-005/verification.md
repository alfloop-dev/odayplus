# ODP-FLOW-005 Verification Evidence

Task: Complete PriceOps simulation approval and rollback flow
Owner: Codex

## Commands

```bash
pytest tests/integration/test_priceops_constraints.py tests/integration/test_priceops_api.py
```

Result: passed, 18 tests.

Warnings: Starlette deprecation warnings for current TestClient/httpx and
`HTTP_422_UNPROCESSABLE_ENTITY`; no test failures.

```bash
npm ci
npm run typecheck --workspace=@oday-plus/web
```

Result: dependency install completed from lockfile; web TypeScript check passed.

`npm ci` reported 2 moderate audit vulnerabilities in existing dependencies.
No package files were changed.

```bash
npx playwright test tests/e2e/e2e-intervention-price-ad.spec.ts --project=chromium
```

Result: passed, 4 tests.

## Covered Behaviors

- Current-vs-candidate comparison exposes demand/revenue/gross-margin bands.
- Rollback plan is created before activation and exposed in comparison/API.
- Infeasible hard-constraint plans cannot be approved by service or API.
- Full lifecycle still records audit-bearing transitions through evaluation.
- UI smoke now asserts the PriceOps closed loop panel includes apply and outcome.
