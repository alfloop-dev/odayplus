# ODP-OC-R4-004 — Verification

All commands run from the task worktree on branch `task/ODP-OC-R4-004`.

## Reopen-fix scope (2026-07-14)

The reopen was a **visual-parity failure**. Fixed by rebuilding the Growth
screen and removing the extra shell chrome (see `visual-parity.md §0`).
Files changed this pass:

- `apps/web/src/app/OpsBoardFrame.tsx` — render `/operator*` full-bleed
  (drop the OpsBoard sidebar + global header for operator routes).
- `apps/web/features/operator/GrowthWorkspace.tsx` — rebuilt to the
  package-6 IA (inline header, entry cards, tab bar, three-column campaign
  workbench, segment cards, PriceOps table); removed the `PageHeader`
  breadcrumb.
- `apps/web/features/operator/growth.module.css` — new, design-faithful
  layout + constrained-width media queries.
- `tests/e2e/operator-growth.spec.ts` — updated for the tabbed IA and the
  bare (no `app-shell`) operator shell.
- `tests/e2e/e2e-growth.spec.ts` — removed (stale duplicate of
  `operator-growth.spec.ts`; its Step-4 asserted against a builder that
  never shipped — `select[name="observationWindow"]`, `recommendationId`
  audit field — so it was already failing).

The Growth view-model, API routes, `modules/opsboard/application/growth.py`,
approval flow, and closeout gate were **not** changed — the reopen was
presentation-only.

## Task-specified verification

```bash
uv run pytest tests/contract -k growth
# 12 passed

npx playwright test tests/e2e/operator-growth.spec.ts --project=chromium
# 11 passed
# (run reusing a fresh web server on port 3124 + live API on 8177:
#  OPSBOARD_PORT=3124 ODP_API_PORT=8177 ODP_API_BASE_URL=http://127.0.0.1:8177 \
#  ODP_PLAYWRIGHT_REUSE_EXISTING=1 npx playwright test ... )
```

> Note: a stale `next dev` from another worktree holds the default ports
> (3100 web / 8099 API — the 8099 socket is dead). Point Playwright at a
> fresh server on this branch's code via `OPSBOARD_PORT` / `ODP_API_PORT`
> + `ODP_API_BASE_URL` as above, otherwise it reuses the stale one.

## Additional checks

```bash
npm run typecheck --workspace=@oday-plus/web     # tsc --noEmit — clean (0 errors)
```

### Sibling-surface regression check (bare-shell change)

Removing the OpsBoard chrome only affects `/operator*` (the 14-route
`opsboard-shell` loop excludes `/operator`; only the two Growth specs
asserted `app-shell` on `/operator`). Verified no regression:

```bash
npx playwright test opsboard-shell operator-store-ops operator-shell-today ...
# opsboard-shell 14-route loop: PASS (app-shell still present on non-operator routes)
# operator-store-ops: PASS
# operator-shell-today: PASS (with ODP_API_BASE_URL pointed at the live API)
```

Two sibling failures were confirmed **pre-existing / environmental**, not
caused by this change:

- `e2e-operator-console.spec.ts` govern test waits for the approval
  "Close escalated service issue" — a string that exists only in the
  `GovernanceWorkspace` fixture; a live API returns different approval
  titles (`SiteScore 複審`, `Google review 回覆`, `PriceOps 折扣上限`)
  which override the fixture. Untouched by this task.
- `e2e-network-find-areas-api-binding.spec.ts` (HeatZone stats / map
  canvas) fails **identically on the original `OpsBoardFrame` with chrome**
  (A/B verified) — a pre-existing Network (ODP-OC-R4-005) surface issue.

## Canonical source + visual parity

```bash
sha256sum "docs_archive/00_source_zips/operator_console/r4-20260707-package-6/Oday Plus 營運管理後台 (6).zip"
# db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76  (matches manifest)
unzip -t ".../Oday Plus 營運管理後台 (6).zip"   # passed
grep -oE 'data-screen-label="[^"]*"' ".../Oday Plus Operator Console.dc.html" | sort -u | wc -l  # 32
```

Archived interactive HTML rendered from its DesignCanvas runtime (role
switched to 行銷經理 to unlock Growth), captured at desktop (1440) and
constrained (768) widths; the delivered app captured at the same widths via
`/operator?ws=growth`. See `visual-parity.md` for the `data-screen-label`
mapping and surface-by-surface comparison.

## Evidence artifacts
- `api-proof.json` — live API responses for all four acceptance criteria.
- `visual-parity.md` — reopen resolution, canonical package-6 identity,
  relevant `data-screen-label` values, and desktop/constrained comparison.
- `archived-growth-desktop.png`, `archived-growth-constrained.png`,
  `archived-growth-builder-desktop.png` — package-6 reference renders.
- `growth-impl-desktop.png`, `growth-impl-constrained.png`,
  `growth-impl-builder-desktop.png`, `growth-impl-segments.png`,
  `growth-impl-priceops.png` — matching implementation renders (fixed).
- `growth-prefix-desktop-fail.png`, `growth-prefix-constrained-fail.png` —
  the rejected before-state, retained as fail evidence.
