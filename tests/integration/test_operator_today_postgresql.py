"""Actual PostgreSQL read projection with isolated synthetic issue records."""

from uuid import uuid4

import pytest

from modules.opsboard.application.operator_live_repository import OperatorLiveRepository
from modules.opsboard.application.store_ops import DurableStoreOpsRepository
from shared.domain import AddressLocation, Brand, Store, Tenant
from shared.infrastructure.persistence import PostgresDocumentStore
from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from tests.integration.test_intake_registry_calendar_postgresql import _bundle, _upgrade

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def case(intake_blank_db, monkeypatch):
    _upgrade(intake_blank_db, "head")
    apply_upgrade_to_database(intake_blank_db.url())
    bundle = _bundle(intake_blank_db, monkeypatch)
    tenant_a, tenant_b = str(uuid4()), str(uuid4())
    brand_a, brand_b = str(uuid4()), str(uuid4())
    stores = [str(uuid4()) for _ in range(3)]
    address_id = str(uuid4())
    bundle.address_location_repository.save_address(AddressLocation(address_id=address_id, raw_address="Synthetic address", city="Taipei"))
    for tenant, brand in ((tenant_a, brand_a), (tenant_b, brand_b)):
        bundle.tenant_repository.save_tenant(Tenant(tenant_id=tenant, tenant_name="Synthetic Today test"))
        bundle.brand_repository.save_brand(Brand(brand_id=brand, tenant_id=tenant, brand_code=brand, brand_name="Synthetic brand"))
    for index, store in enumerate(stores):
        tenant, brand = (tenant_a, brand_a) if index < 2 else (tenant_b, brand_b)
        bundle.store_repository.save_store(Store(store_id=store, tenant_id=tenant, brand_id=brand, address_id=address_id, store_name="Synthetic store", region_code="north" if index == 0 else "south", store_status="open"))
    document_store = PostgresDocumentStore(bundle.engine)
    try:
        yield intake_blank_db, bundle, document_store, tenant_a, tenant_b, brand_a, stores, monkeypatch
    finally:
        bundle.engine.close()


def _issue(store, **changes):
    return {"id": str(uuid4()), "storeId": store, "status": "new", "severity": "critical", "ownerName": "", "slaDueAt": "2026-01-01T00:00:00Z", **changes}


def _write(store, tenant, issues):
    repository = DurableStoreOpsRepository(TenantScopedDocumentStore(store, tenant))
    repository.save_state({"issues": issues, "stores": [], "evidence": [], "auditEvents": []})


def test_real_postgresql_issues_are_tenant_brand_region_and_store_scoped(case):
    db, bundle, documents, tenant_a, tenant_b, brand_a, stores, monkeypatch = case
    _write(documents, tenant_a, [_issue(stores[0]), _issue(stores[1]), _issue(stores[2], title="foreign store inside tenant partition")])
    _write(documents, tenant_b, [_issue(stores[2], title="foreign tenant secret")])
    # A legacy global document must never supply the scoped Today metric.
    documents.put("opsboard.store_ops.state", "default", {"issues": [_issue(stores[0], title="global legacy secret")]})
    scope = {"tenant_id": tenant_a, "brand_ids": (brand_a,), "region_ids": ("north",), "store_ids": (stores[0],)}
    state = OperatorLiveRepository(bundle).load_state(**scope)
    assert state["_meta"]["sections"]["storeOpsIssues"]["recordCount"] == 1
    cards = {item["label"]: item for item in state["kpis"]}
    assert cards["高風險未指派"]["value"] == "1"
    assert cards["已逾期 Issue"]["value"] == "1"
    assert "secret" not in str(state)
    assert "foreign store" not in str(state)
    # Fresh repository/connection reads the same committed scoped facts.
    reopened = _bundle(db, monkeypatch)
    try:
        again = OperatorLiveRepository(reopened).load_state(**scope)
        assert again["kpis"] == state["kpis"]
    finally:
        reopened.engine.close()


def test_missing_materialized_issue_state_is_unavailable_and_never_seeded(case):
    _, bundle, documents, tenant_a, _, _, stores, _ = case
    state = OperatorLiveRepository(bundle).load_state(tenant_id=tenant_a, store_ids=(stores[0],))
    assert state["_meta"]["sections"]["storeOpsIssues"]["state"] == "unavailable"
    assert state["kpis"][0]["value"] is None
    assert TenantScopedDocumentStore(documents, tenant_a).get("opsboard.store_ops.state", "default") is None


def test_issue_without_explicit_store_scope_cannot_make_a_zero_metric(case):
    _, bundle, documents, tenant_a, _, _, stores, _ = case
    _write(documents, tenant_a, [{"status": "new", "severity": "high", "ownerName": ""}])
    state = OperatorLiveRepository(bundle).load_state(tenant_id=tenant_a, store_ids=(stores[0],))
    assert state["_meta"]["sections"]["storeOpsIssues"]["state"] == "unavailable"
    assert state["kpis"][0]["value"] is None
