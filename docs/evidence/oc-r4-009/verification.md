# ODP-OC-R4-009 — Verification

All commands run from the worktree on branch `task/ODP-OC-R4-009`
(rebased onto `origin/dev` @ `09298726`, which contains the required
source-preflight minimum `7eba8098`).

## Source preflight (package 6)

| Command | Result |
| --- | --- |
| `sha256sum … Oday Plus 營運管理後台 (6).zip` | `db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76` — **match** |
| `unzip -t … (6).zip` | **OK** (no errors) |
| interactive HTML data-screen-label | `Govern 治理稽核` — matches `GovernanceWorkspace.tsx` |

## Backend

| Command | Result |
| --- | --- |
| `uv run pytest tests/contract -k govern` | **13 passed**, 146 deselected |
| `uv run pytest tests/contract` (regression) | **159 passed** |
| `uv run ruff check` (owned backend files) | **clean** |

## Frontend

| Command | Result |
| --- | --- |
| `npm run typecheck` (`tsc --noEmit`, apps/web) | **0 errors** |
| `npx next lint --dir features/operator` | **clean** (only a pre-existing GrowthWorkspace a11y warning) |

## End-to-end (Playwright, API + web both booted)

| Command | Result |
| --- | --- |
| `npx playwright test tests/e2e/operator-governance.spec.ts` | **4 passed** |
| `npx playwright test operator-governance + e2e-operator-console` | **8 passed, 1 skipped** (no cross-file state collision) |

## Live API proof

See `api-proof.json` (captured against the running API):

- `GET /snapshot` → `200`; approvals span **Store Ops / Growth / Network /
  Govern**; decisions span **Store Ops / Growth / Network**; status board
  panels = `dataQuality, models, connectors, sla, users, runbooks`;
  `pendingApprovals = 4`.
- `POST /decisions` reject with a 2-char reason → **`422`** (server-side policy).
- `POST /decisions` reject with a full reason → **`200` Rejected**.
- `POST /evidence-package` → **`200`**, records scope (modules + contents),
  range `2026-06-01 – 2026-07-03`, format `PDF`, actor `PM／稽核`,
  correlation `corr-oc-r4-009-proof`, retention `7 天簽章 URL，actor 欄位遮罩`.
- `POST /decisions` without permission → **`403`** (fail-closed).

## Notes

- The e2e suite reuses a single in-memory service per server lifetime; a fresh
  server (or CI's per-run boot) starts from the deterministic seed. All four
  Govern e2e tests pass from clean state; the return/reject test targets the
  Network approval to avoid colliding with the Store Ops approval used by
  `e2e-operator-console` FE-05.
