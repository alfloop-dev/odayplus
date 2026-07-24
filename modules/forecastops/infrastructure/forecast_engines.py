from __future__ import annotations

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from modules.forecastops.domain.forecasting import (
    FORECAST_HORIZON_WEEKS,
    ForecastBand,
    ForecastEngine,
    ForecastEngineError,
    ForecastEngineInputError,
    ForecastEngineResult,
    ForecastEngineUnavailableError,
    ForecastInput,
)

_MAX_HORIZON_DAYS = max(FORECAST_HORIZON_WEEKS) * 7
_BASELINE_NAMES = {"baseline", "heuristic", "legacy"}


def create_forecast_engine(
    engine_name: str,
    *,
    model_name: str | None = None,
    options: dict[str, Any] | None = None,
) -> ForecastEngine | None:
    """Create an explicitly selected engine without importing optional OSS packages."""

    normalized = engine_name.strip().lower().replace("-", "_")
    configuration = dict(options or {})
    if normalized in _BASELINE_NAMES:
        return None
    if normalized == "statsforecast":
        return StatsForecastAdapter(model_name=model_name or "seasonal_naive", **configuration)
    if normalized == "mlforecast":
        return MLForecastSklearnAdapter(
            model_name=model_name or "hist_gradient_boosting", **configuration
        )
    raise ForecastEngineError(
        f"unknown forecast engine {engine_name!r}; expected baseline, statsforecast, or mlforecast"
    )


class StatsForecastAdapter:
    """StatsForecast fit/predict adapter with native central-80 intervals."""

    engine_name = "statsforecast"
    supported_models = ("seasonal_naive", "auto_arima", "auto_ets")

    def __init__(
        self,
        *,
        model_name: str = "seasonal_naive",
        season_length: int = 7,
        frequency: str = "D",
        interval_level: int = 80,
    ) -> None:
        normalized = model_name.strip().lower().replace("-", "_")
        aliases = {
            "seasonalnaive": "seasonal_naive",
            "autoarima": "auto_arima",
            "autoets": "auto_ets",
        }
        self.model_name = aliases.get(normalized, normalized)
        if self.model_name not in self.supported_models:
            supported = ", ".join(self.supported_models)
            raise ForecastEngineError(
                f"unsupported StatsForecast model {model_name!r}; expected one of {supported}"
            )
        if season_length < 1:
            raise ForecastEngineError("StatsForecast season_length must be positive")
        if interval_level != 80:
            raise ForecastEngineError("ForecastOps P10/P90 requires a central-80 interval")
        self.season_length = season_length
        self.frequency = frequency
        self.interval_level = interval_level
        self._runtime: Any | None = None
        self._store_id: str | None = None
        self._history_count = 0

    def fit(self, forecast_input: ForecastInput) -> StatsForecastAdapter:
        _validate_daily_history(
            forecast_input,
            minimum=max(self.season_length * 2, 14),
            engine_name=self.engine_name,
        )
        statsforecast = _require_module("statsforecast", self.engine_name)
        models = _require_module("statsforecast.models", self.engine_name)
        pandas = _require_module("pandas", self.engine_name)

        model_factories = {
            "seasonal_naive": lambda: models.SeasonalNaive(season_length=self.season_length),
            "auto_arima": lambda: models.AutoARIMA(season_length=self.season_length),
            "auto_ets": lambda: models.AutoETS(season_length=self.season_length),
        }
        runtime = statsforecast.StatsForecast(
            models=[model_factories[self.model_name]()],
            freq=self.frequency,
            n_jobs=1,
        )
        try:
            runtime.fit(_history_frame(forecast_input, pandas))
        except Exception as exc:
            raise ForecastEngineError(
                f"StatsForecast {self.model_name} fit failed for {forecast_input.store_id}: {exc}"
            ) from exc
        self._runtime = runtime
        self._store_id = forecast_input.store_id
        self._history_count = len(forecast_input.observations)
        return self

    def predict(self) -> ForecastEngineResult:
        if self._runtime is None or self._store_id is None:
            raise ForecastEngineError("StatsForecast adapter must be fitted before predict")
        try:
            frame = self._runtime.predict(
                h=_MAX_HORIZON_DAYS,
                level=[self.interval_level],
            )
            point_column = _statsforecast_point_column(frame)
            lower_column = f"{point_column}-lo-{self.interval_level}"
            upper_column = f"{point_column}-hi-{self.interval_level}"
            missing = {
                lower_column,
                upper_column,
            } - set(frame.columns)
            if missing:
                raise ForecastEngineError(
                    "StatsForecast interval columns are missing: " + ", ".join(sorted(missing))
                )
            bands = _bands_from_frame(
                frame,
                point_columns=(lower_column, point_column, upper_column),
            )
        except ForecastEngineError:
            raise
        except Exception as exc:
            raise ForecastEngineError(
                f"StatsForecast {self.model_name} predict failed for {self._store_id}: {exc}"
            ) from exc

        library_version = _package_version("statsforecast")
        return ForecastEngineResult(
            bands=bands,
            engine_name=self.engine_name,
            model_name=self.model_name,
            model_version=f"statsforecast-{library_version}:{self.model_name}",
            metadata={
                "library": "statsforecast",
                "library_version": library_version,
                "model": self.model_name,
                "frequency": self.frequency,
                "season_length": self.season_length,
                "history_count": self._history_count,
                "interval_method": "native_central_80",
                "interval_level": self.interval_level,
                "horizon_days": _horizon_days(),
            },
        )

    def fit_predict(self, forecast_input: ForecastInput) -> ForecastEngineResult:
        return self.fit(forecast_input).predict()


class MLForecastSklearnAdapter:
    """MLForecast challenger using three sklearn quantile regressors."""

    engine_name = "mlforecast"
    supported_models = ("hist_gradient_boosting",)

    def __init__(
        self,
        *,
        model_name: str = "hist_gradient_boosting",
        frequency: str = "D",
        lags: tuple[int, ...] = (1, 7, 14),
        date_features: tuple[str, ...] = ("dayofweek", "month"),
        random_state: int = 17,
        max_iter: int = 100,
    ) -> None:
        normalized = model_name.strip().lower().replace("-", "_")
        aliases = {
            "histgradientboosting": "hist_gradient_boosting",
            "hist_gradient_boosting_regressor": "hist_gradient_boosting",
        }
        self.model_name = aliases.get(normalized, normalized)
        if self.model_name not in self.supported_models:
            supported = ", ".join(self.supported_models)
            raise ForecastEngineError(
                f"unsupported MLForecast model {model_name!r}; expected one of {supported}"
            )
        if not lags or min(lags) < 1:
            raise ForecastEngineError("MLForecast lags must contain positive integers")
        self.frequency = frequency
        self.lags = tuple(sorted(set(lags)))
        self.date_features = date_features
        self.random_state = random_state
        self.max_iter = max_iter
        self._runtime: Any | None = None
        self._store_id: str | None = None
        self._history_count = 0

    def fit(self, forecast_input: ForecastInput) -> MLForecastSklearnAdapter:
        _validate_daily_history(
            forecast_input,
            minimum=max(28, max(self.lags) + 2),
            engine_name=self.engine_name,
        )
        mlforecast = _require_module("mlforecast", self.engine_name)
        sklearn_ensemble = _require_module("sklearn.ensemble", self.engine_name)
        pandas = _require_module("pandas", self.engine_name)

        models = {
            label: sklearn_ensemble.HistGradientBoostingRegressor(
                loss="quantile",
                quantile=quantile,
                learning_rate=0.05,
                max_iter=self.max_iter,
                max_leaf_nodes=15,
                min_samples_leaf=5,
                random_state=self.random_state,
            )
            for label, quantile in (("p10", 0.10), ("p50", 0.50), ("p90", 0.90))
        }
        runtime = mlforecast.MLForecast(
            models=models,
            freq=self.frequency,
            lags=list(self.lags),
            date_features=list(self.date_features),
        )
        try:
            runtime.fit(_history_frame(forecast_input, pandas))
        except Exception as exc:
            raise ForecastEngineError(
                f"MLForecast {self.model_name} fit failed for {forecast_input.store_id}: {exc}"
            ) from exc
        self._runtime = runtime
        self._store_id = forecast_input.store_id
        self._history_count = len(forecast_input.observations)
        return self

    def predict(self) -> ForecastEngineResult:
        if self._runtime is None or self._store_id is None:
            raise ForecastEngineError("MLForecast adapter must be fitted before predict")
        try:
            frame = self._runtime.predict(h=_MAX_HORIZON_DAYS)
            missing = {"p10", "p50", "p90"} - set(frame.columns)
            if missing:
                raise ForecastEngineError(
                    "MLForecast quantile columns are missing: " + ", ".join(sorted(missing))
                )
            bands = _bands_from_frame(
                frame,
                point_columns=("p10", "p50", "p90"),
            )
        except ForecastEngineError:
            raise
        except Exception as exc:
            raise ForecastEngineError(
                f"MLForecast {self.model_name} predict failed for {self._store_id}: {exc}"
            ) from exc

        mlforecast_version = _package_version("mlforecast")
        sklearn_version = _package_version("scikit-learn")
        return ForecastEngineResult(
            bands=bands,
            engine_name=self.engine_name,
            model_name=self.model_name,
            model_version=f"mlforecast-{mlforecast_version}:{self.model_name}",
            metadata={
                "library": "mlforecast",
                "library_version": mlforecast_version,
                "estimator_library": "scikit-learn",
                "estimator_library_version": sklearn_version,
                "estimator": "HistGradientBoostingRegressor",
                "objective": "quantile",
                "quantiles": [0.10, 0.50, 0.90],
                "frequency": self.frequency,
                "lags": list(self.lags),
                "date_features": list(self.date_features),
                "history_count": self._history_count,
                "interval_method": "direct_quantile_regression",
                "horizon_days": _horizon_days(),
                "quantile_crossing_policy": "ordered_at_domain_boundary",
            },
        )

    def fit_predict(self, forecast_input: ForecastInput) -> ForecastEngineResult:
        return self.fit(forecast_input).predict()


def _require_module(module_name: str, engine_name: str) -> Any:
    try:
        return import_module(module_name)
    except (ModuleNotFoundError, ImportError) as exc:
        package_name = module_name.split(".", maxsplit=1)[0]
        raise ForecastEngineUnavailableError(
            f"forecast engine {engine_name!r} requires optional package {package_name!r}; "
            "install the approved OSS runtime before selecting this engine"
        ) from exc


def _package_version(package_name: str) -> str:
    try:
        return version(package_name)
    except PackageNotFoundError:
        return "unknown"


def _history_frame(forecast_input: ForecastInput, pandas: Any) -> Any:
    return pandas.DataFrame(
        {
            "unique_id": [forecast_input.store_id] * len(forecast_input.observations),
            "ds": pandas.to_datetime(
                [observation.business_date for observation in forecast_input.observations]
            ),
            "y": [float(observation.actual_revenue) for observation in forecast_input.observations],
        }
    )


def _validate_daily_history(
    forecast_input: ForecastInput,
    *,
    minimum: int,
    engine_name: str,
) -> None:
    observations = forecast_input.observations
    if len(observations) < minimum:
        raise ForecastEngineInputError(
            f"{engine_name} requires at least {minimum} daily observations for "
            f"{forecast_input.store_id}; received {len(observations)}"
        )
    dates = [observation.business_date for observation in observations]
    if dates != sorted(dates):
        raise ForecastEngineInputError(
            f"{engine_name} requires observations sorted by business_date"
        )
    if len(set(dates)) != len(dates):
        raise ForecastEngineInputError(f"{engine_name} requires one observation per business_date")
    for previous, current in zip(dates, dates[1:], strict=False):
        if (current - previous).days != 1:
            raise ForecastEngineInputError(
                f"{engine_name} requires contiguous daily history; gap between "
                f"{previous.isoformat()} and {current.isoformat()}"
            )


def _statsforecast_point_column(frame: Any) -> str:
    candidates = [
        column
        for column in frame.columns
        if column not in {"unique_id", "ds"} and "-lo-" not in column and "-hi-" not in column
    ]
    if len(candidates) != 1:
        raise ForecastEngineError(
            "StatsForecast returned an ambiguous point forecast schema: "
            + ", ".join(str(column) for column in frame.columns)
        )
    return str(candidates[0])


def _bands_from_frame(
    frame: Any,
    *,
    point_columns: tuple[str, str, str],
) -> dict[int, ForecastBand]:
    if len(frame) < _MAX_HORIZON_DAYS:
        raise ForecastEngineError(
            f"forecast engine returned {len(frame)} days; expected {_MAX_HORIZON_DAYS}"
        )
    bands: dict[int, ForecastBand] = {}
    for weeks in FORECAST_HORIZON_WEEKS:
        row = frame.iloc[(weeks * 7) - 1]
        p10, p50, p90 = sorted(max(0.0, float(row[column])) for column in point_columns)
        bands[weeks] = ForecastBand(p10=p10, p50=p50, p90=p90)
    return bands


def _horizon_days() -> dict[str, int]:
    return {f"w{weeks}": weeks * 7 for weeks in FORECAST_HORIZON_WEEKS}


__all__ = [
    "MLForecastSklearnAdapter",
    "StatsForecastAdapter",
    "create_forecast_engine",
]
