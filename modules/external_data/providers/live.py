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
from modules.external_data.geo import GeoPipeline, StaticGeocodeProvider
from modules.integration.connectors.base import (
    ConnectorRecord,
    ConnectorRun,
    RecordLineage,
    first_time,
)
from modules.integration.domain.contracts import ContractIssue
from shared.observability import new_correlation_id

LISTING_PROVIDER_ID = "listing.partner_feed"
LISTING_FEED_ENDPOINT_ENV_VAR = "ODP_LISTING_PROVIDER_FEED_URL"
DEFAULT_REPLAY_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "source_data"
    / "external"
    / "listing_raw_snapshot.valid.json"
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


def _required_listing_credential(provider: ExternalProviderDefinition) -> ProviderCredential:
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


__all__ = [
    "HttpListingFeedClient",
    "ListingFeedClient",
    "ListingFeedIngestionResult",
    "ListingFixtureReplayClient",
    "ListingPartnerFeedProvider",
    "ListingProviderAuthError",
    "ListingProviderError",
    "ListingProviderTimeoutError",
    "normalize_listing_feed_payload",
    "record_idempotency_key",
]
