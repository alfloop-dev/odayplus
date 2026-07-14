# ODP-OC-R4-004 — Verification

All commands run from the task worktree on branch `task/ODP-OC-R4-004`.

## Task-specified verification

```bash
uv run pytest tests/contract -k growth
# 12 passed, 131 deselected

npx playwright test tests/e2e/operator-growth.spec.ts
# 11 passed (fresh servers on OPSBOARD_PORT=3199 / ODP_API_PORT=8199, CI=1)
```

> Note: the shared `next dev` on the default port 3100 belongs to another
> worktree; run the e2e with `OPSBOARD_PORT=3199 ODP_API_PORT=8199 CI=1` so
> Playwright boots fresh servers against this branch's code instead of reusing
> the stale one.

## Additional checks

```bash
npm run typecheck --workspace=@oday-plus/web     # tsc --noEmit — clean
npm run build --workspace=@oday-plus/web         # next build — success
uv run pytest tests/contract                     # 143 passed (no regression)
uv run ruff check modules/opsboard/application/growth.py \
  apps/api/app/routes/operator_modules/growth.py  # All checks passed
```

## Evidence artifacts
- `api-proof.json` — live API responses for all four acceptance criteria.
- `growth-builder.png` — five-step builder at the conflict step with live
  server checks, over the three entry cards + full workspace.
