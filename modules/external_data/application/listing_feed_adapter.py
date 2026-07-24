from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import httpx

from modules.external_data.application.listing_feed_store import (
    FileListingFeedIngestionStore,
    ListingFeedIngestionReceipt,
    ListingFeedIngestionStore,
    ListingFeedSnapshot,
)

if TYPE_CHECKING:
    from modules.listing.application.pipeline import ListingPipeline

_SNAPSHOT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ListingFeedClientError(RuntimeError):
    """Classified, redacted external listing ingestion failure."""

    code = "listing_feed_error"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        correlation_id: str = "",
        status_code: int | None = None,
        retry_after: str | None = None,
    ) -> None:
        self.correlation_id = correlation_id
        self.status_code = status_code
        self.retry_after = retry_after
        suffix = f" (code={self.code}"
        if correlation_id:
            suffix += f", correlation_id={correlation_id}"
        if status_code is not None:
            suffix += f", status_code={status_code}"
        super().__init__(message + suffix + ")")


class ListingFeedConfigurationError(ListingFeedClientError):
    code = "configuration_error"


class UnauthorizedError(ListingFeedClientError):
    code = "unauthorized"


class TimeoutError(ListingFeedClientError):
    code = "timeout"
    retryable = True


class RateLimitError(ListingFeedClientError):
    code = "rate_limited"
    retryable = True


class UpstreamError(ListingFeedClientError):
    code = "upstream_error"
    retryable = True


class TransportError(ListingFeedClientError):
    code = "transport_error"
    retryable = True


class FeedSchemaError(ListingFeedClientError):
    code = "schema_error"


class IdempotencyConflictError(ListingFeedClientError):
    code = "idempotency_key_reused"


@dataclass(frozen=True)
class ListingFeedResponse:
    payload: dict[str, Any]
    raw_bytes: bytes
    checksum_sha256: str
    correlation_id: str
    fetched_at: datetime
    observed_at: datetime
    source_endpoint: str


class ListingFeedClient:
    """Bounded HTTPS client for an approved external listing feed endpoint."""

    def __init__(
        self,
        api_url: str,
        api_key: str | None = None,
        timeout: float = 10.0,
        *,
        approved_endpoint_url: str | None = None,
        auth_header: str = "X-API-Key",
        max_response_bytes: int = 10 * 1024 * 1024,
        max_records: int = 10_000,
        allow_insecure_localhost: bool = False,
    ) -> None:
        self.api_url = api_url.strip()
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self.approved_endpoint_url = (
            approved_endpoint_url.strip() if approved_endpoint_url else None
        )
        self.auth_header = auth_header
        self.max_response_bytes = max_response_bytes
        self.max_records = max_records
        self.allow_insecure_localhost = allow_insecure_localhost

    def fetch_listings(self, *, correlation_id: str) -> ListingFeedResponse:
        request_url = self._validated_url(correlation_id)
        if not self.api_key:
            raise UnauthorizedError(
                "Live listing provider credential is required.",
                correlation_id=correlation_id,
            )
        if not 0 < self.timeout <= 60:
            raise ListingFeedConfigurationError(
                "Listing provider timeout must be greater than 0 and at most 60 seconds.",
                correlation_id=correlation_id,
            )
        if self.max_response_bytes <= 0 or self.max_records <= 0:
            raise ListingFeedConfigurationError(
                "Listing provider response bounds must be positive.",
                correlation_id=correlation_id,
            )

        headers = {
            "Accept": "application/json",
            "X-Correlation-Id": correlation_id,
            self.auth_header: self.api_key,
        }
        timeout = httpx.Timeout(self.timeout)
        try:
            with httpx.Client(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream("GET", request_url, headers=headers) as response:
                    self._raise_for_status(response, correlation_id)
                    content_type = response.headers.get("content-type", "").lower()
                    if "application/json" not in content_type:
                        raise FeedSchemaError(
                            "Listing provider response must use application/json.",
                            correlation_id=correlation_id,
                            status_code=response.status_code,
                        )
                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            declared_length = int(content_length)
                        except ValueError as exc:
                            raise FeedSchemaError(
                                "Listing provider returned an invalid Content-Length.",
                                correlation_id=correlation_id,
                                status_code=response.status_code,
                            ) from exc
                        if declared_length > self.max_response_bytes:
                            raise FeedSchemaError(
                                "Listing provider response exceeded the configured byte limit.",
                                correlation_id=correlation_id,
                                status_code=response.status_code,
                            )
                    chunks: list[bytes] = []
                    received = 0
                    for chunk in response.iter_bytes():
                        received += len(chunk)
                        if received > self.max_response_bytes:
                            raise FeedSchemaError(
                                "Listing provider response exceeded the configured byte limit.",
                                correlation_id=correlation_id,
                                status_code=response.status_code,
                            )
                        chunks.append(chunk)
                    raw_bytes = b"".join(chunks)
        except ListingFeedClientError:
            raise
        except httpx.TimeoutException as exc:
            raise TimeoutError(
                "Listing provider request exceeded the configured timeout.",
                correlation_id=correlation_id,
            ) from exc
        except httpx.TransportError as exc:
            raise TransportError(
                "Listing provider request could not connect.",
                correlation_id=correlation_id,
            ) from exc

        payload = _decode_payload(
            raw_bytes,
            correlation_id=correlation_id,
            max_records=self.max_records,
        )
        fetched_at = datetime.now(UTC)
        observed_at = _payload_observed_at(payload) or fetched_at
        return ListingFeedResponse(
            payload=payload,
            raw_bytes=raw_bytes,
            checksum_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            correlation_id=correlation_id,
            fetched_at=fetched_at,
            observed_at=observed_at,
            source_endpoint=_safe_endpoint(request_url),
        )

    def _validated_url(self, correlation_id: str) -> str:
        if not self.api_url:
            raise ListingFeedConfigurationError(
                "Approved listing provider endpoint is required.",
                correlation_id=correlation_id,
            )
        parsed = urlparse(self.api_url)
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.scheme not in {"https", "http"}
        ):
            raise ListingFeedConfigurationError(
                "Listing provider endpoint must be an absolute URL without embedded credentials or fragments.",
                correlation_id=correlation_id,
            )
        local = _is_loopback(parsed.hostname)
        if parsed.scheme != "https" and not (
            self.allow_insecure_localhost and local
        ):
            raise ListingFeedConfigurationError(
                "Live listing provider endpoint must use HTTPS.",
                correlation_id=correlation_id,
            )
        if self.approved_endpoint_url and (
            _normalized_endpoint(self.api_url)
            != _normalized_endpoint(self.approved_endpoint_url)
        ):
            raise ListingFeedConfigurationError(
                "Listing provider endpoint does not match the approved endpoint.",
                correlation_id=correlation_id,
            )
        return self.api_url

    @staticmethod
    def _raise_for_status(
        response: httpx.Response,
        correlation_id: str,
    ) -> None:
        status = response.status_code
        if status == 200:
            return
        if status in {401, 403}:
            raise UnauthorizedError(
                "Listing provider rejected the configured credential.",
                correlation_id=correlation_id,
                status_code=status,
            )
        if status == 429:
            raise RateLimitError(
                "Listing provider rate limit was reached.",
                correlation_id=correlation_id,
                status_code=status,
                retry_after=response.headers.get("retry-after"),
            )
        if 500 <= status <= 599:
            raise UpstreamError(
                "Listing provider returned a server error.",
                correlation_id=correlation_id,
                status_code=status,
            )
        raise ListingFeedClientError(
            "Listing provider returned an unsupported HTTP response.",
            correlation_id=correlation_id,
            status_code=status,
        )


class LiveListingFeedAdapter:
    """Ingest an external feed into canonical repositories and snapshot storage."""

    def __init__(
        self,
        client: ListingFeedClient,
        pipeline: ListingPipeline,
        snapshot_dir: str = "data/snapshots",
        quarantine_dir: str = "data/quarantine",
        *,
        store: ListingFeedIngestionStore | None = None,
        mode: str = "fixture",
        tenant_id: str = "local",
        provider_id: str = "listing.partner_feed",
    ) -> None:
        normalized_mode = mode.strip().lower()
        if normalized_mode not in {"fixture", "live"}:
            raise ValueError("mode must be fixture or live")
        self.client = client
        self.pipeline = pipeline
        self.mode = normalized_mode
        self.tenant_id = tenant_id.strip()
        self.provider_id = provider_id.strip()
        if self.mode == "live":
            if store is None:
                raise ListingFeedConfigurationError(
                    "Live listing ingestion requires a canonical durable snapshot store."
                )
            if not self.tenant_id:
                raise ListingFeedConfigurationError(
                    "tenant_id is required for live listing ingestion."
                )
            if not self.provider_id:
                raise ListingFeedConfigurationError(
                    "provider_id is required for live listing ingestion."
                )
            if not store.is_durable:
                raise ListingFeedConfigurationError(
                    "Live listing ingestion requires a canonical durable snapshot store."
                )
        self.store = store or FileListingFeedIngestionStore(
            snapshot_dir,
            quarantine_dir,
        )

    def process_feed(
        self,
        force_replay: bool = False,
        replay_payload: dict[str, Any] | None = None,
        *,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        corr = (correlation_id or f"corr-listing-{uuid4()}").strip()
        if not corr or len(corr) > 200:
            raise ListingFeedConfigurationError(
                "correlation_id must be between 1 and 200 characters."
            )
        if self.mode == "live" and replay_payload is not None:
            raise ListingFeedConfigurationError(
                "Fixture replay payloads are prohibited in live mode.",
                correlation_id=corr,
            )
        requested_key = (idempotency_key or "").strip()
        if requested_key and not force_replay:
            existing = self.store.get_receipt(
                tenant_id=self.tenant_id,
                provider_id=self.provider_id,
                idempotency_key=requested_key,
            )
            if existing is not None:
                return existing.to_result(duplicate=True)

        response = (
            _fixture_response(replay_payload, corr)
            if replay_payload is not None
            else self.client.fetch_listings(correlation_id=corr)
        )
        payload = response.payload
        snapshot_id = _payload_snapshot_id(payload, correlation_id=corr)
        contract_id = str(payload.get("contract_id") or "listing_raw_snapshot")
        effective_key = (requested_key or response.checksum_sha256).strip()
        if not effective_key or len(effective_key) > 250:
            raise ListingFeedConfigurationError(
                "idempotency_key must be between 1 and 250 characters.",
                correlation_id=corr,
            )

        existing = self.store.get_receipt(
            tenant_id=self.tenant_id,
            provider_id=self.provider_id,
            idempotency_key=effective_key,
        )
        if existing is not None and not force_replay:
            if existing.payload_checksum_sha256 != response.checksum_sha256:
                raise IdempotencyConflictError(
                    "Idempotency key was already used for a different listing payload.",
                    correlation_id=corr,
                )
            return existing.to_result(duplicate=True)

        raw_uri = self.store.save_snapshot(
            ListingFeedSnapshot(
                tenant_id=self.tenant_id,
                provider_id=self.provider_id,
                snapshot_id=snapshot_id,
                kind="raw",
                payload=payload,
                checksum_sha256=response.checksum_sha256,
                correlation_id=corr,
                captured_at=response.fetched_at,
                source_endpoint=response.source_endpoint,
            )
        )

        records = payload["records"]
        imported_at = datetime.now(UTC)
        import_result = self.pipeline.import_records(
            records,
            source_id=self.provider_id,
            imported_at=imported_at,
        )
        canonical_records = [
            record.to_dict()
            for record in import_result.records
            if record.listing is not None
        ]
        canonical_bytes = _canonical_json_bytes(canonical_records)
        canonical_uri = self.store.save_snapshot(
            ListingFeedSnapshot(
                tenant_id=self.tenant_id,
                provider_id=self.provider_id,
                snapshot_id=snapshot_id,
                kind="canonical",
                payload=canonical_records,
                checksum_sha256=hashlib.sha256(canonical_bytes).hexdigest(),
                correlation_id=corr,
                captured_at=imported_at,
                source_endpoint=response.source_endpoint,
            )
        )

        quarantine_records = [
            {
                "source_record": dict(record.source_record),
                "status": record.status.value,
                "issues": [
                    {
                        "code": issue.code,
                        "message": issue.message,
                        "field": getattr(issue, "field", None),
                    }
                    for issue in record.issues
                ],
            }
            for record in import_result.records
            if record.status.value in {"RAW", "FAILED_HARD_RULE"}
        ]
        quarantine_uri = None
        if quarantine_records:
            quarantine_bytes = _canonical_json_bytes(quarantine_records)
            quarantine_uri = self.store.save_snapshot(
                ListingFeedSnapshot(
                    tenant_id=self.tenant_id,
                    provider_id=self.provider_id,
                    snapshot_id=snapshot_id,
                    kind="quarantine",
                    payload=quarantine_records,
                    checksum_sha256=hashlib.sha256(quarantine_bytes).hexdigest(),
                    correlation_id=corr,
                    captured_at=imported_at,
                    source_endpoint=response.source_endpoint,
                )
            )

        receipt = self.store.save_receipt(
            ListingFeedIngestionReceipt(
                tenant_id=self.tenant_id,
                provider_id=self.provider_id,
                idempotency_key=effective_key,
                payload_checksum_sha256=response.checksum_sha256,
                snapshot_id=snapshot_id,
                contract_id=contract_id,
                status="success",
                correlation_id=corr,
                source_endpoint=response.source_endpoint,
                observed_at=response.observed_at,
                fetched_at=response.fetched_at,
                completed_at=datetime.now(UTC),
                accepted_count=import_result.accepted_count,
                duplicate_count=import_result.duplicate_count,
                rejected_count=import_result.rejected_count,
                quarantined_count=len(quarantine_records),
                raw_snapshot_uri=raw_uri,
                canonical_snapshot_uri=canonical_uri,
                quarantine_snapshot_uri=quarantine_uri,
            )
        )
        return receipt.to_result()


def _fixture_response(
    payload: dict[str, Any] | None,
    correlation_id: str,
) -> ListingFeedResponse:
    if payload is None:
        raise ListingFeedConfigurationError(
            "Fixture mode requires a replay payload.",
            correlation_id=correlation_id,
        )
    validated = _validate_payload(
        payload,
        correlation_id=correlation_id,
        max_records=10_000,
    )
    raw_bytes = _canonical_json_bytes(validated)
    now = datetime.now(UTC)
    return ListingFeedResponse(
        payload=validated,
        raw_bytes=raw_bytes,
        checksum_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        correlation_id=correlation_id,
        fetched_at=now,
        observed_at=_payload_observed_at(validated) or now,
        source_endpoint="fixture://local-replay",
    )


def _decode_payload(
    raw_bytes: bytes,
    *,
    correlation_id: str,
    max_records: int,
) -> dict[str, Any]:
    try:
        payload = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FeedSchemaError(
            "Listing provider response was not valid UTF-8 JSON.",
            correlation_id=correlation_id,
        ) from exc
    return _validate_payload(
        payload,
        correlation_id=correlation_id,
        max_records=max_records,
        require_contract_id=True,
    )


def _validate_payload(
    payload: Any,
    *,
    correlation_id: str,
    max_records: int,
    require_contract_id: bool = False,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise FeedSchemaError(
            "Listing provider response must be a JSON object.",
            correlation_id=correlation_id,
        )
    contract_id = payload.get("contract_id")
    if require_contract_id and contract_id is None:
        raise FeedSchemaError(
            "Listing provider response requires contract_id listing_raw_snapshot.",
            correlation_id=correlation_id,
        )
    if contract_id not in {None, "listing_raw_snapshot"}:
        raise FeedSchemaError(
            "Listing provider response declared an unsupported contract_id.",
            correlation_id=correlation_id,
        )
    records = payload.get("records")
    if not isinstance(records, list):
        raise FeedSchemaError(
            "Listing provider response must contain a records array.",
            correlation_id=correlation_id,
        )
    if len(records) > max_records:
        raise FeedSchemaError(
            "Listing provider response exceeded the configured record limit.",
            correlation_id=correlation_id,
        )
    if any(not isinstance(record, dict) for record in records):
        raise FeedSchemaError(
            "Every listing provider record must be a JSON object.",
            correlation_id=correlation_id,
        )
    return payload


def _payload_snapshot_id(
    payload: dict[str, Any],
    *,
    correlation_id: str,
) -> str:
    value = payload.get("snapshot_id")
    if not value and payload["records"]:
        value = payload["records"][0].get("snapshot_id")
    snapshot_id = str(value or "").strip()
    if not _SNAPSHOT_ID.fullmatch(snapshot_id):
        raise FeedSchemaError(
            "Listing provider response requires a safe, stable snapshot_id.",
            correlation_id=correlation_id,
        )
    return snapshot_id


def _payload_observed_at(payload: dict[str, Any]) -> datetime | None:
    candidates: list[Any] = [
        payload.get("observed_at"),
        payload.get("observation_time"),
        payload.get("snapshot_time"),
    ]
    if payload.get("records"):
        record = payload["records"][0]
        candidates.extend(
            (
                record.get("observed_at"),
                record.get("observation_time"),
                record.get("snapshot_time"),
            )
        )
    for value in candidates:
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return None


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _safe_endpoint(value: str) -> str:
    parsed = urlparse(value)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            "",
            "",
            "",
        )
    )


def _normalized_endpoint(value: str) -> str:
    parsed = urlparse(value.strip())
    path = parsed.path or "/"
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path.rstrip("/") or "/",
            "",
            parsed.query,
            "",
        )
    )


def _is_loopback(hostname: str) -> bool:
    lowered = hostname.lower()
    if lowered in _LOCAL_HOSTS:
        return True
    try:
        return ip_address(lowered).is_loopback
    except ValueError:
        return False


__all__ = [
    "FeedSchemaError",
    "IdempotencyConflictError",
    "ListingFeedClient",
    "ListingFeedClientError",
    "ListingFeedConfigurationError",
    "ListingFeedResponse",
    "LiveListingFeedAdapter",
    "RateLimitError",
    "TimeoutError",
    "TransportError",
    "UnauthorizedError",
    "UpstreamError",
]
