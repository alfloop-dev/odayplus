"""Synthetic durable HTTP proof: derived reads retain verified object scopes."""
from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import replace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator import create_operator_router
from apps.api.app.routes.priceops import create_priceops_router
from apps.api.oday_api.security import dependencies
from shared.api.errors import install_error_handlers
from shared.domain import Brand, Store
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.infrastructure.persistence.operator_domains import (
    DurableOperatorDomainStateRepository,
    TenantScopedDocumentStore,
)
from shared.infrastructure.persistence.repositories import (
    DurableAVMRepository,
    DurableDecisionStore,
    DurableNetPlanRepository,
)
from tests.integration.test_priceops_growth_main_owner import (
    HEADERS,
    ROOT,
    _main_provider,
    _seed_master,
)

BASE = "/api/v1"
AXES = [("x-store-ids", "store-tenant-a"), ("x-brand-ids", "brand-tenant-a"), ("x-region-ids", "north")]


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    monkeypatch.setattr(dependencies, "_default_boundary", None)
    bundle = _durable_bundle(tmp_path / "own-derived-read-scope.sqlite3")
    _seed_master(bundle)
    bundle.brand_repository.save_brand(Brand(brand_id="excluded-brand", tenant_id="tenant-a",
                                             brand_code="excluded", brand_name="Synthetic excluded brand"))
    bundle.store_repository.save_store(Store(store_id="excluded-store", tenant_id="tenant-a",
                                             brand_id="excluded-brand", address_id="address-tenant-a",
                                             store_name="Synthetic excluded store", region_code="south"))
    store = SqliteDocumentStore(bundle.engine)
    provider = _main_provider(bundle.priceops_repository, store)
    app = FastAPI()
    app.state.job_queue = bundle.job_queue
    install_error_handlers(app)

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        request.state.correlation_id = request.headers.get("x-correlation-id", "synthetic-scope-correlation")
        return await call_next(request)

    app.include_router(create_priceops_router(repository=bundle.priceops_repository,
                                             store_repository=bundle.store_repository,
                                             brand_repository=bundle.brand_repository,
                                             audit_log=bundle.audit_log, job_queue=bundle.job_queue,
                                             require_durable_commands=True, runtime_mode="poc"), prefix=BASE)
    app.include_router(create_operator_router(audit_log=bundle.audit_log, document_store=store,
                                              job_queue=bundle.job_queue,
                                              priceops_repository_for_tenant=provider,
                                              priceops_store_repository=bundle.store_repository,
                                              priceops_brand_repository=bundle.brand_repository,
                                              sitescore_decision_repository_for_tenant=lambda tenant: DurableDecisionStore(TenantScopedDocumentStore(store, tenant)),
                                              avm_repository_for_tenant=lambda tenant: DurableAVMRepository(TenantScopedDocumentStore(store, tenant)),
                                              netplan_repository_for_tenant=lambda tenant: DurableNetPlanRepository(TenantScopedDocumentStore(store, tenant)),
                                              require_live_data=True), prefix=BASE)
    client = TestClient(app, headers=HEADERS)
    for plan_id, store_id in (("visible-plan", "store-tenant-a"), ("excluded-plan", "excluded-store")):
        response = client.post(BASE + "/priceops/plans", headers={"Idempotency-Key": "create-" + plan_id}, json={
            "tenant_id": "tenant-a", "plan_id": plan_id,
            "items": [{"item_id": "item-" + plan_id, "store_id": store_id, "machine_type": "washer",
                       "unit_cost": 30, "current_price": 80, "baseline_demand": 40,
                       "elasticity_value": -1, "confidence": 0.8,
                       "applicable_min_price": 60, "applicable_max_price": 100}],
        })
        assert response.status_code == 201, response.text
        for operation in ("simulate", "optimize"):
            response = client.post(BASE + "/priceops/plans/" + plan_id + "/" + operation,
                                   headers={"Idempotency-Key": operation + "-" + plan_id},
                                   json={"reason": "Explicit synthetic POC scope control"})
            assert response.status_code == 200, response.text
    try:
        yield client, bundle
    finally:
        client.close()
        bundle.engine.close()


@pytest.mark.parametrize(("header", "allowed"), AXES)
@pytest.mark.parametrize("endpoint", ["growth/segments", "growth/recommendations", "governance/snapshot"])
def test_derived_plan_reads_match_native_scope_before_rows_and_counts(context, header, allowed, endpoint):
    client, _ = context
    scoped = {**HEADERS, header: allowed}
    native = client.get(BASE + "/priceops/plans", headers=scoped)
    assert native.status_code == 200 and [row["plan_id"] for row in native.json()["items"]] == ["visible-plan"]
    assert client.get(BASE + "/priceops/plans/excluded-plan/comparison", headers=scoped).status_code == 404
    response = client.get(BASE + "/operator/" + endpoint, headers=scoped)
    assert response.status_code == 200, response.text
    body = response.json()
    if endpoint.startswith("growth/"):
        assert [row["planId"] for row in body["items"]] == ["visible-plan"]
        assert body["count"] == 1
    else:
        assert [row["id"] for row in body["approvals"] if row["module"] == "PriceOps"] == ["visible-plan"]
        assert {row["status"] for row in body["approvals"]} == {"not_submitted"}
        assert body["counts"]["pendingApprovals"] == 0

        # Optimizing does not submit a plan. Use the real owner command to
        # prove nonzero pending counts also retain the original object scope.
        for plan_id in ("visible-plan", "excluded-plan"):
            submitted = client.post(
                BASE + "/priceops/plans/" + plan_id + "/submit",
                headers={"Idempotency-Key": "scope-submit-" + plan_id},
                json={"reason": "Explicit synthetic pending scope control"},
            )
            assert submitted.status_code == 200, submitted.text
            assert submitted.json()["status"] == "pending_approval"
        unrestricted = client.get(BASE + "/operator/governance/snapshot")
        assert unrestricted.status_code == 200, unrestricted.text
        assert {row["id"] for row in unrestricted.json()["approvals"]
                if row["status"] == "pending"} == {"visible-plan", "excluded-plan"}
        assert unrestricted.json()["counts"]["pendingApprovals"] == 2
        pending_response = client.get(BASE + "/operator/governance/snapshot", headers=scoped)
        assert pending_response.status_code == 200, pending_response.text
        body = pending_response.json()
        assert [(row["id"], row["status"]) for row in body["approvals"]
                if row["module"] == "PriceOps"] == [("visible-plan", "pending")]
        assert body["counts"]["pendingApprovals"] == 1
        assert "excluded-plan" not in pending_response.text
    assert "excluded-plan" not in response.text


def test_tenant_wide_reader_keeps_both_owned_plans_and_foreign_tenant_keeps_none(context):
    client, _ = context
    for endpoint in ("growth/segments", "growth/recommendations", "governance/snapshot"):
        response = client.get(BASE + "/operator/" + endpoint)
        assert response.status_code == 200, response.text
        assert "visible-plan" in response.text and "excluded-plan" in response.text
        foreign = client.get(BASE + "/operator/" + endpoint, headers={**HEADERS, "x-tenant-id": "tenant-b"})
        assert foreign.status_code == 200, foreign.text
        assert "visible-plan" not in foreign.text and "excluded-plan" not in foreign.text


def test_main_passes_the_same_canonical_masters_to_derived_reads():
    tree = ast.parse((ROOT / "apps/api/oday_api/main.py").read_text())
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "create_operator_router")
    keywords = {node.arg: node.value for node in call.keywords}
    for name, owner in (("priceops_store_repository", "store_repository"),
                        ("priceops_brand_repository", "brand_repository")):
        binding = keywords[name]
        assert isinstance(binding, ast.IfExp) and isinstance(binding.test, ast.Name) and binding.test.id == "require_live_data"
        assert isinstance(binding.body, ast.Attribute) and binding.body.attr == owner
        assert isinstance(binding.body.value, ast.Name) and binding.body.value.id == "bundle"


@pytest.mark.parametrize(("roles", "persona", "endpoint"), [
    ("marketing_manager", "marketing-manager", "growth/segments"),
    ("marketing_manager", "marketing-manager", "growth/recommendations"),
    ("auditor", "pm-audit", "governance/snapshot"),
])
def test_existing_operator_only_readers_keep_their_caller_module_without_native_priceops_grants(context, monkeypatch, roles, persona, endpoint):
    client, _ = context
    original = dependencies.principal_from_headers

    def narrow_original_scope(*args, **kwargs):
        principal = original(*args, **kwargs)
        return replace(principal, scope=replace(principal.scope, modules=frozenset({"operator_console"})))

    monkeypatch.setattr(dependencies, "principal_from_headers", narrow_original_scope)
    headers = {**HEADERS, "x-roles": roles, "x-operator-role": persona,
               "x-subject-id": "synthetic-original-reader", "x-store-ids": "store-tenant-a"}
    assert client.get(BASE + "/priceops/plans", headers=headers).status_code == 403
    response = client.get(BASE + "/operator/" + endpoint, headers=headers)
    assert response.status_code == 200, response.text
    if endpoint.startswith("growth/"):
        assert [row["planId"] for row in response.json()["items"]] == ["visible-plan"]
    else:
        assert [row["id"] for row in response.json()["approvals"]] == ["visible-plan"]
        assert response.json()["actionAuthority"]["decide"] is False
    assert "excluded-plan" not in response.text


@pytest.mark.parametrize("header", ["x-store-ids", "x-brand-ids", "x-region-ids", "x-assigned-area-ids", "x-heat-zone-ids"])
def test_excluded_or_unbound_scope_has_no_derived_rows_freshness_or_aggregates(context, header):
    client, _ = context
    headers = {**HEADERS, header: "not-an-owned-binding"}
    for endpoint in ("segments", "recommendations", "actions", "approvals", "decisions"):
        response = client.get(BASE + "/operator/growth/" + endpoint, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["items"] == [] and response.json()["count"] == 0
    freshness = client.get(BASE + "/operator/growth/freshness", headers=headers).json()
    assert freshness["planCount"] == 0 and freshness["sourceSnapshotId"] == [] and freshness["status"] == "EMPTY"
    summary = client.get(BASE + "/operator/growth/summary", headers=headers).json()
    assert summary["segmentCount"] == summary["activeCount"] == 0
    govern = client.get(BASE + "/operator/governance/snapshot", headers=headers).json()
    assert govern["approvals"] == [] and govern["counts"]["pendingApprovals"] == 0
    assert next(row for row in govern["statusBoard"] if row["name"] == "PriceOps plans")["count"] == 0
    assert client.post(BASE + "/operator/governance/decisions", headers=headers,
                       json={"approvalId": "excluded-plan", "action": "approve"}).status_code == 404


def _action(client, plan, *, headers=None):
    body = {"name": "Explicit synthetic draft for " + plan,
            "segmentId": "priceops:" + plan + ":item-" + plan,
            "sourceRecommendationId": "priceops-rec:" + plan + ":item-" + plan,
            "objective": "Review explicit synthetic pricing input", "targetLift": 0.1,
            "kind": "priceops", "store": plan, "rationale": "Explicit local synthetic scope regression",
            "rollbackPlan": "Return to the recorded current price after independent review"}
    response = client.post(BASE + "/operator/growth/actions", headers={**(headers or HEADERS), "Idempotency-Key": "draft-" + plan}, json=body)
    assert response.status_code == 200, response.text
    return response.json()["id"], body


def _submit(client, action, *, headers=None):
    response = client.post(BASE + "/operator/growth/actions/" + action + "/submit",
                           headers={**(headers or HEADERS), "Idempotency-Key": "submit-" + action}, json={})
    assert response.status_code == 200, response.text
    return response.json()["approval"]["id"]


def _approve(client, approval, *, headers=None):
    body = {"approvalId": approval, "action": "approve", "reason": "Explicit local synthetic owner decision"}
    response = client.post(BASE + "/operator/governance/decisions",
                           headers={**(headers or HEADERS), "Idempotency-Key": "approve-" + approval}, json=body)
    assert response.status_code == 200 and response.json()["approvalId"] == approval, response.text
    return body


def test_scoped_successful_writes_preserve_hidden_records_receipts_and_reads_never_save_projection(context):
    client, bundle = context
    visible, _ = _action(client, "visible-plan")
    hidden, hidden_body = _action(client, "excluded-plan")
    hidden_approval = _submit(client, hidden)
    hidden_decision = _approve(client, hidden_approval)
    store = SqliteDocumentStore(bundle.engine)
    growth = DurableOperatorDomainStateRepository(store, "growth")
    govern = DurableOperatorDomainStateRepository(store, "governance")
    before_growth, before_govern = growth.load("tenant-a"), govern.load("tenant-a")
    assert before_growth["commandIdempotencyResults"] and before_govern["commandIdempotency"]
    scoped = {**HEADERS, "x-store-ids": "store-tenant-a"}
    approval = _submit(client, visible, headers=scoped)
    _approve(client, approval, headers=scoped)
    after_growth, after_govern = growth.load("tenant-a"), govern.load("tenant-a")
    for kind, key, identity in (("actions", "id", hidden), ("approvals", "id", hidden_approval),
                                ("decisions", "ref", hidden)):
        before = next(row for row in before_growth[kind] if row[key] == identity)
        assert next(row for row in after_growth[kind] if row[key] == identity) == before
    for field in ("commandIdempotencyResults", "idempotencyResults"):
        assert all(after_growth[field][key] == value for key, value in before_growth[field].items())
    assert all(after_govern["commandIdempotency"][key] == value for key, value in before_govern["commandIdempotency"].items())
    before_hidden_log = next(row for row in before_govern["decisions"] if row["approvalId"] == hidden_approval)
    assert next(row for row in after_govern["decisions"] if row["approvalId"] == hidden_approval) == before_hidden_log

    saved = deepcopy((after_growth, after_govern))
    assert [row["id"] for row in client.get(BASE + "/operator/growth/actions", headers=scoped).json()["items"]] == [visible]
    assert client.get(BASE + "/operator/growth/actions/" + hidden, headers=scoped).status_code == 404
    assert client.get(BASE + "/operator/growth/actions/" + hidden, headers={**HEADERS, "x-tenant-id": "tenant-b"}).status_code == 404
    snapshot = client.get(BASE + "/operator/governance/snapshot", headers=scoped)
    assert snapshot.status_code == 200 and hidden not in snapshot.text and hidden_approval not in snapshot.text
    assert client.post(BASE + "/operator/growth/actions", headers={**scoped, "Idempotency-Key": "draft-excluded-plan"}, json=hidden_body).status_code == 404
    assert client.post(BASE + "/operator/governance/decisions", headers={**scoped, "Idempotency-Key": "approve-" + hidden_approval}, json=hidden_decision).status_code == 404
    assert (growth.load("tenant-a"), govern.load("tenant-a")) == saved
    assert {row["id"] for row in client.get(BASE + "/operator/growth/actions").json()["items"]} == {visible, hidden}
    assert {row["id"] for row in client.get(BASE + "/operator/growth/approvals").json()["items"]} == {approval, hidden_approval}

    # A legitimate current master change shrinks visible canonical parents;
    # historical saved actions/decisions and their receipts remain untouched.
    original = bundle.store_repository.get_store("excluded-store")
    bundle.store_repository.save_store(replace(original, is_current=False))
    assert client.get(BASE + "/priceops/plans/excluded-plan/comparison").status_code == 404
    assert [row["id"] for row in client.get(BASE + "/operator/growth/actions").json()["items"]] == [visible]
    assert hidden not in client.get(BASE + "/operator/governance/snapshot").text
    assert (growth.load("tenant-a"), govern.load("tenant-a")) == saved


def test_canonical_master_transfer_and_outage_match_native_scope(context, monkeypatch):
    client, bundle = context
    original = bundle.store_repository.get_store("excluded-store")
    bundle.store_repository.save_store(replace(original, brand_id="brand-tenant-a"))
    assert client.get(BASE + "/priceops/plans/excluded-plan/comparison").status_code == 404
    assert "excluded-plan" not in client.get(BASE + "/operator/growth/segments").text
    assert "excluded-plan" not in client.get(BASE + "/operator/governance/snapshot").text

    def unavailable(_store):
        raise RuntimeError("Synthetic canonical owner outage")

    monkeypatch.setattr(bundle.store_repository, "get_store", unavailable)
    for path in ("/priceops/plans", "/operator/growth/recommendations", "/operator/governance/snapshot"):
        response = client.get(BASE + path)
        assert response.status_code == 503 and response.json()["detail"]["code"] == "PRICEOPS_STORE_BINDING_UNAVAILABLE"


def test_hidden_recommendation_link_cannot_escape_through_a_visible_segment_or_replay(context):
    client, bundle = context
    _, body = _action(client, "visible-plan")
    body = {**body, "name": "Synthetic cross-scope reference", "sourceRecommendationId": "priceops-rec:excluded-plan:item-excluded-plan"}
    headers = {**HEADERS, "Idempotency-Key": "cross-reference-intent"}
    created = client.post(BASE + "/operator/growth/actions", headers=headers, json=body)
    assert created.status_code == 200, created.text
    action = created.json()["id"]
    scoped = {**headers, "x-store-ids": "store-tenant-a"}
    owner = DurableOperatorDomainStateRepository(SqliteDocumentStore(bundle.engine), "growth")
    before = owner.load("tenant-a")
    assert client.get(BASE + "/operator/growth/actions/" + action, headers=scoped).status_code == 404
    assert action not in {row["id"] for row in client.get(BASE + "/operator/growth/actions", headers=scoped).json()["items"]}
    assert client.post(BASE + "/operator/growth/actions", headers=scoped, json=body).status_code == 404
    assert owner.load("tenant-a") == before
    assert client.get(BASE + "/operator/growth/actions/" + action).status_code == 200


def test_mixed_item_plan_is_excluded_as_a_whole_like_native(context):
    client, _ = context
    template = client.get(BASE + "/priceops/plans/visible-plan").json()
    items = []
    for store in ("store-tenant-a", "excluded-store"):
        item = template["items"][0]
        items.append({"item_id": "mixed-" + store, "store_id": store, "machine_type": item["machine_type"],
                      "unit_cost": 30, "current_price": 80, "baseline_demand": 40,
                      "elasticity_value": -1, "confidence": 0.8, "applicable_min_price": 60,
                      "applicable_max_price": 100})
    response = client.post(BASE + "/priceops/plans", headers={"Idempotency-Key": "mixed-plan"},
                           json={"plan_id": "mixed-plan", "tenant_id": "tenant-a", "items": items})
    assert response.status_code == 201, response.text
    scoped = {**HEADERS, "x-store-ids": "store-tenant-a"}
    assert client.get(BASE + "/priceops/plans/mixed-plan", headers=scoped).status_code == 404
    assert {row["planId"] for row in client.get(BASE + "/operator/growth/segments", headers=scoped).json()["items"]} == {"visible-plan"}
    assert "mixed-plan" not in client.get(BASE + "/operator/governance/snapshot", headers=scoped).text
    assert "mixed-plan" in client.get(BASE + "/operator/growth/segments").text


def test_other_governance_module_projection_and_original_authority_are_retained(context):
    from modules.opsboard.application.governance import GovernanceService

    client, bundle = context
    owner = DurableOperatorDomainStateRepository(SqliteDocumentStore(bundle.engine), "governance")
    service = GovernanceService(seed_fixtures=False)
    approval = {"id": "synthetic-other-module", "module": "Store Ops", "title": "Synthetic existing owner approval",
                "status": "pending", "entityRef": "synthetic-store-issue"}
    service.upsert_approval(approval)
    owner.save("tenant-a", service.export_state())
    before = owner.load("tenant-a")
    for headers in (HEADERS, {**HEADERS, "x-store-ids": "store-tenant-a"}):
        response = client.get(BASE + "/operator/governance/snapshot", headers=headers)
        assert response.status_code == 200, response.text
        assert next(row for row in response.json()["approvals"] if row["id"] == approval["id"]) == approval
        assert response.json()["actionAuthority"]["verified"] is True
        assert response.json()["actionAuthority"]["decide"] is True
    assert owner.load("tenant-a") == before
    denied = client.post(BASE + "/operator/governance/decisions", headers={**HEADERS, "x-roles": "auditor"},
                         json={"approvalId": approval["id"], "action": "approve"})
    assert denied.status_code == 403 and owner.load("tenant-a") == before
