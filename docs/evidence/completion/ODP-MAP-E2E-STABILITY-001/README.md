# ODP-MAP-E2E-STABILITY-001 completion evidence

## Pre-fix reproducer

The task was dispatched from `origin/dev` at `88e2dbd4` after the canonical
product gate produced both failure modes:

- direct picking timed out because `window.__odpHeatZoneMapProject` remained
  undefined;
- an isolated rerun timed out because `.maplibregl-canvas` did not appear
  within the existing readiness window.

The failures were recorded in the owner dispatch/status checkpoint at
2026-07-27T15:33:30Z. No timeout was increased and no test was skipped.

## Root cause and repair

`HeatZoneMap` tied the MapLibre construction effect to selection and rendered
data arrays. A selection update could therefore remove the canvas and
projection function while a replacement map was still starting. Direct
clicks also entered two navigation paths: the map wrapper's deterministic
nearest-coordinate selection and DeckGL picking.

The repair:

- owns MapLibre construction/cleanup independently from selection updates;
- synchronizes source data and selected-line paint without rebuilding the map;
- publishes `data-map-ready=true` and the projection function together only
  after MapLibre reaches `idle`;
- makes the wrapper's nearest-coordinate selection the single direct-pick
  navigation path;
- publishes DeckGL's rendered layer signature so pixel assertions wait for the
  requested layer state;
- compares the confidence layer across the complete map rather than a
  timing-sensitive small crop.

## Verification

All commands used zero Playwright retries.

Focused critical runs, three consecutive isolated browser runs:

```text
npx playwright test tests/e2e/e2e-map.spec.ts --project=chromium \
  --workers=1 --retries=0 --grep 'renders nonblank|direct picking'

run 1: 2 passed (1.2m)
run 2: 2 passed (55.5s)
run 3: 2 passed (52.2s)
```

Additional checks:

```text
npm run lint --workspace=@oday-plus/web -- --file features/map/HeatZoneMap.tsx
result: no warnings or errors

npm run typecheck --workspace=@oday-plus/web
result: passed

npx playwright test tests/e2e/e2e-map.spec.ts --project=chromium \
  --workers=1 --retries=0
result: 9 passed (1.8m)

./delivery_toolchain/e2e/run_product_e2e.sh
result: product workflows 7 passed (25.8s); map suite 9 passed (1.5m)
diagnostics: .odp_data/e2e-diagnostics/ODP-MAP-E2E-STABILITY-001
```

The canonical runner used task-local ports `3220`, `8220`, and `8120` to avoid
unrelated shared-worktree services. The first runner attempt did not reach the
tests because `.env.e2e.example` overwrote shell-provided ports and collided
with an existing process on `8099`; `.env.e2e` was then used as intended.

## Review and merge

Reviewer: Codex2.

Codex2 approved exact head
`524079fc60c361b9ca50dfbed5d11a806f0fc696`. The reviewer confirmed that the
task-owned `HeatZoneMap`, map E2E, and completion-evidence patch was unchanged
from the previously approved patch after composing `dev` at `a4afdcf2`, and
that the refreshed orchestrator, product E2E, and product CI gates all passed.

PR #426 merged that exact head into `dev` at merge commit
`410365298f05951c665d54f1e64a41c6accc7c28`. Closeout also confirmed with
`git merge-base --is-ancestor 524079fc origin/dev` and `git diff --check
524079fc^1..524079fc`.
