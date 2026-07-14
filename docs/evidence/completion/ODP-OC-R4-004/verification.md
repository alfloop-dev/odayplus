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

## Canonical source + visual parity

```bash
sha256sum "docs_archive/00_source_zips/operator_console/r4-20260707-package-6/Oday Plus 營運管理後台 (6).zip"
# db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76  (matches manifest)
sha256sum ".../extracted/Oday Plus Operator Console.dc.html"
# 65d359f4abaf82b39eb16f67da8e91e7ad1b030628bc15f8f45ce7c18c0e2f48  (matches manifest)
unzip -t ".../Oday Plus 營運管理後台 (6).zip"   # passed
grep -oE 'data-screen-label="[^"]*"' ".../Oday Plus Operator Console.dc.html" | sort -u | wc -l  # 32
```

Archived interactive HTML rendered from its DesignCanvas runtime over a
local static server (role switched to 行銷經理 to unlock Growth), captured
at desktop (1440) and constrained (768) widths; the delivered app captured
at the same widths via `/operator?ws=growth`. See `visual-parity.md` for
the `data-screen-label` mapping and surface-by-surface comparison.

## Evidence artifacts
- `api-proof.json` — live API responses for all four acceptance criteria.
- `growth-builder.png` — implementation five-step builder at the conflict
  step with live server checks, over the three entry cards + full workspace.
- `visual-parity.md` — canonical package-6 identity, relevant
  `data-screen-label` values, and desktop/constrained comparison.
- `archived-growth-desktop.png`, `archived-growth-builder-desktop.png`,
  `archived-growth-constrained.png` — package-6 reference renders.
- `growth-impl-desktop.png`, `growth-impl-constrained.png`,
  `growth-impl-builder-constrained.png` — matching implementation renders.
