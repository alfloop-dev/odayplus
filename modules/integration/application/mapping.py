from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, fields
from datetime import UTC, date, datetime, time
from typing import Any

from modules.integration.application.identity_resolution import (
    IdentityResolution,
    InMemoryIdentityResolver,
    source_key_from_payload,
)
from shared.domain import AddressLocation, Listing, Machine, Store, Transaction

ENTITY_TYPES = {
    "address": AddressLocation,
    "listing": Listing,
    "machine": Machine,
    "store": Store,
    "transaction": Transaction,
}

SOURCE_ID_FIELDS = {
    "address": ("source_address_id", "address_source_id", "raw_address"),
    "listing": ("source_listing_id", "listing_id"),
    "machine": ("source_machine_id", "machine_id", "machine_serial_no"),
    "store": ("source_store_id", "store_id"),
    "transaction": ("source_transaction_id", "transaction_id"),
}

ID_FIELDS = {
    "address": "address_id",
    "listing": "listing_id",
    "machine": "machine_id",
    "store": "store_id",
    "transaction": "transaction_id",
}

FIELD_ALIASES = {
    "raw_address": ("raw_address", "address", "full_address"),
    "normalized_address": ("normalized_address", "normalized_full_address"),
    "latitude": ("latitude", "lat"),
    "longitude": ("longitude", "lng", "lon"),
    "source_listing_id": ("source_listing_id", "listing_id"),
    "source_store_id": ("source_store_id", "store_id"),
    "source_machine_id": ("source_machine_id", "machine_id"),
    "source_transaction_id": ("source_transaction_id", "transaction_id"),
    "store_name": ("store_name", "name"),
    "rent_amount": ("rent_amount", "rent", "monthly_rent"),
    "area_ping": ("area_ping", "area"),
    "event_time": ("event_time", "business_time", "occurred_at"),
    "observation_time": ("observation_time", "observed_at", "received_at"),
    "ingested_at": ("ingested_at", "loaded_at"),
}


@dataclass(frozen=True)
class FieldLineage:
    canonical_field: str
    source_field: str
    source_value: Any


@dataclass(frozen=True)
class MappingResult:
    entity_type: str
    canonical: Any
    identity: IdentityResolution
    field_lineage: tuple[FieldLineage, ...]
    warnings: tuple[str, ...] = ()

    def canonical_dict(self) -> dict[str, Any]:
        return asdict(self.canonical)


def _lookup(payload: Mapping[str, Any], canonical_field: str) -> tuple[str, Any] | None:
    for source_field in FIELD_ALIASES.get(canonical_field, (canonical_field,)):
        if source_field in payload and payload[source_field] not in (None, ""):
            return source_field, payload[source_field]
    return None


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _parse_time(value: Any) -> time:
    if isinstance(value, time):
        return value
    return time.fromisoformat(str(value))


def _coerce(field_type: Any, value: Any) -> Any:
    if value in (None, ""):
        return value
    type_text = str(field_type)
    if field_type is bool or type_text == "bool":
        return _parse_bool(value)
    if field_type is int or type_text == "int":
        return int(value)
    if field_type is float or type_text == "float":
        return float(value)
    if "datetime" in type_text:
        return _parse_datetime(value)
    if "date" in type_text and "datetime" not in type_text:
        return _parse_date(value)
    if "time" in type_text and "datetime" not in type_text:
        return _parse_time(value)
    return value


class SourceToCanonicalMapper:
    def __init__(self, identity_resolver: InMemoryIdentityResolver | None = None) -> None:
        self.identity_resolver = identity_resolver or InMemoryIdentityResolver()

    def map_record(
        self,
        entity_type: str,
        payload: Mapping[str, Any],
        *,
        source_id: str | None = None,
        tenant_id: str = "",
        extra_defaults: Mapping[str, Any] | None = None,
    ) -> MappingResult:
        normalized_entity_type = entity_type.strip().lower()
        entity_cls = ENTITY_TYPES.get(normalized_entity_type)
        if entity_cls is None:
            raise ValueError(f"Unsupported canonical entity type: {entity_type}")

        source_key = source_key_from_payload(
            normalized_entity_type,
            payload,
            source_id=source_id,
            source_entity_fields=SOURCE_ID_FIELDS[normalized_entity_type],
            tenant_id=tenant_id,
        )
        identity = self.identity_resolver.resolve(source_key, lineage={"source_payload": dict(payload)})

        values = dict(extra_defaults or {})
        lineage: list[FieldLineage] = []
        warnings: list[str] = []
        for field_info in fields(entity_cls):
            found = _lookup(payload, field_info.name)
            if found is None:
                continue
            source_field, source_value = found
            try:
                values[field_info.name] = _coerce(field_info.type, source_value)
            except (TypeError, ValueError) as exc:
                warnings.append(f"{field_info.name}: could not coerce {source_field}={source_value!r}: {exc}")
                continue
            lineage.append(FieldLineage(field_info.name, source_field, source_value))

        values[ID_FIELDS[normalized_entity_type]] = identity.canonical_id
        canonical = entity_cls(**values)
        return MappingResult(
            entity_type=normalized_entity_type,
            canonical=canonical,
            identity=identity,
            field_lineage=tuple(lineage),
            warnings=tuple(warnings),
        )


def map_source_record(
    entity_type: str,
    payload: Mapping[str, Any],
    *,
    source_id: str | None = None,
    tenant_id: str = "",
    extra_defaults: Mapping[str, Any] | None = None,
    identity_resolver: InMemoryIdentityResolver | None = None,
) -> MappingResult:
    return SourceToCanonicalMapper(identity_resolver).map_record(
        entity_type,
        payload,
        source_id=source_id,
        tenant_id=tenant_id,
        extra_defaults=extra_defaults,
    )


MapperFactory = Callable[[InMemoryIdentityResolver | None], SourceToCanonicalMapper]


__all__ = [
    "FieldLineage",
    "MappingResult",
    "SourceToCanonicalMapper",
    "map_source_record",
]
