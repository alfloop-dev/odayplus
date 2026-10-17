"""The main composition's PriceOps provider must read the native durable owner.

These synthetic SQLite HTTP tests compile the exact nested main provider only,
then use the real PriceOps and Operator routers with their original guards.
Importing main would construct its global app and seed unrelated model aliases;
this finite composition test deliberately does neither. It is not full-app or
production runtime acceptance. Optimization below is the explicit POC algorithm.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator import create_operator_router
from apps.api.app.routes.priceops import create_priceops_router
from apps.api.oday_api.security import dependencies
from shared.api.errors import install_error_handlers
from shared.domain import AddressLocation, Brand, Store, Tenant
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.repositories import DurablePriceOpsRepository

ROOT = Path(__file__).resolve().parents[2]
BASE = "/api/v1"
HEADERS = {
    "x-subject-id": "synthetic-pricing-owner",
    "x-roles": "pricing_manager,operations_manager",
    "x-tenant-id": "tenant-a",
    "x-operator-role": "ops-lead",
    "x-correlation-id": "synthetic-owner-correlation",
}


def _main_provider(repository: Any, document_store: Any):
    """Execute the original product closure, without replacing its body."""
    source = ROOT / "apps/api/oday_api/main.py"
    tree = ast.parse(source.read_text())
    providers = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                 and node.name == "priceops_repository_for_tenant"]
    assert len(providers) == 1
    for factory, keyword, selected in (
        ("create_priceops_router", "repository", "price_repo"),
        ("create_operator_router", "priceops_repository_for_tenant", "priceops_repository_for_tenant"),
    ):
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == factory]
        assert len(calls) == 1
        binding = next(value.value for value in calls[0].keywords if value.arg == keyword)
        assert isinstance(binding, ast.Name) and binding.id == selected
    namespace = {
        "Any": Any,
        "DurablePriceOpsRepository": DurablePriceOpsRepository,
        "price_repo": repository,
        "tenant_document_store": lambda tenant: TenantScopedDocumentStore(document_store, tenant),
    }
    code = ast.fix_missing_locations(ast.Module(body=[providers[0]], type_ignores=[]))
    exec(compile(code, str(source), "exec"), namespace)
    return namespace["priceops_repository_for_tenant"]


def _seed_master(bundle):
    for tenant in ("tenant-a", "tenant-b"):
        bundle.tenant_repository.save_tenant(Tenant(tenant_id=tenant, tenant_name="Synthetic pricing owner"))
        bundle.brand_repository.save_brand(Brand(brand_id=f"brand-{tenant}", tenant_id=tenant,
                                                 brand_code=tenant, brand_name="Synthetic brand"))
        bundle.address_location_repository.save_address(AddressLocation(address_id=f"address-{tenant}",
                                                                          tenant_id=tenant))
        bundle.store_repository.save_store(Store(store_id=f"store-{tenant}", tenant_id=tenant,
                                                 brand_id=f"brand-{tenant}", address_id=f"address-{tenant}",
                                                 store_name="Synthetic store", region_code="north"))


def _context(path):
    from shared.infrastructure.persistence.document_store import SqliteDocumentStore

    bundle = _durable_bundle(path)
    document_store = SqliteDocumentStore(bundle.engine)
    provider = _main_provider(bundle.priceops_repository, document_store)
    app = FastAPI()
    install_error_handlers(app)
    app.state.job_queue = bundle.job_queue

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        request.state.correlation_id = request.headers.get("x-correlation-id", "synthetic-owner-correlation")
        response = await call_next(request)
        response.headers["x-correlation-id"] = request.state.correlation_id
        return response

    app.include_router(create_priceops_router(repository=bundle.priceops_repository,
                                             store_repository=bundle.store_repository,
                                             brand_repository=bundle.brand_repository,
                                             audit_log=bundle.audit_log, job_queue=bundle.job_queue,
                                             require_durable_commands=True, runtime_mode="poc"), prefix=BASE)
    app.include_router(create_operator_router(audit_log=bundle.audit_log, document_store=document_store,
                                              job_queue=bundle.job_queue,
                                              priceops_repository_for_tenant=provider,
                                              require_live_data=True), prefix=BASE)
    return bundle, provider, TestClient(app, headers=HEADERS)


@pytest.fixture(autouse=True)
def _local_identity(monkeypatch):
    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    monkeypatch.setattr(dependencies, "_default_boundary", None)


def _create(client, plan_id="synthetic-owner-plan", tenant="tenant-a"):
    payload = {
        "tenant_id": tenant, "plan_id": plan_id,
        "items": [{"item_id": f"item-{plan_id}", "store_id": f"store-{tenant}",
                   "machine_type": "washer", "unit_cost": 30, "current_price": 80,
                   "baseline_demand": 40, "elasticity_value": -1, "confidence": 0.8,
                   "applicable_min_price": 60, "applicable_max_price": 100}],
    }
    response = client.post(BASE + "/priceops/plans", headers={"Idempotency-Key": "create-" + plan_id}, json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["created_by"] == HEADERS["x-subject-id"]
    return response.json()


def test_main_priceops_provider_preserves_native_owner_and_existing_exploration(tmp_path):
    bundle, provider, client = _context(tmp_path / "owner.sqlite3")
    try:
        authority = object()
        bundle.priceops_repository.canonical_exploration = authority
        assert provider("tenant-a") is bundle.priceops_repository
        assert provider("tenant-b") is bundle.priceops_repository
        assert provider("tenant-a").canonical_exploration is authority
        assert provider("") is provider(" ") is None
    finally:
        client.close()
        bundle.engine.close()


def test_native_priceops_http_write_growth_and_comparison_share_owner_after_reopen(tmp_path):
    path = tmp_path / "owner-reopen.sqlite3"
    bundle, provider, client = _context(path)
    try:
        _seed_master(bundle)
        plan = _create(client)
        for operation in ("simulate", "optimize"):
            response = client.post(BASE + "/priceops/plans/" + plan["plan_id"] + "/" + operation,
                                   headers={"Idempotency-Key": operation + "-owner"},
                                   json={"reason": "Explicit synthetic POC pricing comparison"})
            assert response.status_code == 200, response.text
        growth = client.get(BASE + "/operator/growth/recommendations")
        assert growth.status_code == 200, growth.text
        assert [row["planId"] for row in growth.json()["items"]] == [plan["plan_id"]]
        segments = client.get(BASE + "/operator/growth/segments").json()["items"]
        assert len(segments) == 1 and segments[0]["planId"] == plan["plan_id"]
        comparison = client.get(BASE + "/priceops/plans/" + plan["plan_id"] + "/comparison")
        assert comparison.status_code == 200, comparison.text
        assert comparison.json()["plan_id"] == growth.json()["items"][0]["planId"]
        assert comparison.json()["created_by"] == HEADERS["x-subject-id"]
    finally:
        client.close()
        bundle.engine.close()

    reopened, provider, client = _context(path)
    try:
        assert provider("tenant-a").get_plan(plan["plan_id"]) == reopened.priceops_repository.get_plan(plan["plan_id"])
        assert client.get(BASE + "/operator/growth/recommendations").json()["items"][0]["planId"] == plan["plan_id"]
        assert client.get(BASE + "/priceops/plans/" + plan["plan_id"] + "/comparison").status_code == 200
    finally:
        client.close()
        reopened.engine.close()


def test_shared_owner_preserves_cross_tenant_and_canonical_master_denial(tmp_path):
    bundle, _, client = _context(tmp_path / "tenant-denial.sqlite3")
    try:
        _seed_master(bundle)
        plan = _create(client)
        foreign = {**HEADERS, "x-tenant-id": "tenant-b"}
        for suffix in ("", "/comparison"):
            assert client.get(BASE + "/priceops/plans/" + plan["plan_id"] + suffix, headers=foreign).status_code == 404
        assert client.get(BASE + "/priceops/plans", headers=foreign).json()["items"] == []
        assert client.get(BASE + "/operator/growth/segments", headers=foreign).json()["items"] == []
        mismatch = client.post(BASE + "/priceops/plans", headers={**foreign, "Idempotency-Key": "foreign-create"},
                               json={"tenant_id": "tenant-a", "items": [{"store_id": "store-tenant-a",
                                     "machine_type": "washer", "unit_cost": 30, "current_price": 80,
                                     "baseline_demand": 40, "elasticity_value": -1,
                                     "applicable_min_price": 60, "applicable_max_price": 100}]})
        assert mismatch.status_code == 403
        restricted = {**HEADERS, "x-store-ids": "store-tenant-b"}
        assert client.get(BASE + "/priceops/plans/" + plan["plan_id"], headers=restricted).status_code == 404
        readonly = {**HEADERS, "x-roles": "auditor"}
        assert client.post(BASE + "/priceops/plans/" + plan["plan_id"] + "/simulate",
                           headers=readonly, json={"reason": "Attempted read-only mutation"}).status_code == 403
    finally:
        client.close()
        bundle.engine.close()
