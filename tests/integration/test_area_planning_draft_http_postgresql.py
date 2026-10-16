"""Normal API, signed current principals and actual PostgreSQL proposal writes.

Declared HeatZone/candidate fixtures exercise canonical-parent structure and
authorization only. They are not production model, source, financial, native
handoff, real-user session, business approval or human acceptance evidence.
"""
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import h3
import pytest
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.area_planning_drafts import AreaPlanningDraftResponse
from apps.api.oday_api.main import create_app
from apps.api.oday_api.security import dependencies
from modules.heatzone.domain.scoring import HeatZoneScoreResult, HeatZoneState
from modules.heatzone.workers import HeatZoneBatchScoreResult
from modules.netplan.infrastructure.area_planning_drafts import DurableAreaPlanningDraftRepository
from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary
from shared.infrastructure.persistence import PostgresDocumentStore
from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
from shared.infrastructure.persistence.operator_domains import (
    DurableOperatorDomainStateRepository,
    TenantScopedDocumentStore,
)
from shared.infrastructure.persistence.repositories import DurableNetPlanRepository
from tests.integration.test_intake_registry_calendar_postgresql import _bundle, _upgrade
from tests.security.test_feature_flag_admin_authorization import ISSUER, SIGNING_KEY, _headers

pytestmark = pytest.mark.requires_live_env
BASE = "/api/v1/operator/area-planning-drafts"


@pytest.fixture
def case(intake_blank_db, monkeypatch, tmp_path):
    _upgrade(intake_blank_db, "head")
    apply_upgrade_to_database(intake_blank_db.url())
    monkeypatch.setenv("ODP_AUDIT_WORM_SINK_URI", (tmp_path / "owned-quarterly-worm").as_uri())
    bundle = _bundle(intake_blank_db, monkeypatch)
    tenant, zone, other_zone, listing, candidate = [str(uuid4()) for _ in range(5)]
    actors = {name: str(uuid4()) for name in ("staff", "manager", "executive", "viewer", "admin", "foreign")}
    mappings = {
        actors[name]: {"roles": [role], "scope": {
            "tenant_id": str(uuid4()) if name == "foreign" else tenant,
            "modules": ["operator_console"],
        }}
        for name, role in (
            ("staff", "expansion_user"), ("manager", "site_reviewer"), ("executive", "executive"),
            ("viewer", "operator_viewer"), ("admin", "platform_admin"), ("foreign", "expansion_user"),
        )
    }
    boundary = AuthenticationBoundary(AuthBoundaryConfig(
        issuer=ISSUER, audiences=frozenset({"unit-feature-api"}),
        signing_keys={SIGNING_KEY.kid: SIGNING_KEY}, principal_mappings=mappings,
    ), audit_log=bundle.audit_log)
    monkeypatch.setattr(dependencies, "default_boundary", lambda: boundary)
    monkeypatch.setattr("apps.api.app.routes._native_request_owner.default_boundary", lambda: boundary)
    documents = PostgresDocumentStore(bundle.engine)
    native_state = DurableOperatorDomainStateRepository(documents, "network-listings")
    snapshot = {
        "listings": [{"id": listing, "tenantId": tenant, "heatZoneId": zone}],
        "candidates": [{"id": candidate, "listingId": listing, "tenantId": tenant, "heatZoneId": zone}],
    }
    native_state.save(tenant, snapshot)
    now = datetime.now(UTC)
    cells = ["8929a1d4d67ffff", next(cell for cell in sorted(h3.grid_disk("8929a1d4d67ffff", 1))
                                     if cell != "8929a1d4d67ffff")]
    scores = tuple(HeatZoneScoreResult(
        heat_zone_id=identifier, h3_index=cells[rank - 1], h3_resolution=9,
        score=60, priority_rank=rank, unmet_demand_score=.6, format_fit_score=.6,
        cannibalization_risk_score=.2, rent_feasibility_score=.6,
        listing_availability_score=.6, confidence=.6, state=HeatZoneState.STILL_EXPANDABLE,
        feature_snapshot_time=now, prediction_origin_time=now, last_scored_at=now,
        model_version="declared-engineering-area-proposal-only", feature_version="declared-engineering-only",
        source_snapshot_ids=("declared-engineering-heatzone-source",),
        reasons=("Declared engineering fixture; no production model claim",),
        admin_city="Synthetic", admin_district="Owned fixture",
    ) for rank, identifier in enumerate((zone, other_zone), 1))
    bundle.heatzone_store_for_tenant(tenant).put(HeatZoneBatchScoreResult(
        job_id=str(uuid4()), tenant_id=tenant, status="succeeded", scores=scores, completed_at=now,
    ))
    scoped = TenantScopedDocumentStore(documents, tenant)
    try:
        app = create_app(persistence=bundle)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield SimpleNamespace(
                client=client, bundle=bundle, tenant=tenant, zone=zone, other_zone=other_zone,
                candidate=candidate, actors=actors, mappings=mappings, boundary=boundary,
                documents=scoped, repository=DurableAreaPlanningDraftRepository(scoped),
                native=DurableNetPlanRepository(scoped), native_state=native_state, snapshot=snapshot,
            )
    finally:
        bundle.engine.close()


def headers(case, actor="staff", *, key="synthetic-quarterly-default-key"):
    persona = {"staff": "expansion-staff", "foreign": "expansion-staff", "manager": "expansion-manager",
               "executive": "expansion-manager", "viewer": "pm-audit", "admin": "platform-admin"}[actor]
    return {**_headers(case.actors[actor], correlation_id=key),
            "x-operator-role": persona, "Idempotency-Key": key}


def body(case, **changes):
    return {"name": "Declared human area planning intent", "heatZoneId": case.zone,
            "quarter": None, "budget": None, "candidateIds": [], **changes}


def create(case, *, actor="staff", key="synthetic-quarterly-create-key", **changes):
    result = case.client.post(BASE, headers=headers(case, actor, key=key), json=body(case, **changes))
    assert result.status_code == 201, result.text
    return result.json()


def test_normal_api_records_human_intent_without_defaults_or_native_permissions(case):
    result = create(case)
    draft = result["draft"]
    assert draft["tenantId"] == case.tenant
    assert draft["createdBy"] == case.actors["staff"]
    assert draft["quarter"] is draft["budget"] is draft["nativeScenarioId"] is None
    assert draft["candidateIds"] == [] and draft["status"] == "DRAFT"
    assert result["auditEventIds"] and result["receiptId"] and not result["replayed"]
    stored = case.repository.get(draft["draftId"]).to_dict()
    assert AreaPlanningDraftResponse.model_validate(stored).model_dump(mode="json") == draft
    assert case.native.list_scenarios() == []
    native = case.client.post("/api/v1/netplan/scenarios", headers=headers(case), json={
        "tenant_id": case.tenant, "scenario_name": "No permission elevation",
        "planning_horizon": "2026Q4", "constraints": {"max_budget": 100},
        "existing_stores": [], "candidate_sites": [],
    })
    assert native.status_code == 403, native.text
    assert case.native.list_scenarios() == []


def test_current_authorized_area_readers_can_reopen_the_saved_planning_intent(case):
    draft = create(case)["draft"]
    for actor in ("staff", "manager", "executive"):
        loaded = case.client.get(BASE + "/" + draft["draftId"], headers=headers(case, actor))
        assert loaded.status_code == 200 and loaded.json() == draft
        listing = case.client.get(BASE, params={"heatZoneId": case.zone}, headers=headers(case, actor))
        assert listing.status_code == 200 and listing.json()["items"] == [draft]


def test_save_replay_and_optimistic_version_conflict_do_not_duplicate_or_lose_edits(case):
    first = create(case)
    replay = create(case)
    assert replay["replayed"] and replay["receiptId"] == first["receiptId"]
    assert len(case.repository.list_all()) == 1
    identity = first["draft"]["draftId"]
    updated_body = body(case, quarter="2026Q4", budget=0, candidateIds=[case.candidate], expectedVersion=1)
    key = "synthetic-quarterly-update-key"
    changed = case.client.put(BASE + "/" + identity, json=updated_body, headers=headers(case, key=key))
    assert changed.status_code == 200, changed.text
    assert changed.json()["draft"]["version"] == 2
    assert changed.json()["draft"]["budget"] == 0
    repeated = case.client.put(BASE + "/" + identity, json=updated_body, headers=headers(case, key=key))
    assert repeated.status_code == 200 and repeated.json()["replayed"]
    assert repeated.json()["receiptId"] == changed.json()["receiptId"]
    stale = case.client.put(BASE + "/" + identity, json=updated_body,
                           headers=headers(case, key="synthetic-stale-quarterly-update"))
    assert stale.status_code == 409, stale.text
    assert case.repository.get(identity).version == 2
    assert case.native.list_scenarios() == []


@pytest.mark.parametrize("actor", ["viewer", "admin"])
def test_viewer_and_platform_admin_cannot_gain_planning_draft_access_from_persona(case, actor):
    draft = create(case)["draft"]
    for method, path, data in (
        ("GET", BASE + "/" + draft["draftId"], None), ("POST", BASE, body(case)),
        ("PUT", BASE + "/" + draft["draftId"], body(case, expectedVersion=1)),
    ):
        response = case.client.request(method, path, headers=headers(case, actor), json=data)
        assert response.status_code == 403, response.text
    assert case.repository.get(draft["draftId"]).version == 1


def test_revoked_candidate_scope_blocks_reopen_and_the_old_successful_command_replay(case):
    first = create(case, quarter="2026Q4", budget=100, candidateIds=[case.candidate])
    changed = deepcopy(case.snapshot)
    changed["candidates"] = []
    case.native_state.save(case.tenant, changed)
    assert case.client.get(BASE + "/" + first["draft"]["draftId"], headers=headers(case)).status_code == 404
    replay = case.client.post(BASE, headers=headers(case, key="synthetic-quarterly-create-key"),
                              json=body(case, quarter="2026Q4", budget=100, candidateIds=[case.candidate]))
    assert replay.status_code == 404, replay.text
    listing = case.client.get(BASE, params={"heatZoneId": case.zone}, headers=headers(case))
    assert listing.status_code == 200 and listing.json()["items"] == []


def test_candidate_from_another_zone_and_caller_owned_scope_fields_are_refused(case):
    wrong_zone = case.client.post(BASE, json=body(case, heatZoneId=case.other_zone, candidateIds=[case.candidate]),
                                  headers=headers(case))
    assert wrong_zone.status_code == 404, wrong_zone.text
    for forged in ({"tenantId": str(uuid4())}, {"createdBy": case.actors["executive"]},
                   {"financialDocuments": {}}, {"nativeScenarioId": "forged-native"}):
        response = case.client.post(BASE, json={**body(case), **forged}, headers=headers(case))
        assert response.status_code == 422, response.text
    assert case.repository.list_all() == [] and case.native.list_scenarios() == []


def test_foreign_tenant_and_newly_restricted_zone_cannot_read_an_existing_draft(case):
    draft = create(case)["draft"]
    assert case.client.get(BASE + "/" + draft["draftId"], headers=headers(case, "foreign")).status_code == 404
    case.mappings[case.actors["staff"]]["scope"]["heat_zone_ids"] = [case.other_zone]
    response = case.client.get(BASE + "/" + draft["draftId"], headers=headers(case))
    assert response.status_code == 404, response.text


def test_failure_recording_the_command_audit_rolls_back_proposal_and_its_receipt(case, monkeypatch):
    actual_record = case.bundle.audit_log.record

    def refuse_command_audit(event):
        if event.event_type == "netplan.area_draft.create":
            raise RuntimeError("Declared engineering audit failure")
        return actual_record(event)

    monkeypatch.setattr(case.bundle.audit_log, "record", refuse_command_audit)
    response = case.client.post(BASE, json=body(case), headers=headers(case))
    assert response.status_code == 500, response.text
    assert case.repository.list_all() == []
    rows = case.bundle.engine.query("SELECT job_id FROM durable_jobs WHERE job_type = ?",
                                    ("netplan-area-draft.command-receipt",))
    assert rows == []


@pytest.mark.parametrize("prefix", ["/jobs", "/api/v1/jobs"])
def test_private_command_receipts_cannot_be_read_retried_or_forged_through_generic_jobs(case, prefix):
    first = create(case)
    identity = first["receiptId"]
    assert case.client.get(prefix + "/" + identity).status_code == 401
    for actor in ("staff", "executive", "admin"):
        assert case.client.get(prefix + "/" + identity, headers=headers(case, actor)).status_code == 404
        assert case.client.post(prefix + "/" + identity + "/retries", headers=headers(case, actor)).status_code == 404
    forged = case.client.post(prefix, json={"job_type": "netplan-area-draft.command-receipt", "payload": {}},
                               headers=headers(case))
    assert forged.status_code == 400, forged.text
    reserved = "command:v1:netplan-area-draft:declared-forgery-control"
    for location in ("header", "body"):
        data = {"job_type": "forecast", "payload": {"tenant_id": case.tenant}}
        hdrs = headers(case, key=reserved if location == "header" else "synthetic-ordinary-queue-key")
        if location == "body":
            data["idempotency_key"] = reserved
        blocked = case.client.post(prefix, json=data, headers=hdrs)
        assert blocked.status_code == 422, blocked.text
    assert case.bundle.job_queue.get_by_idempotency_key(reserved) is None
