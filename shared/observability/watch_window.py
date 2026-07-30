"""Deployment watch-window status metric emission and durable receipt artifact management."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shared.observability.metrics import MetricsRegistry, default_registry

DEFAULT_RECEIPT_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "evidence" / "watch_window_receipt.json"
)
FULL_SHA_REGEX = re.compile(r"^[0-9a-f]{40}$")


def validate_full_sha(sha: str, param_name: str = "release_sha") -> str:
    """Validate that sha is a full 40-character hexadecimal string."""
    if not isinstance(sha, str) or not sha:
        raise ValueError(f"{param_name} must be provided as a non-empty string.")
    normalized = sha.strip().lower()
    if not FULL_SHA_REGEX.match(normalized):
        raise ValueError(
            f"{param_name} must be an exact full 40-character hexadecimal string (got '{sha}'). Fail-closed gate enforced."
        )
    return normalized


def _parse_iso_utc(ts: datetime | str) -> datetime:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
    if isinstance(ts, str) and ts.strip():
        dt = datetime.fromisoformat(ts.strip())
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    raise ValueError(f"Invalid timestamp '{ts}'.")


ALLOWLISTED_INDEPENDENT_WATCH_METRICS = frozenset(
    {
        "custom.googleapis.com/api_request_count",
        "custom.googleapis.com/api_error_count",
        "custom.googleapis.com/api_latency_ms",
        "custom.googleapis.com/db_query_latency_ms",
        "custom.googleapis.com/job_duration_seconds",
        "custom.googleapis.com/job_failure_count",
        "custom.googleapis.com/event_consumer_lag",
        "custom.googleapis.com/dlq_message_count",
        "custom.googleapis.com/external_connector_failure_count",
        "custom.googleapis.com/data_freshness_hours",
        "custom.googleapis.com/data_quality_score",
        "custom.googleapis.com/prediction_count",
        "custom.googleapis.com/model_error_metric",
        "custom.googleapis.com/audit_event_record_count",
        "custom.googleapis.com/audit_event_write_failure_count",
    }
)


def record_deployment_watch_window_status(
    release_sha: str,
    status: int,
    *,
    start_time: datetime | str,
    end_time: datetime | str,
    observed_results: dict[str, Any] | None = None,
    registry: MetricsRegistry | None = None,
    receipt_path: str | Path | None = None,
    watch_window_minutes: int = 15,
    gcp_project: str | None = None,
    provider_route: str | None = None,
    query_transport: Callable[[str, dict], tuple[int, str | dict]] | None = None,
) -> dict[str, Any]:
    """Emit the deployment_watch_window_status gauge metric and persist a durable watch-window receipt.

    status: 1 for WATCH_PASSED, 0 for WATCH_FAILED.
    Requires exact 40-char release SHA, explicit status, and verifiable start/end timestamps covering >= 15 minutes.
    Performs an authentic monitoring query execution call bound to exact SHA and watch duration window.
    Rejects caller-self-attested success if monitoring query execution fails, is unverified, or returns error status.
    """
    clean_sha = validate_full_sha(release_sha, "release_sha")

    if status not in (0, 1):
        raise ValueError("status must be 1 (WATCH_PASSED) or 0 (WATCH_FAILED).")

    if start_time is None or end_time is None:
        raise ValueError(
            "start_time and end_time must be provided as verifiable timestamps for watch-window status recording. Default-pass or unobserved window rejected."
        )

    start_dt = _parse_iso_utc(start_time)
    end_dt = _parse_iso_utc(end_time)
    observed_seconds = (end_dt - start_dt).total_seconds()

    if observed_seconds < 900:
        raise ValueError(
            f"Observed watch duration ({observed_seconds:.1f}s) is less than the required 15-minute minimum (900s). Sub-15-minute receipts rejected."
        )

    if watch_window_minutes < 15:
        raise ValueError(
            f"watch_window_minutes ({watch_window_minutes}) must be at least 15 minutes. Sub-15-minute watch window rejected."
        )

    default_obs = {"error_count": 0, "health_check_pass": True, "telemetry_verified": True}
    res = observed_results or default_obs
    if status == 1 and (res.get("error_count", 0) > 0 or res.get("health_check_pass") is False):
        raise ValueError(
            "Contradictory watch results: status is WATCH_PASSED but observed_results report errors or health check failure."
        )

    target_gcp_project = (
        gcp_project or os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
    )
    if not target_gcp_project or not str(target_gcp_project).strip():
        raise ValueError(
            "GCP_PROJECT environment variable is missing or unconfigured. Fail-closed gate enforced."
        )

    from datetime import timedelta

    from shared.observability.metrics import (
        ProductionMetricsExporter,
        _invoke_transport,
        get_monitoring_provider_route,
    )

    route_str = get_monitoring_provider_route(provider_route)
    gcp_proj = str(target_gcp_project).strip()

    endpoint = (
        f"{route_str}/projects/{gcp_proj}/timeSeries"
        if not route_str.endswith("/timeSeries")
        else route_str
    )
    query_params = {
        "filter": f'metric.labels.release_sha="{clean_sha}" AND metric.type != "custom.googleapis.com/deployment_watch_window_status"',
        "interval.startTime": start_dt.isoformat(),
        "interval.endTime": end_dt.isoformat(),
    }

    transport = query_transport or ProductionMetricsExporter._default_http_transport

    try:
        http_status, query_resp = _invoke_transport(
            transport,
            method="GET",
            url=endpoint,
            params=query_params,
        )
    except Exception as exc:
        raise ValueError(
            f"Monitoring query execution transport failed: {exc}. Caller-self-attested success rejected. Fail-closed gate enforced."
        ) from exc

    if not (200 <= http_status < 300):
        raise ValueError(
            f"Monitoring query execution failed with HTTP {http_status}: {query_resp}. Caller-self-attested success rejected. Fail-closed gate enforced."
        )

    if not isinstance(query_resp, dict):
        raise ValueError(
            "Monitoring query execution response missing object payload. Fail-closed gate enforced."
        )

    returned_series = query_resp.get("timeSeries")
    if not isinstance(returned_series, list) or len(returned_series) == 0:
        raise ValueError(
            f"Monitoring query readback returned zero timeSeries data for release_sha '{clean_sha}'. Caller-self-attested success rejected. Fail-closed gate enforced."
        )

    verified_points_count = 0
    observed_types: set[str] = set()
    point_timestamps: list[datetime] = []
    point_values: list[float] = []
    observed_errors = 0
    health_passed = True

    for ts_item in returned_series:
        if not isinstance(ts_item, dict):
            raise ValueError(
                "Monitoring query readback contains invalid timeSeries item. Fail-closed gate enforced."
            )

        metric_dict = ts_item.get("metric", {})
        metric_type = metric_dict.get("type")
        if not metric_type or not isinstance(metric_type, str) or not metric_type.strip():
            raise ValueError(
                "Monitoring query readback timeSeries missing valid metric.type. Fail-closed gate enforced."
            )

        if metric_type == "custom.googleapis.com/deployment_watch_window_status":
            raise ValueError(
                "Monitoring query readback returned deployment_watch_window_status metric type. "
                "Watch window must be bound to independent health/error/latency provider metrics, not circular status metric. Fail-closed gate enforced."
            )

        if metric_type not in ALLOWLISTED_INDEPENDENT_WATCH_METRICS:
            raise ValueError(
                f"Monitoring query readback returned un-allowlisted metric type '{metric_type}'. "
                "Watch window requires allowlisted independent health/error/latency signals. Fail-closed gate enforced."
            )

        res_proj = ts_item.get("resource", {}).get("labels", {}).get("project_id")
        if not res_proj or str(res_proj).strip() != gcp_proj:
            raise ValueError(
                f"Monitoring query readback project mismatch or missing: expected '{gcp_proj}', got '{res_proj}'. Fail-closed gate enforced."
            )

        metric_sha = metric_dict.get("labels", {}).get("release_sha")
        if not metric_sha or str(metric_sha).strip().lower() != clean_sha:
            raise ValueError(
                f"Monitoring query readback release_sha mismatch: expected '{clean_sha}', got '{metric_sha}'. Fail-closed gate enforced."
            )

        points = ts_item.get("points")
        if not isinstance(points, list) or len(points) == 0:
            raise ValueError(
                f"Monitoring query readback timeSeries for '{metric_type}' contains empty points array. Fail-closed gate enforced."
            )

        for pt in points:
            if not isinstance(pt, dict):
                raise ValueError(
                    "Monitoring query readback contains invalid point item. Fail-closed gate enforced."
                )

            interval = pt.get("interval", {})
            end_ts = interval.get("endTime") or interval.get("startTime")
            if not end_ts or not isinstance(end_ts, str):
                raise ValueError(
                    f"Monitoring query readback point in '{metric_type}' missing timestamp interval. Fail-closed gate enforced."
                )
            try:
                pt_dt = _parse_iso_utc(end_ts)
            except Exception as exc:
                raise ValueError(
                    f"Monitoring query readback point in '{metric_type}' has invalid timestamp '{end_ts}': {exc}. Fail-closed gate enforced."
                ) from exc

            if not (start_dt - timedelta(seconds=60) <= pt_dt <= end_dt + timedelta(seconds=60)):
                raise ValueError(
                    f"Monitoring query readback point timestamp '{end_ts}' lies outside requested watch window [{start_dt.isoformat()}, {end_dt.isoformat()}]. Fail-closed gate enforced."
                )

            point_timestamps.append(pt_dt)

            val_dict = pt.get("value", {})
            val = (
                val_dict.get("doubleValue", val_dict.get("int64Value", val_dict.get("value")))
                if isinstance(val_dict, dict)
                else pt.get("value")
            )
            if val is None:
                raise ValueError(
                    f"Monitoring query readback point in '{metric_type}' missing numerical value. Fail-closed gate enforced."
                )
            try:
                val_float = float(val)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"Monitoring query readback point in '{metric_type}' has non-numeric value '{val}': {exc}. Fail-closed gate enforced."
                ) from exc

            point_values.append(val_float)

            if "error" in metric_type or "failure" in metric_type or "dlq" in metric_type:
                if val_float > 0:
                    observed_errors += int(val_float)
                    health_passed = False
            elif "latency" in metric_type:
                if val_float > 5000.0:  # Latency P95/max threshold 5000ms
                    health_passed = False
            elif status == 1 and val_float <= 0:
                health_passed = False

            verified_points_count += 1

        observed_types.add(metric_type)

    if verified_points_count == 0:
        raise ValueError(
            f"Monitoring query readback verified zero valid points for release_sha '{clean_sha}'. Caller-self-attested success rejected. Fail-closed gate enforced."
        )

    if len(point_timestamps) < 2:
        raise ValueError(
            f"Monitoring query readback requires multiple timestamped points across watch window (got {len(point_timestamps)} points, window_coverage_seconds=0.0). Single point cannot prove watch window. Fail-closed gate enforced."
        )

    min_pt_ts = min(point_timestamps)
    max_pt_ts = max(point_timestamps)
    point_coverage_seconds = (max_pt_ts - min_pt_ts).total_seconds()

    if observed_seconds >= 900 and point_coverage_seconds < 840:
        raise ValueError(
            f"Monitoring query readback point timestamps span only {point_coverage_seconds:.1f}s, failing to cover the required 15-minute ({observed_seconds:.1f}s) watch window. Fail-closed gate enforced."
        )

    res = {
        "error_count": observed_errors,
        "health_check_pass": health_passed,
        "telemetry_verified": True,
        "observed_metric_types": sorted(list(observed_types)),
        "verified_points_count": verified_points_count,
        "window_coverage_seconds": round(point_coverage_seconds, 2),
    }

    if observed_results and isinstance(observed_results, dict):
        if (
            observed_results.get("error_count", 0) > 0
            or observed_results.get("health_check_pass") is False
        ):
            if status == 1:
                raise ValueError(
                    "Contradictory watch results: caller observed_results report errors or health check failure."
                )

    if status == 1 and (observed_errors > 0 or not health_passed):
        raise ValueError(
            "Monitoring query readback metric values indicate health/error failure, contradicting requested WATCH_PASSED status. Fail-closed gate enforced."
        )

    import hashlib

    sorted_metric_types = sorted(list(observed_types))
    sorted_iso_timestamps = [pt.isoformat() for pt in sorted(point_timestamps)]
    canonical_receipt_data = {
        "release_sha": clean_sha,
        "gcp_project": gcp_proj,
        "status": "WATCH_PASSED" if status == 1 else "WATCH_FAILED",
        "status_code": status,
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(),
        "observed_duration_seconds": round(observed_seconds, 2),
        "watch_window_minutes": watch_window_minutes,
        "verified_points_count": verified_points_count,
        "observed_metric_types": sorted_metric_types,
        "point_timestamps": sorted_iso_timestamps,
        "point_values": [round(v, 6) for v in point_values],
        "query_params": query_params,
        "observed_results": res,
    }
    canonical_receipt_json = json.dumps(canonical_receipt_data, sort_keys=True)
    canonical_receipt_hash = hashlib.sha256(canonical_receipt_json.encode("utf-8")).hexdigest()

    monitoring_query_execution = {
        "readback_status": "WATCH_PASSED" if status == 1 else "WATCH_FAILED",
        "readback_verified": True,
        "observed_series_count": len(returned_series),
        "verified_points_count": verified_points_count,
        "observed_metric_types": sorted_metric_types,
        "observed_window_minutes": watch_window_minutes,
        "observed_duration_seconds": round(observed_seconds, 2),
        "query_start_time": start_dt.isoformat(),
        "query_end_time": end_dt.isoformat(),
        "executed_at": datetime.now(UTC).isoformat(),
        "provider_query_response": query_resp,
        "receipt_hash": canonical_receipt_hash,
    }

    status_str = "WATCH_PASSED" if status == 1 else "WATCH_FAILED"
    reg = registry or default_registry()
    reg.set(
        "deployment_watch_window_status",
        float(status),
        labels={"release_sha": clean_sha, "status": status_str},
    )

    out_path = Path(receipt_path or DEFAULT_RECEIPT_PATH)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    receipt = {
        "release_sha": clean_sha,
        "gcp_project": gcp_proj,
        "status": status_str,
        "status_code": status,
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(),
        "observed_duration_seconds": round(observed_seconds, 2),
        "watch_window_minutes": watch_window_minutes,
        "verified_points_count": verified_points_count,
        "observed_metric_types": sorted_metric_types,
        "point_timestamps": sorted_iso_timestamps,
        "point_values": [round(v, 6) for v in point_values],
        "query_params": query_params,
        "monitoring_query_execution": monitoring_query_execution,
        "observed_results": res,
        "recorded_at": datetime.now(UTC).isoformat(),
        "metric_name": "deployment_watch_window_status",
        "canonical_receipt_hash": canonical_receipt_hash,
        "receipt_hash": canonical_receipt_hash,
    }
    out_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def verify_watch_window_receipt(
    expected_release_sha: str,
    receipt_path: str | Path | None = None,
) -> dict[str, Any]:
    """Verify that a durable watch-window receipt exists, matches expected release SHA, carries valid monitoring query readback, and passes canonical SHA-256 integrity digest."""
    clean_expected = validate_full_sha(expected_release_sha, "expected_release_sha")

    out_path = Path(receipt_path or DEFAULT_RECEIPT_PATH)
    if not out_path.exists():
        raise FileNotFoundError(f"Watch-window receipt artifact absent at '{out_path}'.")

    try:
        receipt = json.loads(out_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"Watch-window receipt artifact at '{out_path}' is malformed: {e}") from e

    sha = receipt.get("release_sha")
    clean_sha = validate_full_sha(sha, "release_sha in receipt") if sha else ""
    if clean_sha != clean_expected:
        raise ValueError(
            f"Release SHA mismatch in watch-window receipt: expected '{clean_expected}', got '{sha}'."
        )

    gcp_proj = receipt.get("gcp_project")
    if not gcp_proj or not isinstance(gcp_proj, str) or not gcp_proj.strip():
        raise ValueError("Watch-window receipt missing valid non-empty gcp_project.")

    status_code = receipt.get("status_code")
    status = receipt.get("status")

    if status not in {"WATCH_PASSED", "WATCH_FAILED"}:
        raise ValueError(f"Invalid watch-window status '{status}' in receipt.")

    if status != "WATCH_PASSED" or status_code != 1:
        raise ValueError(
            f"Watch-window verification failed or contradictory: status='{status}' (code {status_code})."
        )

    watch_window_minutes = receipt.get("watch_window_minutes")
    if not isinstance(watch_window_minutes, int) or watch_window_minutes < 15:
        raise ValueError("watch_window_minutes is invalid or non-positive or sub-15-minute (< 15).")

    start_time_raw = receipt.get("start_time")
    end_time_raw = receipt.get("end_time")
    if not start_time_raw or not end_time_raw:
        raise ValueError("Watch-window receipt missing verifiable start_time or end_time.")

    start_dt = _parse_iso_utc(start_time_raw)
    end_dt = _parse_iso_utc(end_time_raw)
    duration_seconds = (end_dt - start_dt).total_seconds()
    if duration_seconds < 900:
        raise ValueError(
            f"Sub-15-minute watch duration in receipt ({duration_seconds:.1f}s < 900s). Fail-closed gate enforced."
        )

    query_exec = receipt.get("monitoring_query_execution")
    if not query_exec or not isinstance(query_exec, dict):
        raise ValueError(
            "Watch-window receipt missing valid monitoring_query_execution readback. Fail-closed gate enforced."
        )

    if query_exec.get("readback_verified") is not True:
        raise ValueError(
            "Watch-window receipt monitoring query readback is unverified. Fail-closed gate enforced."
        )

    verified_points_count = receipt.get("verified_points_count") or query_exec.get(
        "verified_points_count"
    )
    if not isinstance(verified_points_count, int) or verified_points_count < 2:
        raise ValueError(
            "Watch-window receipt verified_points_count must be at least 2 for window coverage. Fail-closed gate enforced."
        )

    observed_metric_types = receipt.get("observed_metric_types") or query_exec.get(
        "observed_metric_types"
    )
    if not isinstance(observed_metric_types, list) or len(observed_metric_types) == 0:
        raise ValueError(
            "Watch-window receipt missing observed_metric_types list. Fail-closed gate enforced."
        )

    for m_type in observed_metric_types:
        if m_type == "custom.googleapis.com/deployment_watch_window_status":
            raise ValueError(
                "Watch-window receipt relies on circular status metric rather than independent provider metrics. Fail-closed gate enforced."
            )
        if m_type not in ALLOWLISTED_INDEPENDENT_WATCH_METRICS:
            raise ValueError(
                f"Watch-window receipt contains un-allowlisted metric type '{m_type}'. Fail-closed gate enforced."
            )

    observed_results = receipt.get("observed_results", {})
    if not isinstance(observed_results, dict):
        raise ValueError("Watch-window receipt missing valid observed_results object.")

    if (
        observed_results.get("error_count", 0) > 0
        or observed_results.get("health_check_pass") is False
    ):
        raise ValueError(
            "Watch-window verification failed or contradictory in observed_results."
        )

    cov_sec = observed_results.get("window_coverage_seconds", 0)
    if cov_sec < 840:
        raise ValueError(
            f"Watch-window receipt window_coverage_seconds ({cov_sec}) is less than required 840s. Fail-closed gate enforced."
        )

    # Re-validate stored provider_query_response metrics and points
    provider_resp = query_exec.get("provider_query_response", {})
    if isinstance(provider_resp, dict):
        ts_list = provider_resp.get("timeSeries", [])
        if not isinstance(ts_list, list) or len(ts_list) == 0:
            raise ValueError("Watch-window receipt provider_query_response contains no timeSeries.")
        for ts_item in ts_list:
            if isinstance(ts_item, dict):
                m_type = ts_item.get("metric", {}).get("type")
                if m_type == "custom.googleapis.com/deployment_watch_window_status":
                    raise ValueError(
                        "Stored provider proof contains circular deployment_watch_window_status metric. Tampered receipt rejected. Fail-closed gate enforced."
                    )
                if m_type not in ALLOWLISTED_INDEPENDENT_WATCH_METRICS:
                    raise ValueError(
                        f"Stored provider proof contains un-allowlisted metric type '{m_type}'. Tampered receipt rejected. Fail-closed gate enforced."
                    )

    point_timestamps = receipt.get("point_timestamps", [])
    point_values = receipt.get("point_values", [])
    query_params = receipt.get("query_params", {})

    import hashlib

    canonical_receipt_data = {
        "release_sha": clean_sha,
        "gcp_project": str(gcp_proj).strip(),
        "status": status,
        "status_code": status_code,
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(),
        "observed_duration_seconds": round(duration_seconds, 2),
        "watch_window_minutes": watch_window_minutes,
        "verified_points_count": verified_points_count,
        "observed_metric_types": sorted(observed_metric_types),
        "point_timestamps": point_timestamps,
        "point_values": point_values,
        "query_params": query_params,
        "observed_results": observed_results,
    }
    expected_hash = hashlib.sha256(
        json.dumps(canonical_receipt_data, sort_keys=True).encode("utf-8")
    ).hexdigest()

    receipt_hash = (
        receipt.get("canonical_receipt_hash")
        or receipt.get("receipt_hash")
        or query_exec.get("receipt_hash")
    )
    if not receipt_hash or receipt_hash != expected_hash:
        raise ValueError(
            f"Watch-window receipt integrity check failed: canonical SHA-256 digest mismatch. Tampered receipt rejected. Expected '{expected_hash}', got '{receipt_hash}'. Fail-closed gate enforced."
        )

    return receipt
