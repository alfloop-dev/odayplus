from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, Protocol

from apps.data_platform.config import DataPlaneConfig
from apps.data_platform.contracts import BackfillWindow, SourceEnvelope, SourceKind
from apps.data_platform.identifiers import snapshot_id_for_content
from apps.data_platform.serialization import json_safe, parse_datetime, sha256_json


MERCHANT_PROJECTION: dict[str, int] = {
    "_id": 1,
    "id": 1,
    "companyName": 1,
    "country": 1,
    "currency": 1,
    "operation": 1,
    "createdAt": 1,
    "updatedAt": 1,
}
PLACE_PROJECTION: dict[str, int] = {
    "_id": 1,
    "id": 1,
    "title": 1,
    "address": 1,
    "geolocation": 1,
    "merchant": 1,
    "merchantId": 1,
    "operation": 1,
    "publish": 1,
    "type": 1,
    "createdAt": 1,
    "updatedAt": 1,
}
TRANSACTION_PROJECTION: dict[str, int] = {
    "_id": 1,
    "transactionId": 1,
    "orderId": 1,
    "merchant": 1,
    "place": 1,
    "amount": 1,
    "amountPaid": 1,
    "currency": 1,
    "payGateway": 1,
    "status": 1,
    "operation": 1,
    "createdAt": 1,
    "updatedAt": 1,
}
TRADE_PROJECTION: dict[str, int] = {
    "_id": 1,
    "transactionId": 1,
    "merchantId": 1,
    "merchant": 1,
    "place": 1,
    "device": 1,
    "amount": 1,
    "amountPaid": 1,
    "dealstatus": 1,
    "gateway": 1,
    "currency": 1,
    "createdAt": 1,
    "updatedAt": 1,
}
ORDERS_PROJECTION: dict[str, int] = {
    "_id": 1,
    "id": 1,
    "orderId": 1,
    "merchant": 1,
    "place": 1,
    "device": 1,
    "deviceOperation": 1,
    "amount": 1,
    "amountPaid": 1,
    "currency": 1,
    "state": 1,
    "payment": 1,
    "createdAt": 1,
    "updatedAt": 1,
}
DEVICE_PROJECTION: dict[str, int] = {
    "_id": 1,
    "id": 1,
    "hwid": 1,
    "merchant": 1,
    "place": 1,
    "product": 1,
    "model": 1,
    "modelStatus": 1,
    "enable": 1,
    "connection": 1,
    "machineType": 1,
    "operation": 1,
    "createdAt": 1,
    "updatedAt": 1,
}
DEVICE_DAILY_STATISTICS_PROJECTION: dict[str, int] = {
    "_id": 1,
    "id": 1,
    "device": 1,
    "merchant": 1,
    "place": 1,
    "startDatetime": 1,
    "endDatetime": 1,
    "amount": 1,
    "count": 1,
    "gateway": 1,
    "createdAt": 1,
    "updatedAt": 1,
}
AI_REVENUE_STATS_PROJECTION: dict[str, int] = {
    "_id": 1,
    "id": 1,
    "merchant": 1,
    "place": 1,
    "date": 1,
    "predict": 1,
    "createdAt": 1,
    "updatedAt": 1,
}
CAMPAIGN_PROJECTION: dict[str, int] = {
    "_id": 1,
    "id": 1,
    "merchant": 1,
    "place": 1,
    "offerName": 1,
    "isActive": 1,
    "startDatetime": 1,
    "endDatetime": 1,
    "discountAmount": 1,
    "discountPercentage": 1,
    "offerType": 1,
    "offerMethod": 1,
    "createdAt": 1,
    "updatedAt": 1,
}
PRODUCT_PROJECTION: dict[str, int] = {
    "_id": 1,
    "id": 1,
    "merchant": 1,
    "title": 1,
    "details": 1,
    "createdAt": 1,
    "updatedAt": 1,
}
PRODUCTS_PROJECTION: dict[str, int] = {
    "_id": 1,
    "id": 1,
    "merchant": 1,
    "name": 1,
    "category": 1,
    "country": 1,
    "publish": 1,
    "template": 1,
    "createdAt": 1,
    "updatedAt": 1,
}
PROMOTION_PROJECTION: dict[str, int] = {
    "_id": 1,
    "id": 1,
    "merchant": 1,
    "place": 1,
    "product": 1,
    "enabled": 1,
    "start": 1,
    "end": 1,
    "rule": 1,
    "createdAt": 1,
    "updatedAt": 1,
}
DEVICE_LOG_PROJECTION: dict[str, int] = {
    "_id": 1,
    "id": 1,
    "merchant": 1,
    "place": 1,
    "device": 1,
    "logType": 1,
    "logData": 1,
    "createdAt": 1,
    "updatedAt": 1,
}
KMEANS_PROJECTION: dict[str, int] = {
    "_id": 1,
    "id": 1,
    "account": 1,
    "merchant": 1,
    "runDate": 1,
    "featureSnapshot": 1,
    "segmentId": 1,
    "segmentLabel": 1,
    "createdAt": 1,
    "updatedAt": 1,
}
# Member ingestion deliberately excludes names, email, phone, address, payment,
# and other direct identifiers. These references are quarantined, never projected.
MEMBER_MINIMIZED_PROJECTION: dict[str, int] = {
    "_id": 1,
    "id": 1,
    "merchant": 1,
    "createdAt": 1,
    "updatedAt": 1,
}
SOURCE_PROJECTIONS: dict[SourceKind, dict[str, int]] = {
    SourceKind.MERCHANT: MERCHANT_PROJECTION,
    SourceKind.PLACE: PLACE_PROJECTION,
    SourceKind.DEVICE: DEVICE_PROJECTION,
    SourceKind.DEVICE_DAILY_STATISTICS: DEVICE_DAILY_STATISTICS_PROJECTION,
    SourceKind.TRANSACTION: TRANSACTION_PROJECTION,
    SourceKind.TRADE: TRADE_PROJECTION,
    SourceKind.ORDERS: ORDERS_PROJECTION,
    SourceKind.AI_REVENUE_STATS: AI_REVENUE_STATS_PROJECTION,
    SourceKind.CAMPAIGN: CAMPAIGN_PROJECTION,
    SourceKind.PRODUCT: PRODUCT_PROJECTION,
    SourceKind.PRODUCTS: PRODUCTS_PROJECTION,
    SourceKind.PROMOTIONS: PROMOTION_PROJECTION,
    SourceKind.AI_CONSUMER_KMEANS_V1: KMEANS_PROJECTION,
    SourceKind.MEMBER: MEMBER_MINIMIZED_PROJECTION,
    SourceKind.DEVICE_LOG: DEVICE_LOG_PROJECTION,
}
SOURCE_TIME_FIELDS: dict[SourceKind, tuple[str, ...]] = {
    SourceKind.DEVICE_DAILY_STATISTICS: ("startDatetime", "createdAt"),
    SourceKind.AI_REVENUE_STATS: ("date", "createdAt"),
    SourceKind.AI_CONSUMER_KMEANS_V1: ("runDate", "createdAt"),
    SourceKind.CAMPAIGN: ("startDatetime", "createdAt"),
    SourceKind.PROMOTIONS: ("start", "createdAt"),
}
SNAPSHOT_SOURCE_KINDS = {
    SourceKind.MERCHANT,
    SourceKind.PLACE,
    SourceKind.DEVICE,
    SourceKind.CAMPAIGN,
    SourceKind.PRODUCT,
    SourceKind.PRODUCTS,
    SourceKind.PROMOTIONS,
    SourceKind.MEMBER,
}

_DEVICE_LOG_ALLOWED_DATA_FIELDS = {"action", "errCode", "state"}


def _minimize_device_log(document: dict[str, Any]) -> dict[str, Any]:
    minimized = dict(document)
    log_data = document.get("logData")
    if not isinstance(log_data, dict):
        minimized["logData"] = {"_redacted_non_object": True}
        return minimized
    kept: dict[str, Any] = {}
    redacted: list[str] = []
    for key, value in log_data.items():
        if key not in _DEVICE_LOG_ALLOWED_DATA_FIELDS or isinstance(
            value, (dict, list, tuple)
        ):
            redacted.append(key)
            continue
        kept[key] = value
    if redacted:
        kept["_redacted_fields"] = sorted(redacted)
    minimized["logData"] = kept
    return minimized


class MongoCollection(Protocol):
    def find(self, *args: Any, **kwargs: Any) -> Any: ...

    def count_documents(self, filter: dict[str, Any]) -> int: ...


class MongoDatabase(Protocol):
    def __getitem__(self, name: str) -> MongoCollection: ...


def source_id_for_document(kind: SourceKind, document: dict[str, Any]) -> str:
    candidates: tuple[Any, ...]
    if kind in {SourceKind.TRANSACTION, SourceKind.TRADE, SourceKind.ORDERS}:
        candidates = (
            document.get("transactionId"),
            document.get("orderId"),
            document.get("_id"),
        )
    else:
        candidates = (document.get("id"), document.get("_id"))
    for value in candidates:
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    raise ValueError(f"{kind.value} document has no stable source id")


def envelope_for_document(
    kind: SourceKind,
    document: dict[str, Any],
    *,
    run_id: str,
    observed_at: datetime,
) -> SourceEnvelope:
    safe_document = json_safe(document)
    if kind is SourceKind.DEVICE_LOG:
        safe_document = _minimize_device_log(safe_document)
    source_id = source_id_for_document(kind, safe_document)
    content_sha256 = sha256_json(safe_document)
    source_timestamp = safe_document.get("updatedAt")
    for field_name in SOURCE_TIME_FIELDS.get(kind, ("createdAt",)):
        source_timestamp = source_timestamp or safe_document.get(field_name)
    try:
        source_updated_at = (
            parse_datetime(source_timestamp, field_name=f"{kind.value}.updatedAt")
            if source_timestamp
            else None
        )
    except ValueError:
        # Invalid source time is a record-level canonical validation result.
        # Raw capture must still succeed so the exact evidence can be quarantined.
        source_updated_at = None
    return SourceEnvelope(
        source_kind=kind,
        source_id=source_id,
        source_document=safe_document,
        source_updated_at=source_updated_at,
        observed_at=observed_at.astimezone(UTC),
        source_snapshot_id=str(
            snapshot_id_for_content(kind.value, source_id, content_sha256)
        ),
        content_sha256=content_sha256,
        run_id=run_id,
    )


class MongoSource:
    """Bounded, projection-complete reader for the approved production database."""

    def __init__(
        self,
        config: DataPlaneConfig,
        *,
        database: MongoDatabase | None = None,
    ) -> None:
        config.validate()
        self._config = config
        self._database = database or self._connect(config)

    @staticmethod
    def _connect(config: DataPlaneConfig) -> MongoDatabase:
        try:
            from pymongo import MongoClient
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("pymongo is required for production ingestion") from exc
        client = MongoClient(
            config.mongo_uri,
            appname="oday-plus-data-plane",
            connectTimeoutMS=config.mongo_connect_timeout_ms,
            serverSelectionTimeoutMS=config.mongo_connect_timeout_ms,
            socketTimeoutMS=config.mongo_socket_timeout_ms,
            retryReads=True,
            retryWrites=False,
            tz_aware=True,
        )
        client.admin.command("ping")
        return client[config.mongo_database]

    @staticmethod
    def _cursor_value(value: str | None) -> Any:
        if not value:
            return None
        try:
            from bson import ObjectId

            if ObjectId.is_valid(value):
                return ObjectId(value)
        except ImportError:  # pragma: no cover - pymongo supplies bson
            pass
        return value

    @staticmethod
    def _window_query(
        kind: SourceKind,
        window: BackfillWindow,
        resume_after: str | None,
    ) -> dict[str, Any]:
        cursor = MongoSource._cursor_value(resume_after)
        if kind in SNAPSHOT_SOURCE_KINDS:
            return {} if cursor is None else {"_id": {"$gt": cursor}}
        time_fields = ("updatedAt", *SOURCE_TIME_FIELDS.get(kind, ("createdAt",)))
        time_query = {
            "$or": [
                {field_name: {"$gte": window.start, "$lt": window.end}}
                for field_name in dict.fromkeys(time_fields)
            ]
        }
        if cursor is None:
            return time_query
        return {"$and": [time_query, {"_id": {"$gt": cursor}}]}

    def iter_envelopes(
        self,
        kind: SourceKind,
        window: BackfillWindow,
        *,
        run_id: str,
        resume_after: str | None,
        limit: int,
    ) -> Iterator[SourceEnvelope]:
        if limit <= 0 or limit > self._config.max_records_per_run:
            raise ValueError("limit is outside the configured production bound")
        collection = self._database[kind.value]
        query = self._window_query(kind, window, resume_after)
        cursor = (
            collection.find(
                query,
                SOURCE_PROJECTIONS[kind],
                no_cursor_timeout=False,
                allow_disk_use=True,
            )
            .sort("_id", 1)
            .limit(limit)
            .batch_size(self._config.batch_size)
        )
        observed_at = datetime.now(UTC)
        for document in cursor:
            yield envelope_for_document(
                kind,
                document,
                run_id=run_id,
                observed_at=observed_at,
            )

    def count(self, kind: SourceKind, window: BackfillWindow) -> int:
        return int(
            self._database[kind.value].count_documents(
                self._window_query(kind, window, resume_after=None)
            )
        )

    def has_changes_since(self, kind: SourceKind, since: datetime) -> bool:
        return bool(
            self._database[kind.value].count_documents(
                {"updatedAt": {"$gt": since}}
            )
        )
