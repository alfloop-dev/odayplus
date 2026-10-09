# ODP-UI-NETWORK-FIND-AREAS-PARITY-001 — Network 找區域 vs Package 10

Recorded 2026-10-09 by Claude2. Base `origin/dev` 963090d6f. Design reference:
`docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/oday-plus-console-r7-standalone.html`
(S05 `nwAreas` / `nwVals` / `flowVals`). Audit:
`docs/audits/operator-console-layout-regression-20261008/content-parity-20261009/README.md`.

All screenshots: Chromium 149 headless, fixture mode (`playwright.config.ts`
web env), role 展店經理, `/operator?ws=network`, full page. The round `N`
button near the legend is the Next.js dev indicator, not product UI.

| File | What |
|---|---|
| `screenshots/design-network-areas-1440.png` | Design, from the audit |
| `screenshots/design-network-areas-390.png` | Design rendered at 390 (it overflows to 657px — VDC-002) |
| `screenshots/before-network-areas-{1440,390}.png` | origin/dev 963090d6f |
| `screenshots/after-network-areas-{1440,390}.png` | This branch |

## Geometry at 1440×900 (px, page coordinates)

| | Design | Before | After |
|---|---|---|---|
| Header top / height | 100 / 36 | 97 / 68 (3 lines) | 97 / 39 (1 line) |
| KPI chips | 4, Chinese | 6, English | 4, Chinese |
| Stepper top / height | 146 / 135 | 177 / 183 | 144 / 140 |
| Tab row top | 312 | 372 | 296 |
| Tab count badges | yes | none | yes (radar, compare, review, rebalance) |
| Find Areas grid top | 347 | 424 | 349 |
| Columns | 206 / 836 / 330 | 206 / map+tray / 330 | 206 / 836 / 330 |
| Lenses | 9 single-line Chinese | 9 two-line English | 9 single-line Chinese (31px) |
| Map | 426px, basemap, caption pill | header bar + flat background + debug line, ~70px dead band below | 426px, offline schematic basemap, lens caption, tray directly below |
| Zone detail | big score, 7 facts, why / risks, next box, 1 + 5 buttons | id/name, 4 English buttons, 6 meters, Reasons / Risks / Next Step / Pipeline | big score (22px), 7 facts, 為什麼是這一區 / 主要風險, 下一步 box, 1 + 5 buttons |
| Legend | Chinese HeatZone bands | English | Chinese HeatZone bands + 物件 / 候選點 |

390×844: before and after both fit (scrollWidth 390). After stacks lens row
(horizontal scroll) → map (300px) → zone detail → tray → address search →
legend. The design itself is 657px wide at 390, so the phone layout follows
VDC-002 rather than the design file.

## Where the design and later specs differ

- **Address search (UX-SCR-EXP-001)** is not in Package 10. It stays, as a card
  under the recommended tray in the centre column, so it never moves the map
  or the detail.
- **Right-panel actions.** Package 10's three placeholder actions (指派找點任務,
  建立物件搜尋條件, 加入季度展店計畫) have no backend; adding them would be
  buttons that only toast. The five secondaries are the existing behaviours
  in the design's button style: ＋ 從網址新增物件（帶入本區） (opens the
  canonical intake dialog on Listing Radar via `dialog=add`, disabled without
  the intake `submit` grant), 加入追蹤 / ✓ 已追蹤（點擊移除）, 查看本區候選點,
  評分最佳候選點 (disabled when the zone has no candidate), 送出審核.
  Primary: 查看本區物件（N）.
- **Basemap.** No tile provider is configured outside production and local /
  E2E builds must not reach the network, so the map draws a bundled schematic
  of the main Taipei rivers and trunk roads (`schematicBasemap.ts`, no network
  request) and says 示意底圖（離線）in its caption. A configured
  `NEXT_PUBLIC_ODP_MAP_TILE_URL` still replaces it with live tiles. Production
  map gating is unchanged.
- **Zone colours** follow the Package 10 lens bands (≥80 teal, 70–79 indigo,
  60–69 amber, <60 red) instead of absorption state on this screen. The
  confidence fill and risk outline overlays are off here (資料信心 is a lens).
  Amber text and fills use #96610B so labels keep 4.5:1 contrast.
- **Stepper.** Current/next copy is Chinese (目前步驟：找區域 Find Areas,
  下一步：查看本區物件並轉為候選點). The amber block notice now appears only when
  the step the operator must do next is blocked; later steps waiting for a
  candidate already carry 缺資料 tags. This removed 43px above the tabs.
- **查看本區候選點 is zone-scoped** (review reopen on bc3a7d4e1). The action
  opens Candidates with `zone=<HeatZone id>` in the URL, so reload and
  back/forward keep the scope. The scope applies to live scoring rows and
  fixture rows alike: board, pipeline counts, default selection and 執行批次評分
  (sent with the zone's `candidateIds`, never an empty list, which the API
  reads as "all"). A 本區：<label>（N） chip and 顯示全部候選點 button clear it;
  any other tab change drops `zone`. A zone with no candidates shows
  「<label> 尚無候選點。」 instead of another zone's rows.
- Store / competitor markers in the design legend are not drawn: the map has
  no store or competitor data. The legend lists what the map shows.

## Verification

- `npm run typecheck --workspace=apps/web` — pass
- `npm run lint --workspace=apps/web` — pass
- `npm run test --workspace=apps/web` — 67 files / 685 tests pass (new
  `NetworkFindAreasPackage10.test.tsx`, 11 tests)
- `npx playwright test tests/e2e/operator-shell-layout.spec.ts -g "Network Find Areas Package 10"` — 4/4
- Regression run (layout, network ×6, assisted intake ×3, operator console,
  market intelligence): 89 passed, 2 failed, both reproduced on origin/dev
  or environmental:
  - `e2e-network-find-areas-api-binding` "zone markers": the map chunk takes
    8–18s on a local dev server; the same test fails identically against an
    origin/dev server. Its timeout is raised to 30s (same as the listings
    spec).
  - `operator-assisted-listing-intake` canonical 5/6: the API honours
    `X-ODP-Test-Fault` only when `CI=1` (`apps/api/app/routes/listings.py`);
    the local API returns 403. Unrelated to this change.
- Cold rerun after clearing `apps/web/.next` (Playwright-started web server):
  `e2e-network-find-areas-api-binding`, `operator-network-listings`,
  `operator-shell-layout` — 33/33 pass.
- `npm run build --workspace=apps/web` — pass; `npm run bundle:budget` — pass
  (`/operator` first load 296.1 kB of 300 kB; the map and basemap stay in the
  lazily loaded map chunk).
- Playwright inventory: 18 specs / 126 tests (`product_e2e_receipt.py`).

### Review repair (zone-scoped candidates)

- `NetworkFindAreasPackage10.test.tsx`: HZ-01 and HZ-02 each show only their
  own candidate (and preselect it) after 查看本區候選點; an added HZ-03 with no
  candidates shows the empty scoped board; `tab=candidates&zone=HZ-02` restores
  the scope, 顯示全部候選點 and a tab-bar change drop it.
  `Package10NetworkPanels.test.tsx`: scoped counts, selection and batch ids;
  empty zone disables the batch run. Reverting only the action wiring to
  `changeActiveTab(2)` fails 3 of the new tests.
- `npm run test --workspace=apps/web` — 67 files / 690 tests pass; typecheck,
  lint, build pass; bundle budget `/operator` 296.5 kB of 300 kB.
