# ODP-ENG-FRONTEND-BUILD-001 — Completion Evidence

- Task: Close actionable frontend build and bundle findings
- Owner: Claude2
- Reviewer: Antigravity4
- Scope: `apps/web` build hygiene and first-load weight. No visual redesign.

## Result summary

| Finding | Before | After |
| --- | --- | --- |
| CSS build warnings | 3 autoprefixer warnings, `next build` ends "Compiled with warnings" | 0 warnings, "Compiled successfully" |
| `/operator` First Load JS | 816 kB | 273 kB (−543 kB, −66%) |
| `/intake/[intakeId]` First Load JS | 816 kB | 273 kB (−543 kB, −66%) |
| Bundle budget | none; `next build` prints the number and exits 0 | `bundle-budget.json` enforced by `npm run bundle:budget`, wired into `make node-check` |
| `NetworkFindAreasWorkspace.tsx` | 1772 lines incl. an inline operator→map adapter layer | 1636 lines; adapters extracted to `network/heatZoneMapAdapters.ts` |
| Web test suite | 35 files / 265 tests | 37 files / 281 tests |

## Files in this packet

- `next-build-before.log` — pre-change warning block and route table.
- `next-build-after.log` — post-change build result and route table.
- `bundle-budget-after.txt` / `.json` — budget checker output on the final build.
- `css-flex-guard-before.txt` — the new CSS guard run against pre-change file
  content, showing the 4 offences it would have caught.

## Acceptance criteria

### 1. Changed scope builds without actionable warnings

The three warnings were all the same class: `align-items: start | end` inside a
`display: flex` block, which autoprefixer flags as "mixed support".

- `features/operator/designAligned.module.css:1155` `end` → `flex-end`
- `features/operator/networkFindAreas.module.css:1939` `start` → `flex-start`
- `features/operator/networkFindAreas.module.css:2049` `end` → `flex-end`
- `features/operator/storeOps.module.css:23` `end` → `flex-end` (same defect,
  latent because the stylesheet is not currently imported)

These are equivalent values in a flex container, so rendering is unchanged.
Grid containers using `align-content: start` were deliberately left alone —
there the keyword is correct.

`next-build-after.log` shows zero `autoprefixer` matches and a clean
"Compiled successfully". A regression guard,
`apps/web/scripts/__tests__/cssFlexAlignment.test.mjs`, fails the suite if the
pattern comes back in any flex block; `css-flex-guard-before.txt` shows it was
red before the fix.

Out of scope and left unchanged: `packages/ui-domain/src/styles/domain.css:239`
has the same pattern but is not in the `apps/web` build graph, so it emits no
warning today. Recorded in `docs/development/frontend-bundle-budget.md`.

### 2. Bundle budget is machine enforced

- `apps/web/bundle-budget.json` — per-route ceilings plus a default for
  undeclared routes and a ceiling on the shared chunk.
- `apps/web/scripts/bundleBudget.mjs` — recomputes Next's First Load JS from
  `.next/app-build-manifest.json` (union of route + layout-chain `.js` chunks,
  gzip level 9, kB = 1000 bytes). Verified against Next's own table: `/operator`
  273.1 vs 273 kB, `/franchisee` 116.5 vs 116 kB, shared 103.3 vs 103 kB.
- `apps/web/scripts/check-bundle-budget.mjs` — CLI, exits 1 when over budget.
- `npm run bundle:budget` in `apps/web` and at the repo root.
- `make node-check` runs it between `build` and `test`, so CI's "Run Node
  workspace checks" step fails on a regression.

Negative proof — same build, `/operator` budget tightened to 200 kB:

```
Budget exceeded:
  /operator: 273.1 kB > 200.0 kB (+73.1 kB)
  /intake/[intakeId]: 273.0 kB > 130.0 kB (+143.0 kB)
exit=1
```

`make node-check` previously separated its steps with `;`, so only the last
command's exit status reached make and a failing `lint`/`typecheck`/`build` was
silently swallowed. Those separators are now `&&`, which is what makes the new
check (and the existing ones) actually blocking.

The budget logic is unit tested in
`apps/web/scripts/__tests__/bundleBudget.test.mjs` (16 tests), including the
pre-split 816 kB `/operator` figure as a regression case.

### 3. No functionality is removed to meet budget

The saving comes entirely from deferring, not deleting. `HeatZoneMap` — the sole
consumer of `@deck.gl/*`, `maplibre-gl` and `h3-js` — is now loaded via
`next/dynamic` with `ssr: false` instead of being statically imported into
`NetworkFindAreasWorkspace`. Both heavy chunks are still emitted and still
loaded, just on demand:

```
lazy chunks, absent from the /operator first load:
  1008 kB  79f774c5.*.js      (deck.gl)
   687 kB  346.*.js           (maplibre-gl)
```

`ssr: false` is correct here rather than a compromise: `HeatZoneMap` constructs
the maplibre instance in an effect against a real DOM node, so the server render
only ever produced an empty container.

No props, callbacks, test ids or behaviour changed. Full web suite: 281 passing.

### 4. Desktop and mobile preserve the design language

- No component markup, layout or token changed. The map renders exactly as
  before once its chunk arrives.
- The only new UI is the `loading` placeholder for the deferred map. It reuses
  the existing surface colours and the same `min-height` as the mounted map
  (`520px` default, `430px` under the `Network 找區域` tab override), so the
  workbench grid does not reflow when the map arrives — on desktop or mobile.
- The four CSS edits are value-equivalent inside a flex container.
- Existing responsive rules (`@media (max-width: 980px)` and below) are
  untouched.

### 5. Before-after evidence is delivered

This packet.

## Bounded decomposition

`NetworkFindAreasWorkspace.tsx` was 1772 lines. The operator→map adapter layer
(`OPERATOR_MAP_FRESHNESS`, `deriveHeatZoneState`, `operatorHeatZoneToMapZone`,
`operatorListingToMapListing`, `operatorCandidateToMapSite`) is pure data
mapping with no React or DOM dependency, so it moved to
`features/operator/network/heatZoneMapAdapters.ts` (1772 → 1636 lines). The
duplicated centroid-offset arithmetic in the two listing/candidate adapters was
folded into one `offsetFromZone` helper. Behaviour is unchanged.

This is the bounded slice the brief asked for. The file is still large and the
remaining split — `FindAreasPanel` and the presentational leaf components — is a
larger, review-heavy change that was deliberately not attempted here.

## Verification

```bash
npm install
npx tsc --noEmit                       # apps/web — clean
npx next lint                          # apps/web — no ESLint warnings or errors
npx next build                         # apps/web — Compiled successfully, 0 warnings
node scripts/check-bundle-budget.mjs   # apps/web — exit 0, all routes within budget
npx vitest run                         # apps/web — 37 files, 281 tests passed
```

Not run: Playwright E2E (`tests/e2e/operator-network-listings.spec.ts`,
`tests/e2e/e2e-network-find-areas-api-binding.spec.ts` assert on the
`heat-zone-map` test id). They require a live environment that is not available
to this worker. Both already await the element, and the map now mounts one
client-side chunk load later; the 15s timeout on the primary assertion covers
that, but a reviewer with a live environment should confirm.

## Base advance after approval (2026-08-08)

The task was approved at `63be0ccf`, but the GitHub merge queue reported PR #709
as `UNMERGEABLE`: `dev` had advanced 28 commits and
`ODP-CAP-GEOCODER-SEARCH-001` (`3125e205`) had edited the same file this task
decomposed. `origin/dev` was merged into the task branch and the one conflict
resolved; nothing was reset, discarded, or force-pushed.

### Conflict resolution

Single conflict, in the import block of
`apps/web/features/operator/NetworkFindAreasWorkspace.tsx`. Both sides edited
the same hunk for unrelated reasons:

- this task replaced `import { HeatZoneMap }` with a type-only import plus the
  `next/dynamic` wrapper, and added the `heatZoneMapAdapters` import
- `dev` added the `GeocoderSearchPanel` / `geocoderPermissions` imports

Both were kept. `HeatZoneMap` stays lazily loaded via `next/dynamic`; the
geocoder imports were taken verbatim from `dev`. The rest of the file merged
cleanly — `dev`'s `activeRoleId` prop threading and `geocodeReceipt` panel, and
this task's typed `useMemo` adapters, do not overlap. `networkFindAreas.module.css`
auto-merged (`.mapLoading` from this task, `.geocodeReceipt` from `dev`).

### Re-verification on the merged tree

```bash
make node-check   # exit 0
```

| check | result |
| --- | --- |
| `npm run lint` | No ESLint warnings or errors |
| `npm run typecheck` | clean |
| `npm run build` | Compiled successfully in 7.9s, 0 warnings |
| `npm run bundle:budget` | exit 0, all routes within budget |
| `npm run test` | 41 files, 349 tests passed |

Test counts rose from 37 files / 281 tests to 41 files / 349 tests: the four
extra files are `dev`'s geocoder suites, not new tests from this task. The
`ECONNREFUSED 127.0.0.1:3000` lines in that run are expected stderr from
`geocoderClient.test.ts`, which exercises the transport-failure path.

### Budget impact of the base advance

`/operator` and `/intake/[intakeId]` First Load JS moved 273.1 kB → 280.0 kB
(+6.9 kB). That is `dev`'s statically imported `GeocoderSearchPanel`, not a
regression in this task's route splitting; both routes remain under the 300.0 kB
budget with 20 kB of headroom, and the machine gate passes. Full output:
`bundle-budget-after-base-advance.txt`. The `bundle-budget-after.*` artifacts are
left as recorded at approval time.
