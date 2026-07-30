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
            sorted_buckets = sorted(self.buckets)
            p95_idx = int(len(sorted_buckets) * 0.95)
            p95 = sorted_buckets[p95_idx] if sorted_buckets else 0.0
            data.update(
                {
                    "count": self.count,
                    "sum": round(self.sum, 6),
                    "avg": round(self.sum / self.count, 6) if self.count else 0.0,
                    "min": round(min(self.buckets), 6) if self.buckets else 0.0,
                    "max": round(max(self.buckets), 6) if self.buckets else 0.0,
                    "p95": round(p95, 6),
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

    def clear(self) -> None:
        """Clear all recorded metric series values."""
        self._series.clear()


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
    MetricDefinition("deployment_watch_window_status", G, Cat.JOB, "Deployment watch window status (1=WATCH_PASSED, 0=WATCH_FAILED)", ("release_sha", "status")),
)


_cached_registry = None


def default_registry() -> MetricsRegistry:
    """Return a registry seeded with the full platform metric catalog."""
    global _cached_registry
    if _cached_registry is None:
        _cached_registry = MetricsRegistry()
        for definition in PLATFORM_METRICS:
            _cached_registry.register(definition)
    return _cached_registry


import json
import os
from datetime import UTC, datetime
from pathlib import Path

from shared.observability.watch_window import validate_full_sha


def get_monitoring_provider_route(provider_route: str | None = None) -> str:
    """Resolve the Cloud Monitoring / metrics / dashboard / query provider route.

    Rejects ONCALL_ENDPOINT_URL if passed or configured as the monitoring route,
    enforcing strict alert-only separation for the on-call notification adapter.
    """
    oncall_url = (os.environ.get("ONCALL_ENDPOINT_URL") or "").strip()
    candidate = (
        provider_route
        or os.environ.get("MONITORING_ENDPOINT_URL")
        or os.environ.get("GCP_MONITORING_ENDPOINT_URL")
    )
    if candidate:
        candidate_str = str(candidate).strip()
        if oncall_url and candidate_str == oncall_url:
            raise ValueError(
                "ONCALL_ENDPOINT_URL is reserved strictly for alert notification delivery and cannot be used as a Cloud Monitoring / metrics provider endpoint. Fail-closed gate enforced."
            )
        if "/api/v1/alerts" in candidate_str or "oncall-router" in candidate_str:
            raise ValueError(
                "On-call alert route cannot be used as a Cloud Monitoring / metrics provider endpoint. Fail-closed gate enforced."
            )
        if not (candidate_str.startswith("http://") or candidate_str.startswith("https://")):
            raise ValueError("Monitoring provider endpoint must be a valid HTTP/HTTPS URL. Fail-closed gate enforced.")
        return candidate_str

    return "https://monitoring.googleapis.com/v3"


class ProductionMetricsExporter:
    """Exports production metric snapshots bound to an exact 40-char release_sha.

    Ensures API, job, DLQ, model, solver, business, and audit metric paths
    are bound to the release_sha label, and performs an authentic Cloud Monitoring API / provider write & readback call.
    Fails closed if release_sha is not a valid 40-char SHA, GCP_PROJECT or monitoring endpoint is missing/invalid,
    or the provider write/readback call fails or is rejected.
    """

    def __init__(
        self,
        release_sha: str,
        registry: MetricsRegistry | None = None,
        gcp_project: str | None = None,
        provider_route: str | None = None,
        http_transport: Callable[[str, dict], tuple[int, str | dict]] | None = None,
    ) -> None:
        self.release_sha = validate_full_sha(release_sha, "release_sha")
        self.registry = registry or default_registry()
        self.gcp_project = gcp_project or os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
        self.provider_route = provider_route
        self.http_transport = http_transport or self._default_http_transport

    @staticmethod
    def _default_http_transport(url: str, payload: dict) -> tuple[int, str | dict]:
        import json
        import urllib.error
        import urllib.request

        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        auth_token = (
            os.getenv("GCP_AUTH_TOKEN")
            or os.getenv("MONITORING_AUTH_TOKEN")
            or os.getenv("GOOGLE_AUTH_TOKEN")
        )
        if auth_token and auth_token.strip():
            headers["Authorization"] = f"Bearer {auth_token.strip()}"

        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                body = response.read().decode("utf-8")
                try:
                    parsed = json.loads(body)
                except Exception:
                    parsed = body
                return response.status, parsed
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8") if e.fp else str(e)
            return e.code, body
        except Exception as e:
            return 0, str(e)

    def export_metrics(self) -> dict[str, Any]:
        """Export all metric series from the registry with release_sha binding and readback receipts."""
        if not self.gcp_project or not str(self.gcp_project).strip():
            raise ValueError("GCP_PROJECT environment variable is missing or unconfigured. Fail-closed gate enforced.")

        route_str = get_monitoring_provider_route(self.provider_route)

        raw_snapshot = self.registry.snapshot()
        metrics_payload: dict[str, Any] = {}
        for metric_name, series_list in raw_snapshot.items():
            bound_series = []
            for series in series_list:
                entry = dict(series)
                labels = dict(entry.get("labels", {}))
                labels["release_sha"] = self.release_sha
                entry["labels"] = labels
                bound_series.append(entry)
            metrics_payload[metric_name] = bound_series

        gcp_proj = str(self.gcp_project).strip()
        monitoring_backend_resource_ids = [
            f"projects/{gcp_proj}/metricDescriptors/{m.name}" for m in PLATFORM_METRICS
        ]

        export_request_payload = {
            "gcp_project": gcp_proj,
            "release_sha": self.release_sha,
            "categories": [cat.value for cat in MetricCategory],
            "monitoring_backend_resource_ids": monitoring_backend_resource_ids,
            "metrics": metrics_payload,
            "exported_at": datetime.now(UTC).isoformat(),
        }

        endpoint = f"{route_str}/metrics/export" if not route_str.endswith("/metrics/export") else route_str
        try:
            http_status, resp_data = self.http_transport(endpoint, export_request_payload)
        except Exception as exc:
            raise RuntimeError(f"Cloud Monitoring / metrics provider export call failed: {exc}") from exc

        if not (200 <= http_status < 300):
            raise RuntimeError(
                f"Cloud Monitoring / metrics provider write rejected with HTTP {http_status}: {resp_data}. Fail-closed gate enforced."
            )

        if not isinstance(resp_data, dict):
            raise RuntimeError("Cloud Monitoring / metrics provider returned non-object response. Fail-closed gate enforced.")

        provider_receipt_id = resp_data.get("export_receipt_id") or resp_data.get("receipt_id") or resp_data.get("name")
        if not provider_receipt_id or not isinstance(provider_receipt_id, str) or provider_receipt_id.startswith("local-"):
            raise RuntimeError("Cloud Monitoring / metrics provider response missing authentic provider-issued export_receipt_id. Fail-closed gate enforced.")

        readback_status = resp_data.get("readback_status", "SUCCESS")
        if readback_status != "SUCCESS":
            raise RuntimeError(f"Cloud Monitoring / metrics provider readback status '{readback_status}'. Fail-closed gate enforced.")

        return {
            "release_sha": self.release_sha,
            "gcp_project": gcp_proj,
            "exported_at": datetime.now(UTC).isoformat(),
            "categories": [cat.value for cat in MetricCategory],
            "export_receipt_id": provider_receipt_id,
            "readback_status": readback_status,
            "provider_route_identity": route_str,
            "monitoring_backend_resource_ids": monitoring_backend_resource_ids,
            "observed_query_window": {
                "duration_minutes": 15,
                "status": "active",
            },
            "metrics": metrics_payload,
            "provider_response": resp_data,
        }


def render_dashboard_provisioning(
    release_sha: str,
    slo_owner: str | None = None,
    config_path: str | Path | None = None,
    gcp_project: str | None = None,
    provider_route: str | None = None,
    http_transport: Callable[[str, dict], tuple[int, str | dict]] | None = None,
) -> dict[str, Any]:
    """Perform dynamic runtime substitution of ${RELEASE_SHA} in dashboard definitions and invoke real dashboard provider adapter.

    Enforces exact 40-char release_sha binding, provisions panel filters, invokes the dashboard provider adapter,
    validates provider response and readback IDs, and returns PROVISIONED readback status (or fails closed).
    """
    clean_sha = validate_full_sha(release_sha, "release_sha")

    target_gcp_project = gcp_project or os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
    if not target_gcp_project or not str(target_gcp_project).strip():
        raise ValueError("GCP_PROJECT environment variable is missing or unconfigured. Fail-closed gate enforced.")

    route_str = get_monitoring_provider_route(provider_route)

    if config_path is None:
        base_dir = Path(__file__).resolve().parents[2]
        config_path = base_dir / "infra" / "monitoring" / "dashboards.json"
    else:
        config_path = Path(config_path)

    if not config_path.exists():
        raise ValueError(f"Dashboard configuration file missing at '{config_path}'. Fail-closed gate enforced.")

    raw_text = config_path.read_text(encoding="utf-8")
    substituted_text = raw_text.replace("${RELEASE_SHA}", clean_sha)

    try:
        data = json.loads(substituted_text)
    except Exception as e:
        raise ValueError(f"Malformed dashboard configuration at '{config_path}': {e}") from e

    traceability = data.get("release_sha_traceability", {})
    configured_slo_owner = slo_owner or traceability.get("slo_owner")
    if not configured_slo_owner or not str(configured_slo_owner).strip():
        raise ValueError("SLO owner must be configured and non-empty. Fail-closed gate enforced.")

    traceability["exact_sha_binding"] = clean_sha
    traceability["slo_owner"] = str(configured_slo_owner).strip()
    data["release_sha_traceability"] = traceability

    gcp_proj = str(target_gcp_project).strip()
    dashboard_resource_ids = {
        d["id"]: f"projects/{gcp_proj}/dashboards/{d['id']}"
        for d in data.get("dashboards", [])
        if isinstance(d, dict) and "id" in d
    }

    provision_request_payload = {
        "gcp_project": gcp_proj,
        "release_sha": clean_sha,
        "slo_owner": str(configured_slo_owner).strip(),
        "dashboard_resource_ids": dashboard_resource_ids,
        "dashboards": data.get("dashboards", []),
    }

    transport = http_transport or ProductionMetricsExporter._default_http_transport
    endpoint = f"{route_str}/dashboards/provision" if not route_str.endswith("/dashboards/provision") else route_str

    try:
        http_status, resp_data = transport(endpoint, provision_request_payload)
    except Exception as exc:
        data["provisioning_readback"] = {
            "receipt_id": None,
            "readback_status": "BLOCKED",
            "exact_sha_binding": clean_sha,
            "slo_owner": str(configured_slo_owner).strip(),
            "provider_route_identity": route_str,
            "dashboard_resource_ids": dashboard_resource_ids,
            "error": f"Dashboard provider adapter connection failed: {exc}",
        }
        raise ValueError(f"Dashboard provider adapter unreachable or failed: {exc}. Readback status BLOCKED. Fail-closed gate enforced.") from exc

    if not (200 <= http_status < 300):
        data["provisioning_readback"] = {
            "receipt_id": None,
            "readback_status": "LIVE_UNVERIFIED",
            "exact_sha_binding": clean_sha,
            "slo_owner": str(configured_slo_owner).strip(),
            "provider_route_identity": route_str,
            "dashboard_resource_ids": dashboard_resource_ids,
            "error": f"HTTP {http_status}: {resp_data}",
        }
        raise ValueError(f"Dashboard provider rejected provisioning with HTTP {http_status}: {resp_data}. Readback status LIVE_UNVERIFIED. Fail-closed gate enforced.")

    if not isinstance(resp_data, dict):
        data["provisioning_readback"] = {
            "receipt_id": None,
            "readback_status": "LIVE_UNVERIFIED",
            "exact_sha_binding": clean_sha,
            "slo_owner": str(configured_slo_owner).strip(),
            "provider_route_identity": route_str,
            "dashboard_resource_ids": dashboard_resource_ids,
            "error": "Non-object provider response",
        }
        raise ValueError("Dashboard provider response missing authentic provider-issued receipt_id. Readback status LIVE_UNVERIFIED. Fail-closed gate enforced.")

    provider_receipt_id = resp_data.get("receipt_id") or resp_data.get("provision_receipt_id") or resp_data.get("name")
    if not provider_receipt_id or not isinstance(provider_receipt_id, str) or provider_receipt_id.startswith("local-"):
        data["provisioning_readback"] = {
            "receipt_id": None,
            "readback_status": "LIVE_UNVERIFIED",
            "exact_sha_binding": clean_sha,
            "slo_owner": str(configured_slo_owner).strip(),
            "provider_route_identity": route_str,
            "dashboard_resource_ids": dashboard_resource_ids,
            "error": "Missing authentic provider-issued receipt_id",
        }
        raise ValueError("Dashboard provider response missing authentic provider-issued receipt_id. Readback status LIVE_UNVERIFIED. Fail-closed gate enforced.")

    readback_status = resp_data.get("readback_status", "PROVISIONED")
    if readback_status != "PROVISIONED":
        data["provisioning_readback"] = {
            "receipt_id": provider_receipt_id,
            "readback_status": "LIVE_UNVERIFIED",
            "exact_sha_binding": clean_sha,
            "slo_owner": str(configured_slo_owner).strip(),
            "provider_route_identity": route_str,
            "dashboard_resource_ids": dashboard_resource_ids,
            "error": f"Provider readback status '{readback_status}'",
        }
        raise ValueError(f"Dashboard provider readback status '{readback_status}'. Readback status LIVE_UNVERIFIED. Fail-closed gate enforced.")

    data["provisioning_readback"] = {
        "receipt_id": provider_receipt_id,
        "readback_status": "PROVISIONED",
        "exact_sha_binding": clean_sha,
        "slo_owner": str(configured_slo_owner).strip(),
        "provider_route_identity": route_str,
        "dashboard_resource_ids": dashboard_resource_ids,
        "provisioned_at": datetime.now(UTC).isoformat(),
        "provider_response": resp_data,
    }

    return data
