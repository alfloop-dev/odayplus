"""Native Store Ops scope and unknown-data semantics (future runtime tests)."""
from dataclasses import asdict, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.opsboard.application.store_ops import StoreOpsLiveDataUnavailable
from modules.opsboard.application.store_ops_native_summary import native_store_summary
from shared.domain.models import Machine, Store

NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)
START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2027, 1, 1, tzinfo=UTC)


def _case(*machines):
    engine = object()
    store = Store(
        store_id="native-store", tenant_id="native-tenant", brand_id="native-brand",
        region_code="north", store_format_code="RECORDED_FORMAT_CODE",
        effective_from=START, effective_to=END,
    )
    reader = Mock(return_value={"store": asdict(store), "machines": list(machines)})
    kwargs = dict(
        store_id=store.store_id, tenant_id=store.tenant_id,
        brand_ids=(store.brand_id,), region_codes=(store.region_code,),
        store_ids=(store.store_id,), engine=engine,
        store_repository=SimpleNamespace(_engine=engine),
        machine_repository=SimpleNamespace(_engine=engine, get_store_machine_snapshot=reader),
        as_of=NOW,
    )
    return store, kwargs, reader


def _machine(key="native-machine", **changes):
    return replace(Machine(
        machine_id=key, store_id="native-store", effective_from=START, effective_to=END,
    ), **changes)


@pytest.mark.parametrize("changes", [
    {"tenant_id": "foreign-tenant"}, {"brand_id": "foreign-brand"},
    {"region_code": "south"}, {"store_id": "hidden-store"},
    {"is_current": False}, {"effective_to": NOW},
    {"effective_from": NOW.replace(tzinfo=None)},
])
def test_native_store_scope_and_current_record_are_required_before_machine_read(changes):
    store, kwargs, reader = _case(_machine())
    result = native_store_summary(replace(store, **changes), **kwargs)
    assert result["storeFormat"]["status"] == "unavailable"
    assert result["machines"]["value"] is None
    reader.assert_not_called()
    assert "foreign" not in str(result)


def test_empty_machine_source_does_not_manufacture_zero_or_finance_currency():
    store, kwargs, reader = _case()
    result = native_store_summary(store, **kwargs)
    assert result["storeFormat"]["value"] == "RECORDED_FORMAT_CODE"
    assert result["storeFormat"]["recordId"] == store.store_id
    assert result["machines"]["status"] == "unavailable"
    assert result["machines"]["value"] is None
    assert result["todayRevenue"]["value"] is None
    assert result["todayRevenue"]["currency"] is None
    assert result["todayRevenue"]["window"] is None
    reader.assert_called_once_with(
        tenant_id=store.tenant_id, store_id=store.store_id,
        brand_ids=(store.brand_id,), region_codes=(store.region_code,), as_of=NOW,
    )


def test_current_registered_count_excludes_retired_expired_and_future_without_physical_claim():
    store, kwargs, _ = _case(
        _machine("active"), _machine("maintenance", machine_status="maintenance"),
        _machine("retired", machine_status="retired"),
        _machine("expired", effective_to=NOW),
        _machine("future", effective_from=END, effective_to=datetime(2028, 1, 1, tzinfo=UTC)),
    )
    result = native_store_summary(store, **kwargs)
    assert result["machines"]["status"] == "recorded"
    assert result["machines"]["value"] == 2
    assert result["machines"]["recordIds"] == ["active", "maintenance"]
    assert result["machines"]["physicalInventoryComplete"] is None


@pytest.mark.parametrize("machines", [
    [_machine(store_id="hidden-store")],
    [_machine(), _machine()],
    [_machine(machine_status="unknown")],
    [_machine(effective_from=START.replace(tzinfo=None))],
    [_machine(effective_to=START)],
    [_machine(machine_status="retired")],
])
def test_invalid_or_no_current_machine_records_remain_unknown(machines):
    store, kwargs, _ = _case(*machines)
    result = native_store_summary(store, **kwargs)
    assert result["machines"]["status"] == "unavailable"
    assert result["machines"]["value"] is None
    assert "recordIds" not in result["machines"]


def test_mismatched_engine_cannot_supply_native_machine_facts():
    store, kwargs, reader = _case(_machine())
    kwargs["machine_repository"]._engine = object()
    result = native_store_summary(store, **kwargs)
    assert result["storeFormat"]["status"] == "unavailable"
    assert result["machines"]["status"] == "unavailable"
    reader.assert_not_called()


def test_unrecorded_store_format_is_not_inferred_from_name_or_ownership():
    store, kwargs, reader = _case(_machine())
    store = replace(store, store_format_code="", store_name="自助洗衣", ownership_type="owned")
    reader.return_value["store"] = asdict(store)
    result = native_store_summary(store, **kwargs)
    assert result["storeFormat"]["value"] is None
    assert result["machines"]["value"] == 1


def test_machine_read_failure_leaves_transaction_via_existing_unavailable_error():
    store, kwargs, reader = _case(_machine())
    reader.side_effect = RuntimeError("Synthetic unavailable backing source")
    with pytest.raises(StoreOpsLiveDataUnavailable, match="Native machine source read unavailable"):
        native_store_summary(store, **kwargs)


def test_format_and_machine_facts_share_the_current_authorized_native_snapshot():
    store, kwargs, reader = _case(_machine())
    reader.return_value["store"]["store_format_code"] = "NEW_CURRENT_RECORDED_CODE"
    result = native_store_summary(store, **kwargs)
    assert result["storeFormat"]["value"] == "NEW_CURRENT_RECORDED_CODE"
    assert result["machines"]["value"] == 1
    reader.return_value = None
    result = native_store_summary(store, **kwargs)
    assert result["storeFormat"]["status"] == "unavailable"
    assert result["machines"]["status"] == "unavailable"
