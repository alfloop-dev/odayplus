from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

_PREFIX = "https://oday.plus/data-plane/fongniao_prod"


def _stable_uuid(entity: str, source_id: str) -> UUID:
    normalized = str(source_id).strip()
    if not normalized:
        raise ValueError(f"source id is required for {entity}")
    return uuid5(NAMESPACE_URL, f"{_PREFIX}/{entity}/{normalized}")


def tenant_id_for_merchant(source_id: str) -> UUID:
    return _stable_uuid("tenant", source_id)


def brand_id_for_merchant(source_id: str) -> UUID:
    return _stable_uuid("owned-brand", source_id)


def address_id_for_place(source_id: str) -> UUID:
    return _stable_uuid("address-location", source_id)


def store_id_for_place(source_id: str) -> UUID:
    return _stable_uuid("store", source_id)


def transaction_id_for_source(source_id: str) -> UUID:
    return _stable_uuid("transaction", source_id)


def machine_id_for_device(source_id: str) -> UUID:
    return _stable_uuid("machine", source_id)


def machine_cycle_id_for_source(source_id: str) -> UUID:
    return _stable_uuid("machine-cycle", source_id)


def machine_status_event_id_for_source(source_id: str) -> UUID:
    return _stable_uuid("machine-status-event", source_id)


def snapshot_id_for_content(source_kind: str, source_id: str, sha256: str) -> UUID:
    return _stable_uuid("source-snapshot", f"{source_kind}/{source_id}/{sha256}")
