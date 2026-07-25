"""Live external provider adapters.

Listing and geocode retain deterministic replay support for local tests. POI
and administrative-boundary production adapters only execute in live mode:
they make authenticated HTTP requests, validate checksums and source contracts,
enforce freshness, and preserve provider lineage before connector ingestion.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from modules.external_data.application.external_contracts import (
    external_contract,
    validate_record,
)
from modules.external_data.connectors.external import (
    AdminBoundaryConnector,
    ListingConnector,
    PoiConnector,
)
from modules.external_data.connectors.provider_registry import (
    PRODUCTION_PROVIDER_IDS_ENV_VAR,
    ExternalProviderConfigError,
    ExternalProviderDefinition,
    ExternalProviderMode,
    ProviderAuthMode,
    ProviderCategory,
    ProviderCredential,
    ProviderValidationError,
    ProviderValidationResult,
    external_provider_mode,
    provider_registry,
)
from modules.external_data.geo import (
    GeocodeCandidate,
    GeoPipeline,
    NormalizedAddress,
    StaticGeocodeProvider,
)
from modules.integration.connectors.base import (
    ConnectorRecord,
    ConnectorRun,
    RecordLineage,
    first_time,
)
from modules.integration.domain.contracts import ContractIssue
from shared.observability import new_correlation_id

LISTING_PROVIDER_ID = "listing.partner_feed"
GEOCODE_PROVIDER_ID = "geocode.primary_api"
POI_PROVIDER_ID = "poi.commercial_api"
ADMIN_BOUNDARY_PROVIDER_ID = "admin_boundary.official_dataset"
LISTING_FEED_ENDPOINT_ENV_VAR = "ODP_LISTING_PROVIDER_FEED_URL"
GEOCODE_ENDPOINT_ENV_VAR = "ODP_GEOCODE_PROVIDER_URL"
POI_PROVIDER_PREFIX = "ODP_POI_PROVIDER"
ADMIN_BOUNDARY_PROVIDER_PREFIX = "ODP_ADMIN_BOUNDARY_PROVIDER"
DEFAULT_REPLAY_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "source_data"
    / "external"
    / "listing_raw_snapshot.valid.json"
)
DEFAULT_GEOCODE_REPLAY_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "source_data"
    / "external"
    / "geocode_primary_api.replay.json"
)
INVALID_AUTH_STATUSES = {"expired", "unauthorized", "revoked", "invalid"}
PLACEHOLDER_VALUES = {"", "changeme", "change-me", "todo", "placeholder", "dummy", "example"}
EVENT_FIELDS = ("event_time", "business_time", "occurred_at", "effective_date", "available_from")
OBSERVATION_FIELDS = (
    "observation_time",
    "observed_at",
    "last_verified_at",
    "source_snapshot_time",
    "snapshot_time",
    "received_at",
)


class ExternalDatasetProviderError(RuntimeError):
    """Classified, redacted failure from a production snapshot provider."""

    retryable = False

    def __init__(
        self,
        message: str,
        *,
        provider_id: str,
        correlation_id: str,
        code: str,
        status_code: int | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.correlation_id = correlation_id
        self.code = code
        self.status_code = status_code
        super().__init__(
            f"{message} (provider_id={provider_id}, correlation_id={correlation_id}, code={code})"
        )


class ExternalDatasetProviderConfigError(ExternalDatasetProviderError):
    """Raised before I/O when a required live provider setting is absent."""


class ExternalDatasetProviderAuthError(ExternalDatasetProviderError):
    """Raised for rejected or unusable provider credentials."""


class ExternalDatasetProviderTimeoutError(ExternalDatasetProviderError):
    """Raised when the configured provider timeout expires."""

    retryable = True


class ExternalDatasetProviderRateLimitError(ExternalDatasetProviderError):
    """Raised when provider quota remains exhausted after the retry budget."""

    retryable = True


class ExternalDatasetProviderTransportError(ExternalDatasetProviderError):
    """Raised for retryable HTTP 5xx or network transport failures."""

    retryable = True


class ExternalDatasetProviderResponseError(ExternalDatasetProviderError):
    """Raised when the provider response violates the declared schema."""


class ExternalDatasetProviderChecksumError(ExternalDatasetProviderResponseError):
    """Raised when response bytes do not match provider checksum evidence."""


class ExternalDatasetProviderStaleError(ExternalDatasetProviderResponseError):
    """Raised when the provider observation exceeds the configured freshness SLA."""


@dataclass(frozen=True, repr=False)
class ExternalDatasetCredentialValue:
    env_var: str
    auth_mode: ProviderAuthMode
    value: str

    def __repr__(self) -> str:
        return (
            "ExternalDatasetCredentialValue("
            f"env_var={self.env_var!r}, auth_mode={self.auth_mode.value!r}, "
            "value='<redacted>')"
        )


@dataclass(frozen=True)
class LiveDatasetProviderConfig:
    endpoint_url: str
    timeout_seconds: float
    max_retries: int
    retry_backoff_seconds: float
    rate_limit_per_second: float
    max_age_seconds: int
    require_checksum: bool
    checksum_header: str
    auth_header: str
    page_token_parameter: str
    max_pages: int


@dataclass(frozen=True)
class ProviderSnapshotLineage:
    provider_id: str
    endpoint_origin: str
    snapshot_id: str
    observed_at: datetime
    fetched_at: datetime
    page_checksums: tuple[str, ...]
    aggregate_checksum: str
    page_count: int
    correlation_id: str


@dataclass(frozen=True)
class RawExternalDatasetSnapshot:
    snapshot_id: str
    provider_id: str
    source_contract_id: str
    fetched_at: datetime
    observed_at: datetime
    correlation_id: str
    records: tuple[Mapping[str, Any], ...]
    checksum_sha256: str
    lineage: ProviderSnapshotLineage


@dataclass(frozen=True)
class CanonicalExternalDatasetSnapshot:
    snapshot_id: str
    provider_id: str
    source_contract_id: str
    ingested_at: datetime
    correlation_id: str
    connector_run: ConnectorRun


@dataclass(frozen=True)
class ExternalDatasetIngestionResult:
    mode: ExternalProviderMode
    provider: ExternalProviderDefinition
    raw_snapshot: RawExternalDatasetSnapshot
    canonical_snapshot: CanonicalExternalDatasetSnapshot

    @property
    def connector_run(self) -> ConnectorRun:
        return self.canonical_snapshot.connector_run


@dataclass(frozen=True)
class ProviderSnapshotPayload:
    snapshot_id: str
    observed_at: datetime
    records: tuple[Mapping[str, Any], ...]
    page_checksums: tuple[str, ...]
    aggregate_checksum: str
    page_count: int


class SnapshotHttpClient(Protocol):
    def fetch_snapshot(
        self,
        *,
        provider: ExternalProviderDefinition,
        config: LiveDatasetProviderConfig,
        credential: ExternalDatasetCredentialValue,
        correlation_id: str,
    ) -> ProviderSnapshotPayload: ...


class HttpJsonSnapshotClient:
    """GET a checksummed provider snapshot with bounded pagination and retries."""

    def __init__(
        self,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: float | None = None

    def fetch_snapshot(
        self,
        *,
        provider: ExternalProviderDefinition,
        config: LiveDatasetProviderConfig,
        credential: ExternalDatasetCredentialValue,
        correlation_id: str,
    ) -> ProviderSnapshotPayload:
        records: list[Mapping[str, Any]] = []
        page_checksums: list[str] = []
        snapshot_id = ""
        observed_at: datetime | None = None
        next_page_token = ""
        seen_tokens: set[str] = set()

        for _page_index in range(config.max_pages):
            request_url = _page_url(
                config.endpoint_url,
                config.page_token_parameter,
                next_page_token,
            )
            body, headers = self._request_with_retry(
                request_url=request_url,
                provider=provider,
                config=config,
                credential=credential,
                correlation_id=correlation_id,
            )
            checksum = hashlib.sha256(body).hexdigest()
            expected_checksum = _header_value(headers, config.checksum_header).strip().lower()
            if config.require_checksum and not expected_checksum:
                raise ExternalDatasetProviderChecksumError(
                    f"provider response omitted required {config.checksum_header} header",
                    provider_id=provider.provider_id,
                    correlation_id=correlation_id,
                    code="checksum_missing",
                )
            if expected_checksum and not _checksum_matches(expected_checksum, checksum):
                raise ExternalDatasetProviderChecksumError(
                    "provider response checksum did not match response bytes",
                    provider_id=provider.provider_id,
                    correlation_id=correlation_id,
                    code="checksum_mismatch",
                )
            page_checksums.append(checksum)
            page = _decode_snapshot_page(
                body,
                provider=provider,
                correlation_id=correlation_id,
            )
            if not snapshot_id:
                snapshot_id = page["snapshot_id"]
                observed_at = page["observed_at"]
            elif page["snapshot_id"] != snapshot_id or page["observed_at"] != observed_at:
                raise ExternalDatasetProviderResponseError(
                    "paginated provider response changed snapshot identity or observation time",
                    provider_id=provider.provider_id,
                    correlation_id=correlation_id,
                    code="pagination_snapshot_changed",
                )
            records.extend(page["records"])
            next_page_token = page["next_page_token"]
            if not next_page_token:
                break
            if next_page_token in seen_tokens:
                raise ExternalDatasetProviderResponseError(
                    "provider pagination repeated a page token",
                    provider_id=provider.provider_id,
                    correlation_id=correlation_id,
                    code="pagination_cycle",
                )
            seen_tokens.add(next_page_token)
        else:
            raise ExternalDatasetProviderResponseError(
                "provider pagination exceeded configured maximum pages",
                provider_id=provider.provider_id,
                correlation_id=correlation_id,
                code="pagination_limit_exceeded",
            )

        if observed_at is None:
            raise ExternalDatasetProviderResponseError(
                "provider returned no snapshot page",
                provider_id=provider.provider_id,
                correlation_id=correlation_id,
                code="empty_snapshot_response",
            )
        aggregate_checksum = hashlib.sha256("".join(page_checksums).encode("ascii")).hexdigest()
        return ProviderSnapshotPayload(
            snapshot_id=snapshot_id,
            observed_at=observed_at,
            records=tuple(records),
            page_checksums=tuple(page_checksums),
            aggregate_checksum=aggregate_checksum,
            page_count=len(page_checksums),
        )

    def _request_with_retry(
        self,
        *,
        request_url: str,
        provider: ExternalProviderDefinition,
        config: LiveDatasetProviderConfig,
        credential: ExternalDatasetCredentialValue,
        correlation_id: str,
    ) -> tuple[bytes, Mapping[str, str]]:
        attempts = config.max_retries + 1
        for attempt in range(attempts):
            try:
                self._throttle(config.rate_limit_per_second)
                headers = {
                    "Accept": "application/json",
                    "X-Correlation-Id": correlation_id,
                }
                if credential.auth_mode is ProviderAuthMode.BEARER_TOKEN:
                    headers[config.auth_header] = f"Bearer {credential.value}"
                else:
                    headers[config.auth_header] = credential.value
                request = urllib.request.Request(
                    request_url,
                    headers=headers,
                    method="GET",
                )
                with urllib.request.urlopen(
                    request,
                    timeout=config.timeout_seconds,
                ) as response:
                    return response.read(), dict(response.headers.items())
            except urllib.error.HTTPError as exc:
                error = _http_snapshot_error(
                    exc,
                    provider=provider,
                    correlation_id=correlation_id,
                )
            except TimeoutError as exc:
                error = ExternalDatasetProviderTimeoutError(
                    "provider request exceeded configured timeout",
                    provider_id=provider.provider_id,
                    correlation_id=correlation_id,
                    code="timeout",
                )
                error.__cause__ = exc
            except urllib.error.URLError as exc:
                if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                    error = ExternalDatasetProviderTimeoutError(
                        "provider request exceeded configured timeout",
                        provider_id=provider.provider_id,
                        correlation_id=correlation_id,
                        code="timeout",
                    )
                else:
                    error = ExternalDatasetProviderTransportError(
                        "provider request could not connect",
                        provider_id=provider.provider_id,
                        correlation_id=correlation_id,
                        code="transport_error",
                    )
                error.__cause__ = exc

            if not error.retryable or attempt + 1 >= attempts:
                raise error
            self._sleep(config.retry_backoff_seconds * (2**attempt))
        raise AssertionError("unreachable provider retry loop")

    def _throttle(self, rate_limit_per_second: float) -> None:
        minimum_interval = 1.0 / rate_limit_per_second
        now = self._monotonic()
        if self._last_request_at is not None:
            remaining = minimum_interval - (now - self._last_request_at)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = self._monotonic()


class ListingProviderError(RuntimeError):
    """Fail-closed listing provider error with redacted rendering."""

    def __init__(
        self,
        message: str,
        *,
        provider_id: str,
        correlation_id: str,
        code: str,
    ) -> None:
        self.provider_id = provider_id
        self.correlation_id = correlation_id
        self.code = code
        super().__init__(
            f"{message} (provider_id={provider_id}, correlation_id={correlation_id}, code={code})"
        )


class ListingProviderAuthError(ListingProviderError):
    """Raised when the live provider refuses credentials."""


class ListingProviderTimeoutError(ListingProviderError):
    """Raised when the live provider request times out."""


class ListingProviderRateLimitError(ListingProviderError):
    """Raised when the live provider reports quota or rate-limit exhaustion."""


class GeocodeProviderError(RuntimeError):
    """Fail-closed geocode provider error with redacted rendering."""

    def __init__(
        self,
        message: str,
        *,
        provider_id: str,
        correlation_id: str,
        code: str,
        status_code: int | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.correlation_id = correlation_id
        self.code = code
        self.status_code = status_code
        super().__init__(
            f"{message} (provider_id={provider_id}, correlation_id={correlation_id}, code={code})"
        )


class GeocodeProviderAuthError(GeocodeProviderError):
    """Raised when the live geocoder refuses credentials."""


class GeocodeProviderTimeoutError(GeocodeProviderError):
    """Raised when the live geocoder request times out."""


class GeocodeProviderRateLimitError(GeocodeProviderError):
    """Raised when the live geocoder reports a retryable quota limit."""


class GeocodeQuarantineError(GeocodeProviderError):
    """Raised when a geocode provider fails repeatedly and is quarantined."""

    def __init__(
        self,
        message: str,
        *,
        provider_id: str,
        correlation_id: str,
        code: str = "quarantined",
    ) -> None:
        super().__init__(
            message,
            provider_id=provider_id,
            correlation_id=correlation_id,
            code=code,
        )


@dataclass(frozen=True, repr=False)
class ListingProviderCredentialValue:
    env_var: str
    auth_mode: str
    value: str

    def __repr__(self) -> str:
        return (
            "ListingProviderCredentialValue("
            f"env_var={self.env_var!r}, auth_mode={self.auth_mode!r}, value='<redacted>')"
        )


@dataclass(frozen=True, repr=False)
class GeocodeProviderCredentialValue:
    env_var: str
    auth_mode: str
    value: str

    def __repr__(self) -> str:
        return (
            "GeocodeProviderCredentialValue("
            f"env_var={self.env_var!r}, auth_mode={self.auth_mode!r}, value='<redacted>')"
        )


class ListingFeedClient(Protocol):
    """Provider client boundary for live and replay listing feeds."""

    def fetch_listing_feed(
        self,
        *,
        provider: ExternalProviderDefinition,
        credential: ListingProviderCredentialValue | None,
        correlation_id: str,
    ) -> Mapping[str, Any] | Sequence[Any]: ...


class GeocodeClient(Protocol):
    """Provider client boundary for live and replay geocoders."""

    def geocode(
        self,
        *,
        provider: ExternalProviderDefinition,
        credential: GeocodeProviderCredentialValue | None,
        normalized_address: NormalizedAddress,
        correlation_id: str,
        retry_budget: int,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class RawListingSnapshot:
    snapshot_id: str
    provider_id: str
    source_contract_id: str
    fetched_at: datetime
    correlation_id: str
    records: tuple[Mapping[str, Any], ...]
    idempotency_keys: tuple[str, ...]
    replay_fixture: str = ""

    @property
    def record_count(self) -> int:
        return len(self.records)


@dataclass(frozen=True)
class CanonicalListingSnapshot:
    snapshot_id: str
    provider_id: str
    source_contract_id: str
    ingested_at: datetime
    correlation_id: str
    connector_run: ConnectorRun

    @property
    def canonical_records(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            asdict(record.canonical)
            for record in self.connector_run.accepted
            if record.canonical is not None
        )

    @property
    def quarantine_records(self) -> tuple[ConnectorRecord, ...]:
        return self.connector_run.quarantined


@dataclass(frozen=True)
class ListingFeedIngestionResult:
    mode: ExternalProviderMode
    provider: ExternalProviderDefinition
    raw_snapshot: RawListingSnapshot
    canonical_snapshot: CanonicalListingSnapshot

    @property
    def connector_run(self) -> ConnectorRun:
        return self.canonical_snapshot.connector_run


class ListingFixtureReplayClient:
    """Deterministic replay client for fixture/source-stub mode."""

    def __init__(self, fixture_path: Path | str = DEFAULT_REPLAY_FIXTURE) -> None:
        self.fixture_path = Path(fixture_path)

    def fetch_listing_feed(
        self,
        *,
        provider: ExternalProviderDefinition,
        credential: ListingProviderCredentialValue | None,
        correlation_id: str,
    ) -> Mapping[str, Any]:
        del provider, credential, correlation_id
        return json.loads(self.fixture_path.read_text(encoding="utf-8"))


class HttpListingFeedClient:
    """Minimal HTTP feed client for injected live-provider configuration."""

    def __init__(self, endpoint_url: str, *, timeout_seconds: float = 10.0) -> None:
        self.endpoint_url = endpoint_url.strip()
        self.timeout_seconds = timeout_seconds

    def fetch_listing_feed(
        self,
        *,
        provider: ExternalProviderDefinition,
        credential: ListingProviderCredentialValue | None,
        correlation_id: str,
    ) -> Mapping[str, Any] | Sequence[Any]:
        if not self.endpoint_url:
            raise ListingProviderError(
                f"{LISTING_FEED_ENDPOINT_ENV_VAR} is required for live listing provider fetch",
                provider_id=provider.provider_id,
                correlation_id=correlation_id,
                code="missing_endpoint",
            )
        headers = {"Accept": "application/json", "X-Correlation-Id": correlation_id}
        if credential is not None:
            headers["X-API-Key"] = credential.value
        request = urllib.request.Request(self.endpoint_url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise ListingProviderAuthError(
                    "live listing provider authorization failed",
                    provider_id=provider.provider_id,
                    correlation_id=correlation_id,
                    code="unauthorized",
                ) from exc
            if exc.code == 429:
                raise ListingProviderRateLimitError(
                    "live listing provider rate limit reached",
                    provider_id=provider.provider_id,
                    correlation_id=correlation_id,
                    code="rate_limited",
                ) from exc
            if 500 <= exc.code <= 599:
                raise ListingProviderError(
                    f"live listing provider returned HTTP {exc.code}",
                    provider_id=provider.provider_id,
                    correlation_id=correlation_id,
                    code="server_error",
                ) from exc
            raise ListingProviderError(
                f"live listing provider returned HTTP {exc.code}",
                provider_id=provider.provider_id,
                correlation_id=correlation_id,
                code="http_error",
            ) from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            raise ListingProviderTimeoutError(
                "live listing provider request timed out or could not connect",
                provider_id=provider.provider_id,
                correlation_id=correlation_id,
                code="timeout",
            ) from exc
        return json.loads(payload)


class GeocodeFixtureReplayClient:
    """Deterministic replay client for fixture/source-stub geocoding."""

    def __init__(self, fixture_path: Path | str = DEFAULT_GEOCODE_REPLAY_FIXTURE) -> None:
        self.fixture_path = Path(fixture_path)
        payload = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        self._responses = {
            _normalize_replay_key(item.get("address_raw")): item
            for item in payload.get("responses", ())
            if isinstance(item, Mapping)
        }

    def geocode(
        self,
        *,
        provider: ExternalProviderDefinition,
        credential: GeocodeProviderCredentialValue | None,
        normalized_address: NormalizedAddress,
        correlation_id: str,
        retry_budget: int,
    ) -> Mapping[str, Any]:
        del provider, credential, correlation_id, retry_budget
        return self._responses.get(normalized_address.normalized_address, {})


class HttpGeocodeClient:
    """Minimal HTTP geocoder client for injected live-provider configuration."""

    def __init__(self, endpoint_url: str, *, timeout_seconds: float = 10.0) -> None:
        self.endpoint_url = endpoint_url.strip()
        self.timeout_seconds = timeout_seconds

    def geocode(
        self,
        *,
        provider: ExternalProviderDefinition,
        credential: GeocodeProviderCredentialValue | None,
        normalized_address: NormalizedAddress,
        correlation_id: str,
        retry_budget: int,
    ) -> Mapping[str, Any]:
        del retry_budget
        if not self.endpoint_url:
            raise GeocodeProviderError(
                f"{GEOCODE_ENDPOINT_ENV_VAR} is required for live geocode provider fetch",
                provider_id=provider.provider_id,
                correlation_id=correlation_id,
                code="missing_endpoint",
            )
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Correlation-Id": correlation_id,
        }
        if credential is not None:
            headers["X-API-Key"] = credential.value
        body = json.dumps({"address": normalized_address.normalized_address}).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint_url, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise GeocodeProviderAuthError(
                    "live geocode provider authorization failed",
                    provider_id=provider.provider_id,
                    correlation_id=correlation_id,
                    code="unauthorized",
                ) from exc
            if exc.code == 429:
                raise GeocodeProviderRateLimitError(
                    "live geocode provider rate limit reached",
                    provider_id=provider.provider_id,
                    correlation_id=correlation_id,
                    code="rate_limited",
                ) from exc
            raise GeocodeProviderError(
                f"live geocode provider returned HTTP {exc.code}",
                provider_id=provider.provider_id,
                correlation_id=correlation_id,
                code="http_error",
                status_code=exc.code,
            ) from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            raise GeocodeProviderTimeoutError(
                "live geocode provider request timed out or could not connect",
                provider_id=provider.provider_id,
                correlation_id=correlation_id,
                code="timeout",
            ) from exc
        return json.loads(payload)


class PrimaryGeocodeProvider:
    """Lookup normalized addresses through fixture replay or a live geocoder."""

    def __init__(
        self,
        *,
        client: GeocodeClient | None = None,
        env: Mapping[str, str] | None = None,
        mode: ExternalProviderMode | str | None = None,
        replay_fixture_path: Path | str = DEFAULT_GEOCODE_REPLAY_FIXTURE,
        retry_budget: int = 0,
        correlation_id: str | None = None,
        metrics: Any | None = None,
    ) -> None:
        from shared.observability import default_registry

        self.env = env or os.environ
        self.mode = (
            ExternalProviderMode(mode)
            if isinstance(mode, str)
            else mode
            if mode is not None
            else external_provider_mode(self.env)
        )
        self.provider = _geocode_provider_definition()
        self.retry_budget = retry_budget
        self.correlation_id = correlation_id or new_correlation_id()
        self.client = client or self._default_client(replay_fixture_path)
        self.metrics = metrics or default_registry()

    def _is_retryable_exception(self, exc: Exception) -> bool:
        # Do not retry auth errors
        if isinstance(exc, GeocodeProviderAuthError):
            return False
        # Do not retry configuration errors
        if isinstance(exc, GeocodeProviderError):
            if exc.code in (
                "unauthorized",
                "missing_credential",
                "credential_invalid",
                "missing_endpoint",
            ):
                return False
            if exc.code == "http_error":
                # Only retry 5xx HTTP errors, but if status_code is None (e.g. from tests), default to True.
                if exc.status_code is not None:
                    return 500 <= exc.status_code < 600
                return True
            # If it's a general GeocodeProviderError that is not rate limit or timeout, don't retry
            if not isinstance(exc, (GeocodeProviderRateLimitError, GeocodeProviderTimeoutError)):
                return False
        # Retry rate limit, timeout, ConnectionError, TimeoutError, and ValueError (JSON decode failure)
        if isinstance(
            exc,
            (
                GeocodeProviderRateLimitError,
                GeocodeProviderTimeoutError,
                TimeoutError,
                ConnectionError,
                ValueError,
            ),
        ):
            return True
        return False

    def lookup(self, normalized_address: NormalizedAddress) -> GeocodeCandidate | None:
        import time

        credential = self._credential_or_raise(self.correlation_id)

        # If retry_budget is 0, we don't catch anything; let exceptions raise through directly.
        if self.retry_budget <= 0:
            payload = self.client.geocode(
                provider=self.provider,
                credential=credential,
                normalized_address=normalized_address,
                correlation_id=self.correlation_id,
                retry_budget=self.retry_budget,
            )
            return _candidate_from_geocode_payload(
                payload, normalized_address, self.provider.provider_id
            )

        attempts_remaining = self.retry_budget
        backoff = 0.001

        while True:
            try:
                payload = self.client.geocode(
                    provider=self.provider,
                    credential=credential,
                    normalized_address=normalized_address,
                    correlation_id=self.correlation_id,
                    retry_budget=self.retry_budget,
                )
                return _candidate_from_geocode_payload(
                    payload, normalized_address, self.provider.provider_id
                )
            except Exception as exc:
                if not self._is_retryable_exception(exc):
                    raise exc
                if attempts_remaining <= 0:
                    self.metrics.increment(
                        "external_connector_failure_count", labels={"source": "geo_provider"}
                    )
                    raise GeocodeQuarantineError(
                        "Quarantined: max retries exceeded",
                        provider_id=self.provider.provider_id,
                        correlation_id=self.correlation_id,
                    ) from exc
                attempts_remaining -= 1
                time.sleep(backoff)
                backoff *= 2

    def _default_client(self, replay_fixture_path: Path | str) -> GeocodeClient:
        if self.mode is ExternalProviderMode.FIXTURE:
            return GeocodeFixtureReplayClient(replay_fixture_path)
        return HttpGeocodeClient(str(self.env.get(GEOCODE_ENDPOINT_ENV_VAR, "")))

    def _credential_or_raise(
        self,
        correlation_id: str,
    ) -> GeocodeProviderCredentialValue | None:
        if self.mode is ExternalProviderMode.FIXTURE:
            return None
        credential = _required_geocode_credential(self.provider)
        value = self.env.get(credential.env_var, "")
        if _is_missing_or_placeholder(value):
            raise _config_error(
                self.provider,
                correlation_id,
                env_var=credential.env_var,
                code="missing_credential",
                message=(
                    "Required live provider credential is missing or placeholder; "
                    "set the named env var before startup."
                ),
            )
        if credential.status_env_var:
            status = self.env.get(credential.status_env_var, "").strip().lower()
            if status in INVALID_AUTH_STATUSES:
                raise _config_error(
                    self.provider,
                    correlation_id,
                    env_var=credential.status_env_var,
                    code=f"credential_{status}",
                    message=(
                        "Live provider credential status is not usable; "
                        "rotate or reauthorize before startup."
                    ),
                )
        return GeocodeProviderCredentialValue(
            env_var=credential.env_var,
            auth_mode=credential.auth_mode.value,
            value=value,
        )


class ListingPartnerFeedProvider:
    """Fetch, normalize, and ingest listing partner feed payloads."""

    def __init__(
        self,
        *,
        client: ListingFeedClient | None = None,
        connector: ListingConnector | None = None,
        geo_pipeline: GeoPipeline | None = None,
        env: Mapping[str, str] | None = None,
        mode: ExternalProviderMode | str | None = None,
        replay_fixture_path: Path | str = DEFAULT_REPLAY_FIXTURE,
    ) -> None:
        self.env = os.environ if env is None else env
        self.mode = (
            ExternalProviderMode(mode)
            if isinstance(mode, str)
            else mode
            if mode is not None
            else external_provider_mode(self.env)
        )
        self.provider = _listing_provider_definition()
        contract = external_contract(self.provider.source_contract_id)
        self.connector = connector or ListingConnector(
            contract,
            geo_pipeline=geo_pipeline or GeoPipeline(StaticGeocodeProvider({})),
        )
        self.client = client or self._default_client(replay_fixture_path)

    def fetch_and_ingest(
        self,
        *,
        ingestion_time: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ListingFeedIngestionResult:
        corr = correlation_id or new_correlation_id()
        fetched_at = ingestion_time or datetime.now(UTC)
        assert_listing_provider_selected(
            env=self.env,
            mode=self.mode,
            correlation_id=corr,
        )
        credential = self._credential_or_raise(corr)
        payload = self.client.fetch_listing_feed(
            provider=self.provider,
            credential=credential,
            correlation_id=corr,
        )
        records, snapshot_id = normalize_listing_feed_payload(payload)
        raw_snapshot = RawListingSnapshot(
            snapshot_id=snapshot_id,
            provider_id=self.provider.provider_id,
            source_contract_id=self.provider.source_contract_id,
            fetched_at=fetched_at,
            correlation_id=corr,
            records=tuple(records),
            idempotency_keys=tuple(
                record_idempotency_key(self.provider.provider_id, record) for record in records
            ),
            replay_fixture=(
                str(self.client.fixture_path)
                if isinstance(self.client, ListingFixtureReplayClient)
                else ""
            ),
        )
        connector_run = self._ingest_with_duplicate_quarantine(records, fetched_at)
        canonical_snapshot = CanonicalListingSnapshot(
            snapshot_id=snapshot_id,
            provider_id=self.provider.provider_id,
            source_contract_id=self.provider.source_contract_id,
            ingested_at=fetched_at,
            correlation_id=corr,
            connector_run=connector_run,
        )
        return ListingFeedIngestionResult(
            mode=self.mode,
            provider=self.provider,
            raw_snapshot=raw_snapshot,
            canonical_snapshot=canonical_snapshot,
        )

    def _default_client(self, replay_fixture_path: Path | str) -> ListingFeedClient:
        if self.mode is ExternalProviderMode.FIXTURE:
            return ListingFixtureReplayClient(replay_fixture_path)
        return HttpListingFeedClient(str(self.env.get(LISTING_FEED_ENDPOINT_ENV_VAR, "")))

    def _credential_or_raise(
        self,
        correlation_id: str,
    ) -> ListingProviderCredentialValue | None:
        if self.mode is ExternalProviderMode.FIXTURE:
            return None
        credential = _required_listing_credential(self.provider)
        value = self.env.get(credential.env_var, "")
        if _is_missing_or_placeholder(value):
            raise _config_error(
                self.provider,
                correlation_id,
                env_var=credential.env_var,
                code="missing_credential",
                message=(
                    "Required live provider credential is missing or placeholder; "
                    "set the named env var before startup."
                ),
            )
        if credential.status_env_var:
            status = self.env.get(credential.status_env_var, "").strip().lower()
            if status in INVALID_AUTH_STATUSES:
                raise _config_error(
                    self.provider,
                    correlation_id,
                    env_var=credential.status_env_var,
                    code=f"credential_{status}",
                    message=(
                        "Live provider credential status is not usable; "
                        "rotate or reauthorize before startup."
                    ),
                )
        return ListingProviderCredentialValue(
            env_var=credential.env_var,
            auth_mode=credential.auth_mode.value,
            value=value,
        )

    def _ingest_with_duplicate_quarantine(
        self,
        records: Sequence[Mapping[str, Any]],
        ingestion_time: datetime,
    ) -> ConnectorRun:
        seen: set[str] = set()
        unique_records: list[Mapping[str, Any]] = []
        duplicate_by_index: dict[int, ConnectorRecord] = {}
        for index, record in enumerate(records):
            key = record_idempotency_key(self.provider.provider_id, record)
            if key and key in seen:
                duplicate_by_index[index] = self._duplicate_record(record, key, ingestion_time)
                continue
            if key:
                seen.add(key)
            unique_records.append(record)

        unique_run = self.connector.ingest(unique_records, ingestion_time=ingestion_time)
        unique_iter = iter(unique_run.records)
        merged: list[ConnectorRecord] = []
        for index in range(len(records)):
            duplicate = duplicate_by_index.get(index)
            merged.append(duplicate if duplicate is not None else next(unique_iter))
        return ConnectorRun(
            connector_id=unique_run.connector_id,
            contract_id=unique_run.contract_id,
            canonical_target=unique_run.canonical_target,
            records=tuple(merged),
        )

    def _duplicate_record(
        self,
        record: Mapping[str, Any],
        idempotency_key: str,
        ingestion_time: datetime,
    ) -> ConnectorRecord:
        issue = ContractIssue(
            field="source_listing_id",
            code="duplicate_idempotency_key",
            severity="error",
            message="record duplicates a listing feed idempotency key",
        )
        return ConnectorRecord(
            accepted=False,
            canonical_target=self.connector.target,
            canonical=None,
            lineage=RecordLineage(
                contract_id=self.connector.contract.contract_id,
                source_system=self.connector.contract.source_system,
                source_id=str(record.get("source_id") or self.connector.contract.source_system),
                source_record_id=str(record.get("source_listing_id") or idempotency_key),
                canonical_target=self.connector.target,
                mapping_id=self.connector.contract.mapping_id,
                schema_version=self.connector.schema_version,
                event_time=first_time(record, EVENT_FIELDS),
                observation_time=first_time(record, OBSERVATION_FIELDS),
                ingestion_time=ingestion_time,
                quarantine_reasons=("duplicate_idempotency_key",),
            ),
            issues=(issue,),
        )


class _LiveSnapshotProvider:
    """Shared production path for contract-backed external snapshot datasets."""

    provider_id = ""
    category: ProviderCategory
    contract_id = ""
    env_prefix = ""

    def __init__(
        self,
        *,
        client: SnapshotHttpClient | None = None,
        connector: PoiConnector | AdminBoundaryConnector | None = None,
        config: LiveDatasetProviderConfig | None = None,
        credential: ExternalDatasetCredentialValue | None = None,
        env: Mapping[str, str] | None = None,
        mode: ExternalProviderMode | str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.env = env or os.environ
        self.mode = (
            ExternalProviderMode(mode)
            if isinstance(mode, str)
            else mode
            if mode is not None
            else external_provider_mode(self.env)
        )
        self.provider = _snapshot_provider_definition(
            self.provider_id,
            category=self.category,
            contract_id=self.contract_id,
        )
        self.contract = external_contract(self.contract_id)
        self.client = client or HttpJsonSnapshotClient()
        self.config = config
        self.credential = credential
        self.clock = clock
        self.connector = connector or self._default_connector()

    def _default_connector(self) -> PoiConnector | AdminBoundaryConnector:
        raise NotImplementedError

    def fetch_and_ingest(
        self,
        *,
        ingestion_time: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ExternalDatasetIngestionResult:
        corr = correlation_id or new_correlation_id()
        fetched_at = _ensure_utc_datetime(ingestion_time or self.clock())
        self._assert_selected(corr)
        config = self.config or _load_live_dataset_config(
            self.env,
            provider=self.provider,
            prefix=self.env_prefix,
            correlation_id=corr,
        )
        credential = self.credential or _live_dataset_credential(
            self.env,
            provider=self.provider,
            correlation_id=corr,
        )
        _validate_injected_live_config(
            config,
            credential=credential,
            provider=self.provider,
            correlation_id=corr,
        )
        payload = self.client.fetch_snapshot(
            provider=self.provider,
            config=config,
            credential=credential,
            correlation_id=corr,
        )
        _validate_snapshot_freshness(
            payload.observed_at,
            fetched_at=fetched_at,
            max_age_seconds=config.max_age_seconds,
            provider=self.provider,
            correlation_id=corr,
        )
        records = tuple(
            dict(record, snapshot_id=str(record.get("snapshot_id") or payload.snapshot_id))
            for record in payload.records
        )
        _validate_contract_records(
            records,
            contract_id=self.contract_id,
            provider=self.provider,
            correlation_id=corr,
        )
        connector_run = self.connector.ingest(records, ingestion_time=fetched_at)
        if connector_run.quarantined:
            first = connector_run.quarantined[0]
            codes = ",".join(issue.code for issue in first.issues)
            raise ExternalDatasetProviderResponseError(
                f"provider records failed connector validation ({codes})",
                provider_id=self.provider.provider_id,
                correlation_id=corr,
                code="connector_validation_failed",
            )
        lineage = ProviderSnapshotLineage(
            provider_id=self.provider.provider_id,
            endpoint_origin=_redacted_endpoint_origin(config.endpoint_url),
            snapshot_id=payload.snapshot_id,
            observed_at=payload.observed_at,
            fetched_at=fetched_at,
            page_checksums=payload.page_checksums,
            aggregate_checksum=payload.aggregate_checksum,
            page_count=payload.page_count,
            correlation_id=corr,
        )
        raw_snapshot = RawExternalDatasetSnapshot(
            snapshot_id=payload.snapshot_id,
            provider_id=self.provider.provider_id,
            source_contract_id=self.provider.source_contract_id,
            fetched_at=fetched_at,
            observed_at=payload.observed_at,
            correlation_id=corr,
            records=records,
            checksum_sha256=payload.aggregate_checksum,
            lineage=lineage,
        )
        canonical_snapshot = CanonicalExternalDatasetSnapshot(
            snapshot_id=payload.snapshot_id,
            provider_id=self.provider.provider_id,
            source_contract_id=self.provider.source_contract_id,
            ingested_at=fetched_at,
            correlation_id=corr,
            connector_run=connector_run,
        )
        return ExternalDatasetIngestionResult(
            mode=self.mode,
            provider=self.provider,
            raw_snapshot=raw_snapshot,
            canonical_snapshot=canonical_snapshot,
        )

    def _assert_selected(self, correlation_id: str) -> None:
        if self.mode is not ExternalProviderMode.LIVE:
            raise ExternalDatasetProviderConfigError(
                "production snapshot adapter requires live provider mode",
                provider_id=self.provider.provider_id,
                correlation_id=correlation_id,
                code="live_mode_required",
            )
        deploy_env = (
            self.env.get(
                "ODP_DEPLOY_ENV",
                self.env.get("APP_ENV", "development"),
            )
            .strip()
            .lower()
        )
        selected = {
            item.strip()
            for item in self.env.get(PRODUCTION_PROVIDER_IDS_ENV_VAR, "").split(",")
            if item.strip()
        }
        if deploy_env in {"prod", "production"} and not selected:
            raise ExternalDatasetProviderConfigError(
                "production live mode requires an explicit provider allowlist",
                provider_id=self.provider.provider_id,
                correlation_id=correlation_id,
                code="provider_allowlist_required",
            )
        if selected and self.provider.provider_id not in selected:
            raise ExternalDatasetProviderConfigError(
                "provider is not selected by the production provider allowlist",
                provider_id=self.provider.provider_id,
                correlation_id=correlation_id,
                code="provider_not_selected",
            )


class PoiCommercialApiProvider(_LiveSnapshotProvider):
    """Production POI snapshot adapter backed by the selected commercial API."""

    provider_id = POI_PROVIDER_ID
    category = ProviderCategory.POI
    contract_id = "poi_snapshot"
    env_prefix = POI_PROVIDER_PREFIX

    def _default_connector(self) -> PoiConnector:
        return PoiConnector(
            self.contract,
            geo_pipeline=GeoPipeline(StaticGeocodeProvider({})),
        )


class AdminBoundaryDatasetProvider(_LiveSnapshotProvider):
    """Production official-boundary snapshot adapter with checksum lineage."""

    provider_id = ADMIN_BOUNDARY_PROVIDER_ID
    category = ProviderCategory.ADMIN_BOUNDARY
    contract_id = "admin_boundary_snapshot"
    env_prefix = ADMIN_BOUNDARY_PROVIDER_PREFIX

    def _default_connector(self) -> AdminBoundaryConnector:
        return AdminBoundaryConnector(self.contract)


def _load_live_dataset_config(
    env: Mapping[str, str],
    *,
    provider: ExternalProviderDefinition,
    prefix: str,
    correlation_id: str,
) -> LiveDatasetProviderConfig:
    endpoint_env = f"{prefix}_URL"
    endpoint_url = env.get(endpoint_env, "").strip()
    parsed = urllib.parse.urlsplit(endpoint_url)
    if not endpoint_url:
        raise ExternalDatasetProviderConfigError(
            f"{endpoint_env} is required for live provider fetch",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="missing_endpoint",
        )
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ExternalDatasetProviderConfigError(
            f"{endpoint_env} must be an absolute HTTP(S) URL",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="invalid_endpoint",
        )
    if parsed.username or parsed.password:
        raise ExternalDatasetProviderConfigError(
            f"{endpoint_env} must not embed credentials",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="credential_in_endpoint",
        )
    return LiveDatasetProviderConfig(
        endpoint_url=endpoint_url,
        timeout_seconds=_positive_float_env(
            env,
            f"{prefix}_TIMEOUT_SECONDS",
            default=10.0,
            provider=provider,
            correlation_id=correlation_id,
        ),
        max_retries=_nonnegative_int_env(
            env,
            f"{prefix}_MAX_RETRIES",
            default=2,
            provider=provider,
            correlation_id=correlation_id,
        ),
        retry_backoff_seconds=_nonnegative_float_env(
            env,
            f"{prefix}_RETRY_BACKOFF_SECONDS",
            default=0.25,
            provider=provider,
            correlation_id=correlation_id,
        ),
        rate_limit_per_second=_positive_float_env(
            env,
            f"{prefix}_RATE_LIMIT_PER_SECOND",
            default=5.0,
            provider=provider,
            correlation_id=correlation_id,
        ),
        max_age_seconds=_positive_int_env(
            env,
            f"{prefix}_MAX_AGE_SECONDS",
            default=86_400,
            provider=provider,
            correlation_id=correlation_id,
        ),
        require_checksum=_bool_env(
            env,
            f"{prefix}_REQUIRE_CHECKSUM",
            default=True,
            provider=provider,
            correlation_id=correlation_id,
        ),
        checksum_header=env.get(
            f"{prefix}_CHECKSUM_HEADER",
            "X-Content-SHA256",
        ).strip()
        or "X-Content-SHA256",
        auth_header=env.get(
            f"{prefix}_AUTH_HEADER",
            (
                "Authorization"
                if provider.auth_modes[0] is ProviderAuthMode.BEARER_TOKEN
                else "X-API-Key"
            ),
        ).strip(),
        page_token_parameter=env.get(
            f"{prefix}_PAGE_TOKEN_PARAMETER",
            "page_token",
        ).strip()
        or "page_token",
        max_pages=_positive_int_env(
            env,
            f"{prefix}_MAX_PAGES",
            default=100,
            provider=provider,
            correlation_id=correlation_id,
        ),
    )


def _validate_injected_live_config(
    config: LiveDatasetProviderConfig,
    *,
    credential: ExternalDatasetCredentialValue,
    provider: ExternalProviderDefinition,
    correlation_id: str,
) -> None:
    parsed = urllib.parse.urlsplit(config.endpoint_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ExternalDatasetProviderConfigError(
            "live provider endpoint must be an absolute HTTP(S) URL",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="missing_endpoint" if not config.endpoint_url.strip() else "invalid_endpoint",
        )
    if parsed.username or parsed.password:
        raise ExternalDatasetProviderConfigError(
            "live provider endpoint must not embed credentials",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="credential_in_endpoint",
        )
    if _is_missing_or_placeholder(credential.value):
        raise ExternalDatasetProviderConfigError(
            "required live provider credential is missing or placeholder",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="missing_credential",
        )
    if credential.auth_mode not in provider.auth_modes:
        raise ExternalDatasetProviderConfigError(
            "injected credential auth mode does not match provider registry",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="auth_mode_mismatch",
        )
    if not config.auth_header.strip() or not config.checksum_header.strip():
        raise ExternalDatasetProviderConfigError(
            "provider auth and checksum header names must be configured",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="invalid_configuration",
        )
    if (
        config.timeout_seconds <= 0
        or config.max_retries < 0
        or config.retry_backoff_seconds < 0
        or config.rate_limit_per_second <= 0
        or config.max_age_seconds <= 0
        or config.max_pages <= 0
    ):
        raise ExternalDatasetProviderConfigError(
            "injected live provider limits are invalid",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="invalid_configuration",
        )


def _live_dataset_credential(
    env: Mapping[str, str],
    *,
    provider: ExternalProviderDefinition,
    correlation_id: str,
) -> ExternalDatasetCredentialValue:
    credential = next(
        (item for item in provider.credentials if item.required_in_live),
        None,
    )
    if credential is None:
        raise ExternalDatasetProviderConfigError(
            "provider registry has no required live credential",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="credential_contract_missing",
        )
    value = env.get(credential.env_var, "")
    if _is_missing_or_placeholder(value):
        raise ExternalDatasetProviderConfigError(
            "required live provider credential is missing or placeholder",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="missing_credential",
        )
    if credential.status_env_var:
        status = env.get(credential.status_env_var, "").strip().lower()
        if status in INVALID_AUTH_STATUSES:
            raise ExternalDatasetProviderAuthError(
                "live provider credential status is not usable",
                provider_id=provider.provider_id,
                correlation_id=correlation_id,
                code=f"credential_{status}",
            )
    return ExternalDatasetCredentialValue(
        env_var=credential.env_var,
        auth_mode=credential.auth_mode,
        value=value,
    )


def _snapshot_provider_definition(
    provider_id: str,
    *,
    category: ProviderCategory,
    contract_id: str,
) -> ExternalProviderDefinition:
    for provider in provider_registry():
        if provider.provider_id != provider_id:
            continue
        if provider.category is not category or provider.source_contract_id != contract_id:
            raise ValueError(f"{provider_id} registry metadata is inconsistent")
        return provider
    raise ValueError(f"{provider_id} is not registered")


def _decode_snapshot_page(
    body: bytes,
    *,
    provider: ExternalProviderDefinition,
    correlation_id: str,
) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExternalDatasetProviderResponseError(
            "provider response is not valid UTF-8 JSON",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="invalid_json",
        ) from exc
    if not isinstance(payload, Mapping):
        raise ExternalDatasetProviderResponseError(
            "provider response envelope must be an object",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="invalid_envelope",
        )
    snapshot_id = str(payload.get("snapshot_id") or "").strip()
    if not snapshot_id:
        raise ExternalDatasetProviderResponseError(
            "provider response is missing snapshot_id",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="snapshot_id_missing",
        )
    observed_value = payload.get("observed_at") or payload.get("snapshot_time")
    try:
        observed_at = _parse_required_provider_datetime(observed_value)
    except (TypeError, ValueError) as exc:
        raise ExternalDatasetProviderResponseError(
            "provider response has missing or invalid observed_at",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="observed_at_invalid",
        ) from exc
    raw_records = next(
        (payload[key] for key in ("records", "items", "data") if key in payload),
        None,
    )
    if not isinstance(raw_records, list):
        raise ExternalDatasetProviderResponseError(
            "provider response records must be an array",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="records_invalid",
        )
    if any(not isinstance(item, Mapping) for item in raw_records):
        raise ExternalDatasetProviderResponseError(
            "provider response records must contain only objects",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="record_invalid",
        )
    next_page_token = payload.get("next_page_token")
    if next_page_token is not None and not isinstance(next_page_token, str):
        raise ExternalDatasetProviderResponseError(
            "provider next_page_token must be a string or null",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="page_token_invalid",
        )
    return {
        "snapshot_id": snapshot_id,
        "observed_at": observed_at,
        "records": tuple(dict(item) for item in raw_records),
        "next_page_token": str(next_page_token or ""),
    }


def _validate_contract_records(
    records: Sequence[Mapping[str, Any]],
    *,
    contract_id: str,
    provider: ExternalProviderDefinition,
    correlation_id: str,
) -> None:
    contract = external_contract(contract_id)
    for index, record in enumerate(records):
        validation = validate_record(contract, dict(record))
        if validation.ok:
            continue
        issues = ",".join(f"{issue.field}:{issue.code}" for issue in validation.errors)
        raise ExternalDatasetProviderResponseError(
            f"provider record {index} violates {contract_id} ({issues})",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="record_schema_invalid",
        )


def _validate_snapshot_freshness(
    observed_at: datetime,
    *,
    fetched_at: datetime,
    max_age_seconds: int,
    provider: ExternalProviderDefinition,
    correlation_id: str,
) -> None:
    if observed_at > fetched_at + timedelta(minutes=5):
        raise ExternalDatasetProviderResponseError(
            "provider observation time is unreasonably in the future",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="observed_at_future",
        )
    if fetched_at - observed_at > timedelta(seconds=max_age_seconds):
        raise ExternalDatasetProviderStaleError(
            "provider snapshot exceeds configured freshness limit",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="snapshot_stale",
        )


def _http_snapshot_error(
    exc: urllib.error.HTTPError,
    *,
    provider: ExternalProviderDefinition,
    correlation_id: str,
) -> ExternalDatasetProviderError:
    if exc.code in {401, 403}:
        return ExternalDatasetProviderAuthError(
            "live provider authorization failed",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="unauthorized",
            status_code=exc.code,
        )
    if exc.code == 429:
        return ExternalDatasetProviderRateLimitError(
            "live provider quota or rate limit was exhausted",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="rate_limited",
            status_code=exc.code,
        )
    if 500 <= exc.code <= 599:
        return ExternalDatasetProviderTransportError(
            f"live provider returned HTTP {exc.code}",
            provider_id=provider.provider_id,
            correlation_id=correlation_id,
            code="server_error",
            status_code=exc.code,
        )
    return ExternalDatasetProviderResponseError(
        f"live provider returned HTTP {exc.code}",
        provider_id=provider.provider_id,
        correlation_id=correlation_id,
        code="http_error",
        status_code=exc.code,
    )


def _page_url(endpoint_url: str, parameter: str, token: str) -> str:
    if not token:
        return endpoint_url
    parsed = urllib.parse.urlsplit(endpoint_url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query.append((parameter, token))
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(query),
            parsed.fragment,
        )
    )


def _checksum_matches(expected: str, actual: str) -> bool:
    normalized = expected.removeprefix("sha256=").removeprefix("sha256:")
    return normalized == actual


def _header_value(headers: Mapping[str, str], name: str) -> str:
    wanted = name.lower()
    return next(
        (str(value) for key, value in headers.items() if key.lower() == wanted),
        "",
    )


def _redacted_endpoint_origin(endpoint_url: str) -> str:
    parsed = urllib.parse.urlsplit(endpoint_url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _parse_required_provider_datetime(value: Any) -> datetime:
    if value in (None, ""):
        raise ValueError("missing datetime")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("provider datetime must include timezone")
    return parsed.astimezone(UTC)


def _ensure_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _positive_float_env(
    env: Mapping[str, str],
    name: str,
    *,
    default: float,
    provider: ExternalProviderDefinition,
    correlation_id: str,
) -> float:
    value = _float_env(
        env,
        name,
        default=default,
        provider=provider,
        correlation_id=correlation_id,
    )
    if value <= 0:
        _raise_invalid_setting(name, provider, correlation_id)
    return value


def _nonnegative_float_env(
    env: Mapping[str, str],
    name: str,
    *,
    default: float,
    provider: ExternalProviderDefinition,
    correlation_id: str,
) -> float:
    value = _float_env(
        env,
        name,
        default=default,
        provider=provider,
        correlation_id=correlation_id,
    )
    if value < 0:
        _raise_invalid_setting(name, provider, correlation_id)
    return value


def _float_env(
    env: Mapping[str, str],
    name: str,
    *,
    default: float,
    provider: ExternalProviderDefinition,
    correlation_id: str,
) -> float:
    try:
        return float(env.get(name, str(default)))
    except ValueError:
        _raise_invalid_setting(name, provider, correlation_id)


def _positive_int_env(
    env: Mapping[str, str],
    name: str,
    *,
    default: int,
    provider: ExternalProviderDefinition,
    correlation_id: str,
) -> int:
    value = _int_env(
        env,
        name,
        default=default,
        provider=provider,
        correlation_id=correlation_id,
    )
    if value <= 0:
        _raise_invalid_setting(name, provider, correlation_id)
    return value


def _nonnegative_int_env(
    env: Mapping[str, str],
    name: str,
    *,
    default: int,
    provider: ExternalProviderDefinition,
    correlation_id: str,
) -> int:
    value = _int_env(
        env,
        name,
        default=default,
        provider=provider,
        correlation_id=correlation_id,
    )
    if value < 0:
        _raise_invalid_setting(name, provider, correlation_id)
    return value


def _int_env(
    env: Mapping[str, str],
    name: str,
    *,
    default: int,
    provider: ExternalProviderDefinition,
    correlation_id: str,
) -> int:
    try:
        return int(env.get(name, str(default)))
    except ValueError:
        _raise_invalid_setting(name, provider, correlation_id)


def _bool_env(
    env: Mapping[str, str],
    name: str,
    *,
    default: bool,
    provider: ExternalProviderDefinition,
    correlation_id: str,
) -> bool:
    raw = env.get(name, "true" if default else "false").strip().lower()
    if raw in {"true", "1", "yes", "on"}:
        return True
    if raw in {"false", "0", "no", "off"}:
        return False
    _raise_invalid_setting(name, provider, correlation_id)


def _raise_invalid_setting(
    name: str,
    provider: ExternalProviderDefinition,
    correlation_id: str,
) -> None:
    raise ExternalDatasetProviderConfigError(
        f"{name} has an invalid value",
        provider_id=provider.provider_id,
        correlation_id=correlation_id,
        code="invalid_configuration",
    )


def normalize_listing_feed_payload(
    payload: Mapping[str, Any] | Sequence[Any],
) -> tuple[list[Mapping[str, Any]], str]:
    if isinstance(payload, Mapping):
        records_payload = _records_from_payload(payload)
        envelope_snapshot_id = str(payload.get("snapshot_id") or payload.get("feed_id") or "")
    else:
        records_payload = payload
        envelope_snapshot_id = ""

    records: list[Mapping[str, Any]] = []
    for item in records_payload:
        record = dict(item) if isinstance(item, Mapping) else {"_raw_record": item}
        if envelope_snapshot_id and not record.get("snapshot_id"):
            record["snapshot_id"] = envelope_snapshot_id
        records.append(record)
    snapshot_id = envelope_snapshot_id or _first_snapshot_id(records)
    return records, snapshot_id


def record_idempotency_key(provider_id: str, record: Mapping[str, Any]) -> str:
    source_listing_id = str(record.get("source_listing_id") or "").strip()
    snapshot_id = str(record.get("snapshot_id") or "").strip()
    if not source_listing_id or not snapshot_id:
        return ""
    return f"{provider_id}:{snapshot_id}:{source_listing_id}"


def _records_from_payload(payload: Mapping[str, Any]) -> Sequence[Any]:
    for key in ("records", "listings", "items", "data"):
        value = payload.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return value
    return ()


def _first_snapshot_id(records: Sequence[Mapping[str, Any]]) -> str:
    for record in records:
        snapshot_id = record.get("snapshot_id")
        if snapshot_id:
            return str(snapshot_id)
    return ""


def _listing_provider_definition() -> ExternalProviderDefinition:
    for provider in provider_registry():
        if provider.provider_id == LISTING_PROVIDER_ID:
            if (
                provider.category is not ProviderCategory.LISTING
                or provider.source_contract_id != "listing_raw_snapshot"
            ):
                raise ValueError(f"{LISTING_PROVIDER_ID} registry metadata is inconsistent")
            return provider
    raise ValueError(f"{LISTING_PROVIDER_ID} is not registered")


def assert_listing_provider_selected(
    *,
    env: Mapping[str, str] | None = None,
    mode: ExternalProviderMode | str | None = None,
    correlation_id: str,
) -> None:
    source_env = os.environ if env is None else env
    resolved_mode = (
        ExternalProviderMode(mode)
        if isinstance(mode, str)
        else mode
        if mode is not None
        else external_provider_mode(source_env)
    )
    if resolved_mode is not ExternalProviderMode.LIVE:
        return

    provider = _listing_provider_definition()
    deployment_mode = source_env.get(
        "ODP_DEPLOY_ENV", source_env.get("APP_ENV", "development")
    ).strip().lower()
    selected = {
        item.strip()
        for item in source_env.get(PRODUCTION_PROVIDER_IDS_ENV_VAR, "").split(",")
        if item.strip()
    }
    if deployment_mode in {"prod", "production"} and not selected:
        raise _config_error(
            provider,
            correlation_id,
            env_var=PRODUCTION_PROVIDER_IDS_ENV_VAR,
            code="provider_allowlist_required",
            message="Production live mode requires an explicit provider allowlist.",
        )
    if selected and provider.provider_id not in selected:
        raise _config_error(
            provider,
            correlation_id,
            env_var=PRODUCTION_PROVIDER_IDS_ENV_VAR,
            code="provider_not_selected",
            message="Provider is not selected by the production provider allowlist.",
        )


def _geocode_provider_definition() -> ExternalProviderDefinition:
    for provider in provider_registry():
        if provider.provider_id == GEOCODE_PROVIDER_ID:
            if (
                provider.category is not ProviderCategory.GEOCODE
                or provider.source_contract_id != "geocode_result_snapshot"
            ):
                raise ValueError(f"{GEOCODE_PROVIDER_ID} registry metadata is inconsistent")
            return provider
    raise ValueError(f"{GEOCODE_PROVIDER_ID} is not registered")


def _required_listing_credential(provider: ExternalProviderDefinition) -> ProviderCredential:
    for credential in provider.credentials:
        if credential.required_in_live:
            return credential
    raise ValueError(f"{provider.provider_id} has no required live credential")


def _required_geocode_credential(provider: ExternalProviderDefinition) -> ProviderCredential:
    for credential in provider.credentials:
        if credential.required_in_live:
            return credential
    raise ValueError(f"{provider.provider_id} has no required live credential")


def _config_error(
    provider: ExternalProviderDefinition,
    correlation_id: str,
    *,
    env_var: str,
    code: str,
    message: str,
) -> ExternalProviderConfigError:
    return ExternalProviderConfigError(
        ProviderValidationResult(
            mode=ExternalProviderMode.LIVE,
            correlation_id=correlation_id,
            providers=(provider,),
            errors=(
                ProviderValidationError(
                    provider_id=provider.provider_id,
                    category=provider.category,
                    env_var=env_var,
                    code=code,
                    message=message,
                ),
            ),
        )
    )


def _is_missing_or_placeholder(value: str) -> bool:
    return value.strip().lower() in PLACEHOLDER_VALUES


def _candidate_from_geocode_payload(
    payload: Mapping[str, Any],
    normalized_address: NormalizedAddress,
    provider_id: str,
) -> GeocodeCandidate | None:
    if not payload:
        return None
    result = payload.get("result") if isinstance(payload.get("result"), Mapping) else payload
    request_id = str(
        payload.get("request_id")
        or payload.get("provider_request_id")
        or result.get("request_id")
        or result.get("provider_request_id")
        or ""
    )
    observed_at = _parse_provider_datetime(
        payload.get("observed_at")
        or payload.get("provider_observed_at")
        or result.get("observed_at")
        or result.get("provider_observed_at")
    )
    flags: list[str] = []
    try:
        latitude = float(result.get("latitude"))
        longitude = float(result.get("longitude"))
        confidence = float(result.get("confidence", result.get("geocode_confidence", 0.0)))
    except (TypeError, ValueError):
        latitude = 0.0
        longitude = 0.0
        confidence = 0.0
        flags.append("malformed_provider_response")
    precision = _normalized_geocode_precision(
        str(result.get("precision") or result.get("geocode_precision") or "unknown")
    )
    return GeocodeCandidate(
        latitude=latitude,
        longitude=longitude,
        precision=precision,
        confidence=confidence,
        provider=str(result.get("provider_id") or result.get("geocode_provider") or provider_id),
        admin_city=str(result.get("city") or result.get("admin_city") or normalized_address.city),
        admin_district=str(
            result.get("district") or result.get("admin_district") or normalized_address.district
        ),
        provider_request_id=request_id,
        provider_observed_at=observed_at,
        quality_flags=tuple(flags),
    )


def _normalized_geocode_precision(value: str) -> str:
    precision = value.strip().lower()
    if precision == "address":
        return "rooftop"
    return precision or "unknown"


def _parse_provider_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _normalize_replay_key(value: Any) -> str:
    from modules.external_data.geo import normalize_address

    return normalize_address(str(value or "")).normalized_address


__all__ = [
    "AdminBoundaryDatasetProvider",
    "CanonicalExternalDatasetSnapshot",
    "ExternalDatasetCredentialValue",
    "ExternalDatasetIngestionResult",
    "ExternalDatasetProviderAuthError",
    "ExternalDatasetProviderChecksumError",
    "ExternalDatasetProviderConfigError",
    "ExternalDatasetProviderError",
    "ExternalDatasetProviderRateLimitError",
    "ExternalDatasetProviderResponseError",
    "ExternalDatasetProviderStaleError",
    "ExternalDatasetProviderTimeoutError",
    "ExternalDatasetProviderTransportError",
    "GeocodeClient",
    "GeocodeFixtureReplayClient",
    "GeocodeProviderAuthError",
    "GeocodeProviderError",
    "GeocodeProviderRateLimitError",
    "GeocodeProviderTimeoutError",
    "GeocodeQuarantineError",
    "HttpListingFeedClient",
    "HttpJsonSnapshotClient",
    "HttpGeocodeClient",
    "LiveDatasetProviderConfig",
    "ListingFeedClient",
    "ListingFeedIngestionResult",
    "ListingFixtureReplayClient",
    "ListingPartnerFeedProvider",
    "ListingProviderAuthError",
    "ListingProviderError",
    "ListingProviderRateLimitError",
    "ListingProviderTimeoutError",
    "PrimaryGeocodeProvider",
    "PoiCommercialApiProvider",
    "ProviderSnapshotLineage",
    "ProviderSnapshotPayload",
    "RawExternalDatasetSnapshot",
    "SnapshotHttpClient",
    "assert_listing_provider_selected",
    "normalize_listing_feed_payload",
    "record_idempotency_key",
]
