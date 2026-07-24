from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from apps.data_platform.contracts import QuarantineReason, SourceEnvelope, SourceKind
from apps.data_platform.identifiers import (
    address_id_for_place,
    brand_id_for_merchant,
    machine_id_for_device,
    machine_status_event_id_for_source,
    store_id_for_place,
    tenant_id_for_merchant,
    transaction_id_for_source,
)
from apps.data_platform.serialization import parse_datetime

if TYPE_CHECKING:
    from apps.data_platform.status_mapping import StatusMappingContract


class SourceContractError(ValueError):
    """Raised when a Mongo source document is incomplete or unsupported."""

    def __init__(self, reason_code: QuarantineReason, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class MissingMappingError(SourceContractError):
    """Raised when a place/merchant dependency is absent from canonical data."""


class MappingLookup(Protocol):
    def require_merchant(self, source_merchant_id: str) -> MerchantIdentity: ...

    def require_place(self, source_place_id: str) -> StoreIdentity: ...

    def require_device(self, source_device_id: str) -> MachineIdentity: ...


@dataclass(frozen=True)
class MerchantIdentity:
    source_merchant_id: str
    tenant_id: UUID
    brand_id: UUID


@dataclass(frozen=True)
class StoreIdentity:
    source_place_id: str
    source_merchant_id: str
    tenant_id: UUID
    brand_id: UUID
    store_id: UUID


@dataclass(frozen=True)
class MachineIdentity:
    source_device_id: str
    tenant_id: UUID
    store_id: UUID
    machine_id: UUID


@dataclass(frozen=True)
class MerchantProjection:
    source_id: str
    tenant_id: UUID
    tenant_name: str
    tenant_status: str
    brand_id: UUID
    brand_code: str
    brand_name: str
    brand_status: str


@dataclass(frozen=True)
class PlaceProjection:
    source_id: str
    source_merchant_id: str
    tenant_id: UUID
    brand_id: UUID
    address_id: UUID | None
    raw_address: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    store_id: UUID
    store_name: str
    store_status: str
    store_format_code: str
    effective_from: datetime


@dataclass(frozen=True)
class TransactionProjection:
    source_id: str
    source_place_id: str
    tenant_id: UUID
    store_id: UUID
    transaction_id: UUID
    event_time: datetime
    observation_time: datetime
    payment_time: datetime | None
    gross_amount: Decimal
    discount_amount: Decimal
    net_amount: Decimal
    currency: str
    payment_method: str
    transaction_status: str
    ingested_at: datetime


@dataclass(frozen=True)
class DeviceProjection:
    source_id: str
    source_merchant_id: str
    source_place_id: str
    tenant_id: UUID
    store_id: UUID
    machine_id: UUID
    serial_number: str
    product_id: str
    model: str
    machine_type: str
    machine_status: str
    effective_from: datetime


@dataclass(frozen=True)
class DailyStatisticProjection:
    source_id: str
    tenant_id: UUID
    store_id: UUID
    machine_id: UUID
    period_start: datetime
    period_end: datetime
    gross_amount: Decimal
    transaction_count: int
    gateway: str


@dataclass(frozen=True)
class ForecastInputProjection:
    source_id: str
    tenant_id: UUID
    store_id: UUID
    forecast_date: datetime
    predicted_value: Decimal
    observed_at: datetime


@dataclass(frozen=True)
class DomainInputProjection:
    source_id: str
    input_kind: str
    tenant_id: UUID
    store_id: UUID | None
    effective_at: datetime
    input_payload: dict[str, Any]


@dataclass(frozen=True)
class LearningImportProjection:
    source_id: str
    tenant_id: UUID
    run_date: datetime
    source_account_ref_hash: str
    feature_snapshot: dict[str, Decimal]
    segment_id: str
    segment_labels: tuple[str, ...]
    observed_at: datetime


@dataclass(frozen=True)
class MachineStatusEventProjection:
    source_id: str
    tenant_id: UUID
    store_id: UUID
    machine_id: UUID
    status_event_id: UUID
    event_time: datetime
    observation_time: datetime
    status_type: str
    severity: str
    error_code: str | None


_INACTIVE_OPERATIONS = {"archive", "archived", "delete", "deleted", "disable", "disabled"}
_ACTIVE_OPERATIONS = {
    "",
    "active",
    "create",
    "created",
    "insert",
    "publish",
    "published",
    "update",
    "updated",
    "upsert",
}
_PLACE_OPERATION_STATUS = {
    "archive": "closed",
    "archived": "closed",
    "close": "closed",
    "closed": "closed",
    "delete": "closed",
    "deleted": "closed",
    "disable": "suspended",
    "disabled": "suspended",
    "suspend": "suspended",
    "suspended": "suspended",
    "transfer": "transferred",
    "transferred": "transferred",
}
_TRANSACTION_STATUS = {
    "success": "succeeded",
    "succeeded": "succeeded",
    "paid": "succeeded",
    "complete": "succeeded",
    "completed": "succeeded",
    "failed": "failed",
    "failure": "failed",
    "declined": "failed",
    "refund": "refunded",
    "refunded": "refunded",
    "void": "voided",
    "voided": "voided",
    "cancel": "voided",
    "canceled": "voided",
    "cancelled": "voided",
    "partial": "partial",
    "partially_paid": "partial",
    "pending": "partial",
    "processing": "partial",
}


def _text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise SourceContractError(
            QuarantineReason.MISSING_REQUIRED_FIELD,
            f"{field_name} is required",
        )
    return normalized


def _source_reference(value: Any, field_name: str) -> str:
    if isinstance(value, dict):
        value = value.get("_id") or value.get("id")
    return _text(value, field_name)


def _decimal(value: Any, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SourceContractError(
            QuarantineReason.INVALID_AMOUNT,
            f"{field_name} must be numeric",
        ) from exc
    if not parsed.is_finite():
        raise SourceContractError(
            QuarantineReason.INVALID_AMOUNT,
            f"{field_name} must be finite",
        )
    return parsed.quantize(Decimal("0.01"))


def _feature_decimal(value: Any, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SourceContractError(
            QuarantineReason.INVALID_AMOUNT,
            f"{field_name} must be numeric",
        ) from exc
    if not parsed.is_finite():
        raise SourceContractError(
            QuarantineReason.INVALID_AMOUNT,
            f"{field_name} must be finite",
        )
    return parsed


def _merchant_status(
    operation: Any, status_contract: StatusMappingContract | None
) -> str:
    normalized = str(operation or "").strip().lower()
    if normalized.lstrip("-").isdigit():
        if status_contract is None:
            raise SourceContractError(
                QuarantineReason.OPERATION_MAPPING_UNAPPROVED,
                f"Approved merchant operation mapping is required for {normalized!r}",
            )
        try:
            return status_contract.resolve_category("merchant_operation", normalized)
        except SourceContractError as exc:
            raise SourceContractError(
                QuarantineReason.OPERATION_MAPPING_UNAPPROVED, str(exc)
            ) from exc
    if normalized in _INACTIVE_OPERATIONS:
        return "inactive"
    if normalized in _ACTIVE_OPERATIONS:
        return "active"
    raise SourceContractError(
        QuarantineReason.UNSUPPORTED_STATUS,
        f"Unsupported merchant operation: {normalized}",
    )


def _required_datetime(value: Any, field_name: str) -> datetime:
    if value is None or value == "":
        raise SourceContractError(
            QuarantineReason.MISSING_REQUIRED_FIELD,
            f"{field_name} is required",
        )
    try:
        return parse_datetime(value, field_name=field_name)
    except ValueError as exc:
        raise SourceContractError(
            QuarantineReason.INVALID_DATETIME,
            str(exc),
        ) from exc


def _event_datetime(
    value: Any,
    field_name: str,
    *,
    observed_at: datetime,
    max_future_days: int = 7,
) -> datetime:
    parsed = _required_datetime(value, field_name)
    if parsed < datetime(2000, 1, 1, tzinfo=UTC):
        raise SourceContractError(
            QuarantineReason.EVENT_TIME_EPOCH_OUTLIER,
            f"{field_name} is earlier than 2000-01-01",
        )
    if parsed > observed_at.astimezone(UTC) + timedelta(days=max_future_days):
        raise SourceContractError(
            QuarantineReason.EVENT_TIME_FUTURE_OUTLIER,
            f"{field_name} exceeds the permitted future window",
        )
    return parsed


def project_merchant(
    envelope: SourceEnvelope,
    status_contract: StatusMappingContract | None = None,
) -> MerchantProjection:
    document = envelope.source_document
    source_id = envelope.source_id
    name = _text(document.get("companyName"), "merchant.companyName")
    _text(document.get("country"), "merchant.country")
    _text(document.get("currency"), "merchant.currency")
    _event_datetime(
        document.get("createdAt"),
        "merchant.createdAt",
        observed_at=envelope.observed_at,
    )
    status = _merchant_status(document.get("operation"), status_contract)
    return MerchantProjection(
        source_id=source_id,
        tenant_id=tenant_id_for_merchant(source_id),
        tenant_name=name,
        tenant_status=status,
        brand_id=brand_id_for_merchant(source_id),
        brand_code=f"fongniao_{source_id}",
        brand_name=name,
        brand_status=status,
    )


def _place_status(
    document: dict[str, Any], status_contract: StatusMappingContract | None
) -> str:
    operation = str(document.get("operation") or "").strip().lower()
    if operation.lstrip("-").isdigit():
        if status_contract is None:
            raise SourceContractError(
                QuarantineReason.OPERATION_MAPPING_UNAPPROVED,
                f"Approved place operation mapping is required for {operation!r}",
            )
        try:
            mapped = status_contract.resolve_category("place_operation", operation)
        except SourceContractError as exc:
            raise SourceContractError(
                QuarantineReason.OPERATION_MAPPING_UNAPPROVED, str(exc)
            ) from exc
        if document.get("publish") is False and mapped in {"open", "planned"}:
            return "suspended"
        return mapped
    if operation in _PLACE_OPERATION_STATUS:
        return _PLACE_OPERATION_STATUS[operation]
    if operation not in _ACTIVE_OPERATIONS:
        raise SourceContractError(
            QuarantineReason.UNSUPPORTED_STATUS,
            f"Unsupported place operation: {operation}",
        )
    publish = document.get("publish")
    if publish is True:
        return "open"
    if publish is False:
        return "suspended"
    return "planned"


def _address_text(value: Any) -> str | None:
    if isinstance(value, str):
        return _text(value, "place.address")
    if isinstance(value, dict):
        ordered_keys = (
            "formatted",
            "full",
            "postalCode",
            "country",
            "city",
            "district",
            "street",
            "line1",
            "line2",
        )
        parts = [str(value[key]).strip() for key in ordered_keys if value.get(key)]
        if parts:
            return ", ".join(parts)
    return None


def _coordinates(
    document: dict[str, Any],
) -> tuple[Decimal | None, Decimal | None]:
    geolocation = document.get("geolocation")
    coordinates = geolocation.get("coordinates") if isinstance(geolocation, dict) else None
    if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 2:
        return None, None
    longitude = _decimal(coordinates[0], "place.longitude")
    latitude = _decimal(coordinates[1], "place.latitude")
    if not Decimal("-180") <= longitude <= Decimal("180"):
        raise SourceContractError(
            QuarantineReason.INVALID_COORDINATES,
            "place.longitude is outside [-180, 180]",
        )
    if not Decimal("-90") <= latitude <= Decimal("90"):
        raise SourceContractError(
            QuarantineReason.INVALID_COORDINATES,
            "place.latitude is outside [-90, 90]",
        )
    return longitude, latitude


def _place_type(
    value: Any, status_contract: StatusMappingContract | None
) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if normalized.lstrip("-").isdigit():
        if status_contract is None:
            raise SourceContractError(
                QuarantineReason.TYPE_MAPPING_UNAPPROVED,
                f"Approved place type mapping is required for {normalized!r}",
            )
        try:
            return status_contract.resolve_category("place_type", normalized)
        except SourceContractError as exc:
            raise SourceContractError(
                QuarantineReason.TYPE_MAPPING_UNAPPROVED, str(exc)
            ) from exc
    return normalized


def project_place(
    envelope: SourceEnvelope,
    lookup: MappingLookup,
    status_contract: StatusMappingContract | None = None,
) -> PlaceProjection:
    document = envelope.source_document
    source_merchant_id = _source_reference(
        document.get("merchant") or document.get("merchantId"),
        "place.merchant",
    )
    merchant = lookup.require_merchant(source_merchant_id)
    longitude, latitude = _coordinates(document)
    source_id = envelope.source_id
    raw_address = _address_text(document.get("address"))
    return PlaceProjection(
        source_id=source_id,
        source_merchant_id=source_merchant_id,
        tenant_id=merchant.tenant_id,
        brand_id=merchant.brand_id,
        address_id=address_id_for_place(source_id) if raw_address else None,
        raw_address=raw_address,
        latitude=latitude,
        longitude=longitude,
        store_id=store_id_for_place(source_id),
        store_name=_text(document.get("title"), "place.title"),
        store_status=_place_status(document, status_contract),
        store_format_code=_place_type(document.get("type"), status_contract),
        effective_from=_event_datetime(
            document.get("createdAt"),
            "place.createdAt",
            observed_at=envelope.observed_at,
        ),
    )


_MACHINE_STATUS = {
    "active": "active",
    "available": "active",
    "connected": "active",
    "online": "active",
    "inactive": "inactive",
    "disabled": "inactive",
    "disconnected": "inactive",
    "offline": "inactive",
    "maintenance": "maintenance",
    "repair": "maintenance",
    "retired": "retired",
    "deleted": "retired",
}


def _device_lifecycle_status(document: dict[str, Any]) -> str:
    enabled = document.get("enable")
    if isinstance(enabled, bool):
        return "active" if enabled else "inactive"

    raw_status = document.get("modelStatus")
    if isinstance(raw_status, str):
        normalized = raw_status.strip().lower()
        if normalized:
            try:
                return _MACHINE_STATUS[normalized]
            except KeyError as exc:
                raise SourceContractError(
                    QuarantineReason.UNSUPPORTED_STATUS,
                    f"Unsupported device lifecycle status: {normalized}",
                ) from exc

    raise SourceContractError(
        QuarantineReason.MISSING_REQUIRED_FIELD,
        "device.enable boolean or string modelStatus lifecycle is required",
    )


def project_device(envelope: SourceEnvelope, lookup: MappingLookup) -> DeviceProjection:
    document = envelope.source_document
    source_merchant_id = _source_reference(document.get("merchant"), "device.merchant")
    source_place_id = _source_reference(document.get("place"), "device.place")
    merchant = lookup.require_merchant(source_merchant_id)
    place = lookup.require_place(source_place_id)
    if merchant.tenant_id != place.tenant_id:
        raise MissingMappingError(
            QuarantineReason.TENANT_OWNERSHIP_MISMATCH,
            "Device merchant does not own the referenced place",
        )
    machine_status = _device_lifecycle_status(document)
    return DeviceProjection(
        source_id=envelope.source_id,
        source_merchant_id=source_merchant_id,
        source_place_id=source_place_id,
        tenant_id=merchant.tenant_id,
        store_id=place.store_id,
        machine_id=machine_id_for_device(envelope.source_id),
        serial_number=_text(
            document.get("hwid") or document.get("id"), "device.hwid"
        ),
        product_id=_source_reference(document.get("product"), "device.product"),
        model=_text(document.get("model"), "device.model"),
        machine_type=_text(
            document.get("machineType") or document.get("model"),
            "device.machineType",
        ),
        machine_status=machine_status,
        effective_from=_event_datetime(
            document.get("createdAt"),
            "device.createdAt",
            observed_at=envelope.observed_at,
        ),
    )


def project_daily_statistic(
    envelope: SourceEnvelope, lookup: MappingLookup
) -> DailyStatisticProjection:
    document = envelope.source_document
    source_merchant_id = _source_reference(
        document.get("merchant"), "device_daily_statistics.merchant"
    )
    source_place_id = _source_reference(
        document.get("place"), "device_daily_statistics.place"
    )
    source_device_id = _source_reference(
        document.get("device"), "device_daily_statistics.device"
    )
    merchant = lookup.require_merchant(source_merchant_id)
    place = lookup.require_place(source_place_id)
    machine = lookup.require_device(source_device_id)
    if len({merchant.tenant_id, place.tenant_id, machine.tenant_id}) != 1:
        raise MissingMappingError(
            QuarantineReason.TENANT_OWNERSHIP_MISMATCH,
            "Daily statistic merchant/place/device tenant mismatch",
        )
    if machine.store_id != place.store_id:
        raise MissingMappingError(
            QuarantineReason.TENANT_OWNERSHIP_MISMATCH,
            "Daily statistic device is not installed at the referenced place",
        )
    period_start = _event_datetime(
        document.get("startDatetime"),
        "device_daily_statistics.startDatetime",
        observed_at=envelope.observed_at,
    )
    period_end = _event_datetime(
        document.get("endDatetime"),
        "device_daily_statistics.endDatetime",
        observed_at=envelope.observed_at,
    )
    if period_end <= period_start:
        raise SourceContractError(
            QuarantineReason.INVALID_DATETIME,
            "device_daily_statistics.end must be after start",
        )
    try:
        count = int(document.get("count"))
    except (TypeError, ValueError) as exc:
        raise SourceContractError(
            QuarantineReason.INVALID_AMOUNT,
            "device_daily_statistics.count must be an integer",
        ) from exc
    if count < 0:
        raise SourceContractError(
            QuarantineReason.INVALID_AMOUNT,
            "device_daily_statistics.count cannot be negative",
        )
    return DailyStatisticProjection(
        source_id=envelope.source_id,
        tenant_id=merchant.tenant_id,
        store_id=place.store_id,
        machine_id=machine.machine_id,
        period_start=period_start,
        period_end=period_end,
        gross_amount=_decimal(
            document.get("amount"), "device_daily_statistics.amount"
        ),
        transaction_count=count,
        gateway=_text(
            document.get("gateway"), "device_daily_statistics.gateway"
        ).lower(),
    )


def project_forecast_input(
    envelope: SourceEnvelope, lookup: MappingLookup
) -> ForecastInputProjection:
    document = envelope.source_document
    merchant = lookup.require_merchant(
        _source_reference(document.get("merchant"), "ai_revenue_stats.merchant")
    )
    place = lookup.require_place(
        _source_reference(document.get("place"), "ai_revenue_stats.place")
    )
    if merchant.tenant_id != place.tenant_id:
        raise MissingMappingError(
            QuarantineReason.TENANT_OWNERSHIP_MISMATCH,
            "AI revenue input merchant does not own the referenced place",
        )
    return ForecastInputProjection(
        source_id=envelope.source_id,
        tenant_id=merchant.tenant_id,
        store_id=place.store_id,
        forecast_date=_event_datetime(
            document.get("date"),
            "ai_revenue_stats.date",
            observed_at=envelope.observed_at,
            max_future_days=366,
        ),
        predicted_value=_decimal(
            document.get("predict"), "ai_revenue_stats.predict"
        ),
        observed_at=envelope.observed_at,
    )


def project_domain_input(
    envelope: SourceEnvelope, lookup: MappingLookup
) -> DomainInputProjection:
    document = envelope.source_document
    merchant = lookup.require_merchant(
        _source_reference(
            document.get("merchant") or document.get("merchantId"),
            f"{envelope.source_kind.value}.merchant",
        )
    )
    place_value = document.get("place")
    store_id: UUID | None = None
    if place_value:
        place = lookup.require_place(
            _source_reference(place_value, f"{envelope.source_kind.value}.place")
        )
        if merchant.tenant_id != place.tenant_id:
            raise MissingMappingError(
                QuarantineReason.TENANT_OWNERSHIP_MISMATCH,
                f"{envelope.source_kind.value} merchant/place tenant mismatch",
            )
        store_id = place.store_id
    payload_fields = {
        SourceKind.CAMPAIGN: (
            "offerName",
            "isActive",
            "startDatetime",
            "endDatetime",
            "discountAmount",
            "discountPercentage",
            "offerType",
            "offerMethod",
        ),
        SourceKind.PRODUCT: ("title", "details"),
        SourceKind.PRODUCTS: (
            "name",
            "category",
            "country",
            "publish",
            "template",
        ),
        SourceKind.PROMOTIONS: ("enabled", "start", "end", "rule"),
    }
    try:
        selected_fields = payload_fields[envelope.source_kind]
    except KeyError as exc:
        raise SourceContractError(
            QuarantineReason.MISSING_REQUIRED_FIELD,
            f"No domain input contract for {envelope.source_kind.value}",
        ) from exc
    input_payload = {
        field_name: document[field_name]
        for field_name in selected_fields
        if field_name in document
    }
    if not input_payload:
        raise SourceContractError(
            QuarantineReason.MISSING_REQUIRED_FIELD,
            f"{envelope.source_kind.value} has no recognized live-shape fields",
        )
    timestamp = (
        document.get("startDatetime")
        or document.get("start")
        or document.get("updatedAt")
        or document.get("createdAt")
    )
    return DomainInputProjection(
        source_id=envelope.source_id,
        input_kind=envelope.source_kind.value,
        tenant_id=merchant.tenant_id,
        store_id=store_id,
        effective_at=_event_datetime(
            timestamp,
            f"{envelope.source_kind.value}.effective_at",
            observed_at=envelope.observed_at,
            max_future_days=366,
        ),
        input_payload=input_payload,
    )


def project_learning_import(
    envelope: SourceEnvelope, lookup: MappingLookup
) -> LearningImportProjection:
    document = envelope.source_document
    merchant = lookup.require_merchant(
        _source_reference(document.get("merchant"), "ai_consumer_kmeans_v1.merchant")
    )
    feature_snapshot = document.get("featureSnapshot")
    if not isinstance(feature_snapshot, dict):
        raise SourceContractError(
            QuarantineReason.MISSING_REQUIRED_FIELD,
            "ai_consumer_kmeans_v1.featureSnapshot object is required",
        )
    expected_features = {
        "avgTicket",
        "extra_dry_ratio",
        "prefer_temperature",
        "totalAmount",
        "transCount",
    }
    if not expected_features.issubset(feature_snapshot):
        raise SourceContractError(
            QuarantineReason.MISSING_REQUIRED_FIELD,
            "ai_consumer_kmeans_v1.featureSnapshot is incomplete",
        )
    labels_value = document.get("segmentLabel")
    if isinstance(labels_value, (list, tuple)):
        labels = tuple(
            _text(value, "ai_consumer_kmeans_v1.segmentLabel")
            for value in labels_value
        )
    else:
        raise SourceContractError(
            QuarantineReason.MISSING_REQUIRED_FIELD,
            "ai_consumer_kmeans_v1.segmentLabel is required",
        )
    account = _text(document.get("account"), "ai_consumer_kmeans_v1.account")
    return LearningImportProjection(
        source_id=envelope.source_id,
        tenant_id=merchant.tenant_id,
        run_date=_event_datetime(
            document.get("runDate"),
            "ai_consumer_kmeans_v1.runDate",
            observed_at=envelope.observed_at,
        ),
        source_account_ref_hash=hashlib.sha256(account.encode("utf-8")).hexdigest(),
        feature_snapshot={
            key: _feature_decimal(feature_snapshot[key], f"featureSnapshot.{key}")
            for key in sorted(expected_features)
        },
        segment_id=_text(
            document.get("segmentId"), "ai_consumer_kmeans_v1.segmentId"
        ),
        segment_labels=labels,
        observed_at=envelope.observed_at,
    )


def project_machine_status_event(
    envelope: SourceEnvelope,
    lookup: MappingLookup,
    status_contract: StatusMappingContract | None = None,
) -> MachineStatusEventProjection:
    document = envelope.source_document
    source_device_id = _source_reference(document.get("device"), "device_log.device")
    machine = lookup.require_device(source_device_id)

    merchant_value = document.get("merchant")
    if merchant_value:
        merchant = lookup.require_merchant(
            _source_reference(merchant_value, "device_log.merchant")
        )
        if merchant.tenant_id != machine.tenant_id:
            raise MissingMappingError(
                QuarantineReason.TENANT_OWNERSHIP_MISMATCH,
                "device_log merchant does not own the referenced device",
            )
    place_value = document.get("place")
    if place_value:
        place = lookup.require_place(
            _source_reference(place_value, "device_log.place")
        )
        if (
            place.tenant_id != machine.tenant_id
            or place.store_id != machine.store_id
        ):
            raise MissingMappingError(
                QuarantineReason.TENANT_OWNERSHIP_MISMATCH,
                "device_log place does not own the referenced device",
            )

    log_type = _text(document.get("logType"), "device_log.logType").lower()
    log_data = document.get("logData")
    if not isinstance(log_data, dict):
        raise SourceContractError(
            QuarantineReason.INVALID_LOG_EVIDENCE,
            "device_log.logData must be an object",
        )
    error_code: str | None = None
    if log_type == "error":
        error_code = _text(log_data.get("errCode"), "device_log.logData.errCode")
        status_type = "error"
        severity = "error"
    elif log_type == "connection":
        source_state = _text(
            log_data.get("state") or log_data.get("action"),
            "device_log.logData.state",
        )
        if status_contract is None:
            raise SourceContractError(
                QuarantineReason.CONNECTION_MAPPING_UNAPPROVED,
                "Approved device_connection mapping is required",
            )
        try:
            status_type = status_contract.resolve_category(
                "device_connection", source_state
            )
        except SourceContractError as exc:
            raise SourceContractError(
                QuarantineReason.CONNECTION_MAPPING_UNAPPROVED, str(exc)
            ) from exc
        severity = "warn" if status_type == "offline" else "info"
    else:
        raise SourceContractError(
            QuarantineReason.NON_CANONICAL_LOG_TYPE,
            f"device_log.logType {log_type!r} is raw-only",
        )

    return MachineStatusEventProjection(
        source_id=envelope.source_id,
        tenant_id=machine.tenant_id,
        store_id=machine.store_id,
        machine_id=machine.machine_id,
        status_event_id=machine_status_event_id_for_source(envelope.source_id),
        event_time=_event_datetime(
            document.get("createdAt"),
            "device_log.createdAt",
            observed_at=envelope.observed_at,
        ),
        observation_time=envelope.observed_at,
        status_type=status_type,
        severity=severity,
        error_code=error_code,
    )


def _transaction_status(document: dict[str, Any]) -> str:
    operation = str(document.get("operation") or "").strip().lower()
    if operation in {"refund", "refunded"}:
        return "refunded"
    if operation in {"delete", "deleted", "void", "voided"}:
        return "voided"
    raw_status = str(document.get("status") or "").strip().lower()
    if raw_status:
        try:
            return _TRANSACTION_STATUS[raw_status]
        except KeyError as exc:
            raise SourceContractError(
                QuarantineReason.UNSUPPORTED_STATUS,
                f"Unsupported transaction status: {raw_status}"
            ) from exc
    raise SourceContractError(
        QuarantineReason.MISSING_REQUIRED_FIELD,
        "transaction.status is required and cannot be inferred from amounts",
    )


def _canonical_transaction_key(
    source_kind: SourceKind, document: dict[str, Any], fallback: str
) -> str:
    if source_kind in {SourceKind.ORDERS, SourceKind.TRANSACTION}:
        candidates = (document.get("orderId"), document.get("transactionId"))
    else:
        candidates = (document.get("transactionId"), document.get("orderId"))
    for value in candidates:
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return fallback


def project_transaction(
    envelope: SourceEnvelope,
    lookup: MappingLookup,
    status_contract: StatusMappingContract | None = None,
) -> TransactionProjection:
    document = dict(envelope.source_document)
    if envelope.source_kind.value == "trade":
        document["merchant"] = document.get("merchant") or document.get("merchantId")
        raw_status = str(document.get("dealstatus") or "").strip()
        if not raw_status or not raw_status.lstrip("-").isdigit() or status_contract is None:
            raise SourceContractError(
                QuarantineReason.STATUS_MAPPING_UNAPPROVED,
                f"Approved numeric trade status mapping is required for {raw_status!r}",
            )
        document["status"] = status_contract.resolve(SourceKind.TRADE, raw_status)
        document["payGateway"] = document.get("gateway")
        if document.get("amountPaid") is None:
            if (
                status_contract.trade_paid_amount_rule
                != "gross_when_succeeded_zero_otherwise"
            ):
                raise SourceContractError(
                    QuarantineReason.MISSING_AUTHORITATIVE_PAYMENT,
                    "Trade has no amountPaid and no approved paid-amount rule",
                )
            document["amountPaid"] = (
                document.get("amount") if document["status"] == "succeeded" else 0
            )
    elif envelope.source_kind.value == "orders":
        explicit_status = {
            "TRADE_SUCCESS": "succeeded",
            "TRADE_FAIL": "failed",
            "TRADE_REFUND": "refunded",
        }
        raw_state = str(document.get("state") or "").strip().upper()
        try:
            document["status"] = explicit_status[raw_state]
        except KeyError as exc:
            raise SourceContractError(
                QuarantineReason.UNSUPPORTED_STATUS,
                f"Unsupported authoritative order state: {raw_state}",
            ) from exc
        payment = document.get("payment")
        if not isinstance(payment, dict):
            raise SourceContractError(
                QuarantineReason.MISSING_AUTHORITATIVE_PAYMENT,
                "orders.payment object is required",
            )
        document["amountPaid"] = payment.get("amountPaid") or payment.get("amount")
        document["payGateway"] = (
            payment.get("gateway")
            or payment.get("payGateway")
            or payment.get("method")
        )
        document["currency"] = document.get("currency") or payment.get("currency")
    else:
        raw_status = str(document.get("status") or "").strip()
        if raw_status.lstrip("-").isdigit():
            if status_contract is None:
                raise SourceContractError(
                    QuarantineReason.STATUS_MAPPING_UNAPPROVED,
                    (
                        "Approved numeric transaction status mapping is required "
                        f"for {raw_status!r}"
                    ),
                )
            document["status"] = status_contract.resolve(
                SourceKind.TRANSACTION, raw_status
            )
    source_place_id = _source_reference(document.get("place"), "transaction.place")
    source_merchant_id = _source_reference(
        document.get("merchant"), "transaction.merchant"
    )
    place = lookup.require_place(source_place_id)
    if source_merchant_id != place.source_merchant_id:
        raise MissingMappingError(
            QuarantineReason.TENANT_OWNERSHIP_MISMATCH,
            "Transaction merchant does not own the referenced place"
        )
    gross = _decimal(document.get("amount"), "transaction.amount")
    net = _decimal(document.get("amountPaid"), "transaction.amountPaid")
    if gross < 0 or net < 0:
        raise SourceContractError(
            QuarantineReason.INVALID_AMOUNT,
            "Transaction amounts cannot be negative",
        )
    event_time = _event_datetime(
        document.get("createdAt"),
        "transaction.createdAt",
        observed_at=envelope.observed_at,
    )
    source_payment_time = document.get("updatedAt") or document.get("createdAt")
    payment_time = _required_datetime(
        source_payment_time, "transaction.updatedAt"
    )
    status = _transaction_status(document)
    canonical_key = _canonical_transaction_key(
        envelope.source_kind, document, envelope.source_id
    )
    return TransactionProjection(
        source_id=canonical_key,
        source_place_id=source_place_id,
        tenant_id=place.tenant_id,
        store_id=place.store_id,
        transaction_id=transaction_id_for_source(canonical_key),
        event_time=event_time,
        observation_time=envelope.observed_at,
        payment_time=payment_time if status in {"succeeded", "partial"} else None,
        gross_amount=gross,
        discount_amount=max(gross - net, Decimal("0.00")),
        net_amount=net,
        currency=_text(document.get("currency"), "transaction.currency").upper(),
        payment_method=_text(
            document.get("payGateway"), "transaction.payGateway"
        ).lower(),
        transaction_status=status,
        ingested_at=envelope.observed_at,
    )
