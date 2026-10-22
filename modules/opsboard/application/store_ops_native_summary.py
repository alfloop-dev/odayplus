"""Read-only Store Ops facts from the already authorized native store scope.

Registered equipment is not a physical-inventory completeness assertion.
Financial business-day policy is not supplied by the current Store domain.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from modules.opsboard.application.store_ops import StoreOpsLiveDataUnavailable
from shared.domain.models import Machine, Store


def _unavailable(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "value": None, "reason": reason, "source": None}


def _current(start: Any, end: Any, as_of: datetime) -> bool | None:
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return None
    if start.tzinfo is None or end.tzinfo is None:
        return None
    if start.utcoffset() is None or end.utcoffset() is None or start >= end:
        return None
    return start <= as_of < end


def native_store_summary(
    store: Any,
    *,
    store_id: str,
    tenant_id: str,
    brand_ids: tuple[str, ...],
    region_codes: tuple[str, ...],
    store_ids: tuple[str, ...],
    engine: Any,
    store_repository: Any,
    machine_repository: Any,
    as_of: datetime,
) -> dict[str, Any]:
    """Add facts only after the route's current principal and store authorization.

    Recheck all native ownership dimensions before reading machines. Repository
    scope is in SQL as well as in the returned-record validation. An empty
    register is unknown, not proof that this physical store has zero machines.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("An explicit observation instant is required")
    result = {
        "storeId": store_id,
        "observedAt": as_of.astimezone(UTC).isoformat(),
        "storeFormat": _unavailable("CURRENT_NATIVE_STORE_REQUIRED"),
        "machines": _unavailable("CURRENT_NATIVE_STORE_REQUIRED"),
        "todayRevenue": {
            **_unavailable("STORE_REVENUE_BUSINESS_DAY_POLICY_UNAVAILABLE"),
            "currency": None,
            "window": None,
        },
    }
    if (
        not tenant_id
        or not isinstance(store, Store)
        or store.store_id != store_id
        or store.tenant_id != tenant_id
        or brand_ids and store.brand_id not in brand_ids
        or region_codes and store.region_code not in region_codes
        or store_ids and store_id not in store_ids
        or store_repository is None
        or getattr(store_repository, "_engine", None) is not engine
        or store.is_current is not True
        or _current(store.effective_from, store.effective_to, as_of) is not True
    ):
        return result

    reader = getattr(machine_repository, "get_store_machine_snapshot", None)
    if (
        machine_repository is None
        or getattr(machine_repository, "_engine", None) is not engine
        or not callable(reader)
    ):
        result["machines"] = _unavailable("SCOPED_MACHINE_SOURCE_UNAVAILABLE")
        return result

    # Database failures propagate through the established route transaction;
    # suppressing one here could leave a PostgreSQL transaction aborted.
    try:
        snapshot = reader(
            tenant_id=tenant_id, store_id=store_id,
            brand_ids=brand_ids, region_codes=region_codes, as_of=as_of,
        )
    except Exception as exc:
        # Raise through the transaction boundary so PostgreSQL rolls back before
        # the established route emits its explicit 503 unavailable response.
        raise StoreOpsLiveDataUnavailable("Native machine source read unavailable") from exc
    parent = snapshot.get("store") if isinstance(snapshot, dict) else None
    if (
        not isinstance(parent, dict)
        or parent.get("store_id") != store_id or parent.get("tenant_id") != tenant_id
        or brand_ids and parent.get("brand_id") not in brand_ids
        or region_codes and parent.get("region_code") not in region_codes
        or parent.get("is_current") is not True
        or _current(parent.get("effective_from"), parent.get("effective_to"), as_of) is not True
    ):
        return result
    code = parent.get("store_format_code")
    if isinstance(code, str) and code.strip():
        result["storeFormat"] = {
            "status": "recorded", "value": code.strip(),
            "source": "core.stores.store_format_code", "recordId": store_id,
            "effectiveFrom": parent["effective_from"].isoformat(),
            "effectiveTo": parent["effective_to"].isoformat(),
        }
    else:
        result["storeFormat"] = _unavailable("STORE_FORMAT_NOT_RECORDED")
    machines = snapshot.get("machines")
    if not isinstance(machines, list) or not machines:
        result["machines"] = _unavailable("MACHINE_REGISTER_HAS_NO_RECORDS")
        return result
    identifiers: set[str] = set()
    current_ids: list[str] = []
    for machine in machines:
        if (
            not isinstance(machine, Machine)
            or machine.store_id != store_id
            or not machine.machine_id
            or machine.machine_id in identifiers
            or machine.machine_status not in {"active", "inactive", "maintenance", "retired"}
        ):
            result["machines"] = _unavailable("MACHINE_REGISTER_INVALID_OR_OUT_OF_SCOPE")
            return result
        identifiers.add(machine.machine_id)
        current = _current(machine.effective_from, machine.effective_to, as_of)
        if current is None:
            result["machines"] = _unavailable("MACHINE_EFFECTIVE_WINDOW_UNAVAILABLE")
            return result
        if current and machine.machine_status != "retired":
            current_ids.append(machine.machine_id)
    if not current_ids:
        result["machines"] = _unavailable("NO_CURRENT_MACHINE_RECORDS")
        return result
    result["machines"] = {
        "status": "recorded",
        "value": len(current_ids),
        "source": "core.machines",
        "basis": "current_nonretired_registered_records",
        "recordIds": sorted(current_ids),
        "physicalInventoryComplete": None,
    }
    return result
