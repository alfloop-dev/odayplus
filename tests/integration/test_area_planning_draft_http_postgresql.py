"""Normal API, signed current principals and actual PostgreSQL proposal writes.

Declared HeatZone/candidate fixtures exercise canonical-parent structure,
authorization and the explicit ID-only native DRAFT link in one transaction.
They are not production model, source, financial, solve, real-user session,
business approval or human acceptance evidence.
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


NATIVE = "/api/v1/netplan/scenarios"


def complete(case, **changes):
    return create(case, quarter="2027Q1", budget=2500000, candidateIds=[case.candidate], **changes)["draft"]


def materialize(case, draft, *, actor="executive", key="synthetic-quarterly-materialize-key", version=None):
    return case.client.post(
        BASE + "/" + draft["draftId"] + "/materialize", headers=headers(case, actor, key=key),
        json={"expectedVersion": draft["version"] if version is None else version},
    )


def receipt_count(case):
    return len(case.bundle.engine.query("SELECT job_id FROM durable_jobs WHERE job_type = ?",
                                        ("netplan-area-draft.command-receipt",)))


def test_native_creator_materializes_complete_draft_and_reopens_the_original_native_draft(case):
    draft = complete(case)
    response = materialize(case, draft)
    assert response.status_code == 200, response.text
    result = response.json()
    scenario_id = DurableAreaPlanningDraftRepository.scenario_id_for_draft(draft["draftId"])
    linked = result["draft"]
    assert linked["status"] == "MATERIALIZED" and linked["version"] == 2
    assert linked["nativeScenarioId"] == scenario_id
    assert linked["materializedBy"] == case.actors["executive"] and linked["createdBy"] == case.actors["staff"]
    assert len(result["auditEventIds"]) == 2 and not result["replayed"]
    reopened = case.client.get(BASE + "/" + draft["draftId"], headers=headers(case, "executive"))
    assert reopened.status_code == 200 and reopened.json() == linked
    native = case.client.get(NATIVE + "/" + scenario_id, headers=headers(case, "executive"))
    assert native.status_code == 200, native.text
    detail = native.json()
    assert detail["tenant_id"] == case.tenant and detail["status"] == "draft"
    assert detail["scenario_name"] == draft["name"] and detail["planning_horizon"] == "2027Q1"
    assert detail["constraints"]["max_budget"] == 2500000
    assert list(detail["options_by_entity"]) == [case.candidate]
    # ID-only original mapping: no source, model, currency or GM is invented.
    assert all(option["source_snapshot_ids"] == [] for option in detail["options_by_entity"][case.candidate])
    assert detail["solve"] is None and detail["approvals"] == [] and detail["status_history"] == []
    assert detail["execution"] is None and detail["outcome"] is None
    stored = case.native.get_scenario(scenario_id)
    assert stored.tenant_id == case.tenant and [row.scenario_id for row in case.native.list_scenarios()] == [scenario_id]
    events = {event.event_type: event for event in case.bundle.audit_log.list_events(tenant_id=case.tenant)}
    assert events["netplan.scenario_created.v1"].actor == case.actors["executive"]
    assert events["netplan.area_draft.materialize"].metadata["native_scenario_id"] == scenario_id


def test_same_receipt_replay_never_creates_again_or_resets_an_advanced_native_draft(case):
    draft = complete(case)
    first = materialize(case, draft)
    assert first.status_code == 200, first.text
    scenario_id = first.json()["draft"]["nativeScenarioId"]
    advanced = case.client.put(NATIVE + "/" + scenario_id, headers=headers(case, "executive", key="synthetic-native-edit"),
                               json={"scenario_name": "Native edit after materialization"})
    assert advanced.status_code == 200, advanced.text
    receipts = receipt_count(case)
    replay = materialize(case, draft)
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] and replay.json()["receiptId"] == first.json()["receiptId"]
    assert replay.json()["draft"] == first.json()["draft"]
    assert case.native.get_scenario(scenario_id).scenario_name == "Native edit after materialization"
    again = materialize(case, draft, key="synthetic-second-materialize-key")
    assert again.status_code == 409, again.text
    assert receipt_count(case) == receipts
    assert len(case.native.list_scenarios()) == 1
    assert case.repository.get(draft["draftId"]).version == 2


def test_planning_only_staff_cannot_use_area_update_to_create_native_scenario(case):
    draft = complete(case)
    receipts = receipt_count(case)
    response = materialize(case, draft, actor="staff")
    assert response.status_code == 403, response.text
    assert case.native.list_scenarios() == [] and receipt_count(case) == receipts
    assert case.repository.get(draft["draftId"]).status.value == "DRAFT"


def test_incomplete_or_stale_draft_is_refused_before_any_native_creation(case):
    incomplete = create(case, quarter="2027Q1", budget=None, candidateIds=[case.candidate])["draft"]
    refused = materialize(case, incomplete)
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"] == "AREA_PLANNING_DRAFT_INPUTS_INCOMPLETE"
    draft = complete(case, key="synthetic-quarterly-second-create")
    stale = materialize(case, draft, key="synthetic-stale-materialize-key", version=2)
    assert stale.status_code == 409, stale.text
    forged = case.client.post(BASE + "/" + draft["draftId"] + "/materialize",
                              headers=headers(case, "executive", key="synthetic-forged-materialize"),
                              json={"expectedVersion": 1, "scenarioId": "forged-native"})
    assert forged.status_code == 422, forged.text
    assert case.native.list_scenarios() == []
    assert case.repository.get(draft["draftId"]).version == 1


def test_existing_native_id_keeps_original_refusal_and_rolls_back_the_link(case):
    draft = complete(case)
    scenario_id = DurableAreaPlanningDraftRepository.scenario_id_for_draft(draft["draftId"])
    squatter = case.client.post(NATIVE, headers=headers(case, "executive", key="synthetic-native-squatter"), json={
        "tenant_id": case.tenant, "scenario_name": "Pre-existing native", "planning_horizon": "2027Q1",
        "constraints": {"max_budget": 1}, "candidate_sites": [{"candidate_site_id": case.candidate}],
        "scenario_id": scenario_id,
    })
    assert squatter.status_code == 201, squatter.text
    receipts = receipt_count(case)
    response = materialize(case, draft)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "NATIVE_SCENARIO_ID_ALREADY_EXISTS"
    assert case.native.get_scenario(scenario_id).scenario_name == "Pre-existing native"
    assert case.repository.get(draft["draftId"]).status.value == "DRAFT" and receipt_count(case) == receipts


def test_revoked_candidate_blocks_materialize_and_its_replay(case):
    draft = complete(case)
    pending = complete(case, key="synthetic-revoked-second-create")
    first = materialize(case, draft)
    assert first.status_code == 200, first.text
    changed = deepcopy(case.snapshot)
    changed["candidates"] = []
    case.native_state.save(case.tenant, changed)
    replay = materialize(case, draft)
    assert replay.status_code == 404, replay.text
    # The refusal is decided inside the shared native transaction; its audit
    # is still recorded after that transaction rolls back.
    denials = [event for event in case.bundle.audit_log.list_events(tenant_id=case.tenant)
               if event.event_type == "netplan.area_draft.authorization_denied"]
    assert [event.metadata["reason"] for event in denials] == ["AREA_PLANNING_PARENT_NOT_FOUND"]
    refused = materialize(case, pending, key="synthetic-revoked-new-materialize")
    assert refused.status_code == 404, refused.text
    assert len(case.native.list_scenarios()) == 1
    assert case.repository.get(pending["draftId"]).status.value == "DRAFT"


@pytest.mark.parametrize("failing", ["netplan.scenario_created.v1", "netplan.area_draft.materialize"])
def test_audit_failure_rolls_back_native_draft_link_and_receipt_together(case, monkeypatch, failing):
    draft = complete(case)
    receipts = receipt_count(case)
    actual_record = case.bundle.audit_log.record

    def refuse(event):
        if event.event_type == failing:
            raise RuntimeError("Declared engineering audit failure")
        return actual_record(event)

    monkeypatch.setattr(case.bundle.audit_log, "record", refuse)
    response = materialize(case, draft)
    assert response.status_code == 500, response.text
    assert case.native.list_scenarios() == [] and receipt_count(case) == receipts
    current = case.repository.get(draft["draftId"])
    assert current.status.value == "DRAFT" and current.version == 1
    monkeypatch.setattr(case.bundle.audit_log, "record", actual_record)
    retried = materialize(case, draft)
    assert retried.status_code == 200 and not retried.json()["replayed"]
    assert len(case.native.list_scenarios()) == 1
