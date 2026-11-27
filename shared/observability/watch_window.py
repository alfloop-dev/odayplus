"""Deployment watch-window status metric emission and durable receipt artifact management.

Reads real Cloud Run metrics from Cloud Monitoring:
- ``run.googleapis.com/request_count`` (5xx error ratio via ``metric.labels.response_code_class``)
- ``run.googleapis.com/request_latencies`` (p95 latency)

Binds the observed Cloud Run services (production API and Web) to the exact
``release_sha`` by verifying via the Cloud Run Admin API v2 that each service
routes 100% traffic to a single revision whose container image digest matches
the admitted ``RELEASE_MANIFEST.json``.

Governance thresholds and minimum window duration are loaded directly from
``docs/evidence/runtime/ODP-PROD-OPS-WATCH-PARAMS-001/prod-ops-governance-parameters.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from shared.observability.metrics import MetricsRegistry, default_registry

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECEIPT_PATH = REPO_ROOT / "docs" / "evidence" / "watch_window_receipt.json"
DEFAULT_GOVERNANCE_PARAMS_PATH = (
    REPO_ROOT
    / "docs"
    / "evidence"
    / "runtime"
    / "ODP-PROD-OPS-WATCH-PARAMS-001"
    / "prod-ops-governance-parameters.json"
)
DEFAULT_CLOUD_RUN_ROUTE = "https://run.googleapis.com/v2"

CLOUD_RUN_REQUEST_COUNT_METRIC = "run.googleapis.com/request_count"
CLOUD_RUN_REQUEST_LATENCIES_METRIC = "run.googleapis.com/request_latencies"
REQUIRED_WATCH_METRICS = (
    CLOUD_RUN_REQUEST_COUNT_METRIC,
    CLOUD_RUN_REQUEST_LATENCIES_METRIC,
)
REQUIRED_SERVICE_ROLES = ("api", "web")
RECEIPT_SCHEMA_VERSION = 2

FULL_SHA_REGEX = re.compile(r"^[0-9a-f]{40}$")
SHA256_DIGEST_REGEX = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_DIGEST_SUFFIX_REGEX = re.compile(r"@(sha256:[0-9a-f]{64})$")
_LESS_THAN_PERCENT_REGEX = re.compile(r"^<\s*([0-9]+(?:\.[0-9]+)?)\s*%$")
_LESS_THAN_SECONDS_REGEX = re.compile(r"^<\s*([0-9]+(?:\.[0-9]+)?)\s*s$", re.IGNORECASE)

# Cloud Run built-in metrics align on 60s sampling intervals; allow one 60s
# alignment interval at each window boundary when validating point coverage.
_WINDOW_BOUNDARY_TOLERANCE_SECONDS = 60.0
_COVERAGE_ALIGNMENT_SLACK_SECONDS = 120.0
_MAX_MONITORING_PAGES = 100


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
        return ts.astimezone(UTC) if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
    if isinstance(ts, str) and ts.strip():
        raw = ts.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        return dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    raise ValueError(f"Invalid timestamp '{ts}'.")


def _extract_image_digest(image_ref: Any, *, field_name: str) -> str:
    if not isinstance(image_ref, str) or not image_ref.strip():
        raise ValueError(f"{field_name} must be a non-empty image reference with @sha256 digest.")
    cleaned = image_ref.strip()
    match = IMAGE_DIGEST_SUFFIX_REGEX.search(cleaned)
    if not match:
        raise ValueError(
            f"{field_name} ('{cleaned}') is missing an immutable @sha256:<64-hex> digest. Fail-closed gate enforced."
        )
    return match.group(1)


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_canonical_receipt_hash(receipt: dict[str, Any]) -> str:
    """Compute SHA-256 over the canonical receipt payload (excluding the hash field itself)."""
    canonical_payload = {
        k: v for k, v in receipt.items() if k not in ("canonical_receipt_hash", "receipt_hash")
    }
    return hashlib.sha256(_canonical_json_bytes(canonical_payload)).hexdigest()


def load_watch_governance_parameters(
    params_path: str | Path | None = None,
) -> dict[str, Any]:
    """Load watch window duration and SLO thresholds from the canonical governance JSON."""
    path = Path(params_path) if params_path is not None else DEFAULT_GOVERNANCE_PARAMS_PATH
    if not path.is_file():
        raise FileNotFoundError(
            f"Watch-window governance parameters file missing at '{path}'. Fail-closed gate enforced."
        )
    raw_bytes = path.read_bytes()
    source_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise ValueError(
            f"Watch-window governance parameters at '{path}' are malformed JSON: {exc}. Fail-closed gate enforced."
        ) from exc

    gov = payload.get("governance_parameters")
    if not isinstance(gov, dict):
        raise ValueError(
            f"Governance parameters file '{path}' missing 'governance_parameters' object. Fail-closed gate enforced."
        )

    watch_minutes = gov.get("watch_window_minutes")
    if not isinstance(watch_minutes, int) or isinstance(watch_minutes, bool) or watch_minutes <= 0:
        raise ValueError(
            f"Governance parameter 'watch_window_minutes' must be a positive integer (got {watch_minutes!r})."
        )

    slo = gov.get("slo_thresholds")
    if not isinstance(slo, dict):
        raise ValueError(
            f"Governance parameters file '{path}' missing 'slo_thresholds' object. Fail-closed gate enforced."
        )

    err_raw = str(slo.get("error_rate_percent", "")).strip()
    err_match = _LESS_THAN_PERCENT_REGEX.match(err_raw)
    if not err_match:
        raise ValueError(
            f"Governance parameter 'slo_thresholds.error_rate_percent' must match '< X%' (got {err_raw!r})."
        )
    max_error_rate_percent = float(err_match.group(1))
    if not math.isfinite(max_error_rate_percent) or max_error_rate_percent <= 0:
        raise ValueError(
            f"Governance parameter 'error_rate_percent' must be finite and positive (got {max_error_rate_percent})."
        )

    p95_raw = str(slo.get("p95_latency_seconds", "")).strip()
    p95_match = _LESS_THAN_SECONDS_REGEX.match(p95_raw)
    if not p95_match:
        raise ValueError(
            f"Governance parameter 'slo_thresholds.p95_latency_seconds' must match '< Xs' (got {p95_raw!r})."
        )
    max_p95_latency_seconds = float(p95_match.group(1))
    if not math.isfinite(max_p95_latency_seconds) or max_p95_latency_seconds <= 0:
        raise ValueError(
            f"Governance parameter 'p95_latency_seconds' must be finite and positive (got {max_p95_latency_seconds})."
        )

    try:
        display_path = str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        display_path = str(path.resolve())

    return {
        "source_path": display_path,
        "source_sha256": source_sha256,
        "watch_window_minutes": watch_minutes,
        "max_error_rate_percent": max_error_rate_percent,
        "max_p95_latency_seconds": max_p95_latency_seconds,
    }


def _resolve_gcp_access_token() -> str | None:
    """Resolve a GCP OAuth access token from GOOGLE_OAUTH_ACCESS_TOKEN or ADC."""
    env_token = (os.getenv("GOOGLE_OAUTH_ACCESS_TOKEN") or "").strip()
    if env_token:
        return env_token
    from shared.observability.metrics import get_gcp_adc_token

    return get_gcp_adc_token()


def _default_gcp_http_transport(
    method: str,
    url: str,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, str | dict[str, Any]]:
    """Default HTTPS transport for Cloud Run Admin API and Cloud Monitoring API.

    Authenticates with GOOGLE_OAUTH_ACCESS_TOKEN or Application Default Credentials (ADC).
    """
    method_actual = method.upper()
    url_actual = url
    if params:
        query_str = urllib.parse.urlencode(params)
        separator = "&" if "?" in url_actual else "?"
        url_actual = f"{url_actual}{separator}{query_str}"

    req_headers: dict[str, str] = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)

    if "Authorization" not in req_headers:
        token = _resolve_gcp_access_token()
        if not token:
            raise ValueError(
                "GCP credentials unavailable: configure Application Default Credentials (ADC) "
                "or set GOOGLE_OAUTH_ACCESS_TOKEN. Fail-closed gate enforced."
            )
        req_headers["Authorization"] = f"Bearer {token}"

    data_bytes = None
    if payload is not None and method_actual != "GET":
        req_headers.setdefault("Content-Type", "application/json")
        data_bytes = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url_actual,
        data=data_bytes,
        headers=req_headers,
        method=method_actual,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
            try:
                parsed: str | dict[str, Any] = json.loads(body) if body else {}
            except Exception:
                parsed = body
            return response.status, parsed
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else str(exc)
        try:
            parsed = json.loads(body) if body else {}
        except Exception:
            parsed = body
        return exc.code, parsed
    except Exception as exc:
        return 0, str(exc)


def _get_cloud_run_route(cloud_run_route: str | None = None) -> str:
    candidate = (
        cloud_run_route
        or os.getenv("CLOUD_RUN_ADMIN_ENDPOINT_URL")
        or DEFAULT_CLOUD_RUN_ROUTE
    )
    candidate_str = str(candidate).strip().rstrip("/")
    if not (candidate_str.startswith("http://") or candidate_str.startswith("https://")):
        raise ValueError(
            f"Cloud Run Admin API route must be a valid HTTP/HTTPS URL (got '{candidate_str}'). Fail-closed gate enforced."
        )
    return candidate_str


def _load_admitted_manifest(
    manifest_path: str | Path,
    expected_release_sha: str,
) -> dict[str, Any]:
    """Load and validate the admitted release manifest and extract API/Web image digests."""
    path = Path(manifest_path)
    if not path.is_file():
        raise FileNotFoundError(
            f"Admitted release manifest not found at '{path}'. Fail-closed gate enforced."
        )
    raw_bytes = path.read_bytes()
    manifest_file_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise ValueError(
            f"Admitted release manifest at '{path}' is malformed JSON: {exc}. Fail-closed gate enforced."
        ) from exc

    if not isinstance(data, dict):
        raise ValueError("Admitted release manifest must be a JSON object. Fail-closed gate enforced.")

    candidate_sha = data.get("candidate_sha")
    if not candidate_sha:
        raise ValueError(
            "Admitted release manifest missing required 'candidate_sha'. Fail-closed gate enforced."
        )
    clean_candidate_sha = validate_full_sha(str(candidate_sha), "candidate_sha in manifest")
    if clean_candidate_sha != expected_release_sha:
        raise ValueError(
            f"Admitted release manifest candidate_sha '{clean_candidate_sha}' does not match "
            f"release_sha '{expected_release_sha}'. Fail-closed gate enforced."
        )

    components = data.get("components")
    if not isinstance(components, dict):
        raise ValueError(
            "Admitted release manifest missing 'components' mapping. Fail-closed gate enforced."
        )

    images: dict[str, str] = {}
    digests: dict[str, str] = {}
    for role in REQUIRED_SERVICE_ROLES:
        comp = components.get(role)
        if not isinstance(comp, dict):
            raise ValueError(
                f"Admitted release manifest missing components.{role}. Fail-closed gate enforced."
            )
        img_ref = comp.get("image")
        digests[role] = _extract_image_digest(img_ref, field_name=f"manifest.components.{role}.image")
        images[role] = str(img_ref).strip()

    manifest_digest = data.get("manifest_digest")
    if manifest_digest is not None:
        manifest_digest_str = str(manifest_digest).strip()
        if not SHA256_DIGEST_REGEX.match(manifest_digest_str):
            raise ValueError(
                f"Admitted release manifest has invalid manifest_digest '{manifest_digest_str}'. Fail-closed gate enforced."
            )
    else:
        manifest_digest_str = None

    return {
        "manifest_digest": manifest_digest_str,
        "manifest_file_sha256": manifest_file_sha256,
        "images": images,
        "digests": digests,
    }


def _normalize_revision_name(raw_revision: Any) -> str:
    if not isinstance(raw_revision, str) or not raw_revision.strip():
        return ""
    cleaned = raw_revision.strip()
    if "/revisions/" in cleaned:
        return cleaned.rsplit("/revisions/", 1)[-1].strip()
    return cleaned


def _resolve_cloud_run_service_binding(
    *,
    transport: Callable[..., tuple[int, str | dict[str, Any]]],
    cloud_run_route: str,
    gcp_project: str,
    gcp_region: str,
    service_name: str,
    expected_manifest_image: str,
    expected_manifest_digest: str,
) -> dict[str, Any]:
    """Read Cloud Run Admin API v2 to verify 100% traffic revision and match image digest to manifest."""
    from shared.observability.metrics import _invoke_transport

    service_url = (
        f"{cloud_run_route}/projects/{gcp_project}/locations/{gcp_region}/services/{service_name}"
    )
    try:
        svc_status, svc_resp = _invoke_transport(transport, method="GET", url=service_url)
    except Exception as exc:
        raise ValueError(
            f"Cloud Run Admin API service lookup failed for '{service_name}': {exc}. Fail-closed gate enforced."
        ) from exc

    if not (200 <= svc_status < 300) or not isinstance(svc_resp, dict):
        raise ValueError(
            f"Cloud Run Admin API service lookup failed for '{service_name}' with HTTP {svc_status}: {svc_resp}. Fail-closed gate enforced."
        )

    traffic_entries = svc_resp.get("trafficStatuses")
    if traffic_entries is None and isinstance(svc_resp.get("status"), dict):
        traffic_entries = svc_resp["status"].get("traffic")

    if not isinstance(traffic_entries, list) or not traffic_entries:
        raise ValueError(
            f"Cloud Run service '{service_name}' has no active trafficStatuses. Fail-closed gate enforced."
        )

    total_percent = 0
    active_revisions: list[tuple[str, int]] = []
    for entry in traffic_entries:
        if not isinstance(entry, dict):
            raise ValueError(
                f"Cloud Run service '{service_name}' contains invalid traffic entry. Fail-closed gate enforced."
            )
        pct_raw = entry.get("percent", 0)
        if isinstance(pct_raw, bool) or not isinstance(pct_raw, int) or pct_raw < 0:
            raise ValueError(
                f"Cloud Run service '{service_name}' contains invalid traffic percent {pct_raw!r}. Fail-closed gate enforced."
            )
        if pct_raw == 0:
            continue
        total_percent += pct_raw
        rev_raw = entry.get("revision") or entry.get("revisionName")
        if not rev_raw and entry.get("type") == "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST":
            rev_raw = svc_resp.get("latestReadyRevision")
        rev_name = _normalize_revision_name(rev_raw)
        if not rev_name:
            raise ValueError(
                f"Cloud Run service '{service_name}' traffic entry with {pct_raw}% is missing revision name. Fail-closed gate enforced."
            )
        active_revisions.append((rev_name, pct_raw))

    if total_percent != 100 or len(active_revisions) != 1 or active_revisions[0][1] != 100:
        raise ValueError(
            f"Cloud Run service '{service_name}' must route 100% traffic to a single revision "
            f"(observed active traffic: {active_revisions}, total={total_percent}%). Fail-closed gate enforced."
        )

    revision_name = active_revisions[0][0]
    revision_url = (
        f"{cloud_run_route}/projects/{gcp_project}/locations/{gcp_region}"
        f"/services/{service_name}/revisions/{revision_name}"
    )
    try:
        rev_status, rev_resp = _invoke_transport(transport, method="GET", url=revision_url)
    except Exception as exc:
        raise ValueError(
            f"Cloud Run Admin API revision lookup failed for '{service_name}' revision '{revision_name}': {exc}. Fail-closed gate enforced."
        ) from exc

    if not (200 <= rev_status < 300) or not isinstance(rev_resp, dict):
        raise ValueError(
            f"Cloud Run Admin API revision lookup failed for '{service_name}' revision '{revision_name}' "
            f"with HTTP {rev_status}: {rev_resp}. Fail-closed gate enforced."
        )

    rev_resource_name = rev_resp.get("name")
    if isinstance(rev_resource_name, str) and rev_resource_name.strip():
        expected_prefix = f"projects/{gcp_project}/locations/{gcp_region}/"
        if not rev_resource_name.strip().startswith(expected_prefix) or not rev_resource_name.strip().endswith(
            f"/revisions/{revision_name}"
        ):
            raise ValueError(
                f"Cloud Run revision resource name '{rev_resource_name}' does not match "
                f"expected project '{gcp_project}', region '{gcp_region}', revision '{revision_name}'. Fail-closed gate enforced."
            )

    containers = rev_resp.get("containers")
    if containers is None and isinstance(rev_resp.get("spec"), dict):
        containers = rev_resp["spec"].get("containers")

    if not isinstance(containers, list) or not containers or not isinstance(containers[0], dict):
        raise ValueError(
            f"Cloud Run revision '{revision_name}' for service '{service_name}' missing container specification. Fail-closed gate enforced."
        )

    deployed_image = str(containers[0].get("image") or "").strip()
    deployed_digest = _extract_image_digest(
        deployed_image,
        field_name=f"Cloud Run revision '{revision_name}' container image",
    )

    if deployed_digest != expected_manifest_digest:
        raise ValueError(
            f"Cloud Run service '{service_name}' active 100%-traffic revision '{revision_name}' "
            f"image digest '{deployed_digest}' does not match admitted manifest digest "
            f"'{expected_manifest_digest}'. Fail-closed gate enforced."
        )

    return {
        "service_name": service_name,
        "revision_name": revision_name,
        "traffic_percent": 100,
        "deployed_image": deployed_image,
        "deployed_image_digest": deployed_digest,
        "manifest_image": expected_manifest_image,
        "manifest_image_digest": expected_manifest_digest,
        "digest_matched": True,
    }


def build_cloud_run_metric_filter(
    metric_type: str,
    *,
    service_name: str,
    revision_name: str,
    gcp_region: str,
) -> str:
    """Build the Cloud Monitoring filter for a Cloud Run built-in metric on a specific revision."""
    return (
        f'metric.type="{metric_type}" '
        f'AND resource.type="cloud_run_revision" '
        f'AND resource.labels.service_name="{service_name}" '
        f'AND resource.labels.revision_name="{revision_name}" '
        f'AND resource.labels.location="{gcp_region}"'
    )


def _query_monitoring_time_series(
    *,
    transport: Callable[..., tuple[int, str | dict[str, Any]]],
    monitoring_route: str,
    gcp_project: str,
    metric_filter: str,
    start_dt: datetime,
    end_dt: datetime,
) -> list[dict[str, Any]]:
    """Query Cloud Monitoring projects.timeSeries.list with pagination support."""
    from shared.observability.metrics import _invoke_transport

    route_clean = monitoring_route.rstrip("/")
    endpoint = (
        route_clean
        if route_clean.endswith("/timeSeries")
        else f"{route_clean}/projects/{gcp_project}/timeSeries"
    )

    collected: list[dict[str, Any]] = []
    page_token: str | None = None

    for _ in range(_MAX_MONITORING_PAGES):
        params: dict[str, Any] = {
            "filter": metric_filter,
            "interval.startTime": start_dt.isoformat(),
            "interval.endTime": end_dt.isoformat(),
        }
        if page_token:
            params["pageToken"] = page_token

        try:
            http_status, resp = _invoke_transport(
                transport,
                method="GET",
                url=endpoint,
                params=params,
            )
        except Exception as exc:
            raise ValueError(
                f"Cloud Monitoring query transport failed: {exc}. Fail-closed gate enforced."
            ) from exc

        if not (200 <= http_status < 300):
            raise ValueError(
                f"Cloud Monitoring query failed with HTTP {http_status}: {resp}. Fail-closed gate enforced."
            )

        if not isinstance(resp, dict):
            raise ValueError(
                "Cloud Monitoring query response missing JSON object payload. Fail-closed gate enforced."
            )

        page_series = resp.get("timeSeries", [])
        if page_series is None:
            page_series = []
        if not isinstance(page_series, list):
            raise ValueError(
                "Cloud Monitoring query response 'timeSeries' field is not a list. Fail-closed gate enforced."
            )
        collected.extend(page_series)

        next_token = resp.get("nextPageToken")
        if isinstance(next_token, str) and next_token.strip():
            page_token = next_token.strip()
        else:
            break
    else:
        raise ValueError(
            f"Cloud Monitoring query exceeded maximum pagination limit ({_MAX_MONITORING_PAGES} pages). Fail-closed gate enforced."
        )

    return collected


def _parse_non_negative_number(raw: Any, *, context: str) -> float:
    if raw is None or isinstance(raw, bool):
        raise ValueError(f"{context} missing numerical value. Fail-closed gate enforced.")
    try:
        val = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{context} has non-numeric value {raw!r}: {exc}. Fail-closed gate enforced.") from exc
    if not math.isfinite(val):
        raise ValueError(
            f"{context} has non-finite value {raw!r}. Finite non-negative domain required. Fail-closed gate enforced."
        )
    if val < 0.0:
        raise ValueError(
            f"{context} has negative value '{val}'. Finite non-negative domain required. Fail-closed gate enforced."
        )
    return val


def _extract_bucket_bounds(bucket_options: Any) -> tuple[list[tuple[float, float]], float]:
    """Return finite bucket intervals [(low, high), ...] and the overflow lower bound."""
    if not isinstance(bucket_options, dict):
        raise ValueError("Distribution point missing valid bucketOptions. Fail-closed gate enforced.")

    if "exponentialBuckets" in bucket_options:
        cfg = bucket_options["exponentialBuckets"]
        if not isinstance(cfg, dict):
            raise ValueError("Invalid exponentialBuckets configuration. Fail-closed gate enforced.")
        num_finite = int(cfg.get("numFiniteBuckets", 0))
        growth = float(cfg.get("growthFactor", 0))
        scale = float(cfg.get("scale", 0))
        if num_finite < 0 or not math.isfinite(growth) or growth <= 1.0 or not math.isfinite(scale) or scale <= 0.0:
            raise ValueError(
                f"Invalid exponentialBuckets parameters: {cfg!r}. Fail-closed gate enforced."
            )
        # Bucket 0 is underflow [0, scale); buckets 1..N are [scale * g^(i-1), scale * g^i)
        finite_bounds: list[tuple[float, float]] = [(0.0, scale)]
        low = scale
        for _ in range(num_finite):
            high = low * growth
            finite_bounds.append((low, high))
            low = high
        return finite_bounds, low

    if "linearBuckets" in bucket_options:
        cfg = bucket_options["linearBuckets"]
        if not isinstance(cfg, dict):
            raise ValueError("Invalid linearBuckets configuration. Fail-closed gate enforced.")
        num_finite = int(cfg.get("numFiniteBuckets", 0))
        width = float(cfg.get("width", 0))
        offset = float(cfg.get("offset", 0))
        if num_finite < 0 or not math.isfinite(width) or width <= 0.0 or not math.isfinite(offset) or offset < 0.0:
            raise ValueError(
                f"Invalid linearBuckets parameters: {cfg!r}. Fail-closed gate enforced."
            )
        finite_bounds = [(0.0, offset)]
        low = offset
        for _ in range(num_finite):
            high = low + width
            finite_bounds.append((low, high))
            low = high
        return finite_bounds, low

    if "explicitBuckets" in bucket_options:
        cfg = bucket_options["explicitBuckets"]
        if not isinstance(cfg, dict) or not isinstance(cfg.get("bounds"), list) or not cfg["bounds"]:
            raise ValueError("Invalid explicitBuckets configuration. Fail-closed gate enforced.")
        bounds = [float(b) for b in cfg["bounds"]]
        if any(not math.isfinite(b) or b < 0.0 for b in bounds):
            raise ValueError("explicitBuckets contains negative or non-finite bound. Fail-closed gate enforced.")
        for prev, nxt in zip(bounds, bounds[1:], strict=False):
            if nxt <= prev:
                raise ValueError("explicitBuckets bounds must be strictly increasing. Fail-closed gate enforced.")
        finite_bounds = [(0.0, bounds[0])]
        for prev, nxt in zip(bounds, bounds[1:], strict=False):
            finite_bounds.append((prev, nxt))
        return finite_bounds, bounds[-1]

    raise ValueError(
        "Distribution bucketOptions must specify exponentialBuckets, linearBuckets, or explicitBuckets. Fail-closed gate enforced."
    )


def _compute_distributions_p95_ms(
    distributions: list[dict[str, Any]],
) -> tuple[float | None, bool]:
    """Compute p95 latency in milliseconds across Cloud Monitoring Distribution values.

    Returns ``(p95_ms, in_overflow_bucket)``. Returns ``(None, False)`` when total
    observation count is zero.
    """
    segments: list[tuple[float, float, float]] = []  # (low, high, count) where high=inf for overflow
    total_count = 0.0

    for dist in distributions:
        finite_bounds, overflow_low = _extract_bucket_bounds(dist.get("bucketOptions"))
        raw_counts = dist.get("bucketCounts")
        if not isinstance(raw_counts, list):
            raise ValueError("Distribution point missing bucketCounts list. Fail-closed gate enforced.")

        dist_sum = 0.0
        for idx, raw_c in enumerate(raw_counts):
            c = _parse_non_negative_number(raw_c, context=f"distribution bucketCounts[{idx}]")
            if c == 0.0:
                continue
            dist_sum += c
            if idx < len(finite_bounds):
                low, high = finite_bounds[idx]
                segments.append((low, high, c))
            elif idx == len(finite_bounds):
                segments.append((overflow_low, math.inf, c))
            else:
                raise ValueError(
                    f"Distribution bucketCounts index {idx} exceeds bucketOptions bounds ({len(finite_bounds) + 1}). Fail-closed gate enforced."
                )

        if "count" in dist and dist["count"] is not None:
            declared = _parse_non_negative_number(dist["count"], context="distribution.count")
            if abs(declared - dist_sum) > 1e-6 and dist_sum > declared:
                raise ValueError(
                    f"Distribution bucketCounts sum ({dist_sum}) exceeds declared count ({declared}). Fail-closed gate enforced."
                )
        total_count += dist_sum

    if total_count <= 0.0:
        return None, False

    target = 0.95 * total_count

    # Check if finite segments alone cover the 95th percentile; otherwise it lands in overflow.
    finite_segments = [(low, high, c) for (low, high, c) in segments if math.isfinite(high)]
    overflow_segments = [(low, c) for (low, high, c) in segments if not math.isfinite(high)]
    finite_total = sum(c for _, _, c in finite_segments)

    if finite_total < target:
        overflow_low = min(low for low, _ in overflow_segments) if overflow_segments else 0.0
        return round(overflow_low, 4), True

    # Piecewise-uniform CDF across merged finite intervals (exact for identical bucketOptions
    # and well-defined even if revisions/series carry different bucket boundaries).
    def cdf(x: float) -> float:
        acc = 0.0
        for low, high, c in finite_segments:
            if x <= low:
                continue
            if x >= high:
                acc += c
            elif high > low:
                acc += c * ((x - low) / (high - low))
            else:
                acc += c
        return acc

    lo = min(low for low, _, _ in finite_segments)
    hi = max(high for _, high, _ in finite_segments)
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if cdf(mid) < target:
            lo = mid
        else:
            hi = mid

    return round((lo + hi) / 2.0, 4), False


def _validate_series_identity(
    ts_item: Any,
    *,
    expected_metric_type: str,
    expected_gcp_project: str,
    expected_gcp_region: str,
    expected_service_name: str,
    expected_revision_name: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(ts_item, dict):
        raise ValueError("Cloud Monitoring timeSeries entry must be an object. Fail-closed gate enforced.")

    metric_dict = ts_item.get("metric")
    if not isinstance(metric_dict, dict):
        raise ValueError("Cloud Monitoring timeSeries missing 'metric' object. Fail-closed gate enforced.")
    metric_type = str(metric_dict.get("type") or "").strip()
    if metric_type != expected_metric_type:
        raise ValueError(
            f"Unexpected Cloud Monitoring metric.type '{metric_type}': expected '{expected_metric_type}'. Fail-closed gate enforced."
        )

    resource_dict = ts_item.get("resource")
    if not isinstance(resource_dict, dict):
        raise ValueError("Cloud Monitoring timeSeries missing 'resource' object. Fail-closed gate enforced.")
    resource_type = str(resource_dict.get("type") or "").strip()
    if resource_type != "cloud_run_revision":
        raise ValueError(
            f"Unexpected Cloud Monitoring resource.type '{resource_type}': expected 'cloud_run_revision'. Fail-closed gate enforced."
        )

    res_labels = resource_dict.get("labels")
    if not isinstance(res_labels, dict):
        raise ValueError("Cloud Monitoring timeSeries missing resource.labels. Fail-closed gate enforced.")

    checks = (
        ("project_id", expected_gcp_project),
        ("location", expected_gcp_region),
        ("service_name", expected_service_name),
        ("revision_name", expected_revision_name),
    )
    for label_key, expected_val in checks:
        actual = str(res_labels.get(label_key) or "").strip()
        if actual != expected_val:
            raise ValueError(
                f"Cloud Monitoring timeSeries resource.labels.{label_key} mismatch: "
                f"expected '{expected_val}', got '{actual}'. Fail-closed gate enforced."
            )

    points = ts_item.get("points")
    if not isinstance(points, list):
        raise ValueError(
            f"Cloud Monitoring timeSeries for '{expected_metric_type}' missing points list. Fail-closed gate enforced."
        )
    return metric_dict, points


def _parse_point_timestamp(
    pt: Any,
    *,
    metric_type: str,
    start_dt: datetime,
    end_dt: datetime,
) -> datetime:
    if not isinstance(pt, dict):
        raise ValueError(
            f"Cloud Monitoring timeSeries '{metric_type}' contains non-object point. Fail-closed gate enforced."
        )
    interval = pt.get("interval")
    if not isinstance(interval, dict):
        raise ValueError(
            f"Cloud Monitoring point in '{metric_type}' missing interval object. Fail-closed gate enforced."
        )
    ts_raw = interval.get("endTime") or interval.get("startTime")
    if not isinstance(ts_raw, str) or not ts_raw.strip():
        raise ValueError(
            f"Cloud Monitoring point in '{metric_type}' missing timestamp. Fail-closed gate enforced."
        )
    pt_dt = _parse_iso_utc(ts_raw)
    tol = timedelta(seconds=_WINDOW_BOUNDARY_TOLERANCE_SECONDS)
    if not (start_dt - tol <= pt_dt <= end_dt + tol):
        raise ValueError(
            f"Cloud Monitoring point timestamp '{ts_raw}' in '{metric_type}' lies outside requested "
            f"watch window [{start_dt.isoformat()}, {end_dt.isoformat()}]. Fail-closed gate enforced."
        )
    return pt_dt


def evaluate_cloud_run_service_metrics(
    *,
    role: str,
    service_name: str,
    revision_name: str,
    gcp_project: str,
    gcp_region: str,
    request_count_series: list[dict[str, Any]],
    request_latencies_series: list[dict[str, Any]],
    start_dt: datetime,
    end_dt: datetime,
    governance: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Validate and evaluate real Cloud Run request_count and request_latencies timeSeries."""
    if not isinstance(request_count_series, list) or not isinstance(request_latencies_series, list):
        raise ValueError(
            f"Service '{service_name}' raw_time_series must contain lists for request_count and request_latencies. Fail-closed gate enforced."
        )

    min_coverage_seconds = max(
        0.0,
        float(governance["watch_window_minutes"]) * 60.0 - _COVERAGE_ALIGNMENT_SLACK_SECONDS,
    )
    max_error_rate_percent = float(governance["max_error_rate_percent"])
    max_p95_latency_seconds = float(governance["max_p95_latency_seconds"])

    failure_reasons: list[str] = []

    # 1. run.googleapis.com/request_count
    total_requests = 0.0
    error_5xx_requests = 0.0
    rc_timestamps: list[datetime] = []

    for ts_item in request_count_series:
        metric_dict, points = _validate_series_identity(
            ts_item,
            expected_metric_type=CLOUD_RUN_REQUEST_COUNT_METRIC,
            expected_gcp_project=gcp_project,
            expected_gcp_region=gcp_region,
            expected_service_name=service_name,
            expected_revision_name=revision_name,
        )
        m_labels = metric_dict.get("labels")
        if not isinstance(m_labels, dict) or not str(m_labels.get("response_code_class") or "").strip():
            raise ValueError(
                f"Cloud Monitoring '{CLOUD_RUN_REQUEST_COUNT_METRIC}' timeSeries for '{service_name}' "
                "missing metric.labels.response_code_class. Fail-closed gate enforced."
            )
        code_class = str(m_labels["response_code_class"]).strip().lower()

        for pt in points:
            pt_dt = _parse_point_timestamp(
                pt,
                metric_type=CLOUD_RUN_REQUEST_COUNT_METRIC,
                start_dt=start_dt,
                end_dt=end_dt,
            )
            val_dict = pt.get("value")
            if not isinstance(val_dict, dict):
                raise ValueError(
                    f"Cloud Monitoring point in '{CLOUD_RUN_REQUEST_COUNT_METRIC}' missing value object. Fail-closed gate enforced."
                )
            raw_val = val_dict.get("int64Value")
            if raw_val is None:
                raw_val = val_dict.get("doubleValue")
            count_val = _parse_non_negative_number(
                raw_val,
                context=f"{CLOUD_RUN_REQUEST_COUNT_METRIC} point for '{service_name}'",
            )
            total_requests += count_val
            if code_class == "5xx":
                error_5xx_requests += count_val
            rc_timestamps.append(pt_dt)

    distinct_rc_ts = sorted(set(rc_timestamps))
    rc_coverage_seconds = (
        (distinct_rc_ts[-1] - distinct_rc_ts[0]).total_seconds() if len(distinct_rc_ts) >= 2 else 0.0
    )

    if not rc_timestamps:
        failure_reasons.append(
            f"{role} ({service_name}): missing {CLOUD_RUN_REQUEST_COUNT_METRIC} data"
        )
        error_rate_percent: float | None = None
    else:
        if len(distinct_rc_ts) < 2:
            failure_reasons.append(
                f"{role} ({service_name}): insufficient {CLOUD_RUN_REQUEST_COUNT_METRIC} data points "
                f"({len(distinct_rc_ts)} < 2)"
            )
        elif rc_coverage_seconds < min_coverage_seconds:
            failure_reasons.append(
                f"{role} ({service_name}): {CLOUD_RUN_REQUEST_COUNT_METRIC} coverage "
                f"{rc_coverage_seconds:.1f}s < required {min_coverage_seconds:.1f}s"
            )

        if total_requests <= 0.0:
            failure_reasons.append(
                f"{role} ({service_name}): zero total requests observed in {CLOUD_RUN_REQUEST_COUNT_METRIC}"
            )
            error_rate_percent = None
        else:
            error_rate_percent = round((error_5xx_requests / total_requests) * 100.0, 4)
            if error_rate_percent >= max_error_rate_percent:
                failure_reasons.append(
                    f"{role} ({service_name}): 5xx error rate {error_rate_percent:.4f}% "
                    f"exceeds threshold (< {max_error_rate_percent}%)"
                )

    # 2. run.googleapis.com/request_latencies
    lat_timestamps: list[datetime] = []
    distributions: list[dict[str, Any]] = []
    scalar_latencies_ms: list[float] = []

    for ts_item in request_latencies_series:
        _metric_dict, points = _validate_series_identity(
            ts_item,
            expected_metric_type=CLOUD_RUN_REQUEST_LATENCIES_METRIC,
            expected_gcp_project=gcp_project,
            expected_gcp_region=gcp_region,
            expected_service_name=service_name,
            expected_revision_name=revision_name,
        )
        for pt in points:
            pt_dt = _parse_point_timestamp(
                pt,
                metric_type=CLOUD_RUN_REQUEST_LATENCIES_METRIC,
                start_dt=start_dt,
                end_dt=end_dt,
            )
            val_dict = pt.get("value")
            if not isinstance(val_dict, dict):
                raise ValueError(
                    f"Cloud Monitoring point in '{CLOUD_RUN_REQUEST_LATENCIES_METRIC}' missing value object. Fail-closed gate enforced."
                )
            if "distributionValue" in val_dict:
                dist_val = val_dict["distributionValue"]
                if not isinstance(dist_val, dict):
                    raise ValueError(
                        f"Cloud Monitoring point in '{CLOUD_RUN_REQUEST_LATENCIES_METRIC}' has invalid distributionValue. Fail-closed gate enforced."
                    )
                distributions.append(dist_val)
            else:
                raw_lat = val_dict.get("doubleValue")
                if raw_lat is None:
                    raw_lat = val_dict.get("int64Value")
                scalar_latencies_ms.append(
                    _parse_non_negative_number(
                        raw_lat,
                        context=f"{CLOUD_RUN_REQUEST_LATENCIES_METRIC} point for '{service_name}'",
                    )
                )
            lat_timestamps.append(pt_dt)

    if distributions and scalar_latencies_ms:
        raise ValueError(
            f"Service '{service_name}' mixes distributionValue and scalar latency points in "
            f"'{CLOUD_RUN_REQUEST_LATENCIES_METRIC}'. Fail-closed gate enforced."
        )

    distinct_lat_ts = sorted(set(lat_timestamps))
    lat_coverage_seconds = (
        (distinct_lat_ts[-1] - distinct_lat_ts[0]).total_seconds() if len(distinct_lat_ts) >= 2 else 0.0
    )

    p95_latency_ms: float | None = None
    p95_latency_seconds: float | None = None

    if not lat_timestamps:
        failure_reasons.append(
            f"{role} ({service_name}): missing {CLOUD_RUN_REQUEST_LATENCIES_METRIC} data"
        )
    else:
        if len(distinct_lat_ts) < 2:
            failure_reasons.append(
                f"{role} ({service_name}): insufficient {CLOUD_RUN_REQUEST_LATENCIES_METRIC} data points "
                f"({len(distinct_lat_ts)} < 2)"
            )
        elif lat_coverage_seconds < min_coverage_seconds:
            failure_reasons.append(
                f"{role} ({service_name}): {CLOUD_RUN_REQUEST_LATENCIES_METRIC} coverage "
                f"{lat_coverage_seconds:.1f}s < required {min_coverage_seconds:.1f}s"
            )

        if distributions:
            p95_latency_ms, in_overflow = _compute_distributions_p95_ms(distributions)
            if p95_latency_ms is None:
                failure_reasons.append(
                    f"{role} ({service_name}): zero latency observations in {CLOUD_RUN_REQUEST_LATENCIES_METRIC}"
                )
            else:
                p95_latency_seconds = round(p95_latency_ms / 1000.0, 6)
                if in_overflow:
                    failure_reasons.append(
                        f"{role} ({service_name}): p95 latency falls in unbounded overflow bucket "
                        f"(>= {p95_latency_seconds:.4f}s)"
                    )
                elif p95_latency_seconds >= max_p95_latency_seconds:
                    failure_reasons.append(
                        f"{role} ({service_name}): p95 latency {p95_latency_seconds:.4f}s "
                        f"({p95_latency_ms:.2f}ms) exceeds threshold (< {max_p95_latency_seconds}s)"
                    )
        else:
            sorted_lat = sorted(scalar_latencies_ms)
            p95_idx = max(0, math.ceil(0.95 * len(sorted_lat)) - 1)
            p95_latency_ms = round(sorted_lat[p95_idx], 4)
            p95_latency_seconds = round(p95_latency_ms / 1000.0, 6)
            if p95_latency_seconds >= max_p95_latency_seconds:
                failure_reasons.append(
                    f"{role} ({service_name}): p95 latency {p95_latency_seconds:.4f}s "
                    f"({p95_latency_ms:.2f}ms) exceeds threshold (< {max_p95_latency_seconds}s)"
                )

    metrics_summary = {
        "total_requests": int(total_requests) if float(total_requests).is_integer() else round(total_requests, 4),
        "error_5xx_requests": (
            int(error_5xx_requests) if float(error_5xx_requests).is_integer() else round(error_5xx_requests, 4)
        ),
        "error_rate_percent": error_rate_percent,
        "p95_latency_ms": p95_latency_ms,
        "p95_latency_seconds": p95_latency_seconds,
        "request_count_points": len(rc_timestamps),
        "request_count_distinct_timestamps": len(distinct_rc_ts),
        "request_count_coverage_seconds": round(rc_coverage_seconds, 2),
        "request_latencies_points": len(lat_timestamps),
        "request_latencies_distinct_timestamps": len(distinct_lat_ts),
        "request_latencies_coverage_seconds": round(lat_coverage_seconds, 2),
    }
    return metrics_summary, failure_reasons


def record_deployment_watch_window_status(
    release_sha: str,
    status: int | None = None,
    *,
    start_time: datetime | str,
    end_time: datetime | str,
    gcp_project: str | None = None,
    gcp_region: str | None = None,
    api_service: str | None = None,
    web_service: str | None = None,
    manifest_path: str | Path | None = None,
    receipt_path: str | Path | None = None,
    registry: MetricsRegistry | None = None,
    watch_window_minutes: int | None = None,
    provider_route: str | None = None,
    cloud_run_route: str | None = None,
    governance_params_path: str | Path | None = None,
    query_transport: Callable[..., tuple[int, str | dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Query real Cloud Run metrics from Cloud Monitoring, verify release binding, and write a durable receipt.

    - ``status=None`` (default): derives ``WATCH_PASSED`` (1) or ``WATCH_FAILED`` (0) from real metrics.
    - ``status=1``: requires derived evaluation to pass; raises ``ValueError`` otherwise.
    - ``status=0``: forces ``WATCH_FAILED``.
    """
    clean_sha = validate_full_sha(release_sha, "release_sha")

    if status is not None and status not in (0, 1):
        raise ValueError("status must be None, 1 (WATCH_PASSED), or 0 (WATCH_FAILED).")

    if start_time is None or end_time is None:
        raise ValueError(
            "start_time and end_time must be provided as verifiable timestamps for watch-window recording."
        )

    governance = load_watch_governance_parameters(governance_params_path)
    min_window_minutes = int(governance["watch_window_minutes"])
    min_window_seconds = min_window_minutes * 60

    effective_window_minutes = (
        min_window_minutes if watch_window_minutes is None else watch_window_minutes
    )
    if (
        not isinstance(effective_window_minutes, int)
        or isinstance(effective_window_minutes, bool)
        or effective_window_minutes < min_window_minutes
    ):
        raise ValueError(
            f"watch_window_minutes ({effective_window_minutes}) is below the governed minimum "
            f"({min_window_minutes} minutes from {governance['source_path']}). Fail-closed gate enforced."
        )

    start_dt = _parse_iso_utc(start_time)
    end_dt = _parse_iso_utc(end_time)
    observed_seconds = (end_dt - start_dt).total_seconds()
    if observed_seconds < min_window_seconds:
        raise ValueError(
            f"Observed watch duration ({observed_seconds:.1f}s) is less than the governed "
            f"{min_window_minutes}-minute minimum ({min_window_seconds}s). Fail-closed gate enforced."
        )

    target_gcp_project = (
        gcp_project or os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT") or ""
    ).strip()
    if not target_gcp_project:
        raise ValueError("gcp_project (or GCP_PROJECT) must be provided. Fail-closed gate enforced.")

    target_gcp_region = (gcp_region or os.getenv("GCP_REGION") or "").strip()
    if not target_gcp_region:
        raise ValueError("gcp_region (or GCP_REGION) must be provided. Fail-closed gate enforced.")

    api_svc = (api_service or "").strip()
    web_svc = (web_service or "").strip()
    if not api_svc or not web_svc:
        raise ValueError(
            "Both api_service and web_service must be provided. Fail-closed gate enforced."
        )

    if manifest_path is None or not str(manifest_path).strip():
        raise ValueError(
            "manifest_path (admitted RELEASE_MANIFEST.json) is required to bind Cloud Run image digests. Fail-closed gate enforced."
        )

    out_path = Path(receipt_path or DEFAULT_RECEIPT_PATH).resolve()
    is_default_receipt = out_path == DEFAULT_RECEIPT_PATH.resolve()
    if is_default_receipt and query_transport is not None:
        raise ValueError(
            "Caller-supplied mock query_transport cannot write to default production evidence path. Fail-closed gate enforced."
        )

    from shared.observability.metrics import get_monitoring_provider_route

    monitoring_route = get_monitoring_provider_route(provider_route)
    run_route = _get_cloud_run_route(cloud_run_route)

    manifest = _load_admitted_manifest(manifest_path, clean_sha)
    transport = query_transport or _default_gcp_http_transport

    service_names = {"api": api_svc, "web": web_svc}
    services_payload: dict[str, Any] = {}
    all_failure_reasons: list[str] = []

    for role in REQUIRED_SERVICE_ROLES:
        svc_name = service_names[role]
        binding = _resolve_cloud_run_service_binding(
            transport=transport,
            cloud_run_route=run_route,
            gcp_project=target_gcp_project,
            gcp_region=target_gcp_region,
            service_name=svc_name,
            expected_manifest_image=manifest["images"][role],
            expected_manifest_digest=manifest["digests"][role],
        )

        rc_filter = build_cloud_run_metric_filter(
            CLOUD_RUN_REQUEST_COUNT_METRIC,
            service_name=svc_name,
            revision_name=binding["revision_name"],
            gcp_region=target_gcp_region,
        )
        lat_filter = build_cloud_run_metric_filter(
            CLOUD_RUN_REQUEST_LATENCIES_METRIC,
            service_name=svc_name,
            revision_name=binding["revision_name"],
            gcp_region=target_gcp_region,
        )

        rc_series = _query_monitoring_time_series(
            transport=transport,
            monitoring_route=monitoring_route,
            gcp_project=target_gcp_project,
            metric_filter=rc_filter,
            start_dt=start_dt,
            end_dt=end_dt,
        )
        lat_series = _query_monitoring_time_series(
            transport=transport,
            monitoring_route=monitoring_route,
            gcp_project=target_gcp_project,
            metric_filter=lat_filter,
            start_dt=start_dt,
            end_dt=end_dt,
        )

        metrics_summary, svc_failures = evaluate_cloud_run_service_metrics(
            role=role,
            service_name=svc_name,
            revision_name=binding["revision_name"],
            gcp_project=target_gcp_project,
            gcp_region=target_gcp_region,
            request_count_series=rc_series,
            request_latencies_series=lat_series,
            start_dt=start_dt,
            end_dt=end_dt,
            governance=governance,
        )
        all_failure_reasons.extend(svc_failures)

        services_payload[role] = {
            **binding,
            "query_filters": {
                "request_count": rc_filter,
                "request_latencies": lat_filter,
            },
            "metrics": metrics_summary,
            "raw_time_series": {
                "request_count": rc_series,
                "request_latencies": lat_series,
            },
        }

    metrics_passed = len(all_failure_reasons) == 0
    if status == 1 and not metrics_passed:
        raise ValueError(
            "Cloud Run metrics failed watch-window governance criteria, contradicting requested WATCH_PASSED status: "
            + "; ".join(all_failure_reasons)
            + ". Fail-closed gate enforced."
        )

    final_status_code = 0 if (status == 0 or not metrics_passed) else 1
    if status == 0 and metrics_passed:
        all_failure_reasons.append("explicit WATCH_FAILED status requested by caller")
    status_str = "WATCH_PASSED" if final_status_code == 1 else "WATCH_FAILED"

    reg = registry or default_registry()
    reg.set(
        "deployment_watch_window_status",
        float(final_status_code),
        labels={"release_sha": clean_sha, "status": status_str},
    )

    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "release_sha": clean_sha,
        "manifest_digest": manifest["manifest_digest"],
        "manifest_file_sha256": manifest["manifest_file_sha256"],
        "gcp_project": target_gcp_project,
        "gcp_region": target_gcp_region,
        "status": status_str,
        "status_code": final_status_code,
        "failure_reasons": all_failure_reasons,
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(),
        "observed_duration_seconds": round(observed_seconds, 2),
        "watch_window_minutes": effective_window_minutes,
        "governance_parameters": governance,
        "services": services_payload,
        "observed_metric_types": list(REQUIRED_WATCH_METRICS),
        "metric_name": "deployment_watch_window_status",
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    receipt["canonical_receipt_hash"] = compute_canonical_receipt_hash(receipt)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def verify_watch_window_receipt(
    expected_release_sha: str,
    receipt_path: str | Path | None = None,
    *,
    governance_params_path: str | Path | None = None,
) -> dict[str, Any]:
    """Verify a durable watch-window receipt against SHA, governance thresholds, raw Cloud Run metrics, and canonical hash."""
    clean_expected = validate_full_sha(expected_release_sha, "expected_release_sha")

    out_path = Path(receipt_path or DEFAULT_RECEIPT_PATH)
    if not out_path.exists():
        raise FileNotFoundError(f"Watch-window receipt artifact absent at '{out_path}'.")

    try:
        receipt = json.loads(out_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Watch-window receipt artifact at '{out_path}' is malformed: {exc}") from exc

    if not isinstance(receipt, dict):
        raise ValueError("Watch-window receipt must be a JSON object. Fail-closed gate enforced.")

    sha = receipt.get("release_sha")
    clean_sha = validate_full_sha(str(sha), "release_sha in receipt") if sha else ""
    if clean_sha != clean_expected:
        raise ValueError(
            f"Release SHA mismatch in watch-window receipt: expected '{clean_expected}', got '{sha}'."
        )

    status = receipt.get("status")
    status_code = receipt.get("status_code")
    if status not in {"WATCH_PASSED", "WATCH_FAILED"}:
        raise ValueError(f"Invalid watch-window status '{status}' in receipt.")
    if status != "WATCH_PASSED" or status_code != 1:
        raise ValueError(
            f"Watch-window verification failed or contradictory: status='{status}' (code {status_code})."
        )

    failure_reasons = receipt.get("failure_reasons")
    if not isinstance(failure_reasons, list) or len(failure_reasons) > 0:
        raise ValueError(
            f"Watch-window receipt reports failure reasons despite WATCH_PASSED status: {failure_reasons!r}. Fail-closed gate enforced."
        )

    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported watch-window receipt schema_version {receipt.get('schema_version')!r} "
            f"(expected {RECEIPT_SCHEMA_VERSION}). Fail-closed gate enforced."
        )

    gcp_proj = str(receipt.get("gcp_project") or "").strip()
    if not gcp_proj:
        raise ValueError("Watch-window receipt missing valid non-empty gcp_project.")

    gcp_region = str(receipt.get("gcp_region") or "").strip()
    if not gcp_region:
        raise ValueError("Watch-window receipt missing valid non-empty gcp_region.")

    governance = load_watch_governance_parameters(governance_params_path)
    min_window_minutes = int(governance["watch_window_minutes"])
    min_window_seconds = min_window_minutes * 60

    watch_window_minutes = receipt.get("watch_window_minutes")
    if (
        not isinstance(watch_window_minutes, int)
        or isinstance(watch_window_minutes, bool)
        or watch_window_minutes < min_window_minutes
    ):
        raise ValueError(
            f"watch_window_minutes ({watch_window_minutes!r}) is below governed minimum ({min_window_minutes} minutes). Fail-closed gate enforced."
        )

    start_time_raw = receipt.get("start_time")
    end_time_raw = receipt.get("end_time")
    if not start_time_raw or not end_time_raw:
        raise ValueError("Watch-window receipt missing verifiable start_time or end_time.")

    start_dt = _parse_iso_utc(start_time_raw)
    end_dt = _parse_iso_utc(end_time_raw)
    duration_seconds = (end_dt - start_dt).total_seconds()
    if duration_seconds < min_window_seconds:
        raise ValueError(
            f"Watch-window duration in receipt ({duration_seconds:.1f}s) is below governed "
            f"{min_window_minutes}-minute minimum ({min_window_seconds}s). Fail-closed gate enforced."
        )

    # Verify canonical SHA-256 integrity hash over all fields + raw timeSeries first
    recorded_hash = receipt.get("canonical_receipt_hash")
    expected_hash = compute_canonical_receipt_hash(receipt)
    if not isinstance(recorded_hash, str) or not recorded_hash or recorded_hash != expected_hash:
        raise ValueError(
            f"Watch-window receipt integrity check failed: canonical_receipt_hash mismatch. "
            f"Expected '{expected_hash}', got '{recorded_hash}'. Tampered receipt rejected. Fail-closed gate enforced."
        )

    services = receipt.get("services")
    if not isinstance(services, dict):
        raise ValueError("Watch-window receipt missing 'services' mapping. Fail-closed gate enforced.")

    recomputed_failures: list[str] = []
    for role in REQUIRED_SERVICE_ROLES:
        svc = services.get(role)
        if not isinstance(svc, dict):
            raise ValueError(
                f"Watch-window receipt missing required service '{role}'. Fail-closed gate enforced."
            )

        svc_name = str(svc.get("service_name") or "").strip()
        rev_name = str(svc.get("revision_name") or "").strip()
        if not svc_name or not rev_name:
            raise ValueError(
                f"Watch-window receipt service '{role}' missing service_name or revision_name. Fail-closed gate enforced."
            )
        if svc.get("traffic_percent") != 100:
            raise ValueError(
                f"Watch-window receipt service '{role}' ({svc_name}) traffic_percent must be 100 "
                f"(got {svc.get('traffic_percent')!r}). Fail-closed gate enforced."
            )

        deployed_digest = str(svc.get("deployed_image_digest") or "").strip()
        manifest_digest = str(svc.get("manifest_image_digest") or "").strip()
        if not SHA256_DIGEST_REGEX.match(deployed_digest) or not SHA256_DIGEST_REGEX.match(manifest_digest):
            raise ValueError(
                f"Watch-window receipt service '{role}' ({svc_name}) has invalid image digest. Fail-closed gate enforced."
            )
        if deployed_digest != manifest_digest or svc.get("digest_matched") is not True:
            raise ValueError(
                f"Watch-window receipt service '{role}' ({svc_name}) deployed digest '{deployed_digest}' "
                f"does not match manifest digest '{manifest_digest}'. Fail-closed gate enforced."
            )

        deployed_img = svc.get("deployed_image")
        manifest_img = svc.get("manifest_image")
        if _extract_image_digest(deployed_img, field_name=f"services.{role}.deployed_image") != deployed_digest:
            raise ValueError(
                f"Watch-window receipt service '{role}' deployed_image digest mismatch. Fail-closed gate enforced."
            )
        if _extract_image_digest(manifest_img, field_name=f"services.{role}.manifest_image") != manifest_digest:
            raise ValueError(
                f"Watch-window receipt service '{role}' manifest_image digest mismatch. Fail-closed gate enforced."
            )

        expected_rc_filter = build_cloud_run_metric_filter(
            CLOUD_RUN_REQUEST_COUNT_METRIC,
            service_name=svc_name,
            revision_name=rev_name,
            gcp_region=gcp_region,
        )
        expected_lat_filter = build_cloud_run_metric_filter(
            CLOUD_RUN_REQUEST_LATENCIES_METRIC,
            service_name=svc_name,
            revision_name=rev_name,
            gcp_region=gcp_region,
        )
        filters = svc.get("query_filters")
        if (
            not isinstance(filters, dict)
            or filters.get("request_count") != expected_rc_filter
            or filters.get("request_latencies") != expected_lat_filter
        ):
            raise ValueError(
                f"Watch-window receipt service '{role}' ({svc_name}) query_filters do not match "
                "canonical Cloud Run revision filters. Fail-closed gate enforced."
            )

        raw_ts = svc.get("raw_time_series")
        if not isinstance(raw_ts, dict):
            raise ValueError(
                f"Watch-window receipt service '{role}' ({svc_name}) missing raw_time_series. Fail-closed gate enforced."
            )

        recomputed_metrics, svc_failures = evaluate_cloud_run_service_metrics(
            role=role,
            service_name=svc_name,
            revision_name=rev_name,
            gcp_project=gcp_proj,
            gcp_region=gcp_region,
            request_count_series=raw_ts.get("request_count"),
            request_latencies_series=raw_ts.get("request_latencies"),
            start_dt=start_dt,
            end_dt=end_dt,
            governance=governance,
        )
        if svc.get("metrics") != recomputed_metrics:
            raise ValueError(
                f"Watch-window receipt service '{role}' ({svc_name}) recorded metrics do not match "
                f"recomputed raw_time_series metrics. Tampered receipt rejected. Fail-closed gate enforced."
            )
        recomputed_failures.extend(svc_failures)

    if recomputed_failures:
        raise ValueError(
            "Watch-window receipt raw timeSeries failed governance re-evaluation: "
            + "; ".join(recomputed_failures)
            + ". Fail-closed gate enforced."
        )

    return receipt


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m shared.observability.watch_window",
        description="Record and verify Cloud Run production watch-window receipts using real Cloud Monitoring metrics.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser(
        "record",
        help="Query Cloud Run Admin API + Cloud Monitoring and write a watch-window receipt.",
    )
    rec.add_argument("--project", required=True, help="GCP project ID hosting production Cloud Run services.")
    rec.add_argument("--region", required=True, help="GCP region (e.g. asia-east1).")
    rec.add_argument("--api-service", required=True, help="Production Cloud Run API service name.")
    rec.add_argument("--web-service", required=True, help="Production Cloud Run Web service name.")
    rec.add_argument("--release-sha", required=True, help="Exact 40-character release commit SHA.")
    rec.add_argument("--manifest", required=True, help="Path to admitted RELEASE_MANIFEST.json.")
    rec.add_argument("--start", required=True, help="Watch window start timestamp (ISO-8601 UTC).")
    rec.add_argument("--end", required=True, help="Watch window end timestamp (ISO-8601 UTC).")
    rec.add_argument("--output", required=True, help="Destination path for watch-window receipt JSON.")
    rec.add_argument(
        "--monitoring-route",
        default=None,
        help="Optional Cloud Monitoring API base URL (default: https://monitoring.googleapis.com/v3).",
    )
    rec.add_argument(
        "--cloud-run-route",
        default=None,
        help="Optional Cloud Run Admin API base URL (default: https://run.googleapis.com/v2).",
    )
    rec.add_argument(
        "--governance-params",
        default=None,
        help="Optional path to prod-ops-governance-parameters.json.",
    )

    ver = sub.add_parser(
        "verify",
        help="Verify a watch-window receipt against release SHA, governance thresholds, raw metrics, and hash.",
    )
    ver.add_argument("--release-sha", required=True, help="Expected 40-character release commit SHA.")
    ver.add_argument("--receipt", required=True, help="Path to watch-window receipt JSON.")
    ver.add_argument(
        "--governance-params",
        default=None,
        help="Optional path to prod-ops-governance-parameters.json.",
    )

    return parser


def main(
    argv: list[str] | None = None,
    *,
    query_transport: Callable[..., tuple[int, str | dict[str, Any]]] | None = None,
) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "record":
            receipt = record_deployment_watch_window_status(
                release_sha=args.release_sha,
                status=None,
                start_time=args.start,
                end_time=args.end,
                gcp_project=args.project,
                gcp_region=args.region,
                api_service=args.api_service,
                web_service=args.web_service,
                manifest_path=args.manifest,
                receipt_path=args.output,
                provider_route=args.monitoring_route,
                cloud_run_route=args.cloud_run_route,
                governance_params_path=args.governance_params,
                query_transport=query_transport,
            )
            summary = {
                "status": receipt["status"],
                "release_sha": receipt["release_sha"],
                "output": str(Path(args.output)),
                "canonical_receipt_hash": receipt["canonical_receipt_hash"],
                "failure_reasons": receipt["failure_reasons"],
            }
            print(json.dumps(summary, indent=2, sort_keys=True))
            return 0 if receipt["status"] == "WATCH_PASSED" else 1

        if args.command == "verify":
            receipt = verify_watch_window_receipt(
                expected_release_sha=args.release_sha,
                receipt_path=args.receipt,
                governance_params_path=args.governance_params,
            )
            summary = {
                "verified": True,
                "status": receipt["status"],
                "release_sha": receipt["release_sha"],
                "canonical_receipt_hash": receipt["canonical_receipt_hash"],
            }
            print(json.dumps(summary, indent=2, sort_keys=True))
            return 0
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
