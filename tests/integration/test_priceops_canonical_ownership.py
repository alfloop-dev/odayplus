"""Synthetic canonical master-data ownership and independent approval HTTP proof."""
from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.priceops import create_priceops_router
from modules.priceops.application import PriceOpsService
from modules.priceops.infrastructure import InMemoryPriceOpsRepository
from shared.api.errors import install_error_handlers
from shared.audit import InMemoryAuditLog
from shared.domain.models import Brand, Store
from shared.infrastructure.persistence.repositories import (
    InMemoryBrandRepository,
    InMemoryStoreRepository,
)
from tests.integration.test_priceops_command_authority import proposal

CREATOR = {"x-subject-id": "verified-creator", "x-roles": "pricing_manager", "x-tenant-id": "tenant-a"}
REVIEWER = {**CREATOR, "x-subject-id": "verified-reviewer"}


def app_context(*, repository=None, store_repository=None, brand_repository=None, **kwargs):
    repo = repository or InMemoryPriceOpsRepository()
    audit = InMemoryAuditLog()
    app = FastAPI()
    install_error_handlers(app)

    @app.middleware("http")
    async def correlation(request, call_next):
        request.state.correlation_id = request.headers.get("x-correlation-id", "synthetic-ownership")
        return await call_next(request)

    app.include_router(create_priceops_router(repository=repo, audit_log=audit,
                                              store_repository=store_repository,
                                              brand_repository=brand_repository, **kwargs))
    return TestClient(app, headers=CREATOR), repo, audit


@pytest.fixture
def context():
    stores = InMemoryStoreRepository()
    brands = InMemoryBrandRepository()
    for tenant, brand, store, region in (("tenant-a", "brand-a", "store-a", "north"),
                                         ("tenant-a", "brand-b", "store-b", "south"),
                                         ("tenant-b", "brand-c", "store-c", "north")):
        brands.save_brand(Brand(brand_id=brand, tenant_id=tenant))
        stores.save_store(Store(store_id=store, tenant_id=tenant, brand_id=brand, region_code=region))
    client, repo, audit = app_context(store_repository=stores, brand_repository=brands, runtime_mode="poc")
    return client, repo, audit, stores, brands


def prepare(client, plan="new-plan"):
    created = client.post("/priceops/plans", json=proposal(plan))
    assert created.status_code == 201, created.text
    for suffix in ("simulate", "optimize", "submit"):
        response = client.post(f"/priceops/plans/{plan}/{suffix}", json={"reason": "synthetic independent review"})
        assert response.status_code == 200, response.text
    return created.json()


def test_store_and_brand_are_resolved_from_master_data_not_body(context):
    client, repo, audit, stores, brands = context
    body = proposal()
    del body["items"][0]["brand_id"]
    body["created_by"] = "forged-creator"
    created = client.post("/priceops/plans", headers={**CREATOR, "x-brand-ids": "brand-a", "x-region-ids": "north"}, json=body)
    assert created.status_code == 201, created.text
    assert created.json()["items"][0]["brand_id"] == "brand-a"
    assert created.json()["created_by"] == "verified-creator"
    assert repo.get_plan("new-plan").created_by == "verified-creator"


@pytest.mark.parametrize("store,brand", [("store-b", "brand-a"), ("store-c", "brand-a"), ("missing-store", "brand-a")])
def test_forged_brand_or_foreign_store_cannot_create_plan(context, store, brand):
    client, repo, audit, stores, brands = context
    response = client.post("/priceops/plans", headers={**CREATOR, "x-brand-ids": "brand-a"}, json=proposal(store_id=store, brand_id=brand))
    assert response.status_code == 403
    assert repo.list_plans() == []


def test_batch_ownership_is_checked_before_first_plan_write(context):
    client, repo, audit, stores, brands = context
    response = client.post("/priceops/optimizer-jobs", json={"plans": [proposal("good-first"), proposal("bad-second", store_id="store-c")]})
    assert response.status_code == 403 and repo.list_plans() == []


def test_canonical_region_and_ownership_are_rechecked_before_replay_and_reads(context):
    client, repo, audit, stores, brands = context
    body = proposal()
    key = {"Idempotency-Key": "canonical-create"}
    first = client.post("/priceops/plans", headers={**key, "x-region-ids": "north"}, json=body)
    assert first.status_code == 201
    assert client.get("/priceops/plans/new-plan", headers={"x-region-ids": "north"}).status_code == 200
    assert client.get("/priceops/plans/new-plan", headers={"x-region-ids": "south"}).status_code == 404
    original = stores.get_store("store-a")
    stores.save_store(replace(original, tenant_id="tenant-b"))
    assert client.post("/priceops/plans", headers=key, json=body).status_code == 403
    assert client.get("/priceops/plans/new-plan").status_code == 404
    assert repo.get_plan("new-plan").tenant_id == "tenant-a"


def test_mismatched_master_brand_tenant_and_noncurrent_store_fail_closed(context):
    client, repo, audit, stores, brands = context
    brand = brands.get_brand("brand-a")
    brands.save_brand(replace(brand, tenant_id="tenant-b"))
    assert client.post("/priceops/plans", json=proposal()).status_code == 403
    brands.save_brand(brand)
    stores.save_store(replace(stores.get_store("store-a"), is_current=False))
    assert client.post("/priceops/plans", json=proposal()).status_code == 403
    assert repo.list_plans() == []


def test_lookup_outage_does_not_fall_back_to_proposal_fields(context, monkeypatch):
    client, repo, audit, stores, brands = context
    def unavailable(_store_id):
        raise RuntimeError("synthetic master-data outage")
    monkeypatch.setattr(stores, "get_store", unavailable)
    response = client.post("/priceops/plans", json=proposal())
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "PRICEOPS_STORE_BINDING_UNAVAILABLE"
    assert repo.list_plans() == []


@pytest.mark.parametrize("endpoint,actor_field", [("approve", "actor_id"), ("decision-writeback", "actor")])
def test_creator_cannot_approve_even_with_forged_body_actor(context, endpoint, actor_field):
    client, repo, audit, stores, brands = context
    prepare(client)
    comparison = client.get("/priceops/plans/new-plan/comparison").json()
    assert comparison["created_by"] == "verified-creator"
    assert comparison["is_approvable"] is False and comparison["approval_actor_blockers"]
    body = {actor_field: "verified-reviewer", "decision": "approved", "reason": "authored reason"}
    denied = client.post(f"/priceops/plans/new-plan/{endpoint}", headers={"Idempotency-Key": "creator-approval"}, json=body)
    assert denied.status_code == 403
    assert repo.list_approvals("new-plan") == []
    assert repo.list_decision_writebacks("new-plan") == []
    assert repo.get_plan("new-plan").status.value == "pending_approval"
    allowed = client.post(f"/priceops/plans/new-plan/{endpoint}", headers={**REVIEWER, "Idempotency-Key": "reviewer-approval"}, json=body)
    assert allowed.status_code == 200, allowed.text
    assert repo.list_approvals("new-plan")[0].actor_id == "verified-reviewer"


def test_batch_creator_is_verified_and_self_approval_is_denied(context):
    client, repo, audit, stores, brands = context
    response = client.post("/priceops/optimizer-jobs", json={"plans": [proposal()]})
    assert response.status_code == 202
    assert repo.get_plan("new-plan").created_by == "verified-creator"
    submitted = client.post("/priceops/plans/new-plan/submit", json={"reason": "review"})
    assert submitted.status_code == 200
    assert client.post("/priceops/plans/new-plan/approve", json={"actor_id": "forged", "decision": "approved", "reason": "review"}).status_code == 403


def test_creator_cannot_inherit_another_actors_successful_approval_receipt(context):
    client, repo, audit, stores, brands = context
    prepare(client)
    body = {"actor_id": "forged", "decision": "approved", "reason": "review"}
    key = {"Idempotency-Key": "shared-approval-key"}
    first = client.post("/priceops/plans/new-plan/approve", headers={**REVIEWER, **key}, json=body)
    assert first.status_code == 200
    assert client.post("/priceops/plans/new-plan/approve", headers=key, json=body).status_code == 403
    replay = client.post("/priceops/plans/new-plan/approve", headers={**REVIEWER, **key}, json=body)
    assert replay.status_code == 200 and replay.json()["decision_id"] == first.json()["decision_id"]
    assert len(repo.list_approvals("new-plan")) == 1


@pytest.mark.parametrize("decision,reason", [("unknown", "review"), ("approved", " ")])
def test_invalid_approval_has_no_business_effect(context, decision, reason):
    client, repo, audit, stores, brands = context
    prepare(client)
    response = client.post("/priceops/plans/new-plan/approve", headers=REVIEWER, json={"actor_id": "forged", "decision": decision, "reason": reason})
    assert response.status_code == 422
    assert repo.list_approvals("new-plan") == []
    assert repo.get_plan("new-plan").status.value == "pending_approval"


def test_production_composition_requires_canonical_ownership_repositories(tmp_path):
    from modules.priceops.infrastructure.oss_optimizer import PriceOpsProductionOptimizer
    from shared.infrastructure.persistence.document_store import SqliteDocumentStore
    from shared.infrastructure.persistence.engine import SqliteEngine
    from shared.infrastructure.persistence.repositories import DurablePriceOpsRepository

    engine = SqliteEngine(tmp_path / "synthetic-composition.sqlite3")
    try:
        repo = DurablePriceOpsRepository(SqliteDocumentStore(engine))
        client, _, _ = app_context(repository=repo, production_optimizer=PriceOpsProductionOptimizer(), runtime_mode="production")
        response = client.get("/priceops/plans")
        assert response.status_code == 503
        assert "canonical store and brand" in response.text
    finally:
        engine.close()


def test_legacy_unknown_creator_is_not_inferred_or_approved_in_production(tmp_path):
    from modules.priceops.infrastructure.oss_optimizer import PriceOpsProductionOptimizer
    from shared.infrastructure.persistence.document_store import SqliteDocumentStore
    from shared.infrastructure.persistence.engine import SqliteEngine
    from shared.infrastructure.persistence.repositories import DurablePriceOpsRepository
    from tests.unit.test_pricing_simulation import _sample_item

    engine = SqliteEngine(tmp_path / "synthetic-legacy-creator.sqlite3")
    try:
        repo = DurablePriceOpsRepository(SqliteDocumentStore(engine))
        service = PriceOpsService(repository=repo, runtime_mode="poc")
        item = replace(_sample_item(), store_id="store-a", brand_id="brand-a")
        plan = service.create_plan(tenant_id="tenant-a", items=(item,), correlation_id="legacy", plan_id="legacy-plan")
        service.simulate(plan.plan_id)
        service.optimize(plan.plan_id)
        service.submit_for_approval(plan.plan_id, actor="cannot-infer-creator", reason="legacy review")
        stores = InMemoryStoreRepository()
        brands = InMemoryBrandRepository()
        brands.save_brand(Brand(brand_id="brand-a", tenant_id="tenant-a"))
        stores.save_store(Store(store_id="store-a", tenant_id="tenant-a", brand_id="brand-a"))
        client, _, _ = app_context(repository=repo, store_repository=stores, brand_repository=brands,
                                   production_optimizer=PriceOpsProductionOptimizer(), runtime_mode="production")
        response = client.post("/priceops/plans/legacy-plan/approve", json={"actor_id": "forged", "decision": "approved", "reason": "review"})
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "PRICEOPS_APPROVAL_SEPARATION_REQUIRED"
        assert repo.get_plan("legacy-plan").created_by is None
        assert repo.list_approvals("legacy-plan") == []
        assert repo.get_plan("legacy-plan").status.value == "pending_approval"
        strict = PriceOpsService(repository=repo, production_optimizer=PriceOpsProductionOptimizer(), runtime_mode="production")
        with pytest.raises(ValueError, match="verified creator"):
            strict.create_plan(tenant_id="tenant-a", items=(item,), correlation_id="unknown", plan_id="unbound-production-plan")
        assert repo.get_plan("unbound-production-plan") is None
    finally:
        engine.close()
