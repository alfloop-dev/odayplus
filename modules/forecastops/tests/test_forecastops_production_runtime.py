from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from modules.forecastops import (
    ForecastBand,
    ForecastEngineResult,
    ForecastInput,
    ForecastOpsRuntimeConfigurationError,
    ForecastOpsService,
    InMemoryForecastOpsRepository,
    StoreDayObservation,
)
from shared.infrastructure.persistence import (
    DurableForecastOpsRepository,
    SqliteDocumentStore,
    SqliteEngine,
)

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


class RecordingStatsForecastEngine:
    engine_name = "statsforecast"
    model_name = "seasonal_naive"

    def __init__(self) -> None:
        self.calls: list[ForecastInput] = []

    def fit_predict(self, forecast_input: ForecastInput) -> ForecastEngineResult:
        self.calls.append(forecast_input)
        return ForecastEngineResult(
            bands={
                horizon: ForecastBand(
                    p10=90_000.0 + horizon,
                    p50=100_000.0 + horizon,
                    p90=110_000.0 + horizon,
                )
                for horizon in (4, 8, 12, 24)
            },
            engine_name=self.engine_name,
            model_name=self.model_name,
            model_version="statsforecast-2.0:seasonal_naive",
            metadata={
                "library": "statsforecast",
                "adapter_invoked": True,
            },
        )


def _input() -> ForecastInput:
    start = date(2026, 5, 1)
    return ForecastInput(
        store_id="store-live-001",
        observations=tuple(
            StoreDayObservation(
                store_id="store-live-001",
                business_date=start + timedelta(days=index),
                actual_revenue=90_000.0 + index * 500.0,
                machine_cycles=100 + index,
                source_snapshot_ids=(f"pos-live-{index:03d}",),
            )
            for index in range(70)
        ),
        prediction_origin_time=NOW,
    )


def _repository(path: Path) -> tuple[SqliteEngine, DurableForecastOpsRepository]:
    engine = SqliteEngine(path)
    return engine, DurableForecastOpsRepository(SqliteDocumentStore(engine))


def test_production_invokes_oss_adapter_and_persists_across_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "forecastops.sqlite3"
    engine, repository = _repository(database)
    adapter = RecordingStatsForecastEngine()
    try:
        result = ForecastOpsService(
            repository=repository,
            engine=adapter,
            runtime_mode="production",
        ).forecast([_input()], prediction_origin_time=NOW, scored_at=NOW)
        output_id = result.forecasts[0].forecast_output_id
        assert len(adapter.calls) == 1
        assert result.forecasts[0].engine_name == "statsforecast"
        assert result.forecasts[0].model_metadata["adapter_invoked"] is True
    finally:
        engine.close()

    reopened_engine, reopened = _repository(database)
    try:
        restored = reopened.latest_forecasts()[0]
        assert restored.forecast_output_id == output_id
        assert restored.engine_name == "statsforecast"
        assert restored.model_version == "statsforecast-2.0:seasonal_naive"
    finally:
        reopened_engine.close()


def test_production_rejects_memory_missing_and_baseline_engines(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ForecastOpsRuntimeConfigurationError,
        match="injected durable repository",
    ):
        ForecastOpsService(runtime_mode="production")
    with pytest.raises(
        ForecastOpsRuntimeConfigurationError,
        match="injected durable repository",
    ):
        ForecastOpsService(
            repository=InMemoryForecastOpsRepository(),
            engine="statsforecast",
            runtime_mode="production",
        )

    engine, repository = _repository(tmp_path / "forecastops.sqlite3")
    try:
        with pytest.raises(
            ForecastOpsRuntimeConfigurationError,
            match="requires StatsForecast",
        ):
            ForecastOpsService(
                repository=repository,
                runtime_mode="production",
            )
        with pytest.raises(
            ForecastOpsRuntimeConfigurationError,
            match="requires StatsForecast",
        ):
            ForecastOpsService(
                repository=repository,
                engine="baseline",
                runtime_mode="production",
            )
    finally:
        engine.close()


def test_production_cannot_be_downgraded_by_local_runtime_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    engine, repository = _repository(tmp_path / "forecastops.sqlite3")
    try:
        with pytest.raises(ForecastOpsRuntimeConfigurationError):
            ForecastOpsService(
                repository=repository,
                runtime_mode="local",
            )
    finally:
        engine.close()
