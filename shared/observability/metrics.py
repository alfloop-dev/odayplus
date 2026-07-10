"""Metrics registry and the platform metric catalog.

Source baseline: ODP-SD-11 §5 (Metrics 設計) — technical, data/model and
business metrics. ODP-AC-SD11-002 / ODP-R7-001 acceptance requires that the
catalog cover, at minimum, latency / error / job / data / model / business
KPIs.

Dependency-free: a counter/gauge/histogram registry that mirrors the
Prometheus/OpenTelemetry data model closely enough to swap an exporter in
later without changing instrumentation call sites.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import TracebackType
from typing import Any


class MetricType(StrEnum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


class MetricCategory(StrEnum):
    # Categories mapped to the ODP-R7-001 acceptance keywords.
    LATENCY = "latency"
    ERROR = "error"
    TRAFFIC = "traffic"
    JOB = "job"
    QUEUE = "queue"
    DATA = "data"
    MODEL = "model"
    BUSINESS = "business"
    AUDIT = "audit"


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    type: MetricType
    category: MetricCategory
    description: str
    labels: tuple[str, ...] = ()
    unit: str = ""


def _label_key(labels: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


@dataclass
class _Series:
    definition: MetricDefinition
    value: float = 0.0
    count: int = 0
    sum: float = 0.0
    buckets: list[float] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        data: dict[str, Any] = {"type": self.definition.type.value}
        if self.definition.type is MetricType.HISTOGRAM:
            data.update(
                {
                    "count": self.count,
                    "sum": round(self.sum, 6),
                    "avg": round(self.sum / self.count, 6) if self.count else 0.0,
                    "min": round(min(self.buckets), 6) if self.buckets else 0.0,
                    "max": round(max(self.buckets), 6) if self.buckets else 0.0,
                }
            )
        else:
            data["value"] = round(self.value, 6)
        return data


class MetricsRegistry:
    """Holds metric definitions and their per-label-set series."""

    def __init__(self) -> None:
        self._definitions: dict[str, MetricDefinition] = {}
        self._series: dict[tuple[str, tuple[tuple[str, str], ...]], _Series] = {}

    def register(self, definition: MetricDefinition) -> MetricDefinition:
        existing = self._definitions.get(definition.name)
        if existing is not None and existing != definition:
            raise ValueError(f"metric {definition.name!r} already registered with a different definition")
        self._definitions[definition.name] = definition
        return definition

    def definition(self, name: str) -> MetricDefinition:
        try:
            return self._definitions[name]
        except KeyError:
            raise KeyError(f"metric {name!r} is not registered") from None

    def _resolve(self, name: str, labels: Mapping[str, str] | None) -> _Series:
        definition = self.definition(name)
        key = (name, _label_key(labels))
        series = self._series.get(key)
        if series is None:
            series = _Series(definition=definition)
            self._series[key] = series
        return series

    def increment(self, name: str, *, labels: Mapping[str, str] | None = None, amount: float = 1.0) -> None:
        series = self._resolve(name, labels)
        if series.definition.type is not MetricType.COUNTER:
            raise TypeError(f"{name!r} is not a counter")
        series.value += amount

    def set(self, name: str, value: float, *, labels: Mapping[str, str] | None = None) -> None:
        series = self._resolve(name, labels)
        if series.definition.type is not MetricType.GAUGE:
            raise TypeError(f"{name!r} is not a gauge")
        series.value = value

    def observe(self, name: str, value: float, *, labels: Mapping[str, str] | None = None) -> None:
        series = self._resolve(name, labels)
        if series.definition.type is not MetricType.HISTOGRAM:
            raise TypeError(f"{name!r} is not a histogram")
        series.count += 1
        series.sum += value
        series.buckets.append(value)

    def timer(
        self,
        name: str,
        *,
        labels: Mapping[str, str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> _Timer:
        return _Timer(self, name, labels, clock)

    def categories(self) -> set[MetricCategory]:
        return {definition.category for definition in self._definitions.values()}

    def names_by_category(self) -> dict[MetricCategory, list[str]]:
        result: dict[MetricCategory, list[str]] = {}
        for definition in self._definitions.values():
            result.setdefault(definition.category, []).append(definition.name)
        return result

    def snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for (name, label_key), series in self._series.items():
            entry = series.snapshot()
            entry["labels"] = dict(label_key)
            entry["category"] = series.definition.category.value
            out.setdefault(name, []).append(entry)
        return out


class _Timer:
    """Context manager that records elapsed time into a histogram metric."""

    def __init__(
        self,
        registry: MetricsRegistry,
        name: str,
        labels: Mapping[str, str] | None,
        clock: Callable[[], float] | None,
    ) -> None:
        import time

        self._registry = registry
        self._name = name
        self._labels = labels
        self._clock = clock or time.monotonic
        self._start = 0.0
        self.elapsed_ms = 0.0

    def __enter__(self) -> _Timer:
        self._start = self._clock()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.elapsed_ms = (self._clock() - self._start) * 1000.0
        self._registry.observe(self._name, self.elapsed_ms, labels=self._labels)


# --- Platform metric catalog (ODP-SD-11 §5) --------------------------------

C, G, H = MetricType.COUNTER, MetricType.GAUGE, MetricType.HISTOGRAM
Cat = MetricCategory

PLATFORM_METRICS: tuple[MetricDefinition, ...] = (
    # §5.1 Technical
    MetricDefinition("api_request_count", C, Cat.TRAFFIC, "API request volume", ("service", "route", "status")),
    MetricDefinition("api_error_count", C, Cat.ERROR, "API 4xx/5xx responses", ("service", "route", "status")),
    MetricDefinition("api_latency_ms", H, Cat.LATENCY, "API latency P50/P95/P99", ("service", "route"), "ms"),
    MetricDefinition("db_query_latency_ms", H, Cat.LATENCY, "DB query latency", ("query_group",), "ms"),
    MetricDefinition("job_duration_seconds", H, Cat.JOB, "Batch job duration", ("job_type", "status"), "s"),
    MetricDefinition("job_failure_count", C, Cat.JOB, "Batch job failures", ("job_type", "error_class")),
    MetricDefinition("event_consumer_lag", G, Cat.QUEUE, "Event backlog", ("topic", "subscription")),
    MetricDefinition("dlq_message_count", G, Cat.QUEUE, "Dead-letter queue depth", ("topic",)),
    MetricDefinition("external_connector_failure_count", C, Cat.ERROR, "External source failures", ("source",)),
    # §5.2 Data / Model
    MetricDefinition("data_freshness_hours", G, Cat.DATA, "Data freshness", ("source", "view"), "h"),
    MetricDefinition("data_quality_score", G, Cat.DATA, "Data quality score", ("dataset", "run")),
    MetricDefinition("feature_null_rate", G, Cat.DATA, "Feature null rate", ("feature", "view")),
    MetricDefinition("prediction_count", C, Cat.MODEL, "Prediction volume", ("model", "module")),
    MetricDefinition("model_error_metric", G, Cat.MODEL, "MAE/MAPE/RMSE", ("model", "horizon", "segment")),
    MetricDefinition("prediction_interval_coverage", G, Cat.MODEL, "P80/P90 coverage", ("model", "horizon")),
    MetricDefinition("drift_score", G, Cat.MODEL, "Feature/model drift", ("feature", "model")),
    MetricDefinition("model_alias_change_count", C, Cat.MODEL, "Release/rollback count", ("model",)),
    # §5.3 Business KPIs
    MetricDefinition("heatzone_topk_adoption_rate", G, Cat.BUSINESS, "HeatZone Top-K survey adoption"),
    MetricDefinition("listing_dedup_accuracy", G, Cat.BUSINESS, "Listing dedup accuracy"),
    MetricDefinition("sitescore_realization_rate", G, Cat.BUSINESS, "SiteScore M3/M6/M12 realization", ("horizon",)),
    MetricDefinition("forecast_alert_precision", G, Cat.BUSINESS, "Forecast alert precision/recall/lead time", ("metric",)),
    MetricDefinition("intervention_recovery_rate", G, Cat.BUSINESS, "Intervention 14/28-day recovery", ("window",)),
    MetricDefinition("price_hard_constraint_violation_count", C, Cat.BUSINESS, "Price hard-constraint violations"),
    MetricDefinition("adlift_incremental_gm", G, Cat.BUSINESS, "AdLift incremental GM / iROMI", ("metric",)),
    MetricDefinition("avm_interval_coverage", G, Cat.BUSINESS, "AVM interval coverage"),
    MetricDefinition("netplan_plan_adoption_rate", G, Cat.BUSINESS, "NetPlan plan adoption/outcome"),
    MetricDefinition("model_adoption_rate", G, Cat.BUSINESS, "Model adoption / override rate", ("kind",)),
    # §7 / §10 Audit trail and evidence export
    MetricDefinition("audit_event_record_count", C, Cat.AUDIT, "Audit events durably recorded", ("event_type", "action", "result")),
    MetricDefinition("audit_event_write_failure_count", C, Cat.ERROR, "Audit event write failures", ("event_type", "action", "error_class")),
    MetricDefinition("audit_event_pipeline_lag_seconds", H, Cat.AUDIT, "Audit pipeline write lag", ("sink", "event_type"), "s"),
    MetricDefinition("audit_event_replay_count", C, Cat.AUDIT, "Audit dead-letter replay attempts", ("result",)),
    MetricDefinition("audit_evidence_export_count", C, Cat.AUDIT, "Audit evidence exports", ("scope", "result")),
    MetricDefinition("audit_completeness_gap_count", C, Cat.AUDIT, "Missing required audit timeline events", ("rule", "resource", "missing_event_type")),
)


def default_registry() -> MetricsRegistry:
    """Return a registry seeded with the full platform metric catalog."""

    registry = MetricsRegistry()
    for definition in PLATFORM_METRICS:
        registry.register(definition)
    return registry
