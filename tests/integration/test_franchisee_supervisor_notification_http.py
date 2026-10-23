"""Signed local HTTP controls for the actual FRAN004 saved-form consumer.

Uses the existing pending-context composition pattern with real SQL/native
owners and real identity/session-backed HMAC authentication. Synthetic masters
are explicit engineering inputs, not production data, email delivery or UAT.
No request-state principal/admission port or business writer is replaced.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator import create_operator_router
from apps.api.oday_api.security import dependencies
from modules.opsboard.application.operator_live_repository import OperatorLiveRepository
from modules.opsboard.application.shell import AUDIT_EVENTS, FRANCHISEE_REPORTS
from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    SigningKey,
    encode_compact_jwt,
)
from shared.api.errors import install_error_handlers
from shared.auth import DataClassification, Role, Scope
from shared.domain import AddressLocation, Brand, Store, Tenant
from shared.identity.store import Account
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.operator_shell import DurableShellRepository
from shared.infrastructure.persistence.repositories import (
    DurableAVMRepository,
    DurableNetPlanRepository,
    DurableSiteScoreRepository,
)
from tests.integration.test_priceops_growth_main_owner import _main_provider

BASE = "/api/v1/operator/shell"
TENANT = "10000000-0000-0000-0000-000000000401"
FOREIGN_TENANT = "10000000-0000-0000-0000-000000000402"
FRANCHISEE = "20000000-0000-0000-0000-000000000401"
SUPERVISOR_A = "20000000-0000-0000-0000-000000000402"
SUPERVISOR_B = "20000000-0000-0000-0000-000000000403"
OTHER_STORE_SUPERVISOR = "20000000-0000-0000-0000-000000000404"
FOREIGN_SUPERVISOR = "20000000-0000-0000-0000-000000000405"
WRONG_ROLE = "20000000-0000-0000-0000-000000000406"
STORE = "fran-http-current-store"
OTHER_STORE = "fran-http-other-store"
FOREIGN_STORE = "fran-http-foreign-store"
MESSAGE = "Explicit saved field report: equipment needs current supervisor review"
CID = "30000000-0000-0000-0000-000000000401"
KEY = SigningKey(kid="fran004-http", algorithm="HS256",
                 secret=b"synthetic-owned-FRAN004-HTTP-session-control-key")


def _scope(tenant=TENANT, store=STORE, *, module="operator_console"):
    return Scope(tenant_id=tenant, store_ids=frozenset({store}),
                 modules=frozenset({module}),
                 clearance=DataClassification.CONFIDENTIAL)


def _seed_actual_masters_and_accounts(bundle):
    for tenant in (TENANT, FOREIGN_TENANT):
        bundle.tenant_repository.save_tenant(Tenant(tenant_id=tenant,
            tenant_name="Synthetic FRAN004 HTTP tenant"))
        bundle.brand_repository.save_brand(Brand(brand_id="brand-" + tenant,
            tenant_id=tenant, brand_code=tenant, brand_name="Synthetic FRAN004 brand"))
    for store, tenant in ((STORE, TENANT), (OTHER_STORE, TENANT), (FOREIGN_STORE, FOREIGN_TENANT)):
        address = AddressLocation(address_id="address-" + store, tenant_id=tenant)
        bundle.address_location_repository.save_address(address)
        bundle.store_repository.save_store(Store(store_id=store, tenant_id=tenant,
            brand_id="brand-" + tenant, address_id=address.address_id,
            store_name="Synthetic current store " + store, region_code="north"))
    for subject, tenant, role, store in (
        (FRANCHISEE, TENANT, Role.FRANCHISEE, STORE),
        (SUPERVISOR_A, TENANT, Role.REGIONAL_SUPERVISOR, STORE),
        (SUPERVISOR_B, TENANT, Role.REGIONAL_SUPERVISOR, STORE),
        (OTHER_STORE_SUPERVISOR, TENANT, Role.REGIONAL_SUPERVISOR, OTHER_STORE),
        (FOREIGN_SUPERVISOR, FOREIGN_TENANT, Role.REGIONAL_SUPERVISOR, FOREIGN_STORE),
        (WRONG_ROLE, TENANT, Role.OPERATIONS_MANAGER, STORE),
    ):
        bundle.identity_store.save_account(Account(account_id=UUID(subject), tenant_id=UUID(tenant),
            username=subject, email=subject + "@example.invalid",
            display_name="Synthetic HTTP account " + subject))
        bundle.identity_store.set_account_roles(subject, [role])
        bundle.identity_store.set_account_scope(subject, _scope(tenant, store,
            module="franchisee_portal" if role == Role.FRANCHISEE else "operator_console"))


@dataclass
class SignedContext:
    bundle: Any
    documents: Any
    client: TestClient

    def headers(self, subject=FRANCHISEE):
        account = self.bundle.identity_store.find_account_by_id(subject)
        assert account is not None and account.is_active
        session = self.bundle.session_service.create_session(account_id=UUID(subject),
                                                             provider="local_password")
        now = datetime.now(UTC)
        token = encode_compact_jwt({"sub": subject, "sid": str(session.session_id),
            "tenant_id": str(account.tenant_id), "iss": "urn:odp:identity:local",
            "aud": "owned-fran004", "iat": now.timestamp(),
            "exp": (now + timedelta(minutes=10)).timestamp()}, KEY)
        return {"Authorization": "Bearer " + token, "X-Correlation-Id": CID}

    def shell_owner(self, tenant=TENANT):
        return DurableShellRepository(TenantScopedDocumentStore(self.documents, tenant))

    def saved_facts(self):
        owner = self.shell_owner()
        return owner.list_records(FRANCHISEE_REPORTS), owner.list_records(AUDIT_EVENTS)


@contextmanager
def _signed_client(bundle, monkeypatch, *, missing_documents=False):
    """The real router owns RequestProjectedState and its original admission."""
    documents = SqliteDocumentStore(bundle.engine)
    boundary = AuthenticationBoundary(AuthBoundaryConfig(local_issuer="urn:odp:identity:local",
        local_audiences=frozenset({"owned-fran004"}), local_signing_keys={KEY.kid: KEY},
        identity_store=bundle.identity_store, session_service=bundle.session_service),
        audit_log=bundle.audit_log)
    with monkeypatch.context() as app_auth:
        app_auth.setattr(dependencies, "_default_boundary", boundary)
        app = FastAPI()
        app.state.job_queue = bundle.job_queue
        app.state.feature_flag_registry = bundle.feature_flag_registry
        install_error_handlers(app)

        @app.middleware("http")
        async def correlation(request: Request, call_next):
            request.state.correlation_id = request.headers.get("x-correlation-id", CID)
            return await call_next(request)

        app.include_router(create_operator_router(
            audit_log=bundle.audit_log,
            document_store=None if missing_documents else documents,
            job_queue=bundle.job_queue, feature_flags=bundle.feature_flag_registry,
            live_repository=OperatorLiveRepository(bundle), require_live_data=True,
            intake_workflow_store=bundle.assisted_intake_store,
            priceops_repository_for_tenant=_main_provider(bundle.priceops_repository, documents),
            priceops_store_repository=bundle.store_repository,
            priceops_brand_repository=bundle.brand_repository,
            listing_repository_for_tenant=bundle.listing_repository_for_tenant,
            sitescore_repository_for_tenant=lambda tenant: DurableSiteScoreRepository(
                TenantScopedDocumentStore(documents, tenant)),
            sitescore_decision_repository_for_tenant=bundle.sitescore_decision_store_for_tenant,
            avm_repository_for_tenant=lambda tenant: DurableAVMRepository(
                TenantScopedDocumentStore(documents, tenant)),
            netplan_repository_for_tenant=lambda tenant: DurableNetPlanRepository(
                TenantScopedDocumentStore(documents, tenant)),
        ), prefix="/api/v1")
        client = TestClient(app)
        try:
            yield SignedContext(bundle, documents, client)
        finally:
            client.close()


@pytest.fixture
def signed_context(tmp_path, monkeypatch):
    # SQLite engineering composition with a real live-read owner and signed
    # identity boundary; never the unauthenticated POC header-trust path.
    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "false")
    monkeypatch.setenv("ODP_DEPLOY_ENV", "test")
    bundle = _durable_bundle(tmp_path / "owned-fran004-signed-http.sqlite3")
    try:
        _seed_actual_masters_and_accounts(bundle)
        with _signed_client(bundle, monkeypatch) as context:
            yield context
    finally:
        bundle.engine.close()


def _saved_report(context):
    body = {"storeId": STORE, "category": "equipment", "message": MESSAGE}
    # A spoofable subject/persona header must not replace the real JWT actor.
    headers = {**context.headers(), "Idempotency-Key": "same-real-form-intent",
               "X-Subject-Id": SUPERVISOR_A, "X-Operator-Role": "field-lead"}
    response = context.client.post(BASE + "/franchisee/reports", headers=headers, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["report"]["subjectId"] == FRANCHISEE
    assert result["report"]["storeId"] == STORE
    assert result["report"]["message"] == MESSAGE
    before = context.saved_facts()
    repeated = context.client.post(BASE + "/franchisee/reports", headers=headers, json=body)
    assert repeated.status_code == 200 and repeated.json()["idempotentReplay"] is True
    assert repeated.json()["report"] == result["report"]
    assert context.saved_facts() == before
    return result["report"]


def _report_notices(response):
    assert response.status_code == 200, response.text
    return [item for item in response.json()["items"]
            if (item.get("sourceEntity") or {}).get("kind") == "franchisee.report"]


def test_signed_saved_form_has_two_current_supervisor_views_and_one_exact_source(signed_context):
    context = signed_context
    report = _saved_report(context)
    stored, audit = context.saved_facts()
    assert stored == [report]
    matched = [event for event in audit if event.get("targetId") == report["reportId"]]
    assert len(matched) == 1 and matched[0]["actorSubjectId"] == FRANCHISEE
    assert matched[0]["metadata"]["storeId"] == STORE
    assert matched[0]["metadata"]["correlationId"] == CID
    before = context.saved_facts()
    for subject in (SUPERVISOR_A, SUPERVISOR_B):
        headers = context.headers(subject)
        inbox = context.client.get(BASE + "/notifications", headers=headers)
        rows = _report_notices(inbox)
        assert len(rows) == 1
        notice = rows[0]
        assert notice["notificationId"] == "franchisee-report:" + report["reportId"]
        assert notice["occurredAt"] == report["createdAt"]
        assert notice["sourceEntity"] == {"kind": "franchisee.report", "id": report["reportId"]}
        assert notice["sourceHref"] == "/notifications/reports/" + report["reportId"]
        assert notice["target"] is None and notice["acknowledged"] is False
        assert inbox.json()["actions"]["acknowledge"]["allowed"] is False
        source = context.client.get(BASE + "/franchisee/reports/" + report["reportId"], headers=headers)
        assert source.status_code == 200, source.text
        assert source.json()["report"] == report
        assert source.json()["source"] == "canonical-franchisee-report-owner"
        assert source.json()["delivery"]["email"] == "not_evidenced"
    assert context.saved_facts() == before


@pytest.mark.parametrize("denial", ["store", "tenant", "role", "anonymous"])
def test_signed_inbox_and_exact_report_require_current_supervisor_scope(signed_context, denial):
    context = signed_context
    report = _saved_report(context)
    subjects = {"store": OTHER_STORE_SUPERVISOR, "tenant": FOREIGN_SUPERVISOR,
                "role": WRONG_ROLE}
    headers = (context.headers(subjects[denial]) if denial != "anonymous" else
               {"X-Subject-Id": SUPERVISOR_A, "X-Operator-Role": "field-lead",
                "X-Tenant-Id": TENANT, "X-Correlation-Id": CID})
    before = context.saved_facts()
    inbox = context.client.get(BASE + "/notifications", headers=headers)
    source = context.client.get(BASE + "/franchisee/reports/" + report["reportId"], headers=headers)
    if denial == "anonymous":
        assert inbox.status_code == source.status_code == 401
    else:
        assert _report_notices(inbox) == []
        assert source.status_code == 404
    assert MESSAGE not in source.text and report["reportId"] not in source.text
    assert context.saved_facts() == before


def test_signed_current_store_revocation_applies_to_an_already_issued_token(signed_context):
    context = signed_context
    report = _saved_report(context)
    headers = context.headers(SUPERVISOR_A)
    endpoint = BASE + "/franchisee/reports/" + report["reportId"]
    assert context.client.get(endpoint, headers=headers).status_code == 200
    context.bundle.identity_store.set_account_scope(SUPERVISOR_A, _scope(store=OTHER_STORE))
    before = context.saved_facts()
    assert context.client.get(endpoint, headers=headers).status_code == 404
    assert _report_notices(context.client.get(BASE + "/notifications", headers=headers)) == []
    other = context.client.get(endpoint, headers=context.headers(SUPERVISOR_B))
    assert other.status_code == 200 and other.json()["report"] == report
    assert context.saved_facts() == before


def test_signed_unknown_report_never_substitutes_a_different_persisted_form(signed_context):
    context = signed_context
    report = _saved_report(context)
    before = context.saved_facts()
    response = context.client.get(BASE + "/franchisee/reports/FR-EXPLICITLY-MISSING",
                                  headers=context.headers(SUPERVISOR_A))
    assert response.status_code == 404
    assert MESSAGE not in response.text and report["reportId"] not in response.text
    assert context.saved_facts() == before


@pytest.mark.parametrize("denial", ["store", "tenant", "role"])
def test_signed_franchisee_write_does_not_borrow_another_store_or_supervisor_authority(signed_context, denial):
    context = signed_context
    subject = SUPERVISOR_A if denial == "role" else FRANCHISEE
    target = STORE if denial == "role" else OTHER_STORE if denial == "store" else FOREIGN_STORE
    before = context.saved_facts()
    response = context.client.post(BASE + "/franchisee/reports",
        headers={**context.headers(subject), "Idempotency-Key": "denied-form"},
        json={"storeId": target, "category": "equipment", "message": MESSAGE})
    assert response.status_code == 403
    assert context.saved_facts() == before


@pytest.mark.parametrize("read", ["inbox", "exact source"])
def test_signed_missing_durable_shell_owner_returns_http503_without_fake_source(signed_context, monkeypatch, read):
    context = signed_context
    report = _saved_report(context)
    before = context.saved_facts()
    with _signed_client(context.bundle, monkeypatch, missing_documents=True) as missing:
        endpoint = (BASE + "/notifications" if read == "inbox" else
                    BASE + "/franchisee/reports/" + report["reportId"])
        response = missing.client.get(endpoint, headers=missing.headers(SUPERVISOR_A))
    assert response.status_code == 503
    assert MESSAGE not in response.text
    assert context.saved_facts() == before


@pytest.mark.parametrize("module", ["operator_console", "unrelated-module"])
def test_signed_franchisee_requires_exact_portal_module_without_console_access(signed_context, module):
    context = signed_context
    report = _saved_report(context)
    assert context.client.get(BASE + "/notifications", headers=context.headers()).status_code == 403
    before = context.saved_facts()
    context.bundle.identity_store.set_account_scope(FRANCHISEE, _scope(module=module))
    response = context.client.post(BASE + "/franchisee/reports",
        headers={**context.headers(), "Idempotency-Key": "exact-module-mismatch"},
        json={"storeId": STORE, "category": "equipment", "message": MESSAGE})
    assert response.status_code == 403
    assert response.json()["detail"] == "module outside principal scope"
    assert context.saved_facts() == before
    assert context.saved_facts()[0] == [report]
