# Package 10 Dev Landing Reconciliation

- Task: `ODP-P10-DEV-LANDING-FIX-001`
- Owner: `Codex7`
- Reviewer: `Codex2`
- Base: `origin/dev` at `828f2a30a96717f22aa6de0f3a258ecf68845342`
- Safe source: `e5e5b724777675e7d8c5e4f92f4f6bc03785f7cb`
- Forbidden staging worktree used: no
- Deployment claimed: no

## Lineage

`git rev-list --count origin/dev..e5e5b724` reports 39 source commits. The
source tip is a synchronization merge whose parents are
`f190a96b3213d3417402d920ac00102b04adbf1a` and
`2cfc225230657b5667d9030ca727ef9ae20ba977`; it composes the 38 Package 10
implementation commits with the then-current `origin/dev`. The landing used the
exact reviewed safe source commit and merged it into the clean current dev tip.

## Conflict Resolution

The merge produced one conflict:

| Path | Current dev | Package 10 source | Resolution |
|---|---|---|---|
| `tests/e2e/e2e-expansion-product.spec.ts` | Modified by `ODP-LIVE-E2E-001` | Deleted by canonical legacy retirement | Delete. The spec serves the retired expansion runtime and is one of the 117 paths in the authoritative retirement inventories. Its freshness coverage remains in the canonical live-E2E gate; the legacy page/spec must not be restored. |

No runtime component, retired selector, or alternate intake detail was restored.

## Landing Remediation

- Fixed Ruff `I001` in the Package 10 product-grade gate test.
- Replaced deleted legacy evidence references in both release closeout queues
  with surviving canonical specs and the retirement verification artifact.
- Replaced the retired remote visual route with the canonical Network listings
  route.
- Made the combined reference label `Dialog 轉交／暫停` require both actual
  command implementations: `Dialog 轉交收件` and `Dialog 暫停 SLA`.
- Removed the 11 trailing blank lines at EOF reported on PR #404.

## Verification

- Package 6 ZIP:
  `db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76`
- Package 10 ZIP:
  `d1583a00496f928b0765c1756c9671fedf615f12c84c00494d454c983645d7f8`
- Retirement inventory: 117 unique paths, zero survivors.
- Active executable pages: `/operator`, `/intake/[intakeId]`, `/franchisee`.
- Retired selector families, `OpsBoard`, `R0 導覽骨架`, and the five forbidden
  intake files: zero active matches/survivors.
- `uv run ruff check delivery_toolchain/e2e/check_product_grade_ci_gates.py tests/e2e/test_package10_product_grade_ci_gate.py tests/ops/test_cloud_run_live_deployment.py`
- `uv run pytest -q tests/e2e/test_package10_product_grade_ci_gate.py tests/ops/test_cloud_run_live_deployment.py`
- `python3 delivery_toolchain/e2e/check_product_grade_ci_gates.py --report`
- `python3 delivery_toolchain/e2e/check_product_closeout_queue.py`
- `python3 delivery_toolchain/e2e/check_external_proof_closeout_queue.py`
- Focused Vitest: 4 files, 19 tests passed.
- `git diff --check`

This artifact records local reconciliation only. Required GitHub checks and
independent exact-head review remain mandatory before merge.

## Dev Refresh And CI Contract Remediation

PR #419 was refreshed with `origin/dev` at
`a2a3c9206d7ea086a32259afbfa10bcf660f021c`. The merge added the P4 router and
LEAN runtime contract surfaces and produced no conflicts. It did not restore
any of the 117 retired Package 10 paths.

The first full CI run on `8f79a2f4` exposed three stale contract assertions
after all 1,729 other product tests passed:

- The external-proof example still named the retired
  `e2e-map-live-boundary.spec.ts` instead of the surviving canonical
  `operator-network-listings.spec.ts` command required by its queue entry.
- The frontend matrix CI test still required retired product-grade E2E files
  from the pre-Package 10 runtime.
- The closeout assertion still opened the retired
  `e2e-avm-netplan.spec.ts` instead of checking the surviving
  `e2e-network-find-areas-api-binding.spec.ts` evidence.

Those assertions and the example artifact now point at the canonical surviving
operator specs already exercised by `delivery_toolchain/e2e/run_product_e2e.sh`. Runtime
components and routes were not changed. The failed product E2E job itself was
an independent Docker Hub registry timeout while pulling the test services.

Refresh verification:

- `uv run pytest -q tests/e2e/test_external_proof_handback_artifact.py tests/e2e/test_frontend_execution_matrix_coverage.py tests/e2e/test_package10_product_grade_ci_gate.py` — 40 passed.
- `uv run ruff check tests/e2e/test_external_proof_handback_artifact.py tests/e2e/test_frontend_execution_matrix_coverage.py tests/e2e/test_package10_product_grade_ci_gate.py`
- `python3 delivery_toolchain/e2e/check_product_release_gate.py`
- `python3 delivery_toolchain/e2e/check_product_grade_ci_gates.py --report`
- `git diff --check`

## 2026-07-27 Dev Refreshes And Canonical Intake Spec Alignment

Three additional `origin/dev` refreshes landed on the PR #419 line, each a
clean `ort` merge with zero conflicts and zero retired-path restorations:

- `9d9d50b0` — merged dev with map PR #426 stability work.
- `9c32eba4` — merged dev at `bfc9b7df` (provider-selection PR #418/#433
  closeout plus map evidence PR #431).
- `04672167` — merged dev at `611edf13` (store-opening authority PR #435).

The intake Playwright specs were aligned to the canonical Package 10 UI in
anchors `cddba2f2` and `9b832d87` (the latter restores the supervisor
worktree-dirt backup `74a8d5e9` lost in the 2026-07-27T19:55Z hard reset):

- Masked governance reads assert the lineage grid `lineage-row-contactPhone`
  renders the server-masked `[MASKED]` value instead of the retired
  `intake-masked-*` testids.
- Policy reasons assert the canonical `evidence-policy-reason` panel instead
  of the retired `intake-policy-reason` testid.
- Inbox counts assert the saved-view tab labels
  (`intake-tab-needsReview|blocked|awaitingEntry`) instead of the retired
  count badges.
- Durable reopen uses the canonical deep link
  `/operator?ws=network&tab=radar&selected=<id>&dialog=detail`; the restored
  detail view replaces the tab shell, so no `network-tab-*` click exists.
- Fresh contexts seed `oday.operator.subject` so the fail-closed operator
  identity keeps write actions enabled.
- The promote API assertion follows the reviewed two-actor saga: the endpoint
  records a `PENDING_REVIEW` promotion request and an
  `intake.promote_request` audit event; candidate creation happens only after
  independent review.

Refresh verification on the merged head:

- `pytest -q tests/e2e/test_package10_product_grade_ci_gate.py` — passed.
- `npx vitest run src/app/__tests__/productionRoutes.test.ts` — 7 passed
  (legacy visual retirement: retired URLs redirect, canonical imports stay
  off retired feature roots).
- `ruff check .` — all checks passed.
- Package 6 / Package 10 ZIP SHA-256 re-verified byte-identical.
- `npx playwright test tests/e2e/operator-assisted-listing-intake.spec.ts
  tests/e2e/operator-network-assisted-intake.spec.ts` — see PR #419 CI for
  the authoritative exact-head product-e2e-gate run.

## 2026-07-27 Store-Opening Authority Dev Refresh

After PR #437 advanced `origin/dev` to
`06f1344617afd67d18b98db3270ffc6b86f18897`, merge
`4bb2ee0fc0d9b4dcaec9c7e63765d3e4c34a10ed` refreshed PR #419 without
conflicts. The incoming delta adds only the store-opening authority inventory
and fail-closed decision evidence; it does not change Package 10 runtime,
routes, tests, source archives, or retired paths.

Verification on the merge head:

- Package 6 ZIP SHA-256:
  `db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76`.
- Package 10 ZIP SHA-256:
  `d1583a00496f928b0765c1756c9671fedf615f12c84c00494d454c983645d7f8`.
- `uv run pytest -q tests/e2e/test_package10_product_grade_ci_gate.py` —
  3 passed.
- `(cd apps/web && npx vitest run
  src/app/__tests__/productionRoutes.test.ts)` — 7 passed.
- `uv run ruff check .orchestrator scripts` — passed.
- `python3 delivery_toolchain/e2e/check_product_release_gate.py` — passed.
- `python3 delivery_toolchain/e2e/check_product_grade_ci_gates.py --report` — Package
  10 ZIP, canonical HTML, all 40 screen labels, and exact label count passed.
- `git diff --check` — passed.

The pushed post-evidence head still requires all three GitHub CI jobs and a
fresh independent Codex2 exact-head review before merge.
