"""Synthetic owned SQLite/HMAC native budget controls, not ads/UAT or PG proof."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid5

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.adlift import create_adlift_router
from apps.api.app.routes.interventions import create_interventions_router
from apps.api.oday_api.routes.feature_flags import create_feature_flags_router
from apps.api.oday_api.security import dependencies
from modules.adlift.application.campaign import AdLiftCampaignService
from modules.adlift.application.campaign_authority import AdLiftCampaignBudgetAuthority
from modules.intervention.application.workflow import InterventionWorkflow
from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary
from shared.api.errors import install_error_handlers
from shared.auth.feature_flags import DEFAULT_FLAGS, FeatureFlagRegistry
from shared.domain.models import AddressLocation, Brand, Store, Tenant
from shared.infrastructure.persistence.factory import _durable_bundle
from tests.integration.test_adlift_campaign_configuration_authority import (
    OTHER,
    PAYLOAD,
    STORE,
)
from tests.integration.test_adlift_incrementality import _flat_observations
from tests.security.test_feature_flag_admin_authorization import ISSUER, SIGNING_KEY
from tests.security.test_feature_flag_admin_authorization import _headers as signed_headers

TENANT = "3628ded2-1552-4ab3-98d5-e3fe278931b1"
CORRELATION = "b1bb41d8-5b88-43aa-92a6-88c1401c144e"
BASE = "/adlift/campaigns"
ADMIN = "/api/v1/admin/feature-flags/high_risk.adlift.approve"
PROOF = {"reason": "Synthetic engineering dual-admin command proof", "riskAcknowledged": True}
CONTROLS = ("synthetic-campaign-control-0", "synthetic-campaign-control-1")


def _headers(actor):
    return signed_headers(actor, correlation_id=CORRELATION)


def _seed(bundle):
    bundle.tenant_repository.save_tenant(Tenant(tenant_id=TENANT, tenant_name="Synthetic canonical UUID tenant"))
    bundle.brand_repository.save_brand(Brand(brand_id="synthetic-brand", tenant_id=TENANT,
        brand_code="synthetic-brand", brand_name="Synthetic brand"))
    for store in (STORE, OTHER):
        bundle.address_location_repository.save_address(AddressLocation(address_id="address-" + store,
            tenant_id=TENANT, raw_address="Synthetic address", normalized_address="Synthetic address"))
        bundle.store_repository.save_store(Store(store_id=store, tenant_id=TENANT,
            brand_id="synthetic-brand", address_id="address-" + store, region_code="north"))


@dataclass
class Case:
    bundle: Any
    client: TestClient
    authority: Any
    mappings: dict


@contextmanager
def case(path, monkeypatch, *, seed=True, restricted=False):
    bundle = _durable_bundle(path)
    try:
        if seed:
            _seed(bundle)
            for store in CONTROLS:
                bundle.address_location_repository.save_address(AddressLocation(address_id="address-" + store,
                    tenant_id=TENANT, raw_address="Synthetic control", normalized_address="Synthetic control"))
                bundle.store_repository.save_store(Store(store_id=store, tenant_id=TENANT,
                    brand_id="synthetic-brand", address_id="address-" + store, region_code="north"))
        mappings = {subject: {"roles": [role], "scope": {"tenant_id": TENANT}}
            for subject, role in [("marketer", "marketing_manager"), ("marketer-b", "marketing_manager"), ("ops", "operations_manager"),
                ("reviewer", "operations_manager"), ("executor", "regional_supervisor"),
                ("admin-a", "platform_admin"), ("admin-b", "platform_admin")]}
        mappings["restricted-ops"] = {"roles": ["operations_manager"],
            "scope": {"tenant_id": TENANT, "store_ids": [STORE]}}
        boundary = AuthenticationBoundary(AuthBoundaryConfig(issuer=ISSUER,
            audiences=frozenset({"unit-feature-api"}), signing_keys={SIGNING_KEY.kid: SIGNING_KEY},
            principal_mappings=mappings), audit_log=bundle.audit_log)
        monkeypatch.setattr(dependencies, "default_boundary", lambda: boundary)
        campaigns = AdLiftCampaignService(repository=bundle.adlift_campaign_repository,
            audit_log=bundle.audit_log, job_queue=bundle.job_queue,
            store_repository=bundle.store_repository, brand_repository=bundle.brand_repository,
            intervention_repository=bundle.intervention_repository)
        authority = AdLiftCampaignBudgetAuthority(campaigns=campaigns,
            interventions=bundle.intervention_repository, reports=bundle.adlift_repository,
            flags=bundle.feature_flag_registry, boundary=boundary)
        workflow = InterventionWorkflow(repository=bundle.intervention_repository,
            audit_log=bundle.audit_log, label_hooks=[bundle.intervention_label_registry],
            campaign_budget_authority=authority)
        app = FastAPI()
        install_error_handlers(app)
        app.state.feature_flag_registry = bundle.feature_flag_registry
        app.state.job_queue = bundle.job_queue

        @app.middleware("http")
        async def correlation(request: Request, call_next):
            request.state.correlation_id = request.headers.get("x-correlation-id", "synthetic-campaign-native")
            return await call_next(request)

        app.include_router(create_adlift_router(repository=bundle.adlift_repository,
            audit_log=bundle.audit_log, job_queue=bundle.job_queue, runtime_mode="poc",
            campaign_repository=bundle.adlift_campaign_repository,
            store_repository=bundle.store_repository, brand_repository=bundle.brand_repository,
            boundary=boundary, campaign_budget_authority=authority))
        app.include_router(create_interventions_router(workflow=workflow,
            store_repository=bundle.store_repository, brand_repository=bundle.brand_repository,
            label_registry=bundle.intervention_label_registry, job_queue=bundle.job_queue,
            require_object_scope=True, require_durable_commands=True))
        app.include_router(create_feature_flags_router(registry=bundle.feature_flag_registry,
            audit_log=bundle.audit_log, queue=bundle.job_queue, boundary=boundary,
            engine=dependencies.build_engine(audit_log=bundle.audit_log,
                flags=bundle.feature_flag_registry)), prefix="/api/v1")
        with TestClient(app) as client:
            yield Case(bundle, client, authority, mappings)
    finally:
        bundle.engine.close()


def create(c, *, stores=(STORE,)):
    result = c.client.post(BASE, json={**PAYLOAD, "treatment_store_ids": list(stores),
        "period_start": "2026-05-11", "period_end": "2026-05-20"},
        headers={**_headers("marketer"), "Idempotency-Key": "synthetic-create"})
    assert result.status_code == 201
    return result.json()


def analyze(c, campaign, *, changes=None, key=None, canonical=True):
    stores = tuple(campaign["treatment_store_ids"])
    controls = CONTROLS[:len(stores)]
    metrics = _flat_observations(treatment_pre=1000, treatment_post=1300,
        control_pre=1000, control_post=1100, treatment_stores=stores, control_stores=controls)
    body = {"campaign_id": campaign["campaign_id"], "name": campaign["name"],
        "channel": campaign["channel"], "treatment_store_ids": list(stores),
        "candidate_control_store_ids": list(controls), "pre_period_start": "2026-05-01",
        "pre_period_end": "2026-05-10", "campaign_period_start": campaign["period_start"],
        "campaign_period_end": campaign["period_end"], "ad_spend": 1000.0,
        "observations": [m.to_dict() for m in metrics],
        "campaign_configuration_revision": campaign["revision"],
        "declared_data_version": campaign["budget_proposals"][-1]["declared_data_version"],
        **(changes or {})}
    if not canonical:
        body.pop("campaign_configuration_revision")
        body.pop("declared_data_version")
    return c.client.post("/adlift/incrementality-jobs", json={"campaigns": [body]},
        headers={**_headers("marketer"), "Idempotency-Key": key or "analysis-" + str(campaign["revision"])})


def open_native(c, campaign, store=STORE, *, actor="ops", action_spec=None):
    body = {"store_id": store, "kind": "AD_CAMPAIGN", "trigger_ref": "synthetic-canonical-campaign",
        "expected_outcome": "Synthetic engineering outcome to be measured later",
        "planned_start": campaign["period_start"] + "T00:00:00Z",
        "planned_end": campaign["period_end"] + "T23:59:59Z", "created_by": actor,
        "action_spec": {"campaign_budget_proposal": {"campaign_id": campaign["campaign_id"],
            "proposal_revision": campaign["revision"]}} if action_spec is None else action_spec}
    return c.client.post("/interventions", json=body, headers={**_headers(actor),
        "Idempotency-Key": f"native-{campaign['campaign_id']}-{campaign['revision']}-{store}"})


def lifecycle(c, native, command, *, actor="ops", extra=None, key=None):
    context = c.client.get(f"/interventions/{native}/command-context", params={"command": command},
        headers=_headers(actor))
    assert context.status_code == 200
    snapshot = context.json()
    # The strict lifecycle receipt belongs to one command, not the create event
    # or another command sharing the fixture's request correlation identifier.
    # Stable per-command/version IDs also preserve exact idempotent replays.
    command_cid = str(uuid5(UUID(CORRELATION), f"{native}:{command}:{snapshot['version']}"))
    body = {"actor": actor, "executor": actor, "expected_version": snapshot["version"],
        "expectedActorSubjectId": actor, "expectedTenantId": TENANT, "command_context": snapshot,
        **(extra or {})}
    return c.client.post(f"/interventions/{native}/{command}", json=body,
        headers={**signed_headers(actor, correlation_id=command_cid),
            "Idempotency-Key": key or f"{native}-{command}-{snapshot['version']}",
            "If-Match": f'W/"{snapshot["version"]}"', "If-Actor-Match": actor})


def pending(c, campaign, store=STORE):
    result = open_native(c, campaign, store)
    assert result.status_code == 201
    native = result.json()["intervention_id"]
    assert lifecycle(c, native, "eligibility", extra={"eligible": True}).status_code == 200
    assert lifecycle(c, native, "action", extra={"action_spec": {}}).status_code == 200
    assert lifecycle(c, native, "conflict-check", extra={"allow_overlap": True,
        "reason": "Explicit synthetic budget revision replaces no external execution"}).status_code == 200
    assert lifecycle(c, native, "submit").status_code == 200
    return native


def decide(c, native, *, actor="reviewer", key=None):
    read = c.client.get("/interventions/" + native, headers=_headers(actor))
    assert read.status_code == 200
    value = read.json()
    return c.client.post(f"/interventions/{native}/approve", json={"action": "APPROVE",
        "reason": "Synthetic independent native approval reason", "expected_version": value["version"],
        "expectedActorSubjectId": actor, "expectedTenantId": TENANT, "review_context": value["review_context"]},
        headers={**_headers(actor), "Idempotency-Key": key or native + "-approval",
            "If-Match": f'W/"{value["version"]}"', "If-Actor-Match": actor})


def enable(c):
    for actor in ("admin-a", "admin-b"):
        assert c.client.post(ADMIN + "/approve", json=PROOF, headers=_headers(actor)).status_code == 200
    assert c.client.post(ADMIN + "/enable", json=PROOF, headers=_headers("admin-a")).status_code == 200


def revise(c, campaign, amount):
    response = c.client.post(BASE + "/" + campaign["campaign_id"] + "/budget-revisions",
        json={"budget_amount": amount, "budget_currency": "TWD", "declared_data_version": "synthetic-version-2",
            "reason": "Synthetic next budget revision"}, headers={**_headers("marketer"),
            "Idempotency-Key": "budget-revise", "If-Match": str(campaign["revision"])})
    assert response.status_code == 200
    return response.json()


def initialize_authority(c):
    campaign = create(c)
    assert analyze(c, campaign).status_code == 202
    native = pending(c, campaign)
    assert decide(c, native).status_code == 200
    history = c.bundle.adlift_campaign_repository.for_tenant(TENANT).budget_history(campaign["campaign_id"])[0]
    assert history.authorizations[-1].binding.before_amount is None
    assert not c.bundle.feature_flag_registry.is_enabled("high_risk.adlift.approve")
    return campaign, native


def business(c):
    return {table: [dict(r) for r in c.bundle.engine.query("SELECT * FROM " + table)]
            for table in ("durable_documents", "durable_jobs", "durable_outbox_events")}


def test_initial_allocation_requires_original_native_approval_not_authorized_zero(tmp_path, monkeypatch):
    with case(tmp_path / "initial.sqlite3", monkeypatch) as c:
        campaign = create(c)
        assert open_native(c, campaign).status_code == 503
        assert c.bundle.intervention_repository.list_all() == []
        assert analyze(c, campaign).status_code == 202
        native = pending(c, campaign)
        assert decide(c, native, actor="ops").status_code == 403
        assert c.bundle.adlift_campaign_repository.for_tenant(TENANT).budget_history(campaign["campaign_id"]) is None
        assert decide(c, native).status_code == 200
        current = c.client.get(BASE + "/" + campaign["campaign_id"], headers=_headers("marketer")).json()
        assert current["status"] == "APPROVED" and current["authorized_budget_revision"] == 1
        assert current["authorized_budget"]["binding"]["before_amount"] is None
        assert current["authorized_budget"]["external_execution_acknowledged"] is False
        events = c.bundle.engine.query("SELECT * FROM durable_outbox_events WHERE event_type = ?",
                                      ("adlift.campaign_approved",))
        assert len(events) == 1 and events[0]["published_at"] is None
        assert not c.bundle.feature_flag_registry.is_enabled("high_risk.adlift.approve")


def test_actual_increase_default_disabled_then_two_verified_admins_and_disable_before_dispatch(tmp_path, monkeypatch):
    with case(tmp_path / "increase.sqlite3", monkeypatch) as c:
        campaign, _ = initialize_authority(c)
        revised = revise(c, campaign, "2000.75")
        assert revised["budget_proposals"][-1]["authorized_before_amount"] == "1000.25"
        assert analyze(c, revised).status_code == 202
        native = pending(c, revised)
        before = business(c)
        assert decide(c, native).status_code == 403
        assert business(c) == before
        enable(c)
        approved = decide(c, native)
        assert approved.status_code == 200
        assert c.client.post(ADMIN + "/disable", json=PROOF, headers=_headers("admin-a")).status_code == 200
        before = business(c)
        assert lifecycle(c, native, "execute", actor="executor").status_code == 403
        assert business(c) == before and c.bundle.intervention_repository.get(native).execution is None
        # Replay the actual original body/context/headers and idempotency key.
        # A new decision using a freshly read context would be a new command.
        replay = c.client.post(str(approved.request.url), content=approved.request.content,
                               headers=approved.request.headers)
        assert replay.status_code == 200 and replay.json()["idempotent_replay"] is True
        original = c.bundle.intervention_repository.get(native)
        assert original.approval.approved and original.status.value == "APPROVED"


@pytest.mark.parametrize("amount", ["1000.25", "999.99"])
def test_equal_or_decreased_budget_has_no_increase_gate(tmp_path, monkeypatch, amount):
    with case(tmp_path / "non-increase.sqlite3", monkeypatch) as c:
        campaign, _ = initialize_authority(c)
        revised = revise(c, campaign, amount)
        assert analyze(c, revised).status_code == 202
        native = pending(c, revised)
        assert decide(c, native).status_code == 200
        assert lifecycle(c, native, "execute", actor="executor").status_code == 200
        assert not c.bundle.feature_flag_registry.is_enabled("high_risk.adlift.approve")
        events = c.bundle.engine.query("SELECT * FROM durable_outbox_events WHERE event_type = ?",
                                      ("adlift.campaign_execution_requested",))
        assert len(events) == 1 and events[0]["published_at"] is None
        assert c.bundle.engine.query("SELECT * FROM durable_outbox_events WHERE event_type = ?",
                                     ("adlift.campaign_started",)) == []


def test_one_store_cannot_authorize_multi_store_and_no_partial_scope_widening(tmp_path, monkeypatch):
    with case(tmp_path / "multi.sqlite3", monkeypatch) as c:
        campaign = create(c, stores=(STORE, OTHER))
        assert analyze(c, campaign).status_code == 202
        before = business(c)
        assert open_native(c, campaign, actor="restricted-ops").status_code == 403
        assert business(c) == before
        first = pending(c, campaign, STORE)
        assert decide(c, first).status_code == 200
        assert c.bundle.adlift_campaign_repository.for_tenant(TENANT).budget_history(campaign["campaign_id"]) is None
        assert lifecycle(c, first, "execute", actor="executor").status_code == 409
        second = pending(c, campaign, OTHER)
        assert decide(c, second).status_code == 200
        history = c.bundle.adlift_campaign_repository.for_tenant(TENANT).budget_history(campaign["campaign_id"])[0]
        assert {r[0] for r in history.authorizations[0].native_approval_references} == {STORE, OTHER}


@pytest.mark.parametrize("bad", ["missing", "RAM", "other-engine", "queue-owner"])
def test_true_increase_rejects_missing_RAM_wrong_engine_or_queue_owner_without_business_write(tmp_path, monkeypatch, bad):
    with case(tmp_path / "owners.sqlite3", monkeypatch) as c:
        campaign, _ = initialize_authority(c)
        revised = revise(c, campaign, "2000.75")
        assert analyze(c, revised).status_code == 202
        native = pending(c, revised)
        with case(tmp_path / "other.sqlite3", monkeypatch) as foreign:
            if bad == "missing":
                c.authority.flags = None
            elif bad == "RAM":
                policy = FeatureFlagRegistry(dict(DEFAULT_FLAGS))
                policy.add_approval("high_risk.adlift.approve", "synthetic-a")
                policy.add_approval("high_risk.adlift.approve", "synthetic-b")
                policy.enable("high_risk.adlift.approve")
                c.authority.flags = policy
            elif bad == "other-engine":
                c.authority.flags = foreign.bundle.feature_flag_registry
            else:
                c.bundle.job_queue._flags = foreign.bundle.feature_flag_registry
            before = business(c)
            assert decide(c, native).status_code == 503
            assert business(c) == before and c.bundle.intervention_repository.get(native).approval is None


def test_superseded_configuration_and_caller_forged_binding_never_authorize(tmp_path, monkeypatch):
    with case(tmp_path / "stale.sqlite3", monkeypatch) as c:
        campaign = create(c)
        assert analyze(c, campaign).status_code == 202
        assert open_native(c, campaign, action_spec={"campaign_budget_binding": {"approved": True}}).status_code == 422
        native = pending(c, campaign)
        revise(c, campaign, "2000.75")
        before = business(c)
        assert decide(c, native).status_code == 409
        assert business(c) == before


def test_actual_audit_failure_rolls_back_native_budget_receipt_and_business_outbox(tmp_path, monkeypatch):
    with case(tmp_path / "audit.sqlite3", monkeypatch) as c:
        campaign = create(c)
        assert analyze(c, campaign).status_code == 202
        native = pending(c, campaign)
        original = c.bundle.audit_log.record

        def fail(event):
            if event.event_type == "adlift.campaign_approved.v1":
                raise RuntimeError("synthetic campaign approval audit failure")
            return original(event)

        monkeypatch.setattr(c.bundle.audit_log, "record", fail)
        before = business(c)
        assert decide(c, native).status_code == 503
        assert business(c) == before and c.bundle.intervention_repository.get(native).approval is None


@pytest.mark.parametrize("changes", [{"declared_data_version": "foreign"},
    {"campaign_configuration_revision": True}, {"treatment_store_ids": [OTHER]}])
def test_actual_analysis_must_join_same_tenant_configuration_version_and_stores(tmp_path, monkeypatch, changes):
    with case(tmp_path / "analysis.sqlite3", monkeypatch) as c:
        campaign = create(c)
        before = business(c)
        assert analyze(c, campaign, changes=changes).status_code == 409
        assert business(c) == before
        assert c.bundle.adlift_campaign_repository.for_tenant(TENANT).readiness(campaign["campaign_id"], 1) is None


def test_new_actual_report_replaces_readiness_and_old_bound_case_cannot_approve(tmp_path, monkeypatch):
    with case(tmp_path / "report-race.sqlite3", monkeypatch) as c:
        campaign = create(c)
        assert analyze(c, campaign).status_code == 202
        native = pending(c, campaign)
        assert analyze(c, campaign, key="actual-report-refresh").status_code == 202
        before = business(c)
        assert decide(c, native).status_code == 409
        assert business(c) == before and c.bundle.intervention_repository.get(native).approval is None


def test_two_real_engine_native_decisions_commit_one_complete_authority_and_reopen(tmp_path, monkeypatch):
    path = tmp_path / "two-native-writers.sqlite3"
    with case(path, monkeypatch) as left:
        campaign = create(left, stores=(STORE, OTHER))
        assert analyze(left, campaign).status_code == 202
        native = [pending(left, campaign, store) for store in (STORE, OTHER)]
        with case(path, monkeypatch, seed=False) as right:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(decide, owner, identity) for owner, identity in zip((left, right), native, strict=True)]
                responses = [future.result() for future in futures]
            assert [r.status_code for r in responses] == [200, 200]
        history = left.bundle.adlift_campaign_repository.for_tenant(TENANT).budget_history(campaign["campaign_id"])[0]
        assert history.version == 1 and len(history.authorizations) == 1
        assert {r[1] for r in history.authorizations[0].native_approval_references} == set(native)
    with case(path, monkeypatch, seed=False) as reopened:
        response = reopened.client.get(BASE + "/" + campaign["campaign_id"], headers=_headers("marketer"))
        assert response.status_code == 200 and response.json()["authorized_budget_revision"] == 1
        assert response.json()["authorized_budget"]["external_execution_acknowledged"] is False


def test_original_unbound_analysis_latest_report_does_not_establish_canonical_readiness(tmp_path, monkeypatch):
    with case(tmp_path / "unbound-report.sqlite3", monkeypatch) as c:
        campaign = create(c)
        assert analyze(c, campaign, canonical=False).status_code == 202
        assert c.bundle.adlift_repository.latest_for_campaign(campaign["campaign_id"]) is not None
        before = business(c)
        assert open_native(c, campaign).status_code == 503
        assert business(c) == before and c.bundle.intervention_repository.list_all() == []


def test_new_bound_native_rejects_invalid_correlation_without_creating_domain_state(tmp_path, monkeypatch):
    with case(tmp_path / "correlation.sqlite3", monkeypatch) as c:
        campaign = create(c)
        assert analyze(c, campaign).status_code == 202
        # Unlike original unbound commands, the canonical DomainEvent writer
        # requires the actual UUID correlation; no replacement ID is invented.
        headers = signed_headers("ops", correlation_id="invalid-correlation")
        before = business(c)
        response = c.client.post("/interventions", json={"store_id": STORE, "kind": "AD_CAMPAIGN",
            "trigger_ref": "synthetic", "expected_outcome": "Synthetic measured later",
            "planned_start": "2026-05-11T00:00:00Z", "planned_end": "2026-05-20T23:59:59Z",
            "created_by": "ops", "action_spec": {"campaign_budget_proposal": {
                "campaign_id": campaign["campaign_id"], "proposal_revision": 1}}},
            headers={**headers, "Idempotency-Key": "bad-correlation"})
        assert response.status_code == 422 and business(c) == before


@pytest.mark.parametrize("change,expected", [("omit-selector", 409), ("body", 409),
    ("subject", 403), ("store-grant", 403), ("brand-grant", 403), ("region-grant", 403), ("module-grant", 403), ("clearance", 403), ("control-only-revoked", 403)])
def test_bound_job_replay_uses_server_targets_and_original_request_without_recompute(
        tmp_path, monkeypatch, change, expected):
    import json

    with case(tmp_path / "cached-replay.sqlite3", monkeypatch) as c:
        campaign = create(c)
        first = analyze(c, campaign)
        assert first.status_code == 202
        body = json.loads(first.request.content)
        actor = "marketer"
        if change == "omit-selector":
            body["campaigns"][0].pop("campaign_configuration_revision")
        elif change == "body":
            body["campaigns"][0]["ad_spend"] += 1
        elif change == "subject":
            actor = "marketer-b"
        elif change == "clearance":
            c.mappings[actor]["scope"]["clearance"] = "INTERNAL"
        elif change == "control-only-revoked":
            c.mappings[actor]["scope"]["store_ids"] = [STORE]
        else:
            field = {"store-grant": "store_ids", "brand-grant": "brand_ids",
                "region-grant": "region_ids", "module-grant": "modules"}[change]
            c.mappings[actor]["scope"][field] = ["outside-current-canonical-target"]
        before = business(c)
        def forbidden_compute(*args, **kwargs):
            raise AssertionError("Denied bound replay recomputed readiness")
        monkeypatch.setattr(c.authority, "run_bound_analysis", forbidden_compute)
        response = c.client.post("/adlift/incrementality-jobs", json=body,
            headers={**_headers(actor), "Idempotency-Key": "analysis-1"})
        assert response.status_code == expected
        assert business(c) == before


@pytest.mark.parametrize("change,expected", [("store-grant", 403), ("brand-grant", 403),
    ("region-grant", 403), ("module-grant", 403), ("clearance", 403), ("control-only-revoked", 403), ("foreign-tenant", 404), ("configuration", 409)])
def test_bound_job_GET_rechecks_current_stored_targets_and_configuration_without_ready_write(
        tmp_path, monkeypatch, change, expected):
    with case(tmp_path / "cached-read.sqlite3", monkeypatch) as c:
        campaign = create(c)
        first = analyze(c, campaign)
        assert first.status_code == 202
        if change == "configuration":
            revise(c, campaign, "1500.25")
        elif change == "foreign-tenant":
            c.mappings["marketer"]["scope"]["tenant_id"] = "6ba4fc89-e62c-413a-bdac-c4ad049ef83d"
        elif change == "clearance":
            c.mappings["marketer"]["scope"]["clearance"] = "INTERNAL"
        elif change == "control-only-revoked":
            c.mappings["marketer"]["scope"]["store_ids"] = [STORE]
        else:
            field = {"store-grant": "store_ids", "brand-grant": "brand_ids",
                "region-grant": "region_ids", "module-grant": "modules"}[change]
            c.mappings["marketer"]["scope"][field] = ["outside-current-canonical-target"]
        before = business(c)
        def forbidden_compute(*args, **kwargs):
            raise AssertionError("Bound GET recomputed readiness")
        monkeypatch.setattr(c.authority, "run_bound_analysis", forbidden_compute)
        result = c.client.get("/adlift/incrementality-jobs/" + first.json()["job_id"], headers=_headers("marketer"))
        assert result.status_code == expected
        assert business(c) == before


def test_bound_job_exact_replay_and_authorized_GET_keep_actual_report_without_internal_owner_data(tmp_path, monkeypatch):
    import json

    with case(tmp_path / "cached-positive.sqlite3", monkeypatch) as c:
        campaign = create(c)
        first = analyze(c, campaign)
        assert first.status_code == 202 and "_canonical_adlift_bound_job" not in first.json()
        before = business(c)
        def forbidden_compute(*args, **kwargs):
            raise AssertionError("Exact bound receipt replay recomputed report")
        monkeypatch.setattr(c.authority, "run_bound_analysis", forbidden_compute)
        replay = c.client.post("/adlift/incrementality-jobs", json=json.loads(first.request.content),
            headers={**_headers("marketer"), "Idempotency-Key": "analysis-1"})
        assert replay.status_code == 202 and replay.json()["created"] is False
        assert replay.json()["job_id"] == first.json()["job_id"]
        assert replay.json()["reports"] == first.json()["reports"]
        read = c.client.get("/adlift/incrementality-jobs/" + first.json()["job_id"], headers=_headers("marketer-b"))
        assert read.status_code == 200 and read.json()["reports"] == first.json()["reports"]
        assert "_canonical_adlift_bound_job" not in read.json() and business(c) == before


@pytest.mark.parametrize("change", ["foreign-tenant", "store", "brand", "region", "module", "clearance", "control-only-revoked"])
def test_canonical_report_list_and_detail_revalidate_current_actual_targets_without_recompute(
        tmp_path, monkeypatch, change):
    with case(tmp_path / "report-visibility.sqlite3", monkeypatch) as c:
        campaign = create(c)
        analysis = analyze(c, campaign)
        assert analysis.status_code == 202
        visible = c.client.get("/adlift/reports", headers=_headers("marketer"))
        assert visible.status_code == 200 and visible.json()["count"] == 1
        detail = c.client.get("/adlift/reports/" + campaign["campaign_id"], headers=_headers("marketer"))
        assert detail.status_code == 200 and detail.json()["report_id"] == analysis.json()["reports"][0]["report_id"]
        if change == "foreign-tenant":
            c.mappings["marketer"]["scope"]["tenant_id"] = "6ba4fc89-e62c-413a-bdac-c4ad049ef83d"
        elif change == "clearance":
            c.mappings["marketer"]["scope"]["clearance"] = "INTERNAL"
        elif change == "control-only-revoked":
            c.mappings["marketer"]["scope"]["store_ids"] = [STORE]
        else:
            field = {"store": "store_ids", "brand": "brand_ids", "region": "region_ids", "module": "modules"}[change]
            c.mappings["marketer"]["scope"][field] = ["outside-current-canonical-target"]
        before = business(c)
        def forbidden_compute(*args, **kwargs):
            raise AssertionError("Report read recomputed canonical readiness")
        monkeypatch.setattr(c.authority, "run_bound_analysis", forbidden_compute)
        hidden = c.client.get("/adlift/reports", headers=_headers("marketer"))
        absent = c.client.get("/adlift/reports/" + campaign["campaign_id"], headers=_headers("marketer"))
        assert hidden.status_code == 200 and hidden.json() == {"items": [], "count": 0}
        assert absent.status_code == 404
        # Installed canonical error handlers retain legacy detail and add the
        # documented envelope. Neither response branch may expose report data.
        missing = absent.json()
        occurred_at = missing["error"]["occurred_at"]
        assert missing == {"detail": "campaign report not found", "error": {
            "code": "not_found", "message": "campaign report not found",
            "next_action": "Verify the identifier; the resource may have been removed.",
            "occurred_at": occurred_at, "details": [], "correlation_id": CORRELATION}}
        assert datetime.fromisoformat(occurred_at.replace("Z", "+00:00")).tzinfo is not None
        assert business(c) == before


def test_canonical_report_reads_never_fall_back_to_unbound_global_latest(tmp_path, monkeypatch):
    with case(tmp_path / "report-owner.sqlite3", monkeypatch) as c:
        campaign = create(c)
        actual = analyze(c, campaign)
        assert actual.status_code == 202
        report_id = actual.json()["reports"][0]["report_id"]
        unbound = analyze(c, campaign, canonical=False, key="unbound-not-current-canonical")
        assert unbound.status_code == 202 and unbound.json()["reports"][0]["report_id"] != report_id
        assert c.bundle.adlift_repository.latest_for_campaign(campaign["campaign_id"]).report_id != report_id
        before = business(c)
        listed = c.client.get("/adlift/reports", headers=_headers("marketer"))
        detail = c.client.get("/adlift/reports/" + campaign["campaign_id"], headers=_headers("marketer"))
        assert listed.status_code == 200 and listed.json()["items"][0]["report_id"] == report_id
        assert detail.status_code == 200 and detail.json()["report_id"] == report_id
        assert business(c) == before
        revise(c, campaign, "1500.25")
        before = business(c)
        assert c.client.get("/adlift/reports", headers=_headers("marketer")).json() == {"items": [], "count": 0}
        assert c.client.get("/adlift/reports/" + campaign["campaign_id"], headers=_headers("marketer")).status_code == 404
        assert business(c) == before


def test_original_unbound_reports_remain_only_in_explicit_no_canonical_owner_POC_router(tmp_path, monkeypatch):
    from modules.adlift.infrastructure import InMemoryAdLiftRepository
    from shared.audit import InMemoryAuditLog

    with case(tmp_path / "original-poc.sqlite3", monkeypatch) as c:
        campaign = create(c)
        assert analyze(c, campaign).status_code == 202
        saved = c.bundle.adlift_repository.latest_for_campaign(campaign["campaign_id"])
        legacy = InMemoryAdLiftRepository()
        legacy.save_report(saved)
        app = FastAPI()
        app.include_router(create_adlift_router(repository=legacy, audit_log=InMemoryAuditLog(), runtime_mode="poc"))
        with TestClient(app) as client:
            result = client.get("/adlift/reports", headers=_headers("marketer"))
            assert result.status_code == 200 and result.json()["count"] == 1
            detail = client.get("/adlift/reports/" + campaign["campaign_id"], headers=_headers("marketer"))
            assert detail.status_code == 200 and detail.json()["campaign_id"] == campaign["campaign_id"]
