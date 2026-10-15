"""Future actual-PG counter: native facts cannot cross the authorized store."""
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.store_ops import create_operator_store_ops_router
from shared.domain.models import Machine
from tests.integration import test_store_ops_scoped_postgresql as scoped_tests

BASE = scoped_tests.BASE
case = scoped_tests.case

pytestmark = pytest.mark.requires_live_env


def _client(bundle):
    app = FastAPI()

    @app.middleware("http")
    async def correlate(request: Request, call_next):
        request.state.correlation_id = "synthetic-native-store-summary"
        return await call_next(request)

    app.include_router(create_operator_store_ops_router(
        repository=bundle.store_ops_repository,
        store_repository=bundle.store_repository,
        machine_repository=bundle.machine_repository,
        delivery_job_queue=bundle.job_queue,
        audit_log=bundle.audit_log,
        require_live_data=True,
    ), prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def test_store_native_facts_use_current_authorized_records_without_persisting_projection(case):
    bundle = case["bundle"]
    start = datetime(2020, 1, 1, tzinfo=UTC)
    end = datetime(2100, 1, 1, tzinfo=UTC)
    current = bundle.store_repository.get_store(case["store"])
    bundle.store_repository.save_store(replace(
        current, store_format_code="EXPLICIT_SYNTHETIC_FORMAT", effective_from=start, effective_to=end,
    ))
    expected = str(uuid4())
    hidden = str(uuid4())
    for key, store, machine_status in (
        (expected, case["store"], "maintenance"),
        (str(uuid4()), case["store"], "retired"),
        (hidden, case["hidden_store"], "active"),
    ):
        bundle.machine_repository.save_machine(Machine(
            machine_id=key, store_id=store, machine_status=machine_status,
            effective_from=start, effective_to=end,
        ))
    before = deepcopy(case["repo"].get_state())
    response = _client(bundle).get(f"{BASE}/issues", headers=case["headers"])
    assert response.status_code == 200, response.text
    rows = response.json()["stores"]
    assert [row["id"] for row in rows] == [case["store"]]
    native = rows[0]["nativeSummary"]
    assert native["storeFormat"]["value"] == "EXPLICIT_SYNTHETIC_FORMAT"
    assert native["machines"]["value"] == 1
    assert native["machines"]["recordIds"] == [expected]
    assert native["machines"]["physicalInventoryComplete"] is None
    assert native["todayRevenue"]["value"] is None
    assert hidden not in response.text
    assert case["repo"].get_state() == before

    wrong_tenant = str(uuid4())
    assert bundle.machine_repository.get_store_machine_snapshot(
        tenant_id=wrong_tenant, store_id=case["store"], as_of=datetime.now(UTC),
    ) is None
    narrowed = {**case["headers"], "x-store-ids": case["hidden_store"], "x-region-ids": "south"}
    read = _client(bundle).get(f"{BASE}/issues", headers=narrowed)
    assert read.status_code == 200, read.text
    assert expected not in read.text
    assert all(row["id"] != case["store"] for row in read.json()["stores"])


@pytest.mark.parametrize("change", ["region", "brand", "current", "effective_window"])
def test_native_machine_query_rechecks_store_scope_changed_between_authorized_reads(case, change):
    from shared.domain.models import Brand
    from tests.integration.test_intake_registry_calendar_postgresql import _bundle

    bundle = case["bundle"]
    start = datetime(2020, 1, 1, tzinfo=UTC)
    end = datetime(2100, 1, 1, tzinfo=UTC)
    store = replace(bundle.store_repository.get_store(case["store"]),
                    effective_from=start, effective_to=end)
    bundle.store_repository.save_store(store)
    machine_id = str(uuid4())
    bundle.machine_repository.save_machine(Machine(
        machine_id=machine_id, store_id=store.store_id,
        effective_from=start, effective_to=end,
    ))
    second = _bundle(case["db"], case["monkeypatch"])
    original_reader = bundle.machine_repository.get_store_machine_snapshot
    changes = {"region": {"region_code": "south"}, "current": {"is_current": False},
               "effective_window": {"effective_to": datetime(2021, 1, 1, tzinfo=UTC)}}
    new_brand = str(uuid4())
    if change == "brand":
        second.brand_repository.save_brand(Brand(
            brand_id=new_brand, tenant_id=case["tenant"], brand_code=new_brand,
            brand_name="Explicit synthetic other brand",
        ))
        changes["brand"] = {"brand_id": new_brand}

    def rotate_then_read(**kwargs):
        # The actual second engine commits after route list_stores and before
        # the new SQL read, without replacing auth or the original SQL reader.
        second.store_repository.save_store(replace(store, **changes[change]))
        return original_reader(**kwargs)

    case["monkeypatch"].setattr(bundle.machine_repository, "get_store_machine_snapshot", rotate_then_read)
    try:
        response = _client(bundle).get(f"{BASE}/issues", headers=case["headers"])
        assert response.status_code == 200, response.text
        snapshot = response.json()["stores"][0]["nativeSummary"]
        assert snapshot["storeFormat"]["status"] == "unavailable"
        assert snapshot["storeFormat"]["value"] is None
        assert snapshot["machines"]["status"] == "unavailable"
        assert snapshot["machines"]["value"] is None
        assert machine_id not in response.text
    finally:
        second.engine.close()
