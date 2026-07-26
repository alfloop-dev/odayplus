# RETIRED: R5/Package-7 Operator Console shell

- doc_id: ODP-OC-REACT-RETIREMENT-001
- date: 2026-07-25
- decision_by: owner (bjoe734)
- status: RETIRED (console shell removed, build-green); rebuild from R7/Package 10

## What was removed (preserved in git history)

The R5/Package-7 operator **console shell** and its screens: `OperatorConsole.tsx`,
`TodayWorkspace`, `DesignAlignedWorkspaces`, `GrowthWorkspace`, `GovernanceWorkspace`,
`NetworkFindAreasWorkspace`, `OperatorDataUnavailableGate`, `StoreOpsWorkflowDialogs`,
the network panels (Candidate/Compare/Review/Rebalance/SiteScore/ListingRadar/NetworkShell…),
the intake panels/dialogs, view-models, state, adapters, policy, navigation view, and their
tests + the operator e2e specs. `/operator` now serves a retirement stub.
Recover with `git log --all -- apps/web/features/operator`.

## What was intentionally KEPT (and why)

The operator feature is a **load-bearing dependency of other features**, so a wholesale
delete breaks the web build (proven by CI). These shared modules are retained under
`apps/web/features/operator/network/` and `.../navigation.tsx`:
- `network/operatorNetworkClient` — used by `features/shell/shellClient.ts`
- `network/intake/AssistedIntakeSection` (+ its subtree) — used by `features/expansion/ExpansionWorkspace.tsx`
- `navigation` `OperatorRoleId` type — used by expansion

The R7 rebuild (ODP-OC-R7-FE-001) owns whether to relocate these to a neutral shared module.

## Honest status (no spin)

The removed console was **CI-label-verified** for R5's 37 screen labels
(`scripts/e2e/check_product_grade_ci_gates.py` PASS) — not "divergent garbage". It was
retired because it only reached R5 (canonical is R7/40 + VDC-001..005), its deployed
instance never showed live data (web→API **401** unwired), and the owner chose a clean R7
rebuild. The empty "OPERATOR_DATA_LOADING" gate people saw was a **no-data/401 state**, not
a wrong design.

## Verification (local, before push)

`npm run -w @oday-plus/web typecheck` → 0 errors; `lint` → 0; `test` (vitest) → 189 passed.

## Hard rule for all workers (human or LLM)

- Do **not** restore the removed console shell. Build the new one from the R7/Package 10
  extracted design (`docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted`)
  and binding review `ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE_REVIEW_003.md`.
- Do not re-diagnose the retired empty gate as "design was wrong": it was no-data/401.
- Reuse the retained shared modules + backend `/api/v1/operator/*` routes.
