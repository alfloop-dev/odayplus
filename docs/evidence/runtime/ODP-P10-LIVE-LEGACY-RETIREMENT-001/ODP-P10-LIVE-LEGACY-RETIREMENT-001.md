# ODP-P10-LIVE-LEGACY-RETIREMENT-001 — Legacy Retirement Reproof

- Task: Reprove 117 retired paths and legacy visuals on current dev and live SHA
- Owner: Claude2 · Reviewer: Antigravity6 · Phase: Package10LiveClosure (T41)
- Static phase verified head: `8eabc9735653710c1b88dcfe2996eb687b609c2b` (`origin/dev`, HEAD == origin/dev)
- Static phase result: **pass** (11/11 checks)
- Runtime phase result: **deferred, not claimed** — see [Runtime Phase](#runtime-phase-deferred)
- Overall task result: `static_reproved_runtime_deferred`

This artifact reproves the Package 10 legacy visual retirement **on current dev**,
independently of the historical 2026-07-26 verification at `435c79e3`. Every check
is re-executed here; the historical document is treated as a claim to be retested,
not as evidence to be inherited.

## Artifacts

| File | Role |
|---|---|
| `verify_static_retirement.py` | Reproducible verifier. Run from the repo root; exits 0 only when all checks pass. |
| `static-verification.json` | Generated result at dev `8eabc973`, including the full 117-path inventory. |
| `negative-control.json` | Proof the gates discriminate: the same verifier at the pre-retirement SHA. |

Reproduce with:

```bash
python3 docs/evidence/runtime/ODP-P10-LIVE-LEGACY-RETIREMENT-001/verify_static_retirement.py
```

## Acceptance criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | 117 unique retired paths reconstructed from committed ACKs | Met | `retired_path_inventory_reconstructed` |
| 2 | Current dev has zero executable survivor / selector / alternate detail / old identity / retired import / legacy spec resurrection | Met | Checks 2–8, 10–11 below |
| 3 | Final live routes are absent or redirect without serving old code | **Not met — deferred** | Runtime phase; no public SHA exists yet |
| 4 | Runtime chunk and import graphs cannot execute retired implementation | **Partially met** | Static import-graph gate passes; the chunk-level half is deferred |
| 5 | Evidence binds current dev and final public SHA rather than historical head alone | **Partially met** | Bound to dev `8eabc973`; public SHA binding deferred |

## 1. Inventory reconstruction

The 117 paths are rebuilt from the two committed ACKs, not copied from the
historical verification document:

- `docs/evidence/fleet_dispatch/package10_20260726/acks/ODP-P10-CAN-001-R3A.json` → 112 `deleted_paths`
- `docs/evidence/fleet_dispatch/package10_20260726/acks/ODP-P10-CAN-001-R3B.json` → 5 `deleted_paths`

The two sets are disjoint and neither contains internal duplicates, so the union
is exactly **117** unique paths, matching the `deleted_path_inventory.unique_paths`
figure asserted at `435c79e3`. Distribution: `apps/web` 92, `packages/ui` 7,
`tests/e2e` 18. The inventory contains no Python module, which matters for the
residual analysis in section 8.

The two source ACKs were read strictly read-only as historical R3 implementation
records, per the task dispatch note. No R3 work was reopened.

## 2–8. Static reproof at dev `8eabc973`

| Check | Result | Observation |
|---|---|---|
| `executable_pages` | pass | Exactly the 3 canonical pages: `/operator`, `/intake/[intakeId]`, `/franchisee` |
| `no_surviving_retired_path` | pass | 0/117 present on disk; 0/117 present in `git ls-files` |
| `retired_css_selectors_absent` | pass | 8 retired `odp-*` shell families: 0 selector definitions, 0 usages anywhere |
| `retained_generic_css_present` | pass | `.odp-select` still present in `packages/ui/src/styles/shell.css` (guards over-retirement) |
| `retired_identity_copy_absent_from_frontend` | pass | `OpsBoard` and `R0 導覽骨架` have 0 case-insensitive matches under `apps/web` and `packages` |
| `retired_intake_alternatives_absent` | pass | All 4 alternate-detail symbols have 0 source or test matches |
| `canonical_detail_graph_intact` | pass | `IntakeProcessingDetail.tsx` renders `ListingCompareTable` + `MatchEvidencePanel`; `Package10VisualP1.test.tsx` mounts the production detail |
| `no_import_edge_into_retired_module` | pass | 238 active source files scanned; 0 import edges resolve to a retired file |
| `legacy_e2e_specs_absent` | pass | 18 retired specs absent and unreferenced by `playwright.config.ts`; exactly the 16 declared canonical specs remain |

Two deliberate strengthenings over the 2026-07-26 check:

1. The retired-selector sweep tolerates leading whitespace and also runs a
   broader class-name usage pass. The anchored-only form is what let an indented
   `.odp-skip-link` inside `@media (prefers-reduced-motion: reduce)` survive the
   first R3A gate before coordinator rejection.
2. The identity sweep is case-insensitive, so `OPSBOARD_`-style identifiers
   cannot hide from it.

### Import graph, and what it does and does not prove

`no_import_edge_into_retired_module` resolves every `import` / `from` / `require`
specifier in active TypeScript sources — relative specifiers against the importing
file, and `@oday-plus/*` against `packages/` — then checks whether any resolves to
one of the 117 retired files. Zero do. `apps/web/tsconfig.json` declares no path
aliases, and a prefix survey confirms intra-repo imports are exclusively relative
or `@oday-plus/*`, so this resolution model covers the whole in-repo graph.

An unreachable module cannot be bundled, so this is the static half of acceptance
criterion 4. It is **not** the chunk-level half: it reasons over source, not over
emitted bundles. The chunk assertion is deferred with the runtime phase.

## 8. Residual references — disclosed, not swept under

A case-insensitive sweep of `apps`, `packages`, `tests`, `scripts` and
`playwright.config.ts` finds 119 residual mentions of the retired identity or of
retired spec filenames. None is an executable resurrection, and each is classified
in `static-verification.json` rather than hidden by narrowing the sweep:

| Kind | Count | Why it is not a resurrection |
|---|---|---|
| `live_backend_namespace_never_in_retirement_inventory` | 82 | `modules.opsboard.*` is a live backend Python namespace. The retirement inventory is entirely frontend (`apps/web`, `packages/ui`, `tests/e2e`) and contains no Python module, so no Python import can reach a retired file. |
| `inert_literal_string_in_document_assertion` | 28 | `tests/e2e/test_frontend_execution_matrix_coverage.py` names 6 retired spec paths, including `tests/e2e/opsboard-shell.spec.ts`. Its assertions check that those strings appear in historical evidence **documents** (`assert e2e_spec in dispatch_text`); it never checks file existence and never imports. Confirmed by execution: `pytest tests/e2e/test_frontend_execution_matrix_coverage.py` → 23 passed. |
| `inert_comment_or_docstring` | 7 | Comments only. `tests/e2e/operator-growth.spec.ts:33` is a comment that accompanies `expect(page.getByTestId("app-shell")).toHaveCount(0)` — it asserts the shell's *absence*. |
| `inert_environment_variable_name` | 2 | `OPSBOARD_PORT`, a port env var name in `playwright.config.ts` and `delivery_toolchain/e2e/run_product_e2e.sh`. A name, not retired code. |

`playwright.config.ts` uses `testDir: "./tests/e2e"` auto-discovery, so a retired
spec can only be collected if the file exists. None does.

These residuals are cosmetic naming debt, not a release blocker under the
Package 10 rule. Cleaning them is out of this task's write scope and is not
proposed here.

## Negative control

The identical verifier was run at `da7da895` — the parent of the R3A retirement
commit `ded04ac4` — in a throwaway worktree. **Eight of eleven checks flip to
fail**: 117/117 paths survive, 118 import edges resolve into retired modules,
41 executable pages instead of 3, 18 legacy specs present. Details and the three
non-discriminating checks are recorded in `negative-control.json`.

This matters for how the pass should be read: `residual_references_are_inert`
passes at *both* SHAs, so it is a disclosure check and must not be counted as
independent evidence of retirement. The discriminating reachability gates are
`no_surviving_retired_path` and `no_import_edge_into_retired_module`.

## Runtime phase (deferred)

The runtime half is **not** claimed. It depends on `ODP-P10-DEV-REDEPLOY-VERIFY-001`
(T30), which is `blocked` as of 2026-08-09T14:56:20Z on
`ODP-P10-LIVE-EXTDATA-DIAG-001` and `ODP-PRODUCTION-MODEL-REGISTRY-001`. Gate
authority at repair time: Deploy Dev run `31316767710` at
`9c95ecc3e1f2d0885bb4078070a116e852487f69`, `live-e2e-gate` `ok=false` 43/50,
blocking dependencies `[external-data, mlflow]`.

Per the task dispatch note the runtime phase must bind to the exact public SHA of
a promoted and retained release, rather than to the historical head. No such
release exists yet, so binding now would produce evidence tied to a SHA that never
went public — precisely the failure mode this task exists to correct.

Deferred and still owed once T30 promotes and retains a release:

1. Final live routes for all 18 retired route surfaces are absent or redirect
   without serving old code (acceptance criterion 3).
2. Emitted runtime chunk graphs from the release build contain no retired
   implementation (the chunk half of criterion 4).
3. Evidence rebound to the exact public SHA (criterion 5).

Re-running `verify_static_retirement.py` at that public SHA is the intended first
step of the runtime phase; it is SHA-agnostic by construction.

## Release boundary

This artifact proves legacy retirement holds on current dev. It does **not**
assert Package 10 release readiness, and it does not alter the standing `no_go`.
The Package 10 rule is unchanged: any resurrected retired path, selector,
alternate intake detail, or legacy E2E spec blocks release, and compatibility
markup is not an accepted remediation.

No product code was read for modification and none was changed. The task write
scope is `docs/evidence/runtime/ODP-P10-LIVE-LEGACY-RETIREMENT-001/**` only, and
nothing outside it was touched.
