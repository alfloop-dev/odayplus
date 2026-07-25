from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from importlib.util import find_spec

import pytest

from modules.forecastops import (
    ForecastEngineError,
    ForecastEngineInputError,
    ForecastEngineUnavailableError,
    ForecastInput,
    ForecastOpsService,
    InMemoryForecastOpsRepository,
    MLForecastSklearnAdapter,
    StatsForecastAdapter,
    StoreDayObservation,
    create_forecast_engine,
    run_forecastops_batch_forecast,
)
from modules.forecastops.infrastructure import forecast_engines

ORIGIN = datetime(2026, 7, 20, 9, 0, tzinfo=UTC)
HAS_STATSFORECAST = find_spec("statsforecast") is not None
HAS_MLFORECAST = find_spec("mlforecast") is not None


def _input(*, days: int = 70, store_id: str = "store-oss-001") -> ForecastInput:
    start = date(2026, 5, 1)
    observations = tuple(
        StoreDayObservation(
            store_id=store_id,
            business_date=start + timedelta(days=index),
            actual_revenue=100_000.0 + (index * 175.0) + ((index % 7) * 1_250.0),
            site_score_baseline_p50=105_000.0,
            source_snapshot_ids=(f"pos-{index:03d}",),
        )
        for index in range(days)
    )
    return ForecastInput(
        store_id=store_id,
        observations=observations,
        prediction_origin_time=ORIGIN,
    )


def _assert_canonical_bands(result) -> None:
    assert set(result.bands) == {4, 8, 12, 24}
    for band in result.bands.values():
        assert 0 <= band.p10 <= band.p50 <= band.p90


def test_default_service_keeps_baseline_api_and_adds_metadata() -> None:
    service = ForecastOpsService()
    forecast_input = _input(days=7)

    result = service.forecast([forecast_input], scored_at=ORIGIN)

    forecast = result.forecasts[0]
    expected = round(
        sum(point.actual_revenue for point in forecast_input.observations) / 7,
        2,
    )
    assert forecast.p50 == expected
    assert forecast.engine_name == "baseline"
    assert forecast.model_name == "trailing_average"
    assert forecast.to_dict()["model_metadata"]["interval"] == "residual_cv_central_80"


@pytest.mark.skipif(not HAS_STATSFORECAST, reason="statsforecast is not installed")
@pytest.mark.parametrize("model_name", StatsForecastAdapter.supported_models)
def test_statsforecast_models_fit_and_predict_native_intervals(model_name: str) -> None:
    adapter = StatsForecastAdapter(model_name=model_name)

    result = adapter.fit(_input()).predict()

    _assert_canonical_bands(result)
    assert result.engine_name == "statsforecast"
    assert result.model_name == model_name
    assert result.model_version.startswith("statsforecast-")
    assert result.metadata["interval_method"] == "native_central_80"
    assert result.metadata["horizon_days"]["w24"] == 168


@pytest.mark.skipif(not HAS_MLFORECAST, reason="mlforecast is not installed")
def test_mlforecast_sklearn_challenger_fits_direct_quantiles() -> None:
    adapter = MLForecastSklearnAdapter(max_iter=35)

    result = adapter.fit_predict(_input(days=84))

    _assert_canonical_bands(result)
    assert result.engine_name == "mlforecast"
    assert result.model_name == "hist_gradient_boosting"
    assert result.model_version.startswith("mlforecast-")
    assert result.metadata["estimator"] == "HistGradientBoostingRegressor"
    assert result.metadata["quantiles"] == [0.10, 0.50, 0.90]
    assert result.metadata["interval_method"] == "direct_quantile_regression"


@pytest.mark.skipif(not HAS_STATSFORECAST, reason="statsforecast is not installed")
def test_application_selects_statsforecast_and_persists_run_metadata() -> None:
    repository = InMemoryForecastOpsRepository()
    service = ForecastOpsService(
        repository=repository,
        engine="statsforecast",
        model_name="seasonal_naive",
    )

    result = service.forecast([_input()], scored_at=ORIGIN)

    forecast = result.forecasts[0]
    assert forecast.engine_name == "statsforecast"
    assert forecast.model_name == "seasonal_naive"
    assert repository.history(forecast.store_id)[0].model_metadata == forecast.model_metadata

    run = repository.get_prediction_run(forecast.prediction_run_id)
    assert run is not None
    assert run.model_version_id == forecast.model_version
    predictions = repository.get_predictions(forecast.prediction_run_id)
    assert predictions[0].explanation_json["engine_name"] == "statsforecast"
    assert predictions[0].explanation_json["model_metadata"]["library"] == "statsforecast"


@pytest.mark.skipif(not HAS_MLFORECAST, reason="mlforecast is not installed")
def test_worker_explicitly_selects_mlforecast_without_changing_existing_signature() -> None:
    result = run_forecastops_batch_forecast(
        inputs=[_input(days=84)],
        prediction_origin_time=ORIGIN,
        engine="mlforecast",
        engine_options={"max_iter": 25},
    )

    payload = result.to_dict()
    assert payload["status"] == "succeeded"
    assert payload["forecasts"][0]["engine_name"] == "mlforecast"
    assert payload["forecasts"][0]["model_name"] == "hist_gradient_boosting"


@pytest.mark.skipif(not HAS_STATSFORECAST, reason="statsforecast is not installed")
def test_worker_uses_deployment_selected_oss_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ODP_FORECAST_ENGINE", "statsforecast")
    monkeypatch.setenv("ODP_FORECAST_MODEL", "seasonal_naive")

    result = run_forecastops_batch_forecast(inputs=[_input()])

    forecast = result.to_dict()["forecasts"][0]
    assert forecast["engine_name"] == "statsforecast"
    assert forecast["model_name"] == "seasonal_naive"
    assert forecast["model_metadata"]["library"] == "statsforecast"


@pytest.mark.parametrize(
    ("engine_name", "missing_package"),
    (("statsforecast", "statsforecast"), ("mlforecast", "mlforecast")),
)
def test_explicit_oss_engine_fails_closed_when_package_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    engine_name: str,
    missing_package: str,
) -> None:
    real_import = forecast_engines.import_module

    def unavailable(module_name: str):
        if module_name == missing_package or module_name.startswith(f"{missing_package}."):
            raise ModuleNotFoundError(
                f"No module named {missing_package!r}",
                name=missing_package,
            )
        return real_import(module_name)

    monkeypatch.setattr(forecast_engines, "import_module", unavailable)
    repository = InMemoryForecastOpsRepository()
    service = ForecastOpsService(repository=repository, engine=engine_name)

    with pytest.raises(
        ForecastEngineUnavailableError,
        match=f"requires optional package {missing_package!r}",
    ):
        service.forecast([_input(days=84)], scored_at=ORIGIN)

    assert repository.latest_forecasts() == []


def test_selected_oss_engine_rejects_short_history_instead_of_using_heuristic() -> None:
    service = ForecastOpsService(engine="statsforecast")

    with pytest.raises(ForecastEngineInputError, match="at least 14 daily observations"):
        service.forecast([_input(days=7)], scored_at=ORIGIN)


def test_engine_factory_rejects_unknown_engine_and_model() -> None:
    with pytest.raises(ForecastEngineError, match="unknown forecast engine"):
        create_forecast_engine("silent-magic-fallback")
    with pytest.raises(ForecastEngineError, match="unsupported StatsForecast model"):
        create_forecast_engine("statsforecast", model_name="invented-model")
