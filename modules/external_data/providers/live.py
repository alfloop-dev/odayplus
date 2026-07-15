"""Live listing feed provider adapter.

The adapter is intentionally narrow: it fetches a listing feed through an
injected provider client or a deterministic replay client, normalizes the feed
envelope, and delegates data-quality, canonical mapping, geocode enrichment,
and lineage to the existing ListingConnector.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from modules.external_data.application.external_contracts import external_contract
from modules.external_data.connectors.external import ListingConnector
from modules.external_data.connectors.provider_registry import (
    ExternalProviderConfigError,
    ExternalProviderDefinition,
    ExternalProviderMode,
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
LISTING_FEED_ENDPOINT_ENV_VAR = "ODP_LISTING_PROVIDER_FEED_URL"
GEOCODE_ENDPOINT_ENV_VAR = "ODP_GEOCODE_PROVIDER_URL"
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
    ) -> None:
        self.provider_id = provider_id
        self.correlation_id = correlation_id
        self.code = code
        super().__init__(
            f"{message} (provider_id={provider_id}, correlation_id={correlation_id}, code={code})"
        )


class GeocodeProviderAuthError(GeocodeProviderError):
    """Raised when the live geocoder refuses credentials."""


class GeocodeProviderTimeoutError(GeocodeProviderError):
    """Raised when the live geocoder request times out."""


class GeocodeProviderRateLimitError(GeocodeProviderError):
    """Raised when the live geocoder reports a retryable quota limit."""


class GeocodeQuarantineError(RuntimeError):
    """Raised when a geocode provider fails repeatedly and is quarantined."""
    pass



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
    ) -> Mapping[str, Any] | Sequence[Any]:
        ...


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
    ) -> Mapping[str, Any]:
        ...


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
        request = urllib.request.Request(self.endpoint_url, data=body, headers=headers, method="POST")
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


    def lookup(self, normalized_address: NormalizedAddress) -> GeocodeCandidate | None:
        import time
        
        credential = self._credential_or_raise(self.correlation_id)
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
                candidate = _candidate_from_geocode_payload(payload, normalized_address, self.provider.provider_id)
                if candidate is not None and "malformed_provider_response" in candidate.quality_flags:
                    raise ValueError("Malformed response")
                return candidate
            except (GeocodeProviderRateLimitError, GeocodeProviderTimeoutError, GeocodeProviderError, ValueError, TimeoutError, ConnectionError) as exc:
                if attempts_remaining <= 0:
                    self.metrics.increment("external_connector_failure_count", labels={"source": "geo_provider"})
                    raise GeocodeQuarantineError("Quarantined: max retries exceeded") from exc
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
        self.env = env or os.environ
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
        admin_district=str(result.get("district") or result.get("admin_district") or normalized_address.district),
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
    "GeocodeClient",
    "GeocodeFixtureReplayClient",
    "GeocodeProviderAuthError",
    "GeocodeProviderError",
    "GeocodeProviderRateLimitError",
    "GeocodeProviderTimeoutError",
    "GeocodeQuarantineError",
    "HttpListingFeedClient",
    "HttpGeocodeClient",
    "ListingFeedClient",
    "ListingFeedIngestionResult",
    "ListingFixtureReplayClient",
    "ListingPartnerFeedProvider",
    "ListingProviderAuthError",
    "ListingProviderError",
    "ListingProviderRateLimitError",
    "ListingProviderTimeoutError",
    "PrimaryGeocodeProvider",
    "normalize_listing_feed_payload",
    "record_idempotency_key",
]
