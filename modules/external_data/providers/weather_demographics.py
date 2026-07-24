from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from shared.observability import new_correlation_id

WEATHER_PROVIDER_ID = "weather.live_api"
DEMOGRAPHICS_PROVIDER_ID = "demographics.live_api"
WEATHER_ENDPOINT_ENV_VAR = "ODP_WEATHER_PROVIDER_URL"
DEMOGRAPHICS_ENDPOINT_ENV_VAR = "ODP_DEMOGRAPHICS_PROVIDER_URL"
WEATHER_API_KEY_ENV_VAR = "ODP_WEATHER_PROVIDER_API_KEY"
DEMOGRAPHICS_API_KEY_ENV_VAR = "ODP_DEMOGRAPHICS_PROVIDER_API_KEY"
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RESPONSE_BYTES = 1_048_576
PLACEHOLDER_HOSTS = {
    "api.weather.example.com",
    "api.demographics.example.com",
    "example.com",
    "localhost.invalid",
}
PLACEHOLDER_VALUES = {"", "changeme", "change-me", "dummy", "example", "placeholder", "todo"}
PRODUCTION_ENVIRONMENTS = {"prod", "production"}
LOCAL_ENVIRONMENTS = {"development", "dev", "local", "test", "testing"}


@dataclass(frozen=True)
class ProviderMetadata:
    source_id: str
    source_name: str
    source_category: str
    provider: str
    acquisition_method: str
    license_type: str
    allowed_usage: tuple[str, ...] = ("display", "feature", "training", "prediction", "report", "audit")
    prohibited_usage: tuple[str, ...] = ()
    retention_policy: str = "90 days"
    refresh_frequency: str = "daily"
    owner: str = "Data Architect"
    integration_owner: str = "Connector Owner"
    dq_profile: str = "standard"
    pii_classification: str = "none"
    cost_model: str = "free"
    status: str = "candidate"


@dataclass(frozen=True)
class ProviderRecordLineage:
    provider_id: str
    endpoint_origin: str
    correlation_id: str
    fetched_at: datetime
    snapshot_id: str

    def to_dict(self) -> dict[str, str]:
        payload = asdict(self)
        payload["fetched_at"] = self.fetched_at.isoformat()
        return payload


class ProviderRecord(dict[str, Any]):
    """Schema-compatible record with request lineage kept out of source fields."""

    def __init__(self, payload: Mapping[str, Any], *, lineage: ProviderRecordLineage) -> None:
        super().__init__(payload)
        self.lineage = lineage


class WeatherDemographicsProviderError(RuntimeError):
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        provider_id: str,
        correlation_id: str,
        code: str,
        endpoint_origin: str = "",
        status_code: int | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.correlation_id = correlation_id
        self.code = code
        self.endpoint_origin = endpoint_origin
        self.status_code = status_code
        super().__init__(
            f"{message} (provider_id={provider_id}, correlation_id={correlation_id}, code={code})"
        )


class ProviderConfigurationError(WeatherDemographicsProviderError):
    pass


class ProviderAuthenticationError(WeatherDemographicsProviderError):
    pass


class ProviderTimeoutError(WeatherDemographicsProviderError):
    retryable = True


class ProviderTransportError(WeatherDemographicsProviderError):
    retryable = True


class ProviderResponseError(WeatherDemographicsProviderError):
    pass


class WeatherProvider(Protocol):
    def get_daily_weather(self, station_id: str, date_str: str) -> dict[str, Any] | None:
        """Fetch one weather observation."""


class DemographicsProvider(Protocol):
    def get_demographics(self, h3_index: str) -> dict[str, Any] | None:
        """Fetch one demographic observation."""


class FixtureWeatherProvider:
    fixture_only = True

    def __init__(self, data_map: dict[tuple[str, str], dict[str, Any]] | None = None) -> None:
        self.data_map = data_map if data_map is not None else {}
        if data_map is None:
            self._load_default_fixture()

    def _load_default_fixture(self) -> None:
        fixture_path = (
            Path(__file__).resolve().parents[3]
            / "tests"
            / "fixtures"
            / "source_data"
            / "external"
            / "weather_daily_snapshot.valid.json"
        )
        if not fixture_path.exists():
            return
        try:
            content = json.loads(fixture_path.read_text(encoding="utf-8"))
            for record in content.get("records", []):
                self.data_map[(record["station_id"], record["date"])] = record
        except (OSError, KeyError, TypeError, ValueError):
            return

    def get_daily_weather(self, station_id: str, date_str: str) -> dict[str, Any] | None:
        return self.data_map.get((station_id, date_str))


class FixtureDemographicsProvider:
    fixture_only = True

    def __init__(self, data_map: dict[str, dict[str, Any]] | None = None) -> None:
        self.data_map = data_map if data_map is not None else {}
        if data_map is None:
            self._load_default_fixture()

    def _load_default_fixture(self) -> None:
        fixture_path = (
            Path(__file__).resolve().parents[3]
            / "tests"
            / "fixtures"
            / "source_data"
            / "external"
            / "demographics_snapshot.valid.json"
        )
        if not fixture_path.exists():
            return
        try:
            content = json.loads(fixture_path.read_text(encoding="utf-8"))
            for record in content.get("records", []):
                self.data_map[record["h3_index"]] = record
        except (OSError, KeyError, TypeError, ValueError):
            return

    def get_demographics(self, h3_index: str) -> dict[str, Any] | None:
        return self.data_map.get(h3_index)


class _BoundedJsonProvider:
    fixture_only = False

    def __init__(
        self,
        *,
        provider_id: str,
        endpoint_url: str | None,
        api_key: str | None,
        deploy_env: str,
        timeout_seconds: float,
        max_response_bytes: int,
        correlation_id_factory: Callable[[], str],
        now: Callable[[], datetime],
    ) -> None:
        self.provider_id = provider_id
        self.endpoint_url = (endpoint_url or "").strip()
        self.api_key = (api_key or "").strip()
        self.deploy_env = deploy_env.strip().lower()
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self._correlation_id_factory = correlation_id_factory
        self._now = now
        self.last_lineage: ProviderRecordLineage | None = None

    def _fetch(self, path: str) -> tuple[Mapping[str, Any], str, str]:
        correlation_id = self._correlation_id_factory()
        endpoint_origin = self._validate_configuration(correlation_id)
        request_url = f"{self.endpoint_url.rstrip('/')}/{path.lstrip('/')}"
        headers = {"Accept": "application/json", "X-Correlation-Id": correlation_id}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        request = urllib.request.Request(request_url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                content_length = response.headers.get("Content-Length", "").strip()
                if content_length and int(content_length) > self.max_response_bytes:
                    raise ProviderResponseError(
                        "provider response exceeded configured byte limit",
                        provider_id=self.provider_id,
                        correlation_id=correlation_id,
                        endpoint_origin=endpoint_origin,
                        code="response_too_large",
                    )
                body = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise self._http_error(
                exc,
                correlation_id=correlation_id,
                endpoint_origin=endpoint_origin,
            ) from exc
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                "provider request exceeded configured timeout",
                provider_id=self.provider_id,
                correlation_id=correlation_id,
                endpoint_origin=endpoint_origin,
                code="timeout",
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                error: WeatherDemographicsProviderError = ProviderTimeoutError(
                    "provider request exceeded configured timeout",
                    provider_id=self.provider_id,
                    correlation_id=correlation_id,
                    endpoint_origin=endpoint_origin,
                    code="timeout",
                )
            else:
                error = ProviderTransportError(
                    "provider request could not connect",
                    provider_id=self.provider_id,
                    correlation_id=correlation_id,
                    endpoint_origin=endpoint_origin,
                    code="transport_error",
                )
            raise error from exc
        except (OSError, ValueError) as exc:
            raise ProviderTransportError(
                "provider request failed",
                provider_id=self.provider_id,
                correlation_id=correlation_id,
                endpoint_origin=endpoint_origin,
                code="transport_error",
            ) from exc

        if len(body) > self.max_response_bytes:
            raise ProviderResponseError(
                "provider response exceeded configured byte limit",
                provider_id=self.provider_id,
                correlation_id=correlation_id,
                endpoint_origin=endpoint_origin,
                code="response_too_large",
            )
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderResponseError(
                "provider response was not valid JSON",
                provider_id=self.provider_id,
                correlation_id=correlation_id,
                endpoint_origin=endpoint_origin,
                code="invalid_json",
            ) from exc
        if not isinstance(payload, Mapping):
            raise ProviderResponseError(
                "provider response must be a JSON object",
                provider_id=self.provider_id,
                correlation_id=correlation_id,
                endpoint_origin=endpoint_origin,
                code="schema_invalid",
            )
        record = payload.get("record", payload)
        if not isinstance(record, Mapping):
            raise ProviderResponseError(
                "provider record must be a JSON object",
                provider_id=self.provider_id,
                correlation_id=correlation_id,
                endpoint_origin=endpoint_origin,
                code="schema_invalid",
            )
        return record, correlation_id, endpoint_origin

    def _validate_configuration(self, correlation_id: str) -> str:
        if not self.endpoint_url:
            raise ProviderConfigurationError(
                "live provider endpoint is required",
                provider_id=self.provider_id,
                correlation_id=correlation_id,
                code="missing_endpoint",
            )
        parsed = urllib.parse.urlsplit(self.endpoint_url)
        endpoint_origin = _redacted_endpoint_origin(parsed)
        if parsed.hostname is None or _is_placeholder_hostname(parsed.hostname):
            raise ProviderConfigurationError(
                "live provider endpoint is a placeholder",
                provider_id=self.provider_id,
                correlation_id=correlation_id,
                endpoint_origin=endpoint_origin,
                code="placeholder_endpoint",
            )
        if parsed.scheme not in {"http", "https"}:
            raise ProviderConfigurationError(
                "live provider endpoint must use HTTP or HTTPS",
                provider_id=self.provider_id,
                correlation_id=correlation_id,
                endpoint_origin=endpoint_origin,
                code="invalid_endpoint",
            )
        if self.deploy_env in PRODUCTION_ENVIRONMENTS and parsed.scheme != "https":
            raise ProviderConfigurationError(
                "production live provider endpoint must use HTTPS",
                provider_id=self.provider_id,
                correlation_id=correlation_id,
                endpoint_origin=endpoint_origin,
                code="insecure_endpoint",
            )
        if not 0 < self.timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ProviderConfigurationError(
                "provider timeout is outside the bounded range",
                provider_id=self.provider_id,
                correlation_id=correlation_id,
                endpoint_origin=endpoint_origin,
                code="invalid_timeout",
            )
        if self.max_response_bytes <= 0:
            raise ProviderConfigurationError(
                "provider response byte limit must be positive",
                provider_id=self.provider_id,
                correlation_id=correlation_id,
                endpoint_origin=endpoint_origin,
                code="invalid_response_limit",
            )
        if self.api_key and self.api_key.lower() in PLACEHOLDER_VALUES:
            raise ProviderConfigurationError(
                "live provider credential is a placeholder",
                provider_id=self.provider_id,
                correlation_id=correlation_id,
                endpoint_origin=endpoint_origin,
                code="placeholder_credential",
            )
        return endpoint_origin

    def _http_error(
        self,
        exc: urllib.error.HTTPError,
        *,
        correlation_id: str,
        endpoint_origin: str,
    ) -> WeatherDemographicsProviderError:
        if exc.code in {401, 403}:
            return ProviderAuthenticationError(
                "provider authorization failed",
                provider_id=self.provider_id,
                correlation_id=correlation_id,
                endpoint_origin=endpoint_origin,
                code="unauthorized",
                status_code=exc.code,
            )
        if exc.code == 429 or 500 <= exc.code <= 599:
            return ProviderTransportError(
                f"provider returned HTTP {exc.code}",
                provider_id=self.provider_id,
                correlation_id=correlation_id,
                endpoint_origin=endpoint_origin,
                code="rate_limited" if exc.code == 429 else "server_error",
                status_code=exc.code,
            )
        return WeatherDemographicsProviderError(
            f"provider returned HTTP {exc.code}",
            provider_id=self.provider_id,
            correlation_id=correlation_id,
            endpoint_origin=endpoint_origin,
            code="http_error",
            status_code=exc.code,
        )

    def _record_with_lineage(
        self,
        record: Mapping[str, Any],
        *,
        correlation_id: str,
        endpoint_origin: str,
    ) -> ProviderRecord:
        snapshot_id = str(record["snapshot_id"])
        lineage = ProviderRecordLineage(
            provider_id=self.provider_id,
            endpoint_origin=endpoint_origin,
            correlation_id=correlation_id,
            fetched_at=self._now().astimezone(UTC),
            snapshot_id=snapshot_id,
        )
        self.last_lineage = lineage
        return ProviderRecord(record, lineage=lineage)


class LiveWeatherProvider(_BoundedJsonProvider):
    def __init__(
        self,
        *,
        endpoint_url: str | None = None,
        api_key: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        correlation_id_factory: Callable[[], str] = new_correlation_id,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        source_env = os.environ if env is None else env
        super().__init__(
            provider_id=WEATHER_PROVIDER_ID,
            endpoint_url=endpoint_url
            if endpoint_url is not None
            else source_env.get(WEATHER_ENDPOINT_ENV_VAR),
            api_key=api_key if api_key is not None else source_env.get(WEATHER_API_KEY_ENV_VAR),
            deploy_env=_deploy_environment(source_env),
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
            correlation_id_factory=correlation_id_factory,
            now=now,
        )

    def get_daily_weather(self, station_id: str, date_str: str) -> dict[str, Any] | None:
        path = (
            f"stations/{urllib.parse.quote(station_id, safe='')}"
            f"/daily/{urllib.parse.quote(date_str, safe='')}"
        )
        record, correlation_id, endpoint_origin = self._fetch(path)
        _validate_weather_record(
            record,
            station_id=station_id,
            date_str=date_str,
            provider_id=self.provider_id,
            correlation_id=correlation_id,
            endpoint_origin=endpoint_origin,
        )
        return self._record_with_lineage(
            record,
            correlation_id=correlation_id,
            endpoint_origin=endpoint_origin,
        )


class LiveDemographicsProvider(_BoundedJsonProvider):
    def __init__(
        self,
        *,
        endpoint_url: str | None = None,
        api_key: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        correlation_id_factory: Callable[[], str] = new_correlation_id,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        source_env = os.environ if env is None else env
        super().__init__(
            provider_id=DEMOGRAPHICS_PROVIDER_ID,
            endpoint_url=endpoint_url
            if endpoint_url is not None
            else source_env.get(DEMOGRAPHICS_ENDPOINT_ENV_VAR),
            api_key=api_key
            if api_key is not None
            else source_env.get(DEMOGRAPHICS_API_KEY_ENV_VAR),
            deploy_env=_deploy_environment(source_env),
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
            correlation_id_factory=correlation_id_factory,
            now=now,
        )

    def get_demographics(self, h3_index: str) -> dict[str, Any] | None:
        path = f"cells/{urllib.parse.quote(h3_index, safe='')}"
        record, correlation_id, endpoint_origin = self._fetch(path)
        _validate_demographics_record(
            record,
            h3_index=h3_index,
            provider_id=self.provider_id,
            correlation_id=correlation_id,
            endpoint_origin=endpoint_origin,
        )
        return self._record_with_lineage(
            record,
            correlation_id=correlation_id,
            endpoint_origin=endpoint_origin,
        )


class LicenseViolationError(Exception):
    """Raised when a provider is used in a prohibited or unlicensed way."""


class ProviderRegistry:
    def __init__(self, *, deploy_env: str = "development") -> None:
        self.deploy_env = deploy_env.strip().lower()
        self._providers: dict[str, tuple[Any, ProviderMetadata]] = {}

    def register(self, source_id: str, provider: Any, metadata: ProviderMetadata) -> None:
        if self.deploy_env in PRODUCTION_ENVIRONMENTS and getattr(provider, "fixture_only", False):
            raise ProviderConfigurationError(
                "fixture provider cannot be registered in production",
                provider_id=source_id,
                correlation_id=new_correlation_id(),
                code="fixture_forbidden",
            )
        self._providers[source_id] = (provider, metadata)

    def get_provider(self, source_id: str) -> Any:
        if source_id not in self._providers:
            raise KeyError(f"Provider {source_id!r} not found in registry")
        return self._providers[source_id][0]

    def get_metadata(self, source_id: str) -> ProviderMetadata:
        if source_id not in self._providers:
            raise KeyError(f"Provider {source_id!r} not found in registry")
        return self._providers[source_id][1]

    def list_providers(self, category: str | None = None) -> list[str]:
        if category is None:
            return list(self._providers)
        return [
            source_id
            for source_id, (_, metadata) in self._providers.items()
            if metadata.source_category == category
        ]

    def verify_usage(self, source_id: str, usage: str) -> bool:
        metadata = self.get_metadata(source_id)
        if metadata.status == "blocked":
            raise LicenseViolationError(f"Provider {source_id} is blocked")
        if usage in metadata.prohibited_usage:
            raise LicenseViolationError(
                f"Usage {usage!r} is explicitly prohibited for {source_id}"
            )
        if usage not in metadata.allowed_usage:
            raise LicenseViolationError(
                f"Usage {usage!r} is not allowed by the license of {source_id}"
            )
        return True


def build_provider_registry(env: Mapping[str, str] | None = None) -> ProviderRegistry:
    source_env = os.environ if env is None else env
    deploy_env = _deploy_environment(source_env)
    registry = ProviderRegistry(deploy_env=deploy_env)
    if deploy_env in LOCAL_ENVIRONMENTS:
        registry.register(
            "CONN-WEATHER-FIXTURE",
            FixtureWeatherProvider(),
            ProviderMetadata(
                source_id="CONN-WEATHER-FIXTURE",
                source_name="Fixture Weather Connector",
                source_category="weather",
                provider="Internal Fixtures",
                acquisition_method="file",
                license_type="internal",
                status="development",
            ),
        )
        registry.register(
            "CONN-DEMOGRAPHICS-FIXTURE",
            FixtureDemographicsProvider(),
            ProviderMetadata(
                source_id="CONN-DEMOGRAPHICS-FIXTURE",
                source_name="Fixture Demographics Connector",
                source_category="demographics",
                provider="Internal Fixtures",
                acquisition_method="file",
                license_type="internal",
                status="development",
            ),
        )

    weather_endpoint = source_env.get(WEATHER_ENDPOINT_ENV_VAR, "")
    registry.register(
        "CONN-WEATHER-LIVE",
        LiveWeatherProvider(env=source_env),
        ProviderMetadata(
            source_id="CONN-WEATHER-LIVE",
            source_name="Live Weather API Connector",
            source_category="weather",
            provider="External Weather Service",
            acquisition_method="api",
            license_type="commercial",
            prohibited_usage=("training",),
            status=_live_provider_status(weather_endpoint, deploy_env),
        ),
    )
    demographics_endpoint = source_env.get(DEMOGRAPHICS_ENDPOINT_ENV_VAR, "")
    registry.register(
        "CONN-DEMOGRAPHICS-LIVE",
        LiveDemographicsProvider(env=source_env),
        ProviderMetadata(
            source_id="CONN-DEMOGRAPHICS-LIVE",
            source_name="Live Demographics API Connector",
            source_category="demographics",
            provider="External Census Service",
            acquisition_method="api",
            license_type="open",
            status=_live_provider_status(demographics_endpoint, deploy_env),
        ),
    )
    return registry


def _validate_weather_record(
    record: Mapping[str, Any],
    *,
    station_id: str,
    date_str: str,
    provider_id: str,
    correlation_id: str,
    endpoint_origin: str,
) -> None:
    required = {
        "station_id",
        "date",
        "temperature_max",
        "temperature_min",
        "precipitation",
        "humidity_avg",
        "snapshot_id",
    }
    _require_fields(
        record,
        required,
        provider_id=provider_id,
        correlation_id=correlation_id,
        endpoint_origin=endpoint_origin,
    )
    if record["station_id"] != station_id or record["date"] != date_str:
        _raise_schema_error(
            "weather provider response identity did not match request",
            provider_id,
            correlation_id,
            endpoint_origin,
        )
    numeric_fields = (
        "temperature_max",
        "temperature_min",
        "precipitation",
        "humidity_avg",
    )
    if any(not _is_number(record[field]) for field in numeric_fields):
        _raise_schema_error(
            "weather provider response contains non-numeric measurements",
            provider_id,
            correlation_id,
            endpoint_origin,
        )
    if (
        float(record["temperature_min"]) > float(record["temperature_max"])
        or float(record["precipitation"]) < 0
        or not 0 <= float(record["humidity_avg"]) <= 100
        or not str(record["snapshot_id"]).strip()
    ):
        _raise_schema_error(
            "weather provider response contains invalid measurements",
            provider_id,
            correlation_id,
            endpoint_origin,
        )


def _validate_demographics_record(
    record: Mapping[str, Any],
    *,
    h3_index: str,
    provider_id: str,
    correlation_id: str,
    endpoint_origin: str,
) -> None:
    required = {
        "h3_index",
        "population_total",
        "household_total",
        "median_income",
        "age_median",
        "snapshot_id",
    }
    _require_fields(
        record,
        required,
        provider_id=provider_id,
        correlation_id=correlation_id,
        endpoint_origin=endpoint_origin,
    )
    if record["h3_index"] != h3_index:
        _raise_schema_error(
            "demographics provider response identity did not match request",
            provider_id,
            correlation_id,
            endpoint_origin,
        )
    numeric_fields = (
        "population_total",
        "household_total",
        "median_income",
        "age_median",
    )
    if any(not _is_number(record[field]) for field in numeric_fields):
        _raise_schema_error(
            "demographics provider response contains non-numeric measurements",
            provider_id,
            correlation_id,
            endpoint_origin,
        )
    if (
        any(float(record[field]) < 0 for field in numeric_fields)
        or float(record["household_total"]) > float(record["population_total"])
        or not str(record["snapshot_id"]).strip()
    ):
        _raise_schema_error(
            "demographics provider response contains invalid measurements",
            provider_id,
            correlation_id,
            endpoint_origin,
        )


def _require_fields(
    record: Mapping[str, Any],
    required: set[str],
    *,
    provider_id: str,
    correlation_id: str,
    endpoint_origin: str,
) -> None:
    if missing := sorted(field for field in required if field not in record):
        _raise_schema_error(
            f"provider response omitted required fields: {', '.join(missing)}",
            provider_id,
            correlation_id,
            endpoint_origin,
        )


def _raise_schema_error(
    message: str,
    provider_id: str,
    correlation_id: str,
    endpoint_origin: str,
) -> None:
    raise ProviderResponseError(
        message,
        provider_id=provider_id,
        correlation_id=correlation_id,
        endpoint_origin=endpoint_origin,
        code="schema_invalid",
    )


def _deploy_environment(env: Mapping[str, str]) -> str:
    return env.get("ODP_DEPLOY_ENV", env.get("APP_ENV", "development")).strip().lower()


def _redacted_endpoint_origin(parsed: urllib.parse.SplitResult) -> str:
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _live_provider_status(endpoint_url: str, deploy_env: str) -> str:
    parsed = urllib.parse.urlsplit(endpoint_url.strip())
    configured = bool(
        parsed.hostname
        and not _is_placeholder_hostname(parsed.hostname)
    )
    if deploy_env in PRODUCTION_ENVIRONMENTS:
        return "production" if configured and parsed.scheme == "https" else "blocked"
    return "staging" if configured else "candidate"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_placeholder_hostname(hostname: str) -> bool:
    normalized = hostname.strip().lower().rstrip(".")
    labels = set(normalized.split("."))
    return bool(
        normalized in PLACEHOLDER_HOSTS
        or normalized.endswith((".example", ".example.com", ".invalid", ".localhost"))
        or labels.intersection(PLACEHOLDER_VALUES - {""})
    )


provider_registry = build_provider_registry()
