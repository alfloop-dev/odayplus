---
doc_id: ODP-OSS-AI-001-EVIDENCE
title: ODay Plus OSS AI, Forecasting, Causal, and Optimization Integration Evidence
version: 1.0.0
status: implementation-complete-release-pending
owner: Product Platform Engineering
task: ODP-OSS-AI-001
updated_at: 2026-07-24
---

# ODay Plus OSS AI Integration Evidence

## 1. Scope

This evidence records executable OSS integration across the whole ODay Plus
model and optimization platform. It supersedes the model-registry and solver
implementation statements in the historical 2026-06-28 current-state audit.
It does not claim that a production model has passed business approval, canary,
or rollout gates.

## 2. Runtime Integration Matrix

| Capability | OSS runtime | Application integration | Executable evidence | Status |
|---|---|---|---|---|
| Tabular point and interval models | CatBoost, LightGBM | `models/shared_ml/oss_estimators.py`; `pipelines/training/model_training.py` | `tests/unit/ml/test_oss_estimators.py`; `tests/integration/test_production_model_lifecycle.py` | Integrated |
| Time-series forecasting | StatsForecast, MLForecast, scikit-learn | `modules/forecastops/infrastructure/forecast_engines.py`; ForecastOps service, batch worker, API, and deployment-selected runtime | `modules/forecastops/tests/test_oss_forecast_engines.py`; `tests/integration/test_oss_ai_execution_flow.py` | Integrated |
| Model registry and lineage | MLflow | `modules/learninghub/infrastructure/mlflow_adapter.py`; Learning Hub release service | `modules/learninghub/tests/test_mlflow_adapter.py`; `tests/integration/test_learninghub_release.py` | Integrated |
| Model-ready data quality | Great Expectations | `pipelines/quality/great_expectations_gate.py`; Dagster quality stage | `tests/data/test_great_expectations_gate.py`; `tests/integration/test_oss_ai_execution_flow.py` | Integrated |
| Drift monitoring | Evidently | `modules/learninghub/infrastructure/evidently_monitor.py` | `tests/models/test_evidently_monitor.py`; `tests/integration/test_oss_ai_execution_flow.py` | Integrated |
| Training orchestration | Dagster | `pipelines/orchestration/dagster_training.py` | `tests/models/test_dagster_training.py`; `tests/integration/test_oss_ai_execution_flow.py` | Integrated |
| Hyperparameter search | Optuna | `models/shared_ml/hyperparameter_search.py` | `tests/models/test_hyperparameter_search.py`; `tests/integration/test_oss_ai_execution_flow.py` | Integrated |
| Causal incrementality | statsmodels WLS matched-control DiD | `modules/adlift/domain/incrementality.py` | `modules/adlift/tests/test_statsmodels_did_and_challengers.py`; `tests/integration/test_adlift_incrementality.py` | Integrated |
| Liquidity survival | lifelines CoxPH | `modules/avm/infrastructure/lifelines_survival.py` | `modules/avm/tests/test_lifelines_survival.py`; `tests/integration/test_oss_ai_execution_flow.py` | Integrated |
| Multi-objective portfolio search | pymoo NSGA-II | `solver/evolutionary/pareto.py` | `tests/solver/test_evolutionary_portfolio.py`; `tests/integration/test_oss_ai_execution_flow.py` | Integrated |
| Discrete campaign, route, and intervention scheduling | OR-Tools CP-SAT | `solver/ad_campaign`; `solver/routeplan`; `solver/scheduling` | each solver package's `tests/test_optimizer.py`; cross-system E2E route solve | Integrated |
| Robust scenario NetPlan | CVXPY mixed-integer optimization | `solver/netplan/robust.py` | `solver/netplan/tests/test_robust.py`; `tests/integration/test_oss_ai_execution_flow.py` | Integrated |
| Alternate algebraic optimization modeling | Pyomo | pinned runtime and capability reporting | `tests/models/test_oss_capabilities.py` | Available, not selected |

All runtimes above are pinned in `pyproject.toml` and `uv.lock`. Capability
availability is exposed by
`GET /api/v1/learninghub/oss-capabilities` and fails closed when a selected
runtime is unavailable.

## 3. Cross-System Functional Proof

`tests/integration/test_oss_ai_execution_flow.py` executes these real flows:

1. Great Expectations validates a model-ready dataset.
2. Dagster runs quality, LightGBM training, native artifact serialization,
   artifact reload, and MLflow registration in order.
3. MLflow persists dataset, feature, label, metric, run, artifact, stage, and
   alias lineage and resolves the production alias.
4. Evidently produces a report and detects shifted feature distributions.
5. The versioned ForecastOps API executes StatsForecast selected through
   `ODP_FORECAST_ENGINE` and `ODP_FORECAST_MODEL`.
6. lifelines fits CoxPH, predicts 30/90-day liquidity, and reloads a
   checksum-protected artifact without changing its prediction.
7. Optuna performs a deterministic parameter study.
8. pymoo returns a constrained NSGA-II portfolio frontier.
9. OR-Tools produces a constrained multi-quarter route plan.
10. CVXPY produces a max-min robust network decision.
11. A failed Great Expectations gate stops training and registry execution.

The ForecastOps runtime no longer creates synthetic observations when a durable
job lacks source timeseries. It fails closed. Deployment selection is controlled
by:

```text
ODP_FORECAST_ENGINE=statsforecast|mlforecast|baseline
ODP_FORECAST_MODEL=seasonal_naive|auto_arima|auto_ets|hist_gradient_boosting
```

## 4. Deliberately Gated Challengers

The following are not represented as completed production engines:

| Challenger | Current boundary | Activation requirement |
|---|---|---|
| DoubleML / EconML | dependency-gated adapter contracts exist; packages are not selected in the v1 runtime | approved learner, treatment/outcome contract, validation dataset, model card, and release gate |
| TFT / N-BEATS / LSTM | not activated | data-volume and stability gate, GPU/runtime ownership, challenger backtest, calibration, and rollback evidence |
| Feast | not activated as an online feature store | approved online-serving latency requirement and source-of-truth migration plan |

These are maturity-gated alternatives in the model specifications, not silent
fallbacks. Selecting one without its dependency and approval raises a runtime
error instead of substituting a heuristic.

## 5. Functional Verification

The final branch was verified through executable runtime boundaries, not only
unit adapters:

| Gate | Result |
|---|---|
| Full Python regression | `1690 passed`, `52 deselected`; no failures |
| OSS cross-system Python E2E | Great Expectations -> Dagster -> LightGBM artifact reload -> MLflow alias -> Evidently, plus StatsForecast -> lifelines -> Optuna -> pymoo -> OR-Tools -> CVXPY and a fail-closed quality path all passed |
| Product browser E2E | 12/12 unique Chromium scenarios passed across AVM, NetPlan, Learning Hub, AdLift/Intervention, Expansion, and all eight Assisted Listing Intake scenarios |
| Candidate promotion recovery | Independent review passed; injected `SCORE_FAILED` retained the Candidate, persisted a failed SiteScore receipt, replayed idempotently, incremented attempt once, and returned to `QUEUED` |
| Durable load and soak | 150 new jobs, 0 failures, P95 1.950 seconds against the 3.0-second budget; all performance tests passed |
| Node workspaces | TypeScript checks and 85 Vitest tests passed |
| API contract | Generated OpenAPI/client are current; one additive OSS capability operation, zero breaking changes |

The browser run first found and then verified fixes for durable Listing address
and H3 hydration, domain-safe Candidate persistence, and CI-only score failure
control. Regression coverage now exists at the durable repository, promotion
service, API, and Playwright layers.

## 6. Remaining Release Work

Implementation completion is distinct from production rollout. Production use
still requires:

- approved dataset snapshots and model cards;
- owner-approved metric, segment, calibration, and drift thresholds;
- shadow/canary evidence and rollback target;
- configured shared `MLFLOW_TRACKING_URI` and artifact storage;
- worker deployment variables and live source timeseries;
- SLO, capacity, UAT, and release-owner sign-off.

No item in this section requires inventing another algorithm implementation.
They are environment, evidence, and release decisions around the integrated
runtime.
