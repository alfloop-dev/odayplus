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

## Final Closeout Refresh

PR #254 merged `task/ODP-FLOW-005` into `dev` at
`2026-07-12T16:00:21Z` with merge commit `95f4c78f`. The owner then
fast-forwarded the task worktree to that dev tip and reran the focused gates:

```bash
uv run ruff check modules/priceops apps/api/app/routes/priceops.py tests/integration/test_priceops_constraints.py tests/integration/test_priceops_api.py
uv run pytest tests/integration/test_priceops_constraints.py tests/integration/test_priceops_api.py -q
npm run typecheck --workspace=@oday-plus/web
npx playwright test tests/e2e/e2e-intervention-price-ad.spec.ts --project=chromium
```

Results: ruff clean; 18 focused PriceOps tests passed; web TypeScript check
passed; Playwright smoke passed 4 tests.

## Covered Behaviors

- Current-vs-candidate comparison exposes demand/revenue/gross-margin bands.
- Rollback plan is created before activation and exposed in comparison/API.
- Infeasible hard-constraint plans cannot be approved by service or API.
- Full lifecycle still records audit-bearing transitions through evaluation.
- UI smoke now asserts the PriceOps closed loop panel includes apply and outcome.
