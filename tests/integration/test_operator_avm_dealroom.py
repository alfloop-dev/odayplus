"""Signed HTTP and owned SQLite AVM consumer controls, not model/PG/UAT proof."""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass, replace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.avm import create_avm_router
from apps.api.app.routes.operator_modules.avm_dealroom import create_avm_dealroom_router
from apps.api.oday_api.security import dependencies
from modules.avm.application.valuation import AVMService
from modules.opsboard.application.avm_dealroom import ScopedAVMDealRoom
from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary
from shared.api.errors import install_error_handlers
from shared.auth import Action
from shared.domain.models import Brand, Store
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.repositories import (
    DurableAVMRepository,
    InMemoryBrandRepository,
    InMemoryStoreRepository,
)
from tests.integration.test_avm_valuation import _valuation_payload
from tests.security.test_feature_flag_admin_authorization import ISSUER, SIGNING_KEY, _headers

TENANT = "avm-consumer-test-tenant"
STORE = "store-red-001"
BASE = "/api/v1/operator/avm"


@dataclass
class Case:
    client: TestClient
    engine: SqliteEngine
    docs: SqliteDocumentStore
    repository: DurableAVMRepository
    native: AVMService
    audit: DurableAuditLog
    stores: InMemoryStoreRepository
    identity: str
    app: FastAPI
    boundary: AuthenticationBoundary
    brands: InMemoryBrandRepository


def headers(actor="finance", *, key="avm-command-test", cid=None):
    return {**_headers(actor, correlation_id=cid or f"{key}-{actor}"), "If-Actor-Match": actor, "Idempotency-Key": key}


@pytest.fixture
def case(tmp_path, monkeypatch):
    # This fixture explicitly exercises native POC arithmetic on synthetic
    # financial inputs. Production admission is a separate negative control.
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "false")
    monkeypatch.setenv("ODP_DEPLOY_ENV", "development")
    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    monkeypatch.setenv("NODE_ENV", "test")
    engine = SqliteEngine(tmp_path / "owned-avm.sqlite")
    try:
        docs = SqliteDocumentStore(engine)
        repository = DurableAVMRepository(TenantScopedDocumentStore(docs, TENANT))
        native = AVMService(repository=repository, runtime_mode="poc")
        audit = DurableAuditLog(engine)
        queue = DurableJobQueue(engine)
        stores = InMemoryStoreRepository()
        stores.save_store(Store(store_id=STORE, tenant_id=TENANT, brand_id="avm-brand", region_code="avm-region"))
        brands = InMemoryBrandRepository()
        brands.save_brand(Brand(brand_id="avm-brand", tenant_id=TENANT))
        mapping = {name: {"roles": [role], "scope": {"tenant_id": tenant, "store_ids": allowed, "modules": ["avm"]}}
            for name, role, tenant, allowed in (
                ("creator", "finance_legal", TENANT, [STORE]), ("finance", "finance_legal", TENANT, [STORE]),
                ("auditor", "auditor", TENANT, [STORE]), ("outside", "finance_legal", TENANT, ["other-store"]),
                ("foreign", "finance_legal", "other-tenant", [STORE]), ("no-role", "platform_admin", TENANT, [STORE]),
            )}
        boundary = AuthenticationBoundary(AuthBoundaryConfig(issuer=ISSUER, audiences=frozenset({"unit-feature-api"}),
            signing_keys={SIGNING_KEY.kid: SIGNING_KEY}, principal_mappings=mapping), audit_log=audit)
        monkeypatch.setattr(dependencies, "default_boundary", lambda: boundary)
        app = FastAPI()
        install_error_handlers(app)
        app.state.job_queue = queue

        @app.middleware("http")
        async def correlation(request: Request, call_next):
            request.state.correlation_id = request.headers.get("X-Correlation-Id", "avm-test-cid")
            return await call_next(request)

        def factory(request):
            principal = request.state.operator_principal
            return ScopedAVMDealRoom(principal=principal,
                repository=DurableAVMRepository(TenantScopedDocumentStore(docs, principal.tenant_id)),
                stores=stores, brands=brands, audit_log=audit, queue=app.state.job_queue,
                correlation_id=request.state.correlation_id, force_production=bool(getattr(app.state, "formal_avm", False)))

        def guard(action):
            return dependencies.require_permission("avm", action,
                engine=dependencies.build_engine(audit_log=audit), boundary=boundary)
        app.include_router(create_avm_dealroom_router(factory=factory, require_view=guard(Action.VIEW),
            require_create=guard(Action.CREATE), require_approve=guard(Action.APPROVE), require_export=guard(Action.EXPORT)), prefix="/api/v1/operator")
        # The original native creation method and real Auth005 writer produce
        # the creator lineage; no fabricated approval/creator receipt is saved.
        app.include_router(create_avm_router(repository=repository, audit_log=audit, job_queue=queue,
            require_durable_commands=True, runtime_mode="poc"), prefix="/api/v1")
        with TestClient(app) as client:
            created = client.post(BASE + "/cases", headers=headers("creator", key="scoped-create", cid="original-signed-scoped-create"),
                json={"valuationInput": _valuation_payload()})
            assert created.status_code == 201, created.text
            identity = created.json()["detail"]["case"]["case_id"]
            assert created.json()["detail"]["creatorVerification"]["verified"] is True
            for operation in ("normalize", "value"):
                current = client.get(BASE + "/cases/" + identity, headers=headers("creator")).json()
                changed = client.post(BASE + "/cases/" + identity + "/" + operation, headers=headers("creator", key=operation),
                    json={"expectedVersion": current["expectedVersion"], "reason": "Explicit engineering POC source transformation"})
                assert changed.status_code == 200, changed.text
            yield Case(client, engine, docs, repository, native, audit, stores, identity, app, boundary, brands)
    finally:
        engine.close()


def read(case, actor="finance"):
    response = case.client.get(BASE + "/cases/" + case.identity, headers=headers(actor))
    assert response.status_code == 200, response.text
    return response.json()


def command(case, operation, detail, *, actor="finance", key=None, extra=None):
    path = {"approve": "finance-approval", "build": "dataroom", "export": "dataroom/exports"}[operation]
    return case.client.post(f"{BASE}/cases/{case.identity}/{path}", headers={**headers(actor, key=key or operation), "Content-Type": "application/json"},
        content=json.dumps({"expectedVersion": detail["expectedVersion"], "reason": "Actual synthetic financial decision reason", **(extra or {})}))


def test_signed_finance_without_operator_persona_uses_original_SoD_approval_room_and_actual_JSON(case):
    first = read(case)
    assert first["creatorVerification"]["verified"] is True
    assert first["actions"]["approve"] is True
    before = case.repository.get_case(case.identity)
    denied = command(case, "approve", read(case, "creator"), actor="creator")
    assert denied.status_code == 403
    assert case.repository.get_case(case.identity) == before
    approved = command(case, "approve", first, extra={"reservePrice": first["report"]["reserve_price"] + 125})
    assert approved.status_code == 200, approved.text
    report = approved.json()["detail"]["report"]
    assert report["finance_approval"]["actor_id"] == "finance"
    assert report["finance_approval"]["reserve_price"] == first["report"]["reserve_price"] + 125
    assert report["finance_approval"]["decision_id"] == approved.json()["receipt"]["sourceId"]
    built = command(case, "build", approved.json()["detail"])
    assert built.status_code == 200, built.text
    room = built.json()["detail"]["dataRoom"]
    assert {r["document_id"] for r in room["checklist"]} == {"financials", "assets", "lease", "comparables", "valuation_card"}
    assert built.json()["detail"]["attachmentProvider"]["available"] is False
    exported = command(case, "export", built.json()["detail"])
    assert exported.status_code == 200, exported.text
    artifact = exported.json()["receipt"]["artifact"]
    response = case.client.get(f"{BASE}/cases/{case.identity}/dataroom/exports/{artifact['exportId']}", headers=headers())
    assert response.status_code == 200, response.text
    assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
    assert response.json()["sourceDocumentBytesIncluded"] is False
    assert response.json()["report"]["report_id"] == report["report_id"]
    event = case.audit.get_event(exported.json()["receipt"]["auditEventId"], tenant_id=TENANT)
    assert event.actor == "finance" and event.event_type == "avm.dataroom_exported.v1"
    assert event.metadata["artifact_sha256"] == artifact["sha256"]
    assert exported.json()["receipt"]["deliveryStatus"] == "not_configured"


@pytest.mark.parametrize("actor,status", [("outside", 404), ("foreign", 404), ("no-role", 403)])
def test_current_scope_and_RBAC_deny_before_case_projection_and_no_domain_write(case, actor, status):
    before = case.repository.get_case(case.identity)
    response = case.client.get(BASE + "/cases/" + case.identity, headers=headers(actor))
    assert response.status_code == status
    if actor != "no-role":
        listing = case.client.get(BASE + "/cases", headers=headers(actor))
        assert listing.status_code == 200 and listing.json()["items"] == []
    assert case.repository.get_case(case.identity) == before
    assert any(e.actor == actor and e.outcome == "deny" for e in case.audit.list_events()) if actor != "foreign" else True


def test_readonly_masks_sensitive_values_and_cannot_approve_build_or_export(case):
    data = read(case, "auditor")
    assert data["report"]["reserve_price"] is None and data["report"]["asking_price"] is None
    assert not any(data["actions"].values())
    for operation in ("approve", "build", "export"):
        assert command(case, operation, data, actor="auditor").status_code == 403
    assert case.repository.latest_report(case.identity).finance_approval is None


def test_stale_version_conflicting_key_and_replay_preserve_original_decision(case):
    data = read(case)
    assert command(case, "approve", {**data, "expectedVersion": "0" * 64}).status_code == 409
    first = command(case, "approve", data, key="same-real-command")
    assert first.status_code == 200
    replay = command(case, "approve", data, key="same-real-command")
    assert replay.status_code == 200 and replay.json()["idempotentReplay"] is True
    assert replay.json()["receipt"] == first.json()["receipt"]
    conflict = command(case, "approve", data, key="same-real-command", extra={"reason": "Different financial command reason"})
    assert conflict.status_code == 409
    assert len([e for e in case.audit.list_events() if e.event_type == "avm.finance_approved.v1"]) == 1


@pytest.mark.parametrize("proof", [{"actor": "other"}, {"created_by": "other"}, {"reservePrice": float("inf")}, {"reason": float("nan")}, {"expectedVersion": None}])
def test_forbidden_body_authority_and_nonfinite_raw_inputs_are_safe_422(case, proof):
    before = case.repository.get_case(case.identity)
    response = command(case, "approve", read(case), extra=proof)
    assert response.status_code == 422
    assert case.repository.get_case(case.identity) == before
    assert case.repository.latest_report(case.identity).finance_approval is None


def test_audit_failure_rolls_back_native_finance_and_receipt(case, monkeypatch):
    data = read(case)
    before = case.repository.latest_report(case.identity)
    receipts_before = case.repository._store.list_all(ScopedAVMDealRoom._RECEIPTS)
    original = case.audit.record
    def failing(event):
        if event.event_type == "avm.finance_approved.v1":
            raise RuntimeError("synthetic durable audit failure")
        return original(event)
    monkeypatch.setattr(case.audit, "record", failing)
    with pytest.raises(RuntimeError, match="synthetic durable audit failure"):
        command(case, "approve", data)
    assert case.repository.latest_report(case.identity) == before
    assert case.repository.get_case(case.identity).status.value == "REVIEW_REQUIRED"
    assert case.repository._store.list_all(ScopedAVMDealRoom._RECEIPTS) == receipts_before


def test_replay_rechecks_actual_store_revocation_and_wrong_engine_owner_is_unavailable(case, tmp_path):
    data = read(case)
    first = command(case, "approve", data)
    assert first.status_code == 200
    actual = case.stores.get_store(STORE)
    case.stores.save_store(replace(actual, tenant_id="foreign-master-tenant"))
    assert command(case, "approve", data).status_code == 403
    case.stores.save_store(actual)
    other = SqliteEngine(tmp_path / "other-owner.sqlite")
    try:
        case.app.state.job_queue = DurableJobQueue(other)
        response = case.client.get(BASE + "/cases/" + case.identity, headers=headers())
        assert response.status_code == 503
    finally:
        other.close()


def test_original_native_body_creator_cannot_be_misrepresented_as_verified_SoD(case):
    forged = case.client.post("/api/v1/avm/cases", headers=headers("finance", key="forged-creator", cid="actual-finance-claimed-someone-else"),
        json={**_valuation_payload(), "created_by": "creator"})
    assert forged.status_code == 201  # Original compatibility path is explicit.
    identity = forged.json()["case_id"]
    case.native.normalize(identity, actor="finance", correlation_id="synthetic-normalize")
    case.native.value(identity, actor="finance", correlation_id="synthetic-value")
    response = case.client.get(BASE + "/cases/" + identity, headers=headers("finance"))
    assert response.status_code == 200
    data = response.json()
    assert data["creatorVerification"]["verified"] is False and data["actions"]["approve"] is False
    refused = case.client.post(BASE + "/cases/" + identity + "/finance-approval", headers=headers("finance"),
        json={"expectedVersion": data["expectedVersion"], "reason": "Cannot invent creator authority"})
    assert refused.status_code == 503
    assert case.repository.latest_report(identity).finance_approval is None


@pytest.mark.parametrize("replay", [False, True])
def test_exact_case_is_reauthorized_inside_transaction_before_mutation_or_replay(case, monkeypatch, replay):
    data = read(case)
    if replay:
        assert command(case, "approve", data).status_code == 200
    reports_before = case.repository.report_history(case.identity)
    receipts_before = case.repository._store.list_all(ScopedAVMDealRoom._RECEIPTS)
    original = ScopedAVMDealRoom._transaction
    changed = False

    @contextmanager
    def change_actual_parent_then_enter(self, case_id):
        nonlocal changed
        if not changed:
            changed = True
            current = case.repository.get_case(case_id)
            case.repository.save_case(replace(current, store_id="revoked-parent",
                valuation_input=replace(current.valuation_input, store_id="revoked-parent")))
        with original(self, case_id):
            yield

    monkeypatch.setattr(ScopedAVMDealRoom, "_transaction", change_actual_parent_then_enter)
    response = command(case, "approve", data)
    assert response.status_code == 403
    assert case.repository.report_history(case.identity) == reports_before
    assert case.repository._store.list_all(ScopedAVMDealRoom._RECEIPTS) == receipts_before
    assert any(e.actor == "finance" and e.outcome == "deny" and e.resource.endswith(case.identity)
               for e in case.audit.list_events())


def test_actual_operator_factory_creates_a_tenant_case_with_real_subject_and_current_input_commands(case):
    from apps.api.app.routes.operator import create_operator_router

    app = FastAPI()
    install_error_handlers(app)
    queue = DurableJobQueue(case.engine)
    app.include_router(create_operator_router(audit_log=case.audit, document_store=case.docs, job_queue=queue,
        avm_repository_for_tenant=lambda tenant: DurableAVMRepository(TenantScopedDocumentStore(case.docs, tenant)),
        priceops_store_repository=case.stores, priceops_brand_repository=case.brands,
        require_live_data=False, persistence_mode="sqlite", provider_mode="fixture"), prefix="/api/v1")
    with TestClient(app) as client:
        first = client.post(BASE + "/cases", headers=headers("creator", key="actual-factory-create", cid="actual-operator-create"),
            json={"valuationInput": _valuation_payload()})
        assert first.status_code == 201, first.text
        data = first.json()["detail"]
        assert data["case"]["created_by"] == "creator" and data["creatorVerification"]["verified"] is True
        assert data["case"]["status"] == "DATA_READY" and data["report"] is None
        identity = data["case"]["case_id"]
        assert case.repository.get_case(identity).created_by == "creator"
        assert case.docs.get(case.repository._CASES, identity) is None
        replay = client.post(BASE + "/cases", headers=headers("creator", key="actual-factory-create", cid="actual-operator-create"),
            json={"valuationInput": _valuation_payload()})
        assert replay.status_code == 201 and replay.json()["receipt"] == first.json()["receipt"]
        assert replay.json()["idempotentReplay"] is True
        for operation in ("normalize", "value"):
            response = client.post(f"{BASE}/cases/{identity}/{operation}", headers=headers("creator", key="actual-" + operation),
                json={"expectedVersion": data["expectedVersion"], "reason": "Actual typed native transformation"})
            assert response.status_code == 200, response.text
            data = response.json()["detail"]
            assert response.json()["receipt"]["actor"] == "creator"
        assert data["case"]["status"] == "REVIEW_REQUIRED"
        assert data["report"]["case_id"] == identity
        assert data["report"]["store_id"] == STORE
        assert data["creatorVerification"]["verified"] is True


@pytest.mark.parametrize("payload", [
    {"created_by": "other"}, {"actor": "other"}, {"gm_ttm": float("nan")}, {"quality_score": float("inf")},
])
def test_scoped_create_cannot_accept_body_actor_or_nonfinite_source(case, payload):
    before = case.repository.list_cases()
    response = case.client.post(BASE + "/cases", headers={**headers("creator", key="invalid-create"), "Content-Type": "application/json"},
        content=json.dumps({"valuationInput": {**_valuation_payload(), **payload}}))
    assert response.status_code == 422
    assert case.repository.list_cases() == before


def test_formal_mount_never_uses_POC_report_as_finance_authority_or_missing_executor_as_a_model(case):
    before = case.repository.report_history(case.identity)
    case.app.state.formal_avm = True
    data = read(case)
    assert data["report"] is None and data["reportHistory"] == []
    assert data["reportAvailability"]["reasonCode"] == "AVM_PRODUCTION_REPORT_NOT_VERIFIED"
    assert data["actions"]["approve"] is False
    assert command(case, "approve", data).status_code == 503
    response = case.client.post(f"{BASE}/cases/{case.identity}/value", headers=headers("creator", key="formal-value"),
        json={"expectedVersion": data["expectedVersion"], "reason": "Require actual production model owner"})
    assert response.status_code == 503
    assert case.repository.report_history(case.identity) == before


@pytest.mark.parametrize("tamper", ["bytes", "audit-id"])
def test_retained_export_checks_actual_bytes_and_actual_audit_before_download(case, tamper):
    approved = command(case, "approve", read(case)).json()["detail"]
    built = command(case, "build", approved).json()["detail"]
    response = command(case, "export", built)
    assert response.status_code == 200
    artifact = response.json()["receipt"]["artifact"]
    row = case.repository._store.get(ScopedAVMDealRoom._EXPORTS, artifact["exportId"])
    row["content" if tamper == "bytes" else "auditEventId"] = b"tampered" if tamper == "bytes" else "not-a-real-audit-id"
    case.repository._store.put(ScopedAVMDealRoom._EXPORTS, artifact["exportId"], row, group_key=case.identity)
    download = case.client.get(f"{BASE}/cases/{case.identity}/dataroom/exports/{artifact['exportId']}", headers=headers())
    assert download.status_code == 503
    assert b"tampered" not in download.content


@pytest.mark.parametrize("newer_formal_report", [False, True])
def test_formal_download_rejects_exact_older_POC_export_even_with_newer_production_provenance(case, newer_formal_report):
    approved = command(case, "approve", read(case)).json()["detail"]
    built = command(case, "build", approved).json()["detail"]
    exported = command(case, "export", built)
    assert exported.status_code == 200
    artifact = exported.json()["receipt"]["artifact"]
    case.app.state.formal_avm = True
    hidden = read(case)
    assert hidden["report"] is None and hidden["exports"] == []
    assert hidden["reportAvailability"]["reasonCode"] == "AVM_PRODUCTION_REPORT_NOT_VERIFIED"
    if newer_formal_report:
        # Declared source admission fixture only: this is not model execution
        # or a claim that the earlier POC numbers became production outputs.
        previous = case.repository.latest_report(case.identity)
        later = case.repository.save_report(replace(previous, finance_approval=None,
            execution_metadata={**previous.execution_metadata, "mode": "production_oss"}))
        assert later.valuation_version > previous.valuation_version
        assert later.report_id != previous.report_id
    response = case.client.get(f"{BASE}/cases/{case.identity}/dataroom/exports/{artifact['exportId']}", headers=headers())
    assert response.status_code == 503
    assert response.json()["detail"] == "AVM_PRODUCTION_REPORT_UNAVAILABLE"
    assert response.headers.get("X-Content-SHA256") is None
    assert response.headers.get("Content-Disposition") is None


def test_formal_download_keeps_a_verified_exact_historical_production_export_readable(case):
    # Explicit provenance fixture for this reader guard, not production model
    # runtime evidence. Real actor/creator/finance/export audit comes from HTTP.
    existing = case.repository.latest_report(case.identity)
    case.repository.replace_latest_report(replace(existing,
        execution_metadata={**existing.execution_metadata, "mode": "production_oss"}))
    case.app.state.formal_avm = True
    approved = command(case, "approve", read(case)).json()["detail"]
    built = command(case, "build", approved).json()["detail"]
    exported = command(case, "export", built)
    assert exported.status_code == 200, exported.text
    artifact = exported.json()["receipt"]["artifact"]
    original_report = exported.json()["detail"]["report"]
    later = case.repository.save_report(replace(case.repository.latest_report(case.identity), finance_approval=None))
    assert later.report_id != original_report["report_id"]
    response = case.client.get(f"{BASE}/cases/{case.identity}/dataroom/exports/{artifact['exportId']}", headers=headers())
    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
    assert response.json()["report"]["report_id"] == original_report["report_id"]
    assert response.json()["report"]["valuation_version"] == original_report["valuation_version"]



def test_matching_header_and_body_creator_without_boundary_authentication_remains_unverified(case, monkeypatch):
    monkeypatch.setattr(dependencies, "default_boundary", lambda: None)
    cid = "legacy-header-creator-matches-body"
    legacy = {"X-Subject-Id": "creator", "X-Roles": "finance_legal", "X-Tenant-Id": TENANT,
              "X-Correlation-Id": cid, "Idempotency-Key": "legacy-matching-create"}
    created = case.client.post("/api/v1/avm/cases", headers=legacy, json={**_valuation_payload(), "created_by": "creator"})
    assert created.status_code == 201, created.text
    identity = created.json()["case_id"]
    case.native.normalize(identity, actor="creator", correlation_id="legacy-explicit-poc-normalize")
    case.native.value(identity, actor="creator", correlation_id="legacy-explicit-poc-value")
    evidence = case.audit.list_events(correlation_id=cid)
    assert any(event.event_type == "security.authorization" and event.outcome == "allow" for event in evidence)
    assert not any(event.event_type == "security.authentication" for event in evidence)
    detail = case.client.get(BASE + "/cases/" + identity, headers=headers()).json()
    assert detail["creatorVerification"]["verified"] is False
    assert detail["actions"]["approve"] is False
    refused = case.client.post(BASE + "/cases/" + identity + "/finance-approval", headers=headers(),
        json={"expectedVersion": detail["expectedVersion"], "reason": "Do not trust matching legacy actor strings"})
    assert refused.status_code == 503
    assert case.repository.latest_report(identity).finance_approval is None


def test_header_only_native_finance_cannot_be_misrepresented_as_verified_approval(case, monkeypatch):
    assert read(case)["creatorVerification"]["verified"] is True
    monkeypatch.setattr(dependencies, "default_boundary", lambda: None)
    cid = "legacy-header-finance-matches-body"
    approved = case.client.post(f"/api/v1/avm/cases/{case.identity}/finance-approval", headers={
        "X-Subject-Id": "finance", "X-Roles": "finance_legal", "X-Tenant-Id": TENANT, "X-Correlation-Id": cid,
    }, json={"actor": "finance", "reason": "Original header-only engineering compatibility"})
    assert approved.status_code == 200, approved.text
    data = read(case)
    assert data["report"]["finance_approval"]["actor_id"] == "finance"
    assert data["creatorVerification"]["verified"] is True
    assert data["financeVerification"]["verified"] is False
    assert data["actions"]["build"] is False and data["actions"]["export"] is False
    assert command(case, "build", data).status_code == 503
    assert case.repository.get_dataroom(case.identity) is None
    assert not any(event.event_type == "security.authentication" for event in case.audit.list_events(correlation_id=cid))


def test_signed_legacy_events_without_direct_credential_binding_are_not_backfilled_as_trusted(case):
    created = case.client.post("/api/v1/avm/cases", headers=headers("creator", key="signed-original-create", cid="signed-original-matching-create"),
        json={**_valuation_payload(), "created_by": "creator"})
    assert created.status_code == 201, created.text
    identity = created.json()["case_id"]
    case.native.normalize(identity, actor="creator", correlation_id="signed-legacy-poc-normalize")
    case.native.value(identity, actor="creator", correlation_id="signed-legacy-poc-value")
    first = case.client.get(BASE + "/cases/" + identity, headers=headers()).json()
    assert first["creatorVerification"]["verified"] is False
    assert first["actions"]["approve"] is False
    approved = case.client.post(f"/api/v1/avm/cases/{identity}/finance-approval", headers=headers("finance", key="signed-original-finance", cid="signed-original-matching-finance"),
        json={"actor": "finance", "reason": "Actual signed native finance command"})
    assert approved.status_code == 200, approved.text
    data = case.client.get(BASE + "/cases/" + identity, headers=headers()).json()
    assert data["creatorVerification"]["verified"] is False
    assert data["financeVerification"]["verified"] is False
    assert data["actions"]["build"] is False



def test_actual_scoped_factory_without_caller_CID_uses_current_verified_principal_for_creator_and_finance(case):
    from apps.api.app.routes.operator import create_operator_router

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(create_operator_router(audit_log=case.audit, document_store=case.docs, job_queue=DurableJobQueue(case.engine),
        avm_repository_for_tenant=lambda tenant: DurableAVMRepository(TenantScopedDocumentStore(case.docs, tenant)),
        priceops_store_repository=case.stores, priceops_brand_repository=case.brands,
        require_live_data=False, persistence_mode="sqlite", provider_mode="fixture"), prefix="/api/v1")
    def no_CID_headers(actor, key):
        return {name: value for name, value in headers(actor, key=key).items() if name.lower() != "x-correlation-id"}
    with TestClient(app) as client:
        created = client.post(BASE + "/cases", headers=no_CID_headers("creator", "no-caller-cid-create"),
            json={"valuationInput": _valuation_payload()})
        assert created.status_code == 201, created.text
        data = created.json()["detail"]
        assert data["creatorVerification"]["verified"] is True
        assert data["creatorVerification"]["credentialSource"] in {"local", "oidc", "service"}
        identity = data["case"]["case_id"]
        for operation in ("normalize", "value"):
            result = client.post(f"{BASE}/cases/{identity}/{operation}", headers=no_CID_headers("creator", "no-cid-" + operation),
                json={"expectedVersion": data["expectedVersion"], "reason": "Explicit engineering native transformation"})
            assert result.status_code == 200, result.text
            data = result.json()["detail"]
        approved = client.post(f"{BASE}/cases/{identity}/finance-approval", headers=no_CID_headers("finance", "no-caller-cid-finance"),
            json={"expectedVersion": data["expectedVersion"], "reason": "Actual verified finance with no caller tracing field"})
        assert approved.status_code == 200, approved.text
        data = approved.json()["detail"]
        assert data["creatorVerification"]["verified"] is True
        assert data["financeVerification"]["verified"] is True
        assert data["report"]["finance_approval"]["actor_id"] == "finance"
        for receipt in (created.json()["receipt"], approved.json()["receipt"]):
            assert receipt["correlationId"]
            event = case.audit.get_event(receipt["auditEventId"], tenant_id=TENANT)
            assert event.correlation_id == receipt["correlationId"]
            assert event.metadata["authenticated_principal"] == {"subject_id": receipt["actor"], "tenant_id": TENANT, "token_type": "oidc"}


@contextmanager
def _fresh_actual_operator_case_for_refusal(case, valuation_input):
    # Actual production router/factory/actor+scope admission with explicit
    # engineering POC arithmetic on an owned SQLite source, not model/PG proof.
    from apps.api.app.routes.operator import create_operator_router

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(create_operator_router(audit_log=case.audit, document_store=case.docs,
        job_queue=DurableJobQueue(case.engine),
        avm_repository_for_tenant=lambda tenant: DurableAVMRepository(TenantScopedDocumentStore(case.docs, tenant)),
        priceops_store_repository=case.stores, priceops_brand_repository=case.brands,
        require_live_data=False, persistence_mode="sqlite", provider_mode="fixture"), prefix="/api/v1")
    with TestClient(app, raise_server_exceptions=False) as client:
        created = client.post(BASE + "/cases", headers=headers("creator", key="actual-refusal-create"),
            json={"valuationInput": valuation_input})
        assert created.status_code == 201, created.text
        detail = created.json()["detail"]
        assert detail["creatorVerification"]["verified"] is True
        assert detail["case"]["status"] == "DATA_READY" and detail["report"] is None
        yield client, detail


def _scoped_actual_business_snapshot(case):
    from types import SimpleNamespace

    from tests.integration.test_operator_netplan_scenario_builder import _business

    # The existing exact AuditLog-id join excludes only true authorization
    # delivery, retaining every business document/receipt/domain outbox/job.
    return _business(SimpleNamespace(bundle=SimpleNamespace(engine=case.engine, audit_log=case.audit)))


def test_actual_scoped_HTTP_named_missing_depreciation_preserves_review_without_report_or_success_receipt(case):
    payload = {**_valuation_payload(), "equipment_depreciation_basis": "original_cost"}
    payload.pop("equipment_original_cost", None)
    payload.pop("asset_in_service_date", None)
    with _fresh_actual_operator_case_for_refusal(case, payload) as (client, detail):
        identity = detail["case"]["case_id"]
        receipts = case.repository._store.list_all(ScopedAVMDealRoom._RECEIPTS)
        valued = [event for event in case.audit.list_events() if event.event_type == "avm.valued.v1"]
        response = client.post(f"{BASE}/cases/{identity}/value", headers=headers("creator", key="missing-scoped-depreciation"),
            json={"expectedVersion": detail["expectedVersion"], "reason": "Actual missing source refuses valuation"})
        assert response.status_code == 409 and response.json()["detail"] == "AVM_NATIVE_WORKFLOW_REFUSED"
        saved = case.repository.get_case(identity)
        assert saved.status.value == "REVIEW_REQUIRED"
        assert "valuation failed: equipment_original_cost is required" in saved.status_history[-1]["reason"]
        assert case.repository.latest_report(identity) is None
        assert case.repository._store.list_all(ScopedAVMDealRoom._RECEIPTS) == receipts
        assert [event for event in case.audit.list_events() if event.event_type == "avm.valued.v1"] == valued
        visible = client.get(f"{BASE}/cases/{identity}", headers=headers("creator"))
        assert visible.status_code == 200, visible.text
        assert visible.json()["case"]["status"] == "REVIEW_REQUIRED"
        assert visible.json()["report"] is None and visible.json()["reportHistory"] == []


@pytest.mark.parametrize("failure_kind", ["unknown-calculation-error", "successful-value-audit-error"])
def test_actual_scoped_HTTP_unknown_and_audit_errors_rollback_all_business_rows_and_receipts(case, monkeypatch, failure_kind):
    with _fresh_actual_operator_case_for_refusal(case, _valuation_payload()) as (client, detail):
        identity = detail["case"]["case_id"]
        before = _scoped_actual_business_snapshot(case)
        saved = case.repository.get_case(identity)
        domain_events = [event for event in case.audit.list_events() if event.event_type.startswith("avm.")]
        if failure_kind == "unknown-calculation-error":
            def unavailable(*args, **kwargs):
                raise RuntimeError("unexpected scoped native calculation failure")
            monkeypatch.setattr("modules.avm.application.valuation.value_store", unavailable)
        else:
            original = case.audit.record
            def unavailable(event):
                if event.event_type == "avm.valued.v1":
                    raise RuntimeError("actual scoped native domain audit unavailable")
                return original(event)
            monkeypatch.setattr(case.audit, "record", unavailable)
        result = client.post(f"{BASE}/cases/{identity}/value", headers=headers("creator", key="uncommitted-scoped-value"),
            json={"expectedVersion": detail["expectedVersion"], "reason": "Do not commit unknown or failed-audit values"})
        assert result.status_code == 500
        assert case.repository.get_case(identity) == saved
        assert case.repository.get_margin(identity) is None and case.repository.latest_report(identity) is None
        assert _scoped_actual_business_snapshot(case) == before
        assert [event for event in case.audit.list_events() if event.event_type.startswith("avm.")] == domain_events


def test_actual_scoped_HTTP_other409_keeps_original_rollback_and_no_success_receipt(case):
    with _fresh_actual_operator_case_for_refusal(case, _valuation_payload()) as (client, detail):
        identity = detail["case"]["case_id"]
        before = _scoped_actual_business_snapshot(case)
        result = client.post(f"{BASE}/cases/{identity}/value", headers=headers("creator", key="stale-version-refusal"),
            json={"expectedVersion": "0" * 64, "reason": "A stale version remains a refused command"})
        assert result.status_code == 409 and result.json()["detail"] == "AVM_VERSION_CONFLICT"
        assert _scoped_actual_business_snapshot(case) == before
        assert case.repository.get_case(identity).status.value == "DATA_READY"
        assert case.repository.get_margin(identity) is None and case.repository.latest_report(identity) is None
