# ODP-MAP-E2E-006 HeatZone Map Tooltip and Fallback Evidence Parity Closeout

Task: ODP-MAP-E2E-006
Owner: Antigravity
Reviewer: Claude
Status: ready_for_review

## Delivered Scope

- **Hover Tooltip**: Integrated an interactive, keyboard-accessible tooltip on each map grid cell that displays rank, score, state, unmet demand, formatting fit, cannibalization risk, rent feasibility, confidence, freshness (feature snapshot date), and model version. Shows the first warning inline when warnings exist.
- **Fallback Evidence Parity**: Refactored the ranked list component (fallback when geometry/map is degraded) to match the visual spec column parity, displaying: `Rank`, `Area`, `Score`, `State`, `Confidence`, `Listings`, and `Action` (with links to drawer and listings inbox).
- **No-Geometry Inline Warning**: Implemented a warning banner (`⚠️ 地圖 geometry 尚未可用；列表仍可用於審查。`) on the HeatZone page when geography data is unavailable (using the `noGeometry=true` URL state parameter).
- **Expanded Drawer Card**: Extended `HeatZoneScoreCard` to display full score breakdowns, evidence metrics (POI count, competitor counts, median rent, existing stores), confidence snapshot/quality metadata, and version/audit fields.
- **Fail-Closed API**: Enforced fail-closed behavior on `POST /heatzones/score-jobs`, returning `HTTP 422 Unprocessable Entity` when external features/inputs are empty or absent.

## Acceptance Evidence

### E2E Verification
- Visual layout, tooltips, fallback table, no-geometry warning, and drawer sections are fully verified in Playwright E2E test `tests/e2e/e2e-map.spec.ts`.
- Command run: `npx playwright test tests/e2e/e2e-map.spec.ts`

### Backend Integration
- `POST /heatzones/score-jobs` fail-closed logic verified with a mock payload test case in `tests/integration/test_heatzone_flow.py`.
- Command run: `uv run pytest tests/integration/test_heatzone_flow.py`

## Verification Command Outputs

```bash
$ uv run pytest tests/integration/test_heatzone_flow.py
5 passed, 2 warnings in 0.94s

$ npx playwright test tests/e2e/e2e-map.spec.ts
4 passed
```

## Boundaries
- This task does not implement actual Leaflet/MapLibre map rendering libraries, which are scheduled for later E2E visual mapping phases. The grid mock remains the interactive preview anchor.
