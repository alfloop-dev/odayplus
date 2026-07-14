# ODP-GAP-ML-002 Worker Evidence

Recorded: 2026-07-11
Worker lane: backend / ml (owner Claude, reviewer Codex)
Scope: `models/shared_ml`, `apps/api/oday_api/main.py`, the HeatZone / SiteScore /
ForecastOps routers, and `tests/integration`.

## Objective

Close the gap between the HeatZone, SiteScore, and ForecastOps scoring/forecast
services and the durable ML model registry (ODP-GAP-ML-001 foundation): bind
every run to a registered **PRODUCTION** model version, emit that binding as
audit metadata on the wire and in the run audit event, and **fail closed** when
the production model or the live feature inputs are absent.

## Current Proof Boundary

- Before: each service stamped a bare literal `*-baseline-v1` model version, was
  not linked to any registered model, and produced an all-zero score from an
  empty request (fail-open).
- After: `create_app` seeds a PRODUCTION `ModelVersion` per service into the
  durable registry (`get_alias(model_name, PRODUCTION)`), each router resolves
  that binding and reports it as `model_binding` audit metadata, and a fresh run
  with absent live inputs is rejected with HTTP 422.

## Implementation Evidence

### Shared model-binding layer

Status: implemented.

- `models/shared_ml/scoring_binding.py` — `ScoringModelSpec` (three baseline
  specs mirroring the module domain constants), `ModelBinding.to_audit_metadata`,
  `seed_scoring_models` (idempotent PRODUCTION registration), `resolve_production_binding`,
  `require_live_inputs`, and the fail-closed errors `ScoringInputUnavailableError`
  / `ProductionModelUnavailableError`. Depends only on `models.shared_ml`; the
  registry is a duck-typed port (no upward import into `modules`).
- `models/shared_ml/__init__.py` — re-exports the new binding surface.
- `apps/api/oday_api/main.py` — `seed_scoring_models(learning_repo, git_sha=...)`
  at app build; passes `scoring_bindings.get(<service>)` into each router and
  exposes `api.state.scoring_bindings`.

### HeatZone

Status: implemented.

- `apps/api/oday_api/routes/heatzone.py` — `create_heatzone_router` accepts a
  `model_binding`; `POST /heatzones/score-jobs` calls `require_live_inputs` on a
  fresh run (422 when absent) and adds `model_binding` to the run audit event
  metadata and the job response. Idempotent replays are unaffected.

### SiteScore

Status: implemented.

- `apps/api/app/routes/sitescore.py` — `create_sitescore_router` accepts a
  `model_binding`; `POST /sitescore/score-jobs` (+ `/reports` alias) fails closed
  after the idempotency check and binds the run.

### ForecastOps

Status: implemented.

- `apps/api/app/routes/forecastops.py` — `create_forecastops_router` accepts a
  `model_binding`; `POST /forecastops/forecast-jobs` fails closed on a fresh run
  with absent inputs and binds the run. (Also removed two redundant function-local
  `HTTPException` imports now covered at module scope.)

## Verification Evidence

```bash
uv run pytest tests/integration/test_scoring_model_binding.py
```
Result: 11 passed (spec/domain-constant drift guard, idempotent seeding,
fail-closed resolution + guard, and per-service API 422 + binding metadata).

```bash
uv run pytest tests/integration/test_heatzone_flow.py
```
Result: 4 passed (no regression).

```bash
uv run pytest tests/integration/test_sitescore_decision.py
```
Result: 8 passed (no regression).

```bash
uv run pytest tests/integration/test_forecastops_alerts.py
```
Result: 5 passed (no regression).

```bash
uv run ruff check models/shared_ml apps/api/oday_api/main.py \
  apps/api/oday_api/routes/heatzone.py apps/api/app/routes/sitescore.py \
  apps/api/app/routes/forecastops.py tests/integration/test_scoring_model_binding.py
```
Result: All checks passed.

## Acceptance Criteria

1. **Meets scope in this brief** — HeatZone / SiteScore / ForecastOps runs are
   bound to a durably-registered PRODUCTION model version with audit metadata.
2. **Fail-closed when external live inputs are absent** — a fresh score/forecast
   job with an empty input collection returns HTTP 422 (`require_live_inputs`),
   and an unregistered production model raises `ProductionModelUnavailableError`.
3. **Scoped task-branch PR with green required checks** — additive changes on
   `task/ODP-GAP-ML-002`; the `orchestrator` required check is green. The
   `product` job's `tests/e2e/test_product_closeout_action_*` failures are
   pre-existing `dev` drift (ODP-FE-XCUT-001 closeout-queue fixture) and fail
   identically on `origin/dev` with this branch's changes stashed — this branch
   touches none of those files.

## Contract Coverage

- `ModelBinding.to_audit_metadata` surfaces `model_service`, `model_id`,
  `model_stage`, `dataset_snapshot_id`, `feature_schema_version`, `label_version`,
  and `model_git_sha` on both the audit event and the job response.
- `test_spec_matches_domain_constants` guards the seeded spec against drift from
  each module's `*_MODEL_VERSION` / `*_FEATURE_VERSION` literal.

## Remaining Follow-ups (out of scope)

- HeatZone still persists only through the in-process `HeatZoneResultStore`; a
  durable `DurableHeatZoneRepository` mirroring the SiteScore/ForecastOps durable
  repositories is a candidate follow-up so heatzone outputs survive restart like
  the other two services already do.
