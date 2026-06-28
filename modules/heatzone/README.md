# HeatZone Module

HeatZone Radar scores H3-like geo cells for expansion prioritization.

Implemented v1 surfaces:

- `modules.heatzone.domain` provides deterministic baseline scoring for
  unmet demand, format fit, cannibalization risk, rent feasibility,
  confidence, state, and priority rank.
- `modules.heatzone.workers` wraps scoring as a batch job result with
  `job_id`, completed status, score rows, and map features.
- `apps.api.oday_api.routes.heatzone` exposes `/heatzones`,
  `/heatzones/map`, `/heatzones/score-jobs`, and snapshot/detail reads.
- `packages/domain-types/src/heatzone.ts` publishes the frontend map and
  score data contract.

HeatZone v1 consumes `GeoFeatureSnapshot` or equivalent `geo_grid_view`
records and intentionally leaves persistent storage, tile generation,
assignment workflow, and frontend rendering to follow-up layers.
