"""Signed native HTTP/owned SQLite controls, not production model/PG proof.

The two executor doubles deliberately exercise healthy native composition with
synthetic calculations. Their production_oss marker is a controlled admission
fixture, never evidence of a registered model, production data or live solver.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes._native_request_owner import NativeRequestOwnerFactory
from apps.api.app.routes.avm import create_avm_router
from apps.api.app.routes.netplan import create_netplan_router
from apps.api.oday_api.security import dependencies
from modules.avm.application.valuation import AVMReviewRequiredFailure, AVMService
from modules.avm.domain import ValuationCaseStatus, value_store
from modules.netplan.application.planning import NetPlanService
from modules.netplan.application.production import NetPlanProductionExecution
from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary
from shared.api.versioning import mount_versioned
from shared.domain.models import Brand, Store
from shared.governance import InMemoryDecisionPolicyRepository
from shared.governance.netplan_disclosure import default_netplan_disclosure_policy
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.netplan_outcomes import (
    DUE_JOB_TYPE,
    LABEL_COLLECTION,
    RECEIPT_COLLECTION,
    TASK_COLLECTION,
    DurableNetPlanOutcomeRegistry,
)
from shared.infrastructure.persistence.operator_domains import (
    DurableOperatorDomainStateRepository,
    TenantScopedDocumentStore,
)
from shared.infrastructure.persistence.repositories import (
    DurableAVMRepository,
    DurableNetPlanRepository,
    InMemoryBrandRepository,
    InMemoryStoreRepository,
)
from solver.netplan import solve_network_plan
from tests.integration._operation_flag_fixtures import new_operation_flags
from tests.integration.test_avm_valuation import _valuation_payload
from tests.integration.test_operator_netplan_scenario_builder import _business
from tests.security.test_feature_flag_admin_authorization import ISSUER, SIGNING_KEY, _headers

TENANT, STORE = "native-owner-tenant", "store-red-001"


class EngineeringAVMExecutor:
    def execute(self, case, margin, *, depreciation_version_pin=None):
        report = value_store(case, margin, depreciation_version_pin=depreciation_version_pin)
        return replace(report, execution_metadata={**report.execution_metadata,
            "mode": "production_oss", "engineering_owner_contract_double": True})


class EngineeringNetPlanExecutor:
    def execute(self, scenario, *, alternative_limit):
        result = solve_network_plan(options_by_entity=scenario.options_by_entity, constraints=scenario.constraints,
                                    alternative_limit=alternative_limit)
        return NetPlanProductionExecution(result, {"mode": "production_oss",
            "engineering_owner_contract_double": True})


@pytest.fixture
def case(tmp_path, monkeypatch):
    # Isolated HTTP owner/model-contract controls, not Main's live persistence
    # middleware or any real model/PG/provider availability claim.
    for key in ("ODP_REQUIRE_LIVE_DATA", "ODP_DEPLOY_ENV", "ODAY_ENV", "ODP_ENV", "APP_ENV", "ENVIRONMENT", "NODE_ENV"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    monkeypatch.setenv("ODP_AUDIT_WORM_SINK_URI", (tmp_path / "owned-native-worm").as_uri())
    engine = SqliteEngine(tmp_path / "native-owner.sqlite")
    try:
        docs, audit, queue = SqliteDocumentStore(engine), DurableAuditLog(engine), DurableJobQueue(engine)
        stores, brands = InMemoryStoreRepository(), InMemoryBrandRepository()
        stores.save_store(Store(store_id=STORE, tenant_id=TENANT, brand_id="native-brand", region_code="north"))
        brands.save_brand(Brand(brand_id="native-brand", tenant_id=TENANT))
        mappings = {
            name: {"roles": roles, "scope": {"tenant_id": tenant,
                "store_ids": allowed, "region_ids": ["north"], "modules": ["avm", "operator_console"]}}
            for name, roles, tenant, allowed in (
                ("creator", ["finance_legal", "executive"], TENANT, [STORE]),
                ("finance", ["finance_legal", "executive"], TENANT, [STORE]),
                ("outside", ["finance_legal", "executive"], TENANT, ["other-store"]),
                ("foreign", ["finance_legal", "executive"], "foreign-tenant", [STORE]),
                ("auditor", ["auditor"], TENANT, [STORE]),
                ("viewer", ["platform_admin"], TENANT, [STORE]),
            )
        }
        boundary = AuthenticationBoundary(AuthBoundaryConfig(issuer=ISSUER,
            audiences=frozenset({"unit-feature-api"}), signing_keys={SIGNING_KEY.kid: SIGNING_KEY},
            principal_mappings=mappings), audit_log=audit)
        monkeypatch.setattr(dependencies, "default_boundary", lambda: boundary)
        # The owner module resolves the same actual function through its import.
        monkeypatch.setattr("apps.api.app.routes._native_request_owner.default_boundary", lambda: boundary)
        flags, _ = new_operation_flags()
        app = FastAPI()
        app.state.feature_flag_registry, app.state.job_queue = flags, queue

        @app.middleware("http")
        async def correlation(request: Request, call_next):
            if request.headers.get("X-Correlation-Id"):
                request.state.correlation_id = request.headers["X-Correlation-Id"]
            return await call_next(request)

        def avm(tenant):
            return DurableAVMRepository(TenantScopedDocumentStore(docs, tenant))

        def netplan(tenant):
            return DurableNetPlanRepository(TenantScopedDocumentStore(docs, tenant))

        listing_state = DurableOperatorDomainStateRepository(docs, "network-listings")
        authz = dependencies.build_engine(audit_log=audit, flags=flags)
        owner_kwargs = dict(stores=stores, brands=brands, audit_log=audit, queue=queue,
                            boundary=boundary, engine=authz, formal=True)
        avm_executor, netplan_executor = EngineeringAVMExecutor(), EngineeringNetPlanExecutor()
        avm_owner = NativeRequestOwnerFactory(resource="avm", repository_for_tenant=avm,
            production_executor=avm_executor, **owner_kwargs)
        def outcomes(*, repository, principal, authorize, correlation_id):
            return DurableNetPlanOutcomeRegistry(repository=repository, principal=principal,
                authorize=authorize, audit_log=audit, queue=queue, correlation_id=correlation_id)

        netplan_owner = NativeRequestOwnerFactory(resource="netplan", repository_for_tenant=netplan,
            candidate_snapshot_for_tenant=lambda tenant: listing_state.load(tenant) or {},
            outcome_registry_for_tenant=outcomes, **owner_kwargs)
        app.state.native_approval_verifier = None

        def verify(*args, **kwargs):
            verifier = app.state.native_approval_verifier
            if verifier is None:
                raise ValueError("No declared engineering management receipt")
            return verifier.verify(*args, **kwargs)

        verifier_port = SimpleNamespace(verify=verify)
        policies = InMemoryDecisionPolicyRepository([default_netplan_disclosure_policy(TENANT)])
        mount_versioned(app, create_avm_router(repository=DurableAVMRepository(docs), audit_log=audit,
            job_queue=queue, runtime_mode="production", production_executor=avm_executor,
            require_durable_commands=True, request_owner=avm_owner))
        mount_versioned(app, create_netplan_router(repository=DurableNetPlanRepository(docs), audit_log=audit,
            job_queue=queue, feature_flags=flags, policy_repository=policies,
            runtime_mode="production", production_executor=netplan_executor, request_owner=netplan_owner,
            approval_verifier=verifier_port))
        with TestClient(app, raise_server_exceptions=False) as client:
            yield SimpleNamespace(client=client, engine=engine, docs=docs, audit=audit, stores=stores,
                brands=brands, avm=avm, netplan=netplan, flags=flags, listing_state=listing_state,
                boundary=boundary, mappings=mappings, app=app, avm_owner=avm_owner, netplan_owner=netplan_owner,
                bundle=SimpleNamespace(engine=engine, audit_log=audit))
    finally:
        engine.close()


def headers(actor="creator", *, key="native-owner-create"):
    return {**_headers(actor, correlation_id=key), "Idempotency-Key": key}


def create(case, resource, *, prefix="/api/v1", actor="creator", key="native-owner-create", **extra):
    if resource == "avm":
        body = {**_valuation_payload(), "created_by": actor, **extra}
        path = "/avm/cases"
    else:
        body = {"tenant_id": TENANT, "scenario_name": "Synthetic current-store plan",
            "planning_horizon": "2026-Q4", "constraints": {"max_budget": 1000000},
            "existing_stores": [{"store_id": STORE, "baseline_gross_margin": 100,
                "improve_gross_margin_uplift": 20, "improve_cost": 10, "move_cost": 30,
                "source_snapshot_ids": ["synthetic-current-source"]}], **extra}
        path = "/netplan/scenarios"
    return case.client.post(prefix + path, headers=headers(actor, key=key), json=body)


def identity(response, resource):
    assert response.status_code == 201, response.text
    return response.json()["case_id" if resource == "avm" else "scenario_id"]


@pytest.mark.parametrize("resource", ["avm", "netplan"])
@pytest.mark.parametrize("prefix", ["/api/v1", ""])
def test_signed_native_create_list_get_and_both_URL_aliases_use_actual_tenant_partition(case, resource, prefix):
    value = identity(create(case, resource, prefix=prefix), resource)
    path = prefix + ("/avm/cases" if resource == "avm" else "/netplan/scenarios")
    listed = case.client.get(path, headers=headers())
    assert listed.status_code == 200 and listed.json()["count"] == 1
    fetched = case.client.get(path + "/" + value, headers=headers())
    assert fetched.status_code == 200, fetched.text
    repo = case.avm(TENANT) if resource == "avm" else case.netplan(TENANT)
    original_global = DurableAVMRepository(case.docs) if resource == "avm" else DurableNetPlanRepository(case.docs)
    assert original_global.list_cases() == [] if resource == "avm" else original_global.list_scenarios() == []
    if resource == "avm":
        assert repo.get_case(value).created_by == "creator"
        event = case.audit.get_event(fetched.json().get("audit_event_id", "missing"), tenant_id=TENANT)
        assert event is None  # GET is not fabricated as a new business event.
    else:
        assert repo.get_scenario(value).tenant_id == TENANT


@pytest.mark.parametrize("resource", ["avm", "netplan"])
@pytest.mark.parametrize("actor,status", [("outside", 404), ("foreign", 404), ("viewer", 403)])
def test_current_object_scope_precedes_exact_GET_and_list_counts(case, resource, actor, status):
    value = identity(create(case, resource), resource)
    path = "/api/v1" + ("/avm/cases" if resource == "avm" else "/netplan/scenarios")
    before = _business(case)
    response = case.client.get(path + "/" + value, headers=headers(actor))
    assert response.status_code == status
    listing = case.client.get(path, headers=headers(actor))
    assert listing.status_code == (403 if actor == "viewer" else 200)
    if actor != "viewer":
        assert listing.json() == {"items": [], "count": 0}
    assert _business(case) == before
    assert case.client.get(path + "/unknown", headers=headers(actor)).status_code in {403, 404}
    assert case.client.get(path + "/" + value).status_code == 401


@pytest.mark.parametrize("resource", ["avm", "netplan"])
def test_body_creator_or_tenant_cannot_override_the_verified_signed_request(case, resource):
    before = _business(case)
    extra = {"created_by": "forged-creator"} if resource == "avm" else {"tenant_id": "foreign-tenant"}
    response = create(case, resource, **extra)
    assert response.status_code == 403 and _business(case) == before
    assert any(event.event_type == "security.authorization" and event.actor == "creator"
               and event.outcome == "deny" and event.metadata.get("policy_id") == "native.identity.pin"
               for event in case.audit.list_events(correlation_id="native-owner-create"))


@pytest.mark.parametrize("resource,suffix,body", [
    ("avm", "normalize", {"actor": "forged"}),
    ("avm", "value", {"actor": "forged"}),
    ("avm", "finance-approval", {"actor": "forged", "reason": "Untrusted actor"}),
    ("avm", "dataroom", {"actor": "forged"}),
    ("avm", "dataroom/export", {"actor": "forged", "reason": "Untrusted actor"}),
    ("netplan", "solve", {"actor": "forged"}),
    ("netplan", "submit", {"actor": "forged"}),
    ("netplan", "decide", {"actor_id": "forged", "reason": "Untrusted actor"}),
    ("netplan", "execute", {"executed_by": "forged"}),
    ("netplan", "outcomes", {"actor": "forged", "actual_gross_margin": 0}),
    ("netplan", "close", {"actor": "forged"}),
])
def test_every_original_command_keeps_its_DTO_but_rejects_a_forged_body_actor(case, resource, suffix, body):
    value = identity(create(case, resource), resource)
    path = "/api/v1" + ("/avm/cases/" if resource == "avm" else "/netplan/scenarios/") + value + "/" + suffix
    before = _business(case)
    response = case.client.post(path, json=body, headers=headers())
    assert response.status_code == 403, response.text
    assert _business(case) == before


def test_healthy_executor_native_finance_SoD_room_export_and_actual_guard_lineage(case):
    value = identity(create(case, "avm"), "avm")
    path = "/api/v1/avm/cases/" + value
    valued = case.client.post(path + "/value", json={"actor": "creator"}, headers=headers(key="native-value"))
    assert valued.status_code == 200, valued.text
    before = _business(case)
    denied = case.client.post(path + "/finance-approval", json={"actor": "creator", "reason": "Self review is forbidden"}, headers=headers(key="native-self-review"))
    assert denied.status_code == 403 and _business(case) == before
    approved = case.client.post(path + "/finance-approval", json={"actor": "finance", "reason": "Separate signed financial review"}, headers=headers("finance", key="native-finance"))
    assert approved.status_code == 200, approved.text
    assert approved.json()["finance_approval"]["actor_id"] == "finance"
    event = case.audit.get_event(approved.json()["audit_event_id"], tenant_id=TENANT)
    assert event.actor == "finance" and event.metadata["authenticated_principal"]["subject_id"] == "finance"
    assert event.metadata["authenticated_principal"]["token_type"] in {"local", "oidc", "service"}
    built = case.client.post(path + "/dataroom", json={"actor": "finance"}, headers=headers("finance", key="native-room"))
    assert built.status_code == 200, built.text
    exported = case.client.post(path + "/dataroom/export", json={"actor": "finance", "reason": "Actual metadata export control"}, headers=headers("finance", key="native-export"))
    assert exported.status_code == 200 and exported.json()["export_audit"][-1]["actor"] == "finance"
    assert case.client.get(path + "/dataroom", headers=headers("outside")).status_code == 404
    auditor = case.client.get(path + "/report", headers=headers("auditor"))
    assert auditor.status_code == 200
    assert auditor.json()["reserve_price"] is None and auditor.json()["asking_price"] is None


def test_native_solve_uses_server_actor_when_old_optional_default_is_omitted(case):
    value = identity(create(case, "netplan"), "netplan")
    path = "/api/v1/netplan/scenarios/" + value
    solved = case.client.post(path + "/solve", json={}, headers=headers(key="native-solve"))
    assert solved.status_code == 200, solved.text
    scenario = case.netplan(TENANT).get_scenario(value)
    assert scenario.status_history[-1].actor == "creator"
    # The original authenticity/disclosure guards remain; a caller string is
    # not converted into an approved management receipt.
    before = _business(case)
    denied = case.client.post(path + "/decide", json={"actor_id": "creator", "reason": "No actual authority receipt", "approval_receipt_id": "invented"}, headers=headers(key="native-decide"))
    assert denied.status_code == 422 and _business(case) == before
    assert case.netplan(TENANT).list_approvals(value) == []


def test_current_master_revocation_is_checked_before_native_idempotent_create_replay(case):
    created = create(case, "avm", key="repeat-same-native-intent")
    value = identity(created, "avm")
    replay = create(case, "avm", key="repeat-same-native-intent")
    assert replay.status_code == 201 and replay.json()["case_id"] == value and replay.json()["created"] is False
    case.stores.save_store(replace(case.stores.get_store(STORE), region_code="south"))
    before = _business(case)
    refused = create(case, "avm", key="repeat-same-native-intent")
    assert refused.status_code == 403 and _business(case) == before


def test_native_audit_failure_rolls_back_case_and_durable_receipt_on_same_SQL_owner(case, monkeypatch):
    record = case.audit.record

    def unavailable(event):
        if event.event_type == "avm.case_created.v1":
            raise RuntimeError("owned native domain audit unavailable")
        return record(event)

    monkeypatch.setattr(case.audit, "record", unavailable)
    before = _business(case)
    response = create(case, "avm", key="rollback-native-create")
    assert response.status_code == 500
    assert _business(case) == before and case.avm(TENANT).list_cases() == []


def test_unpartitioned_legacy_global_case_is_not_copied_or_retroactively_given_tenant_authority(case):
    global_repository = DurableAVMRepository(case.docs)
    legacy = AVMService(repository=global_repository, runtime_mode="poc").create_case(
        _valuation_payload(), created_by="unverified-historical-name", correlation_id="legacy-native-source")
    before = _business(case)
    response = case.client.get("/api/v1/avm/cases/" + legacy.case_id, headers=headers())
    assert response.status_code == 404
    listing = case.client.get("/avm/cases", headers=headers())
    assert listing.status_code == 200 and listing.json() == {"items": [], "count": 0}
    assert case.avm(TENANT).list_cases() == [] and global_repository.get_case(legacy.case_id) == legacy
    assert _business(case) == before


def test_wrong_repository_partition_is_unavailable_instead_of_falling_back_global(case):
    case.avm_owner.repositories = lambda tenant: DurableAVMRepository(case.docs)
    before = _business(case)
    response = create(case, "avm")
    assert response.status_code == 503 and _business(case) == before


@pytest.mark.parametrize("resource", ["avm", "netplan"])
@pytest.mark.parametrize("axis,value", [
    ("brand_ids", ["unrelated-brand"]), ("region_ids", ["unrelated-region"]),
    ("modules", ["unrelated-module"]), ("clearance", "PUBLIC"),
    ("assigned_area_ids", ["area-without-store-proof"]), ("heat_zone_ids", ["zone-without-store-proof"]),
])
def test_all_current_object_axes_are_checked_again_before_read(case, resource, axis, value):
    current = identity(create(case, resource), resource)
    case.mappings["creator"]["scope"][axis] = value
    before = _business(case)
    path = "/api/v1" + ("/avm/cases/" if resource == "avm" else "/netplan/scenarios/") + current
    response = case.client.get(path, headers=headers())
    assert response.status_code == 404 and _business(case) == before


def test_explicit_native_scenario_create_id_cannot_reset_existing_authority(case):
    current = identity(create(case, "netplan", scenario_id="existing-native-id"), "netplan")
    before = _business(case)
    response = create(case, "netplan", scenario_id=current, scenario_name="Replacement cannot erase history")
    assert response.status_code == 409 and _business(case) == before


def test_candidate_options_reuse_actual_persisted_listing_parent_scope(case):
    parent = {"id": "source-listing", "tenantId": TENANT, "storeId": STORE, "regionId": "north"}
    child = {"id": "candidate-1", "listingId": parent["id"], "tenantId": TENANT}
    case.listing_state.save(TENANT, {"listings": [parent], "candidates": [child]})
    created = create(case, "netplan", existing_stores=[], candidate_sites=[{
        "candidate_site_id": child["id"], "expected_gross_margin": 30,
        "open_cost": 10, "risk_score": 0.1, "source_snapshot_ids": ["synthetic-listing-source"]}])
    current = identity(created, "netplan")
    case.listing_state.save(TENANT, {"listings": [parent], "candidates": [{**child, "tenantId": "foreign"}]})
    before = _business(case)
    response = case.client.get("/api/v1/netplan/scenarios/" + current, headers=headers())
    assert response.status_code == 404 and _business(case) == before


def test_pinned_boundary_and_captured_none_do_not_follow_another_app_owner(case, monkeypatch):
    current = identity(create(case, "avm"), "avm")
    selected = {"boundary": case.boundary}

    def same_getter():
        return selected["boundary"]

    monkeypatch.setattr(dependencies, "default_boundary", same_getter)
    monkeypatch.setattr("apps.api.app.routes._native_request_owner.default_boundary", same_getter)
    path = "/api/v1/avm/cases/" + current
    assert case.client.get(path, headers=headers()).status_code == 200
    other = AuthenticationBoundary(AuthBoundaryConfig(issuer="other-isolated-service",
        audiences=frozenset({"other-service"}), signing_keys={SIGNING_KEY.kid: SIGNING_KEY},
        principal_mappings=case.mappings), audit_log=case.audit)
    selected["boundary"] = other
    before = _business(case)
    assert case.client.get(path, headers=headers()).status_code == 503
    assert _business(case) == before
    selected["boundary"] = case.boundary
    assert case.client.get(path, headers=headers()).status_code == 200
    case.avm_owner.boundary = None
    assert case.client.get(path, headers=headers()).status_code == 503
    assert _business(case) == before


def test_native_missing_executor_stays_unavailable_without_touching_owner_records(case):
    app = FastAPI()
    mount_versioned(app, create_avm_router(repository=DurableAVMRepository(case.docs),
        audit_log=case.audit, job_queue=case.app.state.job_queue,
        runtime_mode="production", request_owner=case.avm_owner))
    before = _business(case)
    with TestClient(app) as client:
        response = client.get("/api/v1/avm/cases", headers=headers())
    assert response.status_code == 503 and _business(case) == before


def test_explicit_nonlive_native_embed_keeps_compatibility_but_never_becomes_a_live_owner(case, monkeypatch):
    app = FastAPI()

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        request.state.correlation_id = "explicit-nonlive-native-embed"
        return await call_next(request)

    mount_versioned(app, create_avm_router(repository=DurableAVMRepository(case.docs),
        audit_log=case.audit, job_queue=case.app.state.job_queue, runtime_mode="poc"))
    with TestClient(app) as client:
        result = client.post("/api/v1/avm/cases", headers=headers(),
            json={**_valuation_payload(), "created_by": "synthetic-legacy-body-label"})
        assert result.status_code == 201, result.text
        assert result.json()["created_by"] == "synthetic-legacy-body-label"
        event = next(event for event in case.audit.list_events(correlation_id="explicit-nonlive-native-embed")
                     if event.event_id == result.json()["audit_event_id"])
        assert "authenticated_principal" not in event.metadata
        before = _business(case)
        monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
        denied = client.get("/api/v1/avm/cases", headers=headers())
        assert denied.status_code == 503 and _business(case) == before


def test_signed_native_create_without_caller_CID_has_same_actual_guard_and_creator_event(case):
    from uuid import UUID

    actual_headers = headers(key="no-caller-correlation")
    actual_headers.pop("x-correlation-id")
    response = case.client.post("/api/v1/avm/cases", headers=actual_headers,
        json={**_valuation_payload(), "created_by": "creator"})
    assert response.status_code == 201, response.text
    result = response.json()
    assert str(UUID(result["correlation_id"])) == result["correlation_id"]
    event = case.audit.get_event(result["audit_event_id"], tenant_id=TENANT)
    assert event.actor == "creator" and event.correlation_id == result["correlation_id"]
    assert event.metadata["authenticated_principal"]["subject_id"] == "creator"
    assert any(row.event_type == "security.authorization" and row.actor == "creator"
               and row.resource == "avm" and row.outcome == "allow"
               for row in case.audit.list_events(correlation_id=result["correlation_id"]))


@pytest.mark.parametrize("transport", ["direct-native", "signed-native-HTTP"])
def test_named_valuation_refusal_commits_REVIEW_REQUIRED_and_no_report_only(transport, case):
    payload = {**_valuation_payload(), "equipment_depreciation_basis": "original_cost"}
    payload.pop("equipment_original_cost", None)
    payload.pop("asset_in_service_date", None)
    value = identity(create(case, "avm", key="missing-native-depreciation", **payload), "avm")
    repository = case.avm(TENANT)
    if transport == "direct-native":
        native = AVMService(repository=repository, runtime_mode="poc")
        with pytest.raises(AVMReviewRequiredFailure, match="equipment_original_cost is required") as failure:
            native.value(value, actor="creator", correlation_id="named-native-failure")
        assert type(failure.value) is AVMReviewRequiredFailure
    else:
        result = case.client.post("/api/v1/avm/cases/" + value + "/value",
            headers=headers(key="named-native-failure"), json={"actor": "creator"})
        assert result.status_code == 422 and "equipment_original_cost is required" in result.json()["detail"]
    saved = repository.get_case(value)
    assert saved.status is ValuationCaseStatus.REVIEW_REQUIRED
    assert "valuation failed" in saved.status_history[-1]["reason"]
    assert repository.latest_report(value) is None
    assert case.client.get("/avm/cases/" + value, headers=headers()).json()["status"] == ValuationCaseStatus.REVIEW_REQUIRED.value


@pytest.mark.parametrize("failure_kind", ["unknown-executor", "success-audit-unavailable"])
def test_only_named_completed_valuation_refusal_commits_other_errors_rollback(case, monkeypatch, failure_kind):
    value = identity(create(case, "avm", key="uncommitted-valuation"), "avm")
    repository = case.avm(TENANT)
    before = _business(case)
    saved = repository.get_case(value)
    if failure_kind == "unknown-executor":
        def unavailable(*args, **kwargs):
            raise RuntimeError("unexpected owned executor failure")
        monkeypatch.setattr(EngineeringAVMExecutor, "execute", unavailable)
    else:
        original = case.audit.record
        def unavailable(event):
            if event.event_type == "avm.valued.v1":
                raise RuntimeError("owned valued audit unavailable")
            return original(event)
        monkeypatch.setattr(case.audit, "record", unavailable)
    response = case.client.post("/avm/cases/" + value + "/value",
        headers=headers(key="uncommitted-value"), json={"actor": "creator"})
    assert response.status_code == 500
    assert repository.get_case(value) == saved and repository.latest_report(value) is None
    assert repository.get_margin(value) is None and _business(case) == before


@pytest.mark.parametrize("suffix", ["finance-approval", "dataroom", "dataroom/export", "report", "reports"])
def test_explicit_production_native_router_never_admits_historical_POC_report_when_live_flags_false(case, suffix):
    # Router has explicit production executor but deployment env is POC. This
    # exercises source admission, not the engineering executor as a real model.
    case.avm_owner.formal = False
    value = identity(create(case, "avm", key="historical-poc-report"), "avm")
    path = "/avm/cases/" + value
    assert case.client.post(path + "/value", headers=headers(key="value-poc-source"),
        json={"actor": "creator"}).status_code == 200
    repository = case.avm(TENANT)
    report = repository.latest_report(value)
    repository.replace_latest_report(replace(report, execution_metadata={"mode": "poc"}))
    before = _business(case)
    if suffix in {"report", "reports"}:
        response = case.client.get(path + "/" + suffix, headers=headers("finance"))
    else:
        response = case.client.post(path + "/" + suffix, headers=headers("finance"),
            json={"actor": "finance", "reason": "A real source admission is required"})
    assert response.status_code == 503 and response.json()["detail"] == "AVM_PRODUCTION_REPORT_UNAVAILABLE"
    assert _business(case) == before


def _prepared_native_approval(case):
    # Actual native solve, disclosure acknowledgement and fixed management
    # verifier. Receipt authority is explicitly synthetic engineering evidence.
    from tests.integration.test_netplan_solver import (
        _approval_receipt,
        _approval_verifier,
        _management_baseline,
    )

    value = identity(create(case, "netplan", key="native-tracking-scenario"), "netplan")
    repository = case.netplan(TENANT)
    original = repository.get_scenario(value)
    constraints = replace(original.constraints, max_construction_days=1000,
        max_equipment_units=1000, max_labour_headcount=1000,
        min_coverage_delta=0, max_open_per_dilution_zone=2)
    options = {identity: tuple(replace(row, construction_days=0, equipment_units=0,
        labour_headcount=0, coverage_delta=0) for row in rows)
        for identity, rows in original.options_by_entity.items()}
    repository.save_scenario(replace(original, constraints=constraints, options_by_entity=options))
    policies = InMemoryDecisionPolicyRepository([default_netplan_disclosure_policy(TENANT)])
    native = NetPlanService(repository=repository, runtime_mode="poc", policy_repository=policies,
        feature_flags=case.flags, job_queue=case.app.state.job_queue)
    solve = native.solve(value)
    now = datetime.now(UTC)
    baseline = _management_baseline(scenario_id=value, baseline_id=value,
        baseline_name=original.scenario_name, scope="tenant:" + TENANT, release_id=original.planning_horizon,
        actions_by_entity={row.entity_id: row.action for row in solve.result.selected_actions},
        source_snapshot_ids=tuple(sorted({ref for row in solve.result.selected_actions for ref in row.source_snapshot_ids})))
    receipt = _approval_receipt(baseline, options, constraints, principal_id="finance",
        issued_at="2026-10-01T00:00:00Z", expires_at="2099-01-01T00:00:00Z")
    verifier = _approval_verifier(receipt, principal_id="finance", clock=now)
    native.approval_verifier = verifier
    native.acknowledge_unmodelled_constraints(value, actor_id="finance",
        reason="Declared engineering lease and sequencing authority",
        acknowledged_classes=("LEASE", "SEQUENCING"), approval_receipt_id=receipt.receipt_id)
    native.submit_for_approval(value, actor="creator", reason="Explicit native solve submission")
    case.app.state.native_approval_verifier = verifier
    assert repository.list_approvals(value) == []
    return value, receipt


def _approve_native(case, value, receipt):
    return case.client.post("/api/v1/netplan/scenarios/" + value + "/decide", headers=headers("finance", key="native-real-approval"),
        json={"actor_id": "finance", "reason": "Original management receipt verified",
              "approval_receipt_id": receipt.receipt_id})


def test_signed_original_native_approval_execution_and_manual_zero_share_three_tasks_due_jobs_and_label_SQL(case):
    value, receipt = _prepared_native_approval(case)
    repository, queue = case.netplan(TENANT), case.app.state.job_queue
    documents = repository._store
    assert documents.list_all(TASK_COLLECTION) == []
    approved = _approve_native(case, value, receipt)
    assert approved.status_code == 200, approved.text
    assert repository.list_approvals(value)[0].authentic_approval_verified
    tasks = documents.list_all(TASK_COLLECTION)
    assert len(tasks) == 3 and {row["horizon_days"] for row in tasks} == {90, 180, 365}
    assert all(row["status"] == "PENDING_EXECUTION" and row["due_at"] is None
        and row["execution_id"] is None for row in tasks)
    assert case.engine.query("SELECT job_id FROM durable_jobs WHERE job_type = ?", (DUE_JOB_TYPE,)) == []
    actual = datetime.now(UTC)
    response = case.client.post("/netplan/scenarios/" + value + "/execute", headers=headers(key="native-real-execution"),
        json={"executed_by": "creator", "executed_at": actual.isoformat()})
    assert response.status_code == 200, response.text
    execution = repository.get_execution(value)
    assert execution.executed_by == "creator" and execution.executed_at == actual
    scheduled = documents.list_all(TASK_COLLECTION)
    jobs = case.engine.query("SELECT job_id, available_at FROM durable_jobs WHERE job_type = ?", (DUE_JOB_TYPE,))
    assert len(scheduled) == len(jobs) == 3
    assert {row["task_id"] for row in scheduled} == {row["task_id"] for row in tasks}
    by_id = {row["job_id"]: row for row in jobs}
    assert all(row["execution_id"] == execution.execution_id and row["status"] == "SCHEDULED"
        and datetime.fromisoformat(row["due_at"]) == actual + timedelta(days=row["horizon_days"])
        and datetime.fromisoformat(by_id[row["collection_job_id"]]["available_at"]) == datetime.fromisoformat(row["due_at"])
        for row in scheduled)
    assert repository._store.engine is case.audit._engine is queue._engine is case.engine
    observed = case.client.post("/netplan/scenarios/" + value + "/outcomes", headers=headers(key="native-manual-zero"),
        json={"actor": "creator", "actual_gross_margin": 0, "source_snapshot_ids": ["declared-owned-ledger"]})
    assert observed.status_code == 200, observed.text
    labels = documents.list_all(LABEL_COLLECTION)
    assert len(labels) == 1 and labels[0]["status"] == "OBSERVED" and labels[0]["actual_gross_margin"] == 0
    assert labels[0]["horizon_days"] is None and not labels[0]["mature"] and not labels[0]["training_eligible"]
    assert len(documents.list_all(TASK_COLLECTION)) == 3
    assert all(row["measured_value"] is None for row in documents.list_all(TASK_COLLECTION))


@pytest.mark.parametrize("stage", ["approval", "execution", "outcome"])
def test_signed_native_registry_audit_failure_rolls_back_exact_transition_rows_jobs_and_receipts(case, monkeypatch, stage):
    value, receipt = _prepared_native_approval(case)
    repository = case.netplan(TENANT)
    if stage != "approval":
        assert _approve_native(case, value, receipt).status_code == 200
    if stage == "outcome":
        response = case.client.post("/netplan/scenarios/" + value + "/execute", headers=headers(key="pre-outcome-execution"), json={"executed_by": "creator"})
        assert response.status_code == 200, response.text
    before, scenario = _business(case), repository.get_scenario(value)
    original = case.audit.record
    def unavailable(event):
        if event.event_type == "netplan.outcome_tracking_recorded.v1":
            raise RuntimeError("same SQL registry audit unavailable")
        return original(event)
    monkeypatch.setattr(case.audit, "record", unavailable)
    if stage == "approval":
        response = _approve_native(case, value, receipt)
    elif stage == "execution":
        response = case.client.post("/netplan/scenarios/" + value + "/execute", headers=headers(key="failed-execution"), json={"executed_by": "creator"})
    else:
        response = case.client.post("/netplan/scenarios/" + value + "/outcomes", headers=headers(key="failed-outcome"),
            json={"actor": "creator", "actual_gross_margin": 0})
    assert response.status_code == 500
    assert repository.get_scenario(value) == scenario and _business(case) == before
    assert len(repository._store.list_all(TASK_COLLECTION)) == (0 if stage == "approval" else 3)
    if stage == "approval":
        assert repository.list_approvals(value) == [] and repository._store.list_all(RECEIPT_COLLECTION) == []
    if stage != "outcome":
        assert repository.get_execution(value) is None
    assert repository.get_outcome(value) is None and repository._store.list_all(LABEL_COLLECTION) == []


@pytest.mark.parametrize("bad_owner", ["missing-provider", "foreign-partition"])
def test_signed_native_outcome_registry_refuses_missing_or_cross_tenant_owner_without_global_fallback(case, bad_owner):
    if bad_owner == "missing-provider":
        case.netplan_owner.outcomes = None
    else:
        def foreign(*, repository, principal, authorize, correlation_id):
            return DurableNetPlanOutcomeRegistry(repository=case.netplan("foreign-tenant"), principal=principal,
                authorize=authorize, audit_log=case.audit, queue=case.app.state.job_queue, correlation_id=correlation_id)
        case.netplan_owner.outcomes = foreign
    before = _business(case)
    response = create(case, "netplan")
    assert response.status_code == 503 and _business(case) == before


@pytest.mark.parametrize("decider", ["forged-finance-owner", "finance"])
def test_signed_native_body_rollback_receipt_cannot_attribute_a_decision_to_another_subject(case, decider):
    value = identity(create(case, "avm", key="rollback-current-subject"), "avm")
    before = _business(case)
    now = datetime.now(UTC)
    response = case.client.post("/avm/cases/" + value + "/value", headers=headers(key="forged-rollback-person"),
        json={"actor": "creator", "depreciation_version_pin": "avm-depreciation-absent-v0",
            "rollback_receipt": {"decider": decider, "decision_time": now.isoformat(),
                "reason": "Actual current actor is required for a body record",
                "target_expiry": (now + timedelta(hours=1)).isoformat()}})
    assert response.status_code == 403 and response.json()["detail"] == "NATIVE_BODY_ROLLBACK_DECIDER_MISMATCH"
    assert _business(case) == before
    assert any(event.event_type == "security.authorization" and event.actor == "creator"
        and event.outcome == "deny" for event in case.audit.list_events(correlation_id="forged-rollback-person"))


def test_signed_native_body_rollback_self_attribution_and_separate_server_configured_record_remain_usable(case):
    from modules.avm.application.valuation import DepreciationRollbackReceipt

    now = datetime.now(UTC)
    receipt = DepreciationRollbackReceipt(decider="configured-finance-owner", decision_time=now,
        reason="Explicit trusted server configuration record, not a body assertion",
        target_expiry=now + timedelta(hours=1), depreciation_version_pin="avm-depreciation-absent-v0")
    app = FastAPI()
    app.state.job_queue = case.app.state.job_queue
    mount_versioned(app, create_avm_router(repository=DurableAVMRepository(case.docs), audit_log=case.audit,
        job_queue=case.app.state.job_queue, runtime_mode="production", production_executor=EngineeringAVMExecutor(),
        depreciation_version_pin=receipt.depreciation_version_pin, rollback_receipt=receipt,
        require_durable_commands=True, request_owner=case.avm_owner))
    with TestClient(app) as client:
        for explicit_body in (False, True):
            created = client.post("/avm/cases", headers=headers(key="server-rollback-case-" + str(explicit_body)),
                json={**_valuation_payload(), "created_by": "creator"})
            value = identity(created, "avm")
            body = {"actor": "creator"}
            if explicit_body:
                body["rollback_receipt"] = {**receipt.to_dict(), "decider": "creator"}
            result = client.post("/avm/cases/" + value + "/value", headers=headers(key="server-rollback-value-" + str(explicit_body)), json=body)
            assert result.status_code == 200, result.text
            recorded = case.avm(TENANT).latest_report(value).execution_metadata["depreciation_rollback_receipt"]
            assert recorded["decider"] == ("creator" if explicit_body else "configured-finance-owner")
            assert recorded["reason"] == receipt.reason
