"""Bounded, authenticated connectivity probes for required live providers."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from modules.external_data.connectors.provider_registry import (
    REQUIRED_PRODUCTION_PROVIDER_IDS,
    ExternalProviderDefinition,
    ExternalProviderMode,
    ProviderAuthMode,
    ProviderValidationResult,
)
from shared.observability import new_correlation_id

PROBE_TIMEOUT_ENV_VAR = "ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS"
PROBE_MAX_AGE_ENV_VAR = "ODP_EXTERNAL_PROVIDER_PROBE_MAX_AGE_SECONDS"
DEFAULT_PROBE_TIMEOUT_SECONDS = 3.0
DEFAULT_PROBE_MAX_AGE_SECONDS = 60
MAX_PROBE_TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class ProviderProbeEvidence:
    provider_id: str
    configuration_valid: bool
    connectivity_healthy: bool
    authentication_accepted: bool
    response_valid: bool
    schema_valid: bool
    checked_at: datetime
    expires_at: datetime
    latency_ms: int | None
    http_status: int | None
    reason_code: str

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "configuration_valid": self.configuration_valid,
            "connectivity_healthy": self.connectivity_healthy,
            "authentication_accepted": self.authentication_accepted,
            "response_valid": self.response_valid,
            "schema_valid": self.schema_valid,
            "checked_at": self.checked_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "latency_ms": self.latency_ms,
            "http_status": self.http_status,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class ProviderConnectivityResult:
    mode: ExternalProviderMode
    correlation_id: str
    configuration_valid: bool
    connectivity_healthy: bool
    checked_at: datetime
    expires_at: datetime
    required_provider_ids: tuple[str, ...]
    probes: tuple[ProviderProbeEvidence, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "healthy" if self.connectivity_healthy else "unhealthy",
            "mode": self.mode.value,
            "configuration_valid": self.configuration_valid,
            "connectivity_healthy": self.connectivity_healthy,
            "correlation_id": self.correlation_id,
            "checked_at": self.checked_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "required_provider_ids": list(self.required_provider_ids),
            "probes": [probe.to_dict() for probe in self.probes],
        }


@dataclass(frozen=True)
class _ProbeResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def probe_external_provider_connectivity(
    *,
    validation: ProviderValidationResult,
    env: Mapping[str, str] | None = None,
    correlation_id: str | None = None,
    clock: Callable[[], datetime] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> ProviderConnectivityResult:
    """Probe all required providers without treating configuration as connectivity."""

    source_env = env or os.environ
    now_fn = clock or (lambda: datetime.now(UTC))
    corr = correlation_id or new_correlation_id()
    required_ids = tuple(sorted(REQUIRED_PRODUCTION_PROVIDER_IDS))
    max_age_seconds = _bounded_int(
        source_env.get(PROBE_MAX_AGE_ENV_VAR),
        default=DEFAULT_PROBE_MAX_AGE_SECONDS,
        minimum=1,
        maximum=300,
    )
    timeout_seconds = _bounded_float(
        source_env.get(PROBE_TIMEOUT_ENV_VAR),
        default=DEFAULT_PROBE_TIMEOUT_SECONDS,
        minimum=0.05,
        maximum=MAX_PROBE_TIMEOUT_SECONDS,
    )
    started_at = _as_utc(now_fn())
    expires_at = started_at + timedelta(seconds=max_age_seconds)
    providers = {provider.provider_id: provider for provider in validation.providers}
    errors_by_provider = _configuration_errors_by_provider(validation)

    configuration_valid = (
        validation.mode is ExternalProviderMode.LIVE
        and validation.ok
        and set(required_ids) <= providers.keys()
    )
    if not configuration_valid:
        probes = tuple(
            ProviderProbeEvidence(
                provider_id=provider_id,
                configuration_valid=False,
                connectivity_healthy=False,
                authentication_accepted=False,
                response_valid=False,
                schema_valid=False,
                checked_at=started_at,
                expires_at=expires_at,
                latency_ms=None,
                http_status=None,
                reason_code=_configuration_reason(
                    provider_id=provider_id,
                    validation=validation,
                    providers=providers,
                    errors_by_provider=errors_by_provider,
                ),
            )
            for provider_id in required_ids
        )
        return ProviderConnectivityResult(
            mode=validation.mode,
            correlation_id=corr,
            configuration_valid=False,
            connectivity_healthy=False,
            checked_at=started_at,
            expires_at=expires_at,
            required_provider_ids=required_ids,
            probes=probes,
        )

    executor = ThreadPoolExecutor(
        max_workers=len(required_ids),
        thread_name_prefix="external-provider-probe",
    )
    futures: dict[Future[ProviderProbeEvidence], str] = {
        executor.submit(
            _probe_provider,
            provider=providers[provider_id],
            env=source_env,
            correlation_id=corr,
            timeout_seconds=timeout_seconds,
            max_age_seconds=max_age_seconds,
            clock=now_fn,
            monotonic=monotonic,
        ): provider_id
        for provider_id in required_ids
    }
    done, pending = wait(futures, timeout=timeout_seconds + 0.25)
    evidence_by_provider = {futures[future]: future.result() for future in done}
    timeout_checked_at = _as_utc(now_fn())
    for future in pending:
        future.cancel()
        provider_id = futures[future]
        evidence_by_provider[provider_id] = ProviderProbeEvidence(
            provider_id=provider_id,
            configuration_valid=True,
            connectivity_healthy=False,
            authentication_accepted=False,
            response_valid=False,
            schema_valid=False,
            checked_at=timeout_checked_at,
            expires_at=timeout_checked_at + timedelta(seconds=max_age_seconds),
            latency_ms=int(timeout_seconds * 1000),
            http_status=None,
            reason_code="timeout",
        )
    executor.shutdown(wait=False, cancel_futures=True)

    probes = tuple(evidence_by_provider[provider_id] for provider_id in required_ids)
    checked_at = max(probe.checked_at for probe in probes)
    result_expires_at = min(probe.expires_at for probe in probes)
    return ProviderConnectivityResult(
        mode=validation.mode,
        correlation_id=corr,
        configuration_valid=True,
        connectivity_healthy=all(probe.connectivity_healthy for probe in probes),
        checked_at=checked_at,
        expires_at=result_expires_at,
        required_provider_ids=required_ids,
        probes=probes,
    )


def _probe_provider(
    *,
    provider: ExternalProviderDefinition,
    env: Mapping[str, str],
    correlation_id: str,
    timeout_seconds: float,
    max_age_seconds: int,
    clock: Callable[[], datetime],
    monotonic: Callable[[], float],
) -> ProviderProbeEvidence:
    started = monotonic()
    status: int | None = None
    authentication_accepted = False
    response_valid = False
    schema_valid = False
    reason_code = "unknown"
    try:
        response = _request_provider(
            provider=provider,
            env=env,
            correlation_id=correlation_id,
            timeout_seconds=timeout_seconds,
        )
        status = response.status
        if status != 200:
            raise _ProbeFailure("unexpected_http_status", http_status=status)
        authentication_accepted = True
        _validate_content_type(response.headers)
        response_valid = True
        _validate_provider_payload(provider.provider_id, response.body)
        if provider.provider_id in {
            "poi.commercial_api",
            "admin_boundary.official_dataset",
        }:
            _validate_checksum(response.headers, response.body)
        schema_valid = True
        reason_code = "ok"
    except _ProbeFailure as exc:
        status = exc.http_status
        reason_code = exc.reason_code
    except Exception:
        reason_code = "internal_probe_error"
    completed_at = _as_utc(clock())
    latency_ms = max(0, int((monotonic() - started) * 1000))
    return ProviderProbeEvidence(
        provider_id=provider.provider_id,
        configuration_valid=True,
        connectivity_healthy=(authentication_accepted and response_valid and schema_valid),
        authentication_accepted=authentication_accepted,
        response_valid=response_valid,
        schema_valid=schema_valid,
        checked_at=completed_at,
        expires_at=completed_at + timedelta(seconds=max_age_seconds),
        latency_ms=latency_ms,
        http_status=status,
        reason_code=reason_code,
    )


def _request_provider(
    *,
    provider: ExternalProviderDefinition,
    env: Mapping[str, str],
    correlation_id: str,
    timeout_seconds: float,
) -> _ProbeResponse:
    if provider.endpoint_env_var is None:
        raise _ProbeFailure("endpoint_missing")
    endpoint = env.get(provider.endpoint_env_var, "").strip()
    credential = provider.credentials[0]
    credential_value = env.get(credential.env_var, "")
    headers = {
        "Accept": "application/json",
        "X-Correlation-Id": correlation_id,
        "User-Agent": "oday-plus-provider-connectivity-probe/1",
    }
    if credential.auth_mode is ProviderAuthMode.BEARER_TOKEN:
        headers["Authorization"] = f"Bearer {credential_value}"
    else:
        headers["X-API-Key"] = credential_value

    body: bytes | None = None
    method = "GET"
    if provider.provider_id == "geocode.primary_api":
        method = "POST"
        headers["Content-Type"] = "application/json"
        body = json.dumps(
            {
                "address": env.get(
                    "ODP_GEOCODE_PROVIDER_PROBE_ADDRESS",
                    "台北市信義區市府路1號",
                )
            },
            ensure_ascii=False,
        ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers=headers,
        method=method,
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout_seconds) as response:  # noqa: S310
            response_body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(response_body) > MAX_RESPONSE_BYTES:
                raise _ProbeFailure("response_too_large", http_status=response.status)
            if response.status != 200:
                raise _ProbeFailure(
                    "unexpected_http_status",
                    http_status=response.status,
                )
            return _ProbeResponse(
                status=response.status,
                headers=dict(response.headers.items()),
                body=response_body,
            )
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            reason_code = "redirect_rejected"
        elif exc.code in {401, 403}:
            reason_code = "unauthorized"
        elif exc.code == 429:
            reason_code = "rate_limited"
        elif 500 <= exc.code < 600:
            reason_code = "provider_server_error"
        else:
            reason_code = "http_error"
        raise _ProbeFailure(reason_code, http_status=exc.code) from None
    except TimeoutError:
        raise _ProbeFailure("timeout") from None
    except urllib.error.URLError as exc:
        reason_code = "timeout" if isinstance(exc.reason, TimeoutError) else "transport_error"
        raise _ProbeFailure(reason_code) from None


def _validate_content_type(headers: Mapping[str, str]) -> None:
    content_type = _header_value(headers, "Content-Type").lower()
    if "application/json" not in content_type:
        raise _ProbeFailure("invalid_content_type", http_status=200)


def _validate_checksum(headers: Mapping[str, str], body: bytes) -> None:
    expected = _header_value(headers, "X-Content-SHA256").strip().lower()
    actual = hashlib.sha256(body).hexdigest()
    if not expected:
        raise _ProbeFailure("checksum_missing", http_status=200)
    if expected.removeprefix("sha256:") != actual:
        raise _ProbeFailure("checksum_mismatch", http_status=200)


def _validate_provider_payload(provider_id: str, body: bytes) -> None:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _ProbeFailure("invalid_json", http_status=200) from None
    if not isinstance(payload, Mapping):
        raise _ProbeFailure("schema_invalid", http_status=200)
    if provider_id == "listing.partner_feed":
        if not _nonempty_string(payload.get("snapshot_id")) or not isinstance(
            payload.get("records"), list
        ):
            raise _ProbeFailure("schema_invalid", http_status=200)
        return
    if provider_id in {
        "poi.commercial_api",
        "admin_boundary.official_dataset",
    }:
        if (
            not _nonempty_string(payload.get("snapshot_id"))
            or not _valid_timestamp(payload.get("observed_at"))
            or not isinstance(payload.get("records"), list)
        ):
            raise _ProbeFailure("schema_invalid", http_status=200)
        return
    if provider_id == "geocode.primary_api":
        result = payload.get("result")
        if (
            not _nonempty_string(payload.get("request_id"))
            or not _valid_timestamp(payload.get("observed_at"))
            or not isinstance(result, Mapping)
            or not _valid_coordinate(result.get("latitude"), minimum=-90, maximum=90)
            or not _valid_coordinate(result.get("longitude"), minimum=-180, maximum=180)
        ):
            raise _ProbeFailure("schema_invalid", http_status=200)
        return
    raise _ProbeFailure("unsupported_provider", http_status=200)


class _ProbeFailure(RuntimeError):
    def __init__(self, reason_code: str, *, http_status: int | None = None) -> None:
        self.reason_code = reason_code
        self.http_status = http_status
        super().__init__(reason_code)


def _configuration_errors_by_provider(
    validation: ProviderValidationResult,
) -> dict[str, tuple[str, ...]]:
    result: dict[str, list[str]] = {}
    for error in validation.errors:
        result.setdefault(error.provider_id, []).append(error.code)
    return {provider_id: tuple(codes) for provider_id, codes in result.items()}


def _configuration_reason(
    *,
    provider_id: str,
    validation: ProviderValidationResult,
    providers: Mapping[str, ExternalProviderDefinition],
    errors_by_provider: Mapping[str, tuple[str, ...]],
) -> str:
    provider_errors = errors_by_provider.get(provider_id)
    if provider_errors:
        return provider_errors[0]
    if validation.mode is not ExternalProviderMode.LIVE:
        return "provider_mode_not_live"
    if provider_id not in providers:
        return "required_provider_not_selected"
    return "configuration_invalid"


def _header_value(headers: Mapping[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return str(value)
    return ""


def _valid_timestamp(value: object) -> bool:
    if not _nonempty_string(value):
        return False
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _valid_coordinate(value: object, *, minimum: float, maximum: float) -> bool:
    if isinstance(value, bool):
        return False
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(numeric) and minimum <= numeric <= maximum


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _bounded_float(
    value: str | None,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        parsed = float(value) if value is not None else default
    except ValueError:
        return default
    return min(maximum, max(minimum, parsed))


def _bounded_int(
    value: str | None,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value) if value is not None else default
    except ValueError:
        return default
    return min(maximum, max(minimum, parsed))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
