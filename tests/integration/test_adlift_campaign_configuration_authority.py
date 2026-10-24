"""Real owned SQLite/HMAC engineering controls; no advertising or UAT claim."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.adlift_campaigns import create_adlift_campaign_router
from apps.api.oday_api.security import dependencies
from modules.adlift.domain.campaign import CampaignConflict, CampaignUnavailable
from modules.adlift.infrastructure.campaign_repository import DurableAdLiftCampaignRepository
from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary
from shared.api.errors import install_error_handlers
from shared.domain.models import AddressLocation, Brand, Store, Tenant
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from tests.security.test_feature_flag_admin_authorization import ISSUER, SIGNING_KEY, _headers

BASE = "/adlift/campaigns"
TENANT = "synthetic-campaign-tenant"
STORE = "synthetic-campaign-store"
OTHER = "synthetic-other-store"
PAYLOAD = {
    "name": "Synthetic campaign configuration",
    "channel": "paid_search",
    "treatment_store_ids": [STORE],
    "period_start": "2026-10-01",
    "period_end": "2026-10-31",
    "creative_reference": "synthetic-creative-reference",
    "budget_amount": "1000.25",
    "budget_currency": "TWD",
    "declared_data_version": "synthetic-declared-input-version-1",
    "reason": "Synthetic initial DRAFT configuration",
}
REVISION = {
    "budget_amount": "2000.75", "budget_currency": "TWD",
    "declared_data_version": "synthetic-declared-input-version-2",
    "reason": "Synthetic budget proposal, not an authorized increase",
}
_DEFAULT = object()


@dataclass
class Case:
    bundle: Any
    client: TestClient
    boundary: AuthenticationBoundary


def _seed(bundle):
    for tenant, brand, store in [(TENANT, "synthetic-brand", STORE),
                                 (TENANT, "synthetic-brand", OTHER),
                                 ("synthetic-foreign", "synthetic-foreign-brand", "foreign-store")]:
        bundle.tenant_repository.save_tenant(Tenant(tenant_id=tenant, tenant_name="Synthetic tenant"))
        bundle.brand_repository.save_brand(Brand(brand_id=brand, tenant_id=tenant,
            brand_code=brand, brand_name="Synthetic brand"))
        bundle.address_location_repository.save_address(AddressLocation(address_id="address-" + store,
            tenant_id=tenant, raw_address="Synthetic address", normalized_address="Synthetic address"))
        bundle.store_repository.save_store(Store(store_id=store, tenant_id=tenant,
            brand_id=brand, address_id="address-" + store, region_code="north"))


@contextmanager
def _case(path, *, scope=None, repository=_DEFAULT, audit=_DEFAULT, queue=_DEFAULT, seed=True):
    bundle = _durable_bundle(path)
    try:
        if seed:
            _seed(bundle)
        mapping = {actor: {"roles": ["marketing_manager"],
            "scope": {"tenant_id": TENANT, **(scope or {})}}
            for actor in ("marketer", "other-marketer")}
        mapping["auditor"] = {"roles": ["auditor"], "scope": {"tenant_id": TENANT}}
        mapping["foreign-marketer"] = {"roles": ["marketing_manager"],
                                       "scope": {"tenant_id": "synthetic-foreign"}}
        boundary = AuthenticationBoundary(AuthBoundaryConfig(issuer=ISSUER,
            audiences=frozenset({"unit-feature-api"}), signing_keys={SIGNING_KEY.kid: SIGNING_KEY},
            principal_mappings=mapping), audit_log=bundle.audit_log)
        app = FastAPI()
        install_error_handlers(app)

        @app.middleware("http")
        async def correlation(request: Request, call_next):
            request.state.correlation_id = request.headers.get("x-correlation-id", "synthetic-campaign-request")
            return await call_next(request)

        app.include_router(create_adlift_campaign_router(
            repository=bundle.adlift_campaign_repository if repository is _DEFAULT else repository,
            audit_log=bundle.audit_log if audit is _DEFAULT else audit,
            job_queue=bundle.job_queue if queue is _DEFAULT else queue,
            store_repository=bundle.store_repository, brand_repository=bundle.brand_repository,
            boundary=boundary), prefix="/adlift")
        with TestClient(app) as client:
            yield Case(bundle, client, boundary)
    finally:
        bundle.engine.close()


def _create(case, *, payload=None, actor="marketer", key="synthetic-create"):
    return case.client.post(BASE, json=PAYLOAD if payload is None else payload,
        headers={**_headers(actor), "Idempotency-Key": key})


def _proposal(case, campaign, *, payload=None, actor="marketer", key="synthetic-revise", revision="1"):
    return case.client.post(BASE + "/" + campaign + "/budget-revisions",
        json=REVISION if payload is None else payload,
        headers={**_headers(actor), "Idempotency-Key": key, "If-Match": revision})


def _campaigns(bundle, tenant=TENANT):
    return TenantScopedDocumentStore(SqliteDocumentStore(bundle.engine), tenant).list_all(
        DurableAdLiftCampaignRepository.COLLECTION)


def _receipts(bundle):
    return list(bundle.engine.query("SELECT job_id, status, payload_json FROM durable_jobs "
        "WHERE job_type = ? ORDER BY job_id", ("adlift.campaign-configurations.command-receipt",)))


def test_campaign_draft_replay_and_reopen_keep_unapproved_budget_null(tmp_path):
    path = tmp_path / "campaign.sqlite3"
    with _case(path) as case:
        accepted = _create(case)
        assert accepted.status_code == 201
        first = accepted.json()
        assert first["status"] == "DRAFT" and first["revision"] == 1
        assert first["authorized_budget_revision"] is None
        assert first["budget_proposals"][0]["authorized_before_amount"] is None
        assert first["budget_proposals"][0]["amount"] == "1000.25"
        assert first["created_by"] == "marketer"
        assert first["external_execution_acknowledged"] is False
        assert first["status_history"][0]["actor"] == "marketer"
        assert first["status_history"][0]["correlation_id"] == "unit-request"
        assert case.bundle.intervention_repository.list_all() == []
        replay = _create(case).json()
        assert replay["campaign_id"] == first["campaign_id"] and replay["replayed"] is True
        assert len(_campaigns(case.bundle)) == 1 and len(_receipts(case.bundle)) == 1
        events = [event for event in case.bundle.audit_log.list_events(tenant_id=TENANT)
                  if event.event_type == "adlift.campaign_configuration.v1"]
        assert len(events) == 1 and events[0].event_id == first["audit_event_id"]
        assert events[0].actor == "marketer" and events[0].outcome == "draft"
        assert all(not flag.enabled for flag in case.bundle.feature_flag_registry.all())
    with _case(path, seed=False) as reopened:
        current = reopened.client.get(BASE + "/" + first["campaign_id"], headers=_headers("marketer"))
        assert current.status_code == 200 and current.json()["revision"] == 1
        assert current.json()["budget_proposals"] == first["budget_proposals"]
        assert current.json()["authorized_budget_revision"] is None


def test_budget_proposal_real_two_engine_cas_and_immutable_history(tmp_path):
    path = tmp_path / "two-writers.sqlite3"
    with _case(path) as left, _case(path, seed=False) as right:
        original = _create(left).json()
        campaign = original["campaign_id"]
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(_proposal, case, campaign, key="writer-" + str(index))
                       for index, case in enumerate((left, right))]
            responses = [f.result() for f in futures]
        assert sorted(r.status_code for r in responses) == [200, 409]
        result = left.client.get(BASE + "/" + campaign, headers=_headers("marketer")).json()
        assert result["revision"] == 2 and len(result["budget_proposals"]) == 2
        assert result["budget_proposals"][0] == original["budget_proposals"][0]
        assert result["budget_proposals"][1]["amount"] == "2000.75"
        assert result["authorized_budget_revision"] is None and result["status"] == "DRAFT"
        assert len(_receipts(left.bundle)) == 2
        stale = _proposal(left, campaign, key="later-stale")
        assert stale.status_code == 409
        assert left.bundle.adlift_campaign_repository.for_tenant(TENANT).get(campaign).revision == 2


def test_idempotency_payload_and_subject_are_bound(tmp_path):
    with _case(tmp_path / "keys.sqlite3") as case:
        first = _create(case).json()
        assert _create(case, payload={**PAYLOAD, "budget_amount": "999.99"}).status_code == 409
        independent = _create(case, actor="other-marketer")
        assert independent.status_code == 201 and independent.json()["campaign_id"] != first["campaign_id"]
        assert len(_campaigns(case.bundle)) == 2


@pytest.mark.parametrize("scope", [
    {"store_ids": [OTHER]}, {"brand_ids": ["foreign-brand"]}, {"region_ids": ["south"]},
    {"assigned_area_ids": ["unbound-area"]}, {"heat_zone_ids": ["unbound-heat"]},
    {"modules": ["sitescore"]},
])
def test_campaign_creation_requires_every_actual_store_scope(tmp_path, scope):
    with _case(tmp_path / "scope.sqlite3", scope=scope) as case:
        response = _create(case)
        assert response.status_code == 403
        assert _campaigns(case.bundle) == [] and _receipts(case.bundle) == []


def test_multi_store_and_foreign_tenant_are_not_implicitly_granted(tmp_path):
    with _case(tmp_path / "multi.sqlite3", scope={"store_ids": [STORE]}) as case:
        denied = _create(case, payload={**PAYLOAD, "treatment_store_ids": [STORE, OTHER]})
        assert denied.status_code == 403
        foreign = _create(case, payload={**PAYLOAD, "treatment_store_ids": ["foreign-store"]})
        assert foreign.status_code == 403
        denials = [event for event in case.bundle.audit_log.list_events(correlation_id="unit-request")
                   if event.resource == "adlift/foreign-store" and event.outcome == "deny"]
        assert len(denials) == 1 and denials[0].actor == "marketer"
        assert denials[0].metadata["policy_id"] == "adlift.object_scope"
        assert _campaigns(case.bundle) == [] and _receipts(case.bundle) == []
        created = _create(case).json()
        hidden = case.client.get(BASE + "/" + created["campaign_id"], headers=_headers("foreign-marketer"))
        assert hidden.status_code == 404
        assert _proposal(case, created["campaign_id"], actor="foreign-marketer").status_code == 404


def test_trusted_principal_and_body_authority_cannot_be_forged(tmp_path):
    with _case(tmp_path / "principal.sqlite3") as case:
        assert _create(case, actor="auditor").status_code == 403
        no_auth = case.client.post(BASE, json=PAYLOAD,
            headers={"Idempotency-Key": "spoofed", "x-roles": "marketing_manager", "x-subject-id": "marketer"})
        assert no_auth.status_code == 401
        for field, value in [("actor", "other-marketer"), ("tenant_id", "synthetic-foreign"),
                             ("authorized_budget_revision", 1), ("authorized_before_amount", "0")]:
            assert _create(case, payload={**PAYLOAD, field: value}).status_code == 422
        assert _campaigns(case.bundle) == [] and _receipts(case.bundle) == []


@pytest.mark.parametrize("operation", ["create", "revise"])
def test_actual_audit_failure_rolls_back_campaign_cas_and_receipt(tmp_path, monkeypatch, operation):
    with _case(tmp_path / "rollback.sqlite3") as case:
        campaign = _create(case).json()["campaign_id"] if operation == "revise" else None
        before, receipts = _campaigns(case.bundle), _receipts(case.bundle)
        record = case.bundle.audit_log.record

        def fault(event):
            if event.event_type == "adlift.campaign_configuration.v1":
                raise RuntimeError("synthetic actual audit failure")
            return record(event)

        monkeypatch.setattr(case.bundle.audit_log, "record", fault)
        result = _create(case) if campaign is None else _proposal(case, campaign)
        assert result.status_code == 503
        assert _campaigns(case.bundle) == before and _receipts(case.bundle) == receipts


def test_shared_sql_owner_mismatch_and_public_unbound_repository_fail_closed(tmp_path):
    with _case(tmp_path / "owner.sqlite3") as case:
        raw = case.bundle.adlift_campaign_repository
        with pytest.raises(CampaignUnavailable, match="TENANT_SCOPE_REQUIRED"):
            raw.get("unknown")
        with pytest.raises(CampaignUnavailable, match="TENANT_BINDING_CONFLICT"):
            raw.for_tenant(TENANT).for_tenant("synthetic-foreign")
        with _case(tmp_path / "wrong.sqlite3", repository=raw) as wrong:
            assert _create(wrong).status_code == 503
            assert _campaigns(case.bundle) == [] and _campaigns(wrong.bundle) == []
        bound = raw.for_tenant(TENANT)
        value = _create(case).json()
        saved = bound.get(value["campaign_id"])
        assert _proposal(case, value["campaign_id"]).status_code == 200
        stale = replace(saved, revision=2,
            proposals=(*saved.proposals, replace(saved.proposals[0], revision=2)))
        before = bound.get(value["campaign_id"])
        with pytest.raises(CampaignConflict):
            bound.compare_and_save(stale, expected_revision=1)
        assert bound.get(value["campaign_id"]) == before


@pytest.mark.parametrize("missing", ["repository", "audit", "queue"])
def test_missing_actual_sql_owner_never_falls_back_to_memory(tmp_path, missing):
    with _case(tmp_path / "missing.sqlite3", **{missing: None}) as case:
        result = _create(case)
        assert result.status_code == 503
        assert _campaigns(case.bundle) == [] and _receipts(case.bundle) == []


def test_original_replay_rechecks_current_store_scope_and_retained_shape(tmp_path):
    path = tmp_path / "revoked.sqlite3"
    with _case(path) as case:
        value = _create(case).json()
    with _case(path, scope={"store_ids": [OTHER]}, seed=False) as revoked:
        assert _create(revoked).status_code == 403
        assert revoked.client.get(BASE + "/" + value["campaign_id"],
            headers=_headers("marketer")).status_code == 403
        assert len(_receipts(revoked.bundle)) == 1
        # Explicit historical poison exercises the reader, not the public writer.
        scoped = TenantScopedDocumentStore(SqliteDocumentStore(revoked.bundle.engine), TENANT)
        scoped.put(DurableAdLiftCampaignRepository.COLLECTION, "historical-bad", {"status": "APPROVED"})
        with pytest.raises(CampaignUnavailable, match="STATE_INVALID"):
            revoked.bundle.adlift_campaign_repository.for_tenant(TENANT).get("historical-bad")


@pytest.mark.parametrize("raw", [
    '{"budget_amount":NaN}', '{"budget_amount":1e309}',
    '{"unrelated":NaN}', '{"unrelated":1e309}',
])
def test_raw_nonfinite_input_is_constant_safe_422(tmp_path, raw):
    with _case(tmp_path / "nonfinite.sqlite3") as case:
        result = case.client.post(BASE, content=raw, headers={**_headers("marketer"),
            "Content-Type": "application/json", "Idempotency-Key": "nonfinite"})
        assert result.status_code == 422
        assert "NaN" not in result.text and "Infinity" not in result.text
        assert _campaigns(case.bundle) == [] and _receipts(case.bundle) == []


def test_required_configuration_schema_currency_and_revision_are_honest(tmp_path):
    with _case(tmp_path / "contract.sqlite3") as case:
        schema = case.client.get("/openapi.json").json()["components"]["schemas"]
        assert set(schema["CampaignCreatePayload"]["required"]) == set(PAYLOAD) - {"creative_reference"}
        assert set(schema["CampaignBudgetProposalPayload"]["required"]) == set(REVISION)
        for field in ("budget_currency", "declared_data_version"):
            assert _create(case, payload={k: v for k, v in PAYLOAD.items() if k != field}).status_code == 422
        value = _create(case).json()
        assert _proposal(case, value["campaign_id"], payload={**REVISION, "budget_currency": "USD"}).status_code == 422
        assert _proposal(case, value["campaign_id"], revision="0").status_code == 422
        assert case.bundle.adlift_campaign_repository.for_tenant(TENANT).get(value["campaign_id"]).revision == 1


def test_original_main_mount_uses_the_same_persistence_campaign_owner(tmp_path, monkeypatch):
    from apps.api.oday_api.main import create_app

    monkeypatch.setenv("ODP_PRODUCT_MODE", "poc")
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "false")
    with _case(tmp_path / "main.sqlite3") as case:
        monkeypatch.setattr(dependencies, "default_boundary", lambda: case.boundary)
        with TestClient(create_app(persistence=case.bundle)) as client:
            result = client.post("/api/v1" + BASE, json=PAYLOAD,
                headers={**_headers("marketer"), "Idempotency-Key": "real-main-mount"})
            assert result.status_code == 201
            value = case.bundle.adlift_campaign_repository.for_tenant(TENANT).get(result.json()["campaign_id"])
            assert value is not None and value.revision == 1
            assert case.bundle.intervention_repository.list_all() == []
