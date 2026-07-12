# ODP-FLOW-010 Verification Evidence

Commands run from `/tmp/pantheon-worker-worktrees/oday-plus/odp-flow-010` on
2026-07-12.

## Passed

```bash
python3 -m pytest tests/contract/test_operator_api.py
```

Result: `3 passed, 1 warning`.

After adding durable persistence coverage:

```bash
python3 -m pytest tests/contract/test_operator_api.py
```

Result: `4 passed, 1 warning`.

```bash
npm run typecheck --workspace=@oday-plus/web
```

Result: passed after installing dependencies with `npm ci`.

```bash
python3 -m pytest tests/contract/test_platform_api.py tests/contract/test_operator_api.py
```

Result: `9 passed, 1 warning`.

```bash
ODP_OPERATOR_PRODUCT_GATE=1 npx playwright test tests/e2e/e2e-operator-console.spec.ts --project=chromium
```

Result: `4 passed`.

## Warnings Observed

- FastAPI `TestClient` emitted the existing Starlette/httpx deprecation warning.
- Playwright/Next emitted existing autoprefixer warnings for `start`/`end`
  alignment values in operator CSS modules unrelated to this task's selectors.
- `npm ci` reported two moderate dependency audit findings; no package versions
  were changed by this task.
