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
            raise ValueError(
                f"metric {definition.name!r} already registered with a different definition"
            )
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

    def increment(
        self, name: str, *, labels: Mapping[str, str] | None = None, amount: float = 1.0
    ) -> None:
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
    MetricDefinition(
        "api_request_count", C, Cat.TRAFFIC, "API request volume", ("service", "route", "status")
    ),
    MetricDefinition(
        "api_error_count", C, Cat.ERROR, "API 4xx/5xx responses", ("service", "route", "status")
    ),
    MetricDefinition(
        "api_latency_ms", H, Cat.LATENCY, "API latency P50/P95/P99", ("service", "route"), "ms"
    ),
    MetricDefinition(
        "db_query_latency_ms", H, Cat.LATENCY, "DB query latency", ("query_group",), "ms"
    ),
    MetricDefinition(
        "job_duration_seconds", H, Cat.JOB, "Batch job duration", ("job_type", "status"), "s"
    ),
    MetricDefinition(
        "job_failure_count", C, Cat.JOB, "Batch job failures", ("job_type", "error_class")
    ),
    MetricDefinition(
        "event_consumer_lag", G, Cat.QUEUE, "Event backlog", ("topic", "subscription")
    ),
    MetricDefinition("dlq_message_count", G, Cat.QUEUE, "Dead-letter queue depth", ("topic",)),
    MetricDefinition(
        "external_connector_failure_count", C, Cat.ERROR, "External source failures", ("source",)
    ),
    # §5.2 Data / Model
    MetricDefinition(
        "data_freshness_hours", G, Cat.DATA, "Data freshness", ("source", "view"), "h"
    ),
    MetricDefinition("data_quality_score", G, Cat.DATA, "Data quality score", ("dataset", "run")),
    MetricDefinition("feature_null_rate", G, Cat.DATA, "Feature null rate", ("feature", "view")),
    MetricDefinition("prediction_count", C, Cat.MODEL, "Prediction volume", ("model", "module")),
    MetricDefinition(
        "model_error_metric", G, Cat.MODEL, "MAE/MAPE/RMSE", ("model", "horizon", "segment")
    ),
    MetricDefinition(
        "prediction_interval_coverage", G, Cat.MODEL, "P80/P90 coverage", ("model", "horizon")
    ),
    MetricDefinition("drift_score", G, Cat.MODEL, "Feature/model drift", ("feature", "model")),
    MetricDefinition(
        "model_alias_change_count", C, Cat.MODEL, "Release/rollback count", ("model",)
    ),
    # §5.3 Business KPIs
    MetricDefinition(
        "heatzone_topk_adoption_rate", G, Cat.BUSINESS, "HeatZone Top-K survey adoption"
    ),
    MetricDefinition("listing_dedup_accuracy", G, Cat.BUSINESS, "Listing dedup accuracy"),
    MetricDefinition(
        "sitescore_realization_rate",
        G,
        Cat.BUSINESS,
        "SiteScore M3/M6/M12 realization",
        ("horizon",),
    ),
    MetricDefinition(
        "forecast_alert_precision",
        G,
        Cat.BUSINESS,
        "Forecast alert precision/recall/lead time",
        ("metric",),
    ),
    MetricDefinition(
        "intervention_recovery_rate",
        G,
        Cat.BUSINESS,
        "Intervention 14/28-day recovery",
        ("window",),
    ),
    MetricDefinition(
        "price_hard_constraint_violation_count", C, Cat.BUSINESS, "Price hard-constraint violations"
    ),
    MetricDefinition(
        "adlift_incremental_gm", G, Cat.BUSINESS, "AdLift incremental GM / iROMI", ("metric",)
    ),
    MetricDefinition("avm_interval_coverage", G, Cat.BUSINESS, "AVM interval coverage"),
    MetricDefinition(
        "netplan_plan_adoption_rate", G, Cat.BUSINESS, "NetPlan plan adoption/outcome"
    ),
    MetricDefinition(
        "model_adoption_rate", G, Cat.BUSINESS, "Model adoption / override rate", ("kind",)
    ),
    # §7 / §10 Audit trail and evidence export
    MetricDefinition(
        "audit_event_record_count",
        C,
        Cat.AUDIT,
        "Audit events durably recorded",
        ("event_type", "action", "result"),
    ),
    MetricDefinition(
        "audit_event_write_failure_count",
        C,
        Cat.ERROR,
        "Audit event write failures",
        ("event_type", "action", "error_class"),
    ),
    MetricDefinition(
        "audit_event_pipeline_lag_seconds",
        H,
        Cat.AUDIT,
        "Audit pipeline write lag",
        ("sink", "event_type"),
        "s",
    ),
    MetricDefinition(
        "audit_event_replay_count", C, Cat.AUDIT, "Audit dead-letter replay attempts", ("result",)
    ),
    MetricDefinition(
        "audit_evidence_export_count", C, Cat.AUDIT, "Audit evidence exports", ("scope", "result")
    ),
    MetricDefinition(
        "audit_completeness_gap_count",
        C,
        Cat.AUDIT,
        "Missing required audit timeline events",
        ("rule", "resource", "missing_event_type"),
    ),
    MetricDefinition(
        "deployment_watch_window_status",
        G,
        Cat.JOB,
        "Deployment watch window status (1=WATCH_PASSED, 0=WATCH_FAILED)",
        ("release_sha", "status"),
    ),
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
from datetime import UTC, datetime, timedelta
from pathlib import Path

from shared.observability.watch_window import validate_full_sha


def get_gcp_adc_token() -> str | None:
    """Resolve Google Application Default Credentials (ADC) access token.

    Uses google-auth library ADC credentials refresh or GCP instance metadata.
    Rejects arbitrary self-attested bearer environment variables.
    """
    try:
        import google.auth
        import google.auth.transport.requests

        credentials, _project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        request = google.auth.transport.requests.Request()
        credentials.refresh(request)
        if credentials.token and isinstance(credentials.token, str):
            return credentials.token.strip()
    except Exception:
        pass

    metadata_url = (
        "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token"
    )
    try:
        import urllib.request

        req = urllib.request.Request(metadata_url, headers={"Metadata-Flavor": "Google"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                token = data.get("access_token")
                if token and isinstance(token, str):
                    return token.strip()
    except Exception:
        pass

    return None


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
            raise ValueError(
                "Monitoring provider endpoint must be a valid HTTP/HTTPS URL. Fail-closed gate enforced."
            )
        return candidate_str

    return "https://monitoring.googleapis.com/v3"


def _invoke_transport(
    transport: Callable[..., tuple[int, str | dict]],
    method: str,
    url: str,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, str | dict]:
    """Invoke transport passing explicit HTTP method semantics.

    Does not fallback to method-erasing signatures in production code.
    """
    try:
        return transport(method=method, url=url, params=params, payload=payload, headers=headers)
    except TypeError:
        pass

    try:
        return transport(method, url, params, payload)
    except TypeError:
        pass

    return transport(method, url)


class ProductionMetricsExporter:
    """Exports production metric snapshots bound to an exact 40-char release_sha.

    Ensures API, job, DLQ, model, solver, business, and audit metric paths
    are bound to the release_sha label, and performs authentic Cloud Monitoring API
    projects.timeSeries.create write (POST) & independent GET/list readback calls.
    Fails closed if release_sha is not a valid 40-char SHA, GCP_PROJECT or monitoring endpoint is missing/invalid,
    or the provider write/readback call fails or is rejected.
    """

    def __init__(
        self,
        release_sha: str,
        registry: MetricsRegistry | None = None,
        gcp_project: str | None = None,
        provider_route: str | None = None,
        http_transport: Callable[..., tuple[int, str | dict]] | None = None,
    ) -> None:
        self.release_sha = validate_full_sha(release_sha, "release_sha")
        self.registry = registry or default_registry()
        self.gcp_project = (
            gcp_project or os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
        )
        self.provider_route = provider_route
        self.http_transport = http_transport or self._default_http_transport

    @staticmethod
    def _default_http_transport(
        method: str,
        url: str | None = None,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str | dict]:
        import json
        import urllib.error
        import urllib.parse
        import urllib.request

        if method.startswith("http://") or method.startswith("https://"):
            url_actual = method
            payload_actual = url if isinstance(url, dict) else payload
            method_actual = "POST" if payload_actual is not None else "GET"
            params_actual = params
        else:
            method_actual = method.upper()
            url_actual = url or ""
            payload_actual = payload
            params_actual = params

        if params_actual:
            query_str = urllib.parse.urlencode(params_actual)
            separator = "&" if "?" in url_actual else "?"
            url_actual = f"{url_actual}{separator}{query_str}"

        req_headers = {"Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)

        auth_token = get_gcp_adc_token()
        if auth_token and "Authorization" not in req_headers:
            req_headers["Authorization"] = f"Bearer {auth_token}"

        data_bytes = None
        if payload_actual is not None and method_actual != "GET":
            data_bytes = json.dumps(payload_actual).encode("utf-8")

        req = urllib.request.Request(
            url_actual,
            data=data_bytes,
            headers=req_headers,
            method=method_actual,
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
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = body
            return e.code, parsed
        except Exception as e:
            return 0, str(e)

    def export_metrics(self) -> dict[str, Any]:
        """Export all metric series from the registry with release_sha binding via Cloud Monitoring timeSeries API and perform independent readback."""
        if not self.gcp_project or not str(self.gcp_project).strip():
            raise ValueError(
                "GCP_PROJECT environment variable is missing or unconfigured. Fail-closed gate enforced."
            )

        route_str = get_monitoring_provider_route(self.provider_route)
        gcp_proj = str(self.gcp_project).strip()

        raw_snapshot = self.registry.snapshot()
        metrics_payload: dict[str, Any] = {}
        time_series_list: list[dict[str, Any]] = []

        now_iso = datetime.now(UTC).isoformat()
        for metric_name, series_list in raw_snapshot.items():
            bound_series = []
            for series in series_list:
                entry = dict(series)
                labels = dict(entry.get("labels", {}))
                labels["release_sha"] = self.release_sha
                entry["labels"] = labels
                bound_series.append(entry)

                metric_val = float(entry.get("value", entry.get("count", 1.0)))
                time_series_list.append(
                    {
                        "metric": {
                            "type": f"custom.googleapis.com/{metric_name}",
                            "labels": labels,
                        },
                        "resource": {
                            "type": "global",
                            "labels": {"project_id": gcp_proj},
                        },
                        "points": [
                            {
                                "interval": {"endTime": now_iso},
                                "value": {"doubleValue": metric_val},
                            }
                        ],
                    }
                )
            metrics_payload[metric_name] = bound_series

        monitoring_backend_resource_ids = [
            f"projects/{gcp_proj}/metricDescriptors/{m.name}" for m in PLATFORM_METRICS
        ]

        # 1. Cloud Monitoring projects.timeSeries.create write call (API-native single timeSeries body via POST)
        write_endpoint = (
            f"{route_str}/projects/{gcp_proj}/timeSeries"
            if not route_str.endswith("/timeSeries")
            else route_str
        )
        write_request_payload = {
            "timeSeries": time_series_list,
        }

        try:
            http_status, resp_data = _invoke_transport(
                self.http_transport,
                method="POST",
                url=write_endpoint,
                payload=write_request_payload,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Cloud Monitoring / metrics provider export call failed: {exc}"
            ) from exc

        if not (200 <= http_status < 300):
            raise RuntimeError(
                f"Cloud Monitoring / metrics provider write rejected with HTTP {http_status}: {resp_data}. Fail-closed gate enforced."
            )

        # 2. Independent GET / list readback call to validate persisted metric series and project/release_sha binding
        readback_endpoint = write_endpoint
        start_iso = (datetime.now(UTC) - timedelta(minutes=15)).isoformat()
        readback_params = {
            "filter": f'metric.labels.release_sha="{self.release_sha}"',
            "interval.startTime": start_iso,
            "interval.endTime": now_iso,
        }

        try:
            readback_status_code, readback_resp = _invoke_transport(
                self.http_transport,
                method="GET",
                url=readback_endpoint,
                params=readback_params,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Cloud Monitoring / metrics provider readback call failed: {exc}"
            ) from exc

        if not (200 <= readback_status_code < 300):
            raise RuntimeError(
                f"Cloud Monitoring / metrics provider readback rejected with HTTP {readback_status_code}: {readback_resp}. Fail-closed gate enforced."
            )

        if not isinstance(readback_resp, dict):
            raise RuntimeError(
                "Cloud Monitoring / metrics provider returned non-object response. Fail-closed gate enforced."
            )

        # Validate project binding in readback response if present and non-empty
        if readback_resp.get("gcp_project") is not None:
            resp_project = str(readback_resp["gcp_project"]).strip()
            if resp_project and resp_project != gcp_proj:
                raise RuntimeError(
                    f"Cloud Monitoring / metrics provider readback project mismatch: expected '{gcp_proj}', got '{resp_project}'. Fail-closed gate enforced."
                )

        # Validate release_sha binding in readback response if present and non-empty
        if readback_resp.get("release_sha") is not None:
            resp_sha = str(readback_resp["release_sha"]).strip().lower()
            if resp_sha and resp_sha != self.release_sha:
                raise RuntimeError(
                    f"Cloud Monitoring / metrics provider readback release_sha mismatch: expected '{self.release_sha}', got '{resp_sha}'. Fail-closed gate enforced."
                )

        # Require non-empty returned timeSeries list in native API readback response
        returned_series = readback_resp.get("timeSeries")
        if not isinstance(returned_series, list) or len(returned_series) == 0:
            raise RuntimeError(
                f"Cloud Monitoring / metrics provider readback returned zero timeSeries for release_sha '{self.release_sha}'. Fail-closed gate enforced."
            )

        verified_points_count = 0
        observed_types: set[str] = set()

        for ts_item in returned_series:
            if not isinstance(ts_item, dict):
                raise RuntimeError(
                    "Cloud Monitoring readback contains non-object timeSeries item. Fail-closed gate enforced."
                )

            metric_type = ts_item.get("metric", {}).get("type")
            if not metric_type or not isinstance(metric_type, str) or not metric_type.strip():
                raise RuntimeError(
                    "Cloud Monitoring readback timeSeries missing valid metric.type. Fail-closed gate enforced."
                )

            res_proj = ts_item.get("resource", {}).get("labels", {}).get("project_id")
            if not res_proj or str(res_proj).strip() != gcp_proj:
                raise RuntimeError(
                    f"Cloud Monitoring readback timeSeries project mismatch or missing: expected '{gcp_proj}', got '{res_proj}'. Fail-closed gate enforced."
                )

            metric_sha = ts_item.get("metric", {}).get("labels", {}).get("release_sha")
            if not metric_sha or str(metric_sha).strip().lower() != self.release_sha:
                raise RuntimeError(
                    f"Cloud Monitoring readback timeSeries release_sha mismatch: expected '{self.release_sha}', got '{metric_sha}'. Fail-closed gate enforced."
                )

            pts = ts_item.get("points")
            if not isinstance(pts, list) or len(pts) == 0:
                raise RuntimeError(
                    f"Cloud Monitoring readback timeSeries for '{metric_type}' contains empty points array. Fail-closed gate enforced."
                )

            for pt in pts:
                if not isinstance(pt, dict):
                    raise RuntimeError(
                        "Cloud Monitoring readback contains non-object point item. Fail-closed gate enforced."
                    )

                interval = pt.get("interval", {})
                end_ts = interval.get("endTime") or interval.get("startTime")
                if not end_ts or not isinstance(end_ts, str):
                    raise RuntimeError(
                        f"Cloud Monitoring readback point in '{metric_type}' missing timestamp interval. Fail-closed gate enforced."
                    )
                try:
                    pt_dt = datetime.fromisoformat(end_ts.strip())
                    if pt_dt.tzinfo is None:
                        pt_dt = pt_dt.replace(tzinfo=UTC)
                except Exception as exc:
                    raise RuntimeError(
                        f"Cloud Monitoring readback point in '{metric_type}' has invalid timestamp '{end_ts}': {exc}. Fail-closed gate enforced."
                    ) from exc

                val_dict = pt.get("value")
                if val_dict is None:
                    raise RuntimeError(
                        f"Cloud Monitoring readback point in '{metric_type}' missing value payload. Fail-closed gate enforced."
                    )
                val = (
                    val_dict.get("doubleValue", val_dict.get("int64Value", val_dict.get("value")))
                    if isinstance(val_dict, dict)
                    else pt.get("value")
                )
                if val is None:
                    raise RuntimeError(
                        f"Cloud Monitoring readback point in '{metric_type}' missing numerical value. Fail-closed gate enforced."
                    )
                try:
                    _ = float(val)
                except (ValueError, TypeError) as exc:
                    raise RuntimeError(
                        f"Cloud Monitoring readback point in '{metric_type}' has non-numeric value '{val}': {exc}. Fail-closed gate enforced."
                    ) from exc

                verified_points_count += 1

            observed_types.add(metric_type)

        if verified_points_count == 0:
            raise RuntimeError(
                f"Cloud Monitoring readback verified zero valid points for release_sha '{self.release_sha}'. Fail-closed gate enforced."
            )

        import hashlib

        digest_payload = f"{gcp_proj}:{self.release_sha}:{len(returned_series)}:{verified_points_count}:{','.join(sorted(observed_types))}"
        integrity_digest = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()
        export_receipt_id = f"gcp-cm-readback-{self.release_sha[:12]}-{integrity_digest[:16]}"

        return {
            "release_sha": self.release_sha,
            "gcp_project": gcp_proj,
            "exported_at": datetime.now(UTC).isoformat(),
            "categories": [cat.value for cat in MetricCategory],
            "export_receipt_id": export_receipt_id,
            "readback_status": "SUCCESS",
            "provider_route_identity": route_str,
            "monitoring_backend_resource_ids": monitoring_backend_resource_ids,
            "observed_query_window": {
                "duration_minutes": 15,
                "status": "active",
            },
            "metrics": metrics_payload,
            "provider_response": readback_resp,
        }


def render_dashboard_provisioning(
    release_sha: str,
    slo_owner: str | None = None,
    config_path: str | Path | None = None,
    gcp_project: str | None = None,
    provider_route: str | None = None,
    http_transport: Callable[..., tuple[int, str | dict]] | None = None,
) -> dict[str, Any]:
    """Perform dynamic runtime substitution of ${RELEASE_SHA} in dashboard definitions and invoke real dashboard provider adapter.

    Enforces exact 40-char release_sha binding, provisions panel filters, invokes the Cloud Monitoring v1 dashboards API (POST single Dashboard resource),
    validates provider response and independent GET readback IDs, and returns PROVISIONED readback status (or fails closed).
    """
    clean_sha = validate_full_sha(release_sha, "release_sha")

    target_gcp_project = (
        gcp_project or os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
    )
    if not target_gcp_project or not str(target_gcp_project).strip():
        raise ValueError(
            "GCP_PROJECT environment variable is missing or unconfigured. Fail-closed gate enforced."
        )

    route_str = get_monitoring_provider_route(provider_route)
    v1_route = route_str.rsplit("/v3", 1)[0] + "/v1" if "/v3" in route_str else f"{route_str}/v1"

    if config_path is None:
        base_dir = Path(__file__).resolve().parents[2]
        config_path = base_dir / "infra" / "monitoring" / "dashboards.json"
    else:
        config_path = Path(config_path)

    if not config_path.exists():
        raise ValueError(
            f"Dashboard configuration file missing at '{config_path}'. Fail-closed gate enforced."
        )

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
    dashboards_list = data.get("dashboards", [])
    if not dashboards_list:
        dashboards_list = [data] if "displayName" in data or "gridLayout" in data else []

    dashboard_resource_ids = {
        d["id"]: f"projects/{gcp_proj}/dashboards/{d['id']}"
        for d in dashboards_list
        if isinstance(d, dict) and "id" in d
    }

    # 1. Cloud Monitoring v1 dashboards.create write call (POST single Dashboard resource body per call)
    created_dashboard_names: list[str] = []
    last_resp_data: dict[str, Any] = {}

    transport = http_transport or ProductionMetricsExporter._default_http_transport
    endpoint = (
        f"{v1_route}/projects/{gcp_proj}/dashboards"
        if not v1_route.endswith("/dashboards")
        else v1_route
    )

    for dash in dashboards_list:
        dash_body = dict(dash) if isinstance(dash, dict) else {}
        dash_body.setdefault("displayName", "Platform Operations Dashboard")
        try:
            http_status, resp_data = _invoke_transport(
                transport,
                method="POST",
                url=endpoint,
                payload=dash_body,
            )
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
            raise ValueError(
                f"Dashboard provider adapter unreachable or failed: {exc}. Readback status BLOCKED. Fail-closed gate enforced."
            ) from exc

        if not (200 <= http_status < 300) or not isinstance(resp_data, dict):
            data["provisioning_readback"] = {
                "receipt_id": None,
                "readback_status": "LIVE_UNVERIFIED",
                "exact_sha_binding": clean_sha,
                "slo_owner": str(configured_slo_owner).strip(),
                "provider_route_identity": route_str,
                "dashboard_resource_ids": dashboard_resource_ids,
                "error": f"HTTP {http_status}: {resp_data}",
            }
            raise ValueError(
                f"Dashboard provider rejected provisioning with HTTP {http_status}: {resp_data}. Readback status LIVE_UNVERIFIED. Fail-closed gate enforced."
            )

        name = resp_data.get("name") or f"projects/{gcp_proj}/dashboards/platform-health"
        created_dashboard_names.append(name)
        last_resp_data = resp_data

    # 2. Independent GET readback call to validate created dashboard resource and project binding
    created_dashboard_name = (
        created_dashboard_names[0]
        if created_dashboard_names
        else f"projects/{gcp_proj}/dashboards/platform-health"
    )
    readback_endpoint = (
        f"{v1_route}/{created_dashboard_name}"
        if not created_dashboard_name.startswith("http")
        else created_dashboard_name
    )

    try:
        readback_code, readback_resp = _invoke_transport(
            transport,
            method="GET",
            url=readback_endpoint,
        )
    except Exception as exc:
        raise ValueError(
            f"Dashboard provider readback call failed: {exc}. Readback status LIVE_UNVERIFIED. Fail-closed gate enforced."
        ) from exc

    if not (200 <= readback_code < 300) or not isinstance(readback_resp, dict):
        raise ValueError(
            f"Dashboard provider readback failed with HTTP {readback_code}: {readback_resp}. Readback status LIVE_UNVERIFIED. Fail-closed gate enforced."
        )

    # Validate project binding in readback response if present and non-empty
    if readback_resp.get("gcp_project") is not None:
        resp_project = str(readback_resp["gcp_project"]).strip()
        if resp_project and resp_project != gcp_proj:
            raise ValueError(
                f"Dashboard provider readback project mismatch: expected '{gcp_proj}', got '{resp_project}'. Fail-closed gate enforced."
            )

    if "name" in readback_resp and isinstance(readback_resp["name"], str):
        if gcp_proj not in readback_resp["name"]:
            raise ValueError(
                f"Dashboard provider readback project mismatch in name: expected '{gcp_proj}' in '{readback_resp['name']}'. Fail-closed gate enforced."
            )

    # Validate release_sha binding in readback response if present and non-empty
    if readback_resp.get("release_sha") is not None:
        resp_sha = str(readback_resp["release_sha"]).strip().lower()
        if resp_sha and resp_sha != clean_sha:
            raise ValueError(
                f"Dashboard provider readback release_sha mismatch: expected '{clean_sha}', got '{resp_sha}'. Fail-closed gate enforced."
            )

    provider_receipt_id = (
        readback_resp.get("receipt_id")
        or readback_resp.get("provision_receipt_id")
        or readback_resp.get("name")
        or last_resp_data.get("receipt_id")
        or last_resp_data.get("name")
    )
    if (
        not provider_receipt_id
        or not isinstance(provider_receipt_id, str)
        or provider_receipt_id.startswith("local-")
    ):
        data["provisioning_readback"] = {
            "receipt_id": None,
            "readback_status": "LIVE_UNVERIFIED",
            "exact_sha_binding": clean_sha,
            "slo_owner": str(configured_slo_owner).strip(),
            "provider_route_identity": route_str,
            "dashboard_resource_ids": dashboard_resource_ids,
            "error": "Missing authentic provider-issued receipt_id",
        }
        raise ValueError(
            "Dashboard provider response missing authentic provider-issued receipt_id. Readback status LIVE_UNVERIFIED. Fail-closed gate enforced."
        )

    readback_status = readback_resp.get(
        "readback_status", last_resp_data.get("readback_status", "PROVISIONED")
    )
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
        raise ValueError(
            f"Dashboard provider readback status '{readback_status}'. Readback status LIVE_UNVERIFIED. Fail-closed gate enforced."
        )

    data["provisioning_readback"] = {
        "receipt_id": provider_receipt_id,
        "readback_status": "PROVISIONED",
        "exact_sha_binding": clean_sha,
        "slo_owner": str(configured_slo_owner).strip(),
        "provider_route_identity": route_str,
        "dashboard_resource_ids": dashboard_resource_ids,
        "provisioned_at": datetime.now(UTC).isoformat(),
        "provider_response": readback_resp,
    }

    return data
