"""Owned SQLite/native Survey engineering controls, not formal UAT or media proof.

Canonical parent documents and identity records below are explicit synthetic test
inputs. HTTP uses the real signed boundary, factory, queue, audit and SQL owner.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.market_survey_assignments import create_canonical_survey_assignment_router
from modules.heatzone.domain.scoring import HeatZoneScoreResult, HeatZoneState
from modules.heatzone.workers.scoring_worker import HeatZoneBatchScoreResult
from modules.listing.domain.models import CandidateSiteDraft, ListingDedupKey
from modules.market_survey.application.assignment_owner import SurveyAssignmentNotFound
from modules.market_survey.domain.models import AssignmentStatus, TargetEntityKind
from modules.market_survey.infrastructure.assignment_factory import CanonicalSurveyFactory
from modules.market_survey.infrastructure.assignment_repository import (
    SurveyOwnerUnavailable,
    SurveyRevisionConflict,
)
from modules.opsboard.auth import (
    AuthBoundaryConfig,
    AuthenticationBoundary,
    SigningKey,
    encode_compact_jwt,
)
from shared.audit.worm import LocalAppendOnlyWormSink
from shared.auth import DataClassification, Principal, Role, Scope
from shared.domain import AddressLocation, CandidateSite, Listing
from shared.identity.store import Account
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.factory import _durable_bundle

TENANT = "10000000-0000-0000-0000-000000000001"
ACTOR = "20000000-0000-0000-0000-000000000001"
ASSIGNEE = "20000000-0000-0000-0000-000000000002"
CID = "30000000-0000-0000-0000-000000000001"
ROOT = Path(__file__).resolve().parents[2]
BASE = "/api/v1/market-survey"


def _business(engine):
    return {
        "documents": [tuple(row) for row in engine.query("SELECT * FROM durable_documents ORDER BY collection,doc_id")],
        "jobs": [tuple(row) for row in engine.query("SELECT * FROM durable_jobs ORDER BY job_id")],
        "events": [tuple(row) for row in engine.query(
            "SELECT * FROM durable_audit_events WHERE event_type LIKE 'market_survey.%' ORDER BY seq")],
    }


@pytest.fixture
def context(tmp_path):
    path = tmp_path / "owned-native-survey.sqlite3"
    bundle = _durable_bundle(path)
    scope = Scope(tenant_id=TENANT, modules=frozenset({"operator_console"}),
                  clearance=DataClassification.CONFIDENTIAL)
    principal = Principal(ACTOR, roles=frozenset({Role.EXPANSION_USER}), scope=scope)
    for subject in (ACTOR, ASSIGNEE):
        bundle.identity_store.save_account(Account(account_id=UUID(subject), tenant_id=UUID(TENANT),
            username=subject, email=subject + "@example.invalid", display_name="Synthetic field-task account"))
        bundle.identity_store.set_account_roles(subject, [Role.EXPANSION_USER])
        bundle.identity_store.set_account_scope(subject, scope)
    owner = bundle.listing_repository_for_tenant(TENANT)
    metadata = bundle.operator_intake_repository_for_tenant(TENANT)
    address = AddressLocation(address_id="address-owned", tenant_id=TENANT,
                              raw_address="Explicit synthetic candidate address")
    listing = Listing(listing_id="listing-owned", tenant_id=TENANT, address_id=address.address_id,
        source_id="synthetic-field-test", source_listing_id="one", rent_amount=1000, area_ping=20)
    owner.save_listing(listing, address, ListingDedupKey("synthetic", "one", "one", 1000, 20))
    owner.save_candidate(CandidateSiteDraft(listing=listing, address=address,
        candidate_site=CandidateSite(candidate_site_id="candidate-owned", listing_id=listing.listing_id,
                                     address_id=address.address_id), heat_zone_id="zone-owned"))
    metadata.save_listing_metadata(listing.listing_id, {"tenantId": TENANT, "brandId": "brand-owned",
        "regionId": "region-owned", "storeId": "store-owned", "assignedAreaId": "area-owned",
        "heatZoneId": "zone-owned"})
    key = SigningKey(kid="owned-survey", algorithm="HS256", secret=b"synthetic-isolated-survey-HMAC-key")
    boundary = AuthenticationBoundary(AuthBoundaryConfig(local_issuer="urn:odp:identity:local",
        local_audiences=frozenset({"owned-survey"}), local_signing_keys={key.kid: key},
        identity_store=bundle.identity_store, session_service=bundle.session_service), audit_log=bundle.audit_log)
    factory = CanonicalSurveyFactory(bundle, audit_log=bundle.audit_log, queue=bundle.job_queue)
    app = FastAPI()
    app.state.feature_flag_registry = bundle.feature_flag_registry
    app.state.survey_test_boundary = boundary

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        request.state.correlation_id = request.headers.get("x-correlation-id", CID)
        return await call_next(request)

    app.include_router(create_canonical_survey_assignment_router(factory=factory,
        audit_log=bundle.audit_log, boundary=boundary), prefix="/api/v1")

    def headers(subject=ACTOR):
        session = bundle.session_service.create_session(account_id=UUID(subject), provider="local_password")
        now = datetime.now(UTC)
        token = encode_compact_jwt({"sub": subject, "sid": str(session.session_id),
            "tenant_id": TENANT, "iss": "urn:odp:identity:local", "aud": "owned-survey",
            "iat": now.timestamp(), "exp": (now + timedelta(minutes=10)).timestamp()}, key)
        return {"Authorization": "Bearer " + token, "x-correlation-id": CID}

    client = TestClient(app)
    try:
        yield SimpleNamespace(bundle=bundle, factory=factory, principal=principal,
            client=client, headers=headers, path=path, listing_owner=owner, metadata=metadata)
    finally:
        client.close()
        bundle.engine.close()


def _payload(**changes):
    return {"targetKind": "CANDIDATE_SITE", "targetId": "candidate-owned", "assignedTo": ASSIGNEE,
        "expiresAt": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        "reason": "Inspect the actual synthetic candidate before the next review", **changes}


def _service(context, principal=None):
    return context.factory(principal or context.principal, correlation_id=CID)


def _create(context, *, key="create-one", payload=None):
    body = payload or _payload()
    return _service(context).command(context.principal, operation="create", payload=body,
                                    idempotency_key=key, correlation_id=CID)


def test_native_assignment_real_sql_replay_and_restart(context):
    payload = _payload()
    result = _create(context, payload=payload)
    value = result["assignment"]
    assert value["created_by"] == ACTOR and value["assigned_by"] == ACTOR
    assert value["assigned_to"] == ASSIGNEE and value["status"] == "ASSIGNED"
    assert value["revision"] == 1 and value["target_entity_id"] == "candidate-owned"
    assert value["campaign_id"] == ""  # no fabricated business campaign
    assert value["mediaAvailability"]["state"] == "unavailable"
    before = _business(context.bundle.engine)
    replay = _create(context, payload=payload)
    assert replay["replayed"] and replay["assignment"] == value
    assert replay["auditEventIds"] == result["auditEventIds"]
    assert _business(context.bundle.engine) == before
    reopened = _durable_bundle(context.path)
    try:
        restored = reopened.survey_assignment_repository_for_tenant(TENANT).get_assignment(value["assignment_id"])
        assert restored.to_dict() == context.bundle.survey_assignment_repository_for_tenant(TENANT).get_assignment(value["assignment_id"]).to_dict()
    finally:
        reopened.engine.close()


def test_native_claim_start_and_stale_revision_are_real_lifecycle_controls(context):
    created = _create(context)["assignment"]
    assignee = replace(context.principal, subject_id=ASSIGNEE)
    for operation, expected, status in (("claim", 1, "CLAIMED"), ("start", 2, "IN_PROGRESS")):
        result = _service(context, assignee).command(assignee, operation=operation,
            assignment_id=created["assignment_id"], payload={"expectedRevision": expected, "reason": "Start the assigned synthetic field work"},
            idempotency_key=operation, correlation_id=CID)
        assert result["assignment"]["revision"] == expected + 1
        assert result["assignment"]["status"] == status
    before = _business(context.bundle.engine)
    with pytest.raises(SurveyRevisionConflict, match="SURVEY_REVISION_CONFLICT"):
        _service(context).command(context.principal, operation="cancel", assignment_id=created["assignment_id"],
            payload={"expectedRevision": 1, "reason": "A stale writer must not replace the field task"},
            idempotency_key="stale", correlation_id=CID)
    assert _business(context.bundle.engine) == before


@pytest.mark.parametrize("axis,value", [("brand_ids", "other-brand"), ("region_ids", "other-region"),
    ("store_ids", "other-store"), ("assigned_area_ids", "other-area"), ("heat_zone_ids", "other-zone")])
def test_current_scope_revocation_blocks_read_and_receipt_replay_without_business_writes(context, axis, value):
    payload = _payload()
    result = _create(context, payload=payload)
    principal = replace(context.principal, scope=replace(context.principal.scope, **{axis: frozenset({value})}))
    before = _business(context.bundle.engine)
    with pytest.raises(SurveyAssignmentNotFound):
        _service(context, principal).get(principal, result["assignment"]["assignment_id"])
    with pytest.raises(SurveyAssignmentNotFound):
        _service(context, principal).command(principal, operation="create", payload=payload,
                                           idempotency_key="create-one", correlation_id=CID)
    assert _business(context.bundle.engine) == before
    denied = [e for e in context.bundle.audit_log.list_events(tenant_id=TENANT)
              if e.event_type == "security.authorization" and e.outcome == "deny"]
    assert denied and all(e.actor == ACTOR and e.correlation_id == CID for e in denied)


def test_native_audit_failure_rolls_back_assignment_receipt_and_jobs(context, monkeypatch):
    before = _business(context.bundle.engine)
    original = context.bundle.audit_log.record

    def fail(event):
        if event.event_type.startswith("market_survey."):
            raise RuntimeError("synthetic audit write failure")
        return original(event)

    monkeypatch.setattr(context.bundle.audit_log, "record", fail)
    with pytest.raises(RuntimeError, match="synthetic audit write failure"):
        _create(context)
    assert _business(context.bundle.engine) == before


@pytest.mark.parametrize("field,value", [("created_by", "forged-actor"), ("actor_id", "forged-actor"),
    ("media_attachments", [{"storage_uri": "gs://unverified", "sha256": "claimed"}])])
def test_http_caller_actor_or_media_claims_are_not_assignment_authority(context, field, value):
    before = _business(context.bundle.engine)
    response = context.client.post(BASE + "/assignments", headers={**context.headers(), "Idempotency-Key": "bad-claim"},
                                   json=_payload(**{field: value}))
    assert response.status_code == 422
    assert _business(context.bundle.engine) == before


def test_real_signed_http_uses_authenticated_actor_and_exact_tenant_owner(context):
    response = context.client.post(BASE + "/assignments", headers={**context.headers(), "Idempotency-Key": "http-create"}, json=_payload())
    assert response.status_code == 201, response.text
    assignment = response.json()["assignment"]
    assert assignment["created_by"] == ACTOR and assignment["tenant_id"] == TENANT
    read = context.client.get(BASE + "/assignments/" + assignment["assignment_id"], headers=context.headers())
    assert read.status_code == 200 and read.json() == assignment
    records = context.client.get(BASE + "/assignments", params={"targetKind": "CANDIDATE_SITE", "targetId": "candidate-owned"}, headers=context.headers())
    assert records.status_code == 200 and records.json()["items"] == [assignment]
    events = context.bundle.audit_log.list_events(tenant_id=TENANT)
    created = [e for e in events if e.event_type == "market_survey.assignment_created.v1"]
    assert len(created) == 1 and created[0].actor == ACTOR and created[0].correlation_id == CID
    assert created[0].metadata["reason"] == _payload()["reason"]


def test_true_heat_zone_target_enum_reads_native_retained_score_not_a_ui_zone(context):
    now = datetime.now(UTC)
    score = HeatZoneScoreResult(heat_zone_id="zone-owned", h3_index="synthetic-h3-not-geometry-proof",
        h3_resolution=9, score=50, priority_rank=1, unmet_demand_score=.5, format_fit_score=.5,
        cannibalization_risk_score=.5, rent_feasibility_score=.5, listing_availability_score=.5,
        confidence=.5, state=HeatZoneState.UNTOUCHED, feature_snapshot_time=now,
        prediction_origin_time=now, last_scored_at=now, model_version="explicit-synthetic-model",
        feature_version="explicit-synthetic-feature", source_snapshot_ids=("explicit-synthetic-input",))
    context.bundle.heatzone_store_for_tenant(TENANT).put(HeatZoneBatchScoreResult(
        job_id="synthetic-owned-zone-job", status="succeeded", scores=(score,), completed_at=now, tenant_id=TENANT))
    result = _create(context, key="zone-task", payload=_payload(targetKind="HEAT_ZONE", targetId="zone-owned"))
    assert result["assignment"]["target_entity_kind"] == TargetEntityKind.HEAT_ZONE.value
    assert result["assignment"]["target_entity_id"] == score.heat_zone_id
    assert context.bundle.survey_assignment_repository_for_tenant(TENANT).get_assignment(result["assignment"]["assignment_id"]).status is AssignmentStatus.ASSIGNED


def test_missing_durable_owner_and_foreign_rebinding_fail_closed(context):
    missing = CanonicalSurveyFactory(replace(context.bundle, survey_assignment_repository=None),
                                     audit_log=context.bundle.audit_log, queue=context.bundle.job_queue)
    with pytest.raises(SurveyOwnerUnavailable, match="SURVEY_ASSIGNMENT_OWNER_UNAVAILABLE"):
        missing(context.principal, correlation_id=CID)
    scoped = context.bundle.survey_assignment_repository_for_tenant(TENANT)
    with pytest.raises(SurveyOwnerUnavailable, match="SURVEY_TENANT_REBIND_DENIED"):
        scoped.for_tenant("another-tenant")
    bound_bundle = replace(context.bundle, survey_assignment_repository=scoped)
    with pytest.raises(SurveyOwnerUnavailable, match="SURVEY_TENANT_REBIND_DENIED"):
        bound_bundle.survey_assignment_repository_for_tenant("another-tenant")


def test_revoked_actual_assignee_cannot_be_reused_by_create_receipt_replay(context):
    payload = _payload()
    _create(context, payload=payload)
    current = context.bundle.identity_store.find_account_by_id(ASSIGNEE)
    context.bundle.identity_store.save_account(replace(current, status="disabled"))
    before = _business(context.bundle.engine)
    with pytest.raises(PermissionError, match="SURVEY_ASSIGNEE_UNAVAILABLE"):
        _create(context, payload=payload)
    assert _business(context.bundle.engine) == before


def test_configured_worm_uses_same_sql_pending_delivery_not_an_external_ack(context, tmp_path):
    sink_root = tmp_path / "synthetic-owned-worm"
    sink = LocalAppendOnlyWormSink(sink_root)
    audit = DurableAuditLog(context.bundle.engine, worm_sink=sink)
    factory = CanonicalSurveyFactory(context.bundle, audit_log=audit, queue=context.bundle.job_queue)
    result = factory(context.principal, correlation_id=CID).command(context.principal,
        operation="create", payload=_payload(), idempotency_key="pending-worm", correlation_id=CID)
    event = audit.get_event(result["auditEventIds"][0], tenant_id=TENANT)
    assert event is not None and event.actor == ACTOR
    # A transaction writes the event and actual delivery job; the external
    # local test sink is not invoked by the assignment request.
    assert list(sink_root.rglob("*.json")) == []
    jobs = context.bundle.engine.query("SELECT job_type,payload_json FROM durable_jobs WHERE job_type = ?",
                                      ("durable-audit-delivery",))
    assert len(jobs) == 1 and result["auditEventIds"][0] in jobs[0]["payload_json"]


def test_same_engine_is_required_for_real_queue_audit_and_assignment_owner(context, tmp_path):
    other = _durable_bundle(tmp_path / "other-owned.sqlite3")
    try:
        factory = CanonicalSurveyFactory(context.bundle, audit_log=context.bundle.audit_log, queue=other.job_queue)
        before = _business(context.bundle.engine)
        with pytest.raises(SurveyOwnerUnavailable, match="SURVEY_SAME_SQL_OWNER_REQUIRED"):
            factory(context.principal, correlation_id=CID).list(context.principal,
                target_kind="CANDIDATE_SITE", target_id="candidate-owned")
        assert _business(context.bundle.engine) == before
    finally:
        other.engine.close()


def test_main_mount_source_binds_actual_bundle_factory_and_captured_boundary():
    tree = ast.parse((ROOT / "apps/api/oday_api/main.py").read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "create_canonical_survey_assignment_router"]
    assert len(calls) == 1
    keywords = {row.arg: row.value for row in calls[0].keywords}
    factory = keywords["factory"]
    assert isinstance(factory, ast.Call) and isinstance(factory.func, ast.Name) and factory.func.id == "CanonicalSurveyFactory"
    assert isinstance(factory.args[0], ast.Name) and factory.args[0].id == "bundle"
    assert isinstance(keywords["boundary"], ast.Call) and keywords["boundary"].func.id == "default_boundary"
    # This is a source composition check, not execution of the full app.



def test_signed_http_reassign_claim_start_and_cancel_use_real_assignee_and_original_native_states(context):
    response = context.client.post(BASE + "/assignments", headers={**context.headers(), "Idempotency-Key": "http-native-sequence"}, json=_payload())
    assert response.status_code == 201, response.text
    identity = response.json()["assignment"]["assignment_id"]
    for version, recipient in ((1, ACTOR), (2, ASSIGNEE)):
        response = context.client.post(f"{BASE}/assignments/{identity}/assign",
            headers={**context.headers(), "Idempotency-Key": f"http-reassign-{version}"},
            json={"expectedRevision": version, "assignedTo": recipient, "reason": "Reassign the actual retained field task"})
        assert response.status_code == 200, response.text
        assert response.json()["assignment"]["revision"] == version + 1
        assert response.json()["assignment"]["assigned_to"] == recipient
        assert response.json()["assignment"]["assigned_by"] == ACTOR
    # ACTOR is a legitimate authenticated target reader/updater, but is not
    # the actual surveyor after reassignment. Native assignee checks still deny.
    for operation in ("claim", "start"):
        before = _business(context.bundle.engine)
        denied = context.client.post(f"{BASE}/assignments/{identity}/{operation}",
            headers={**context.headers(), "Idempotency-Key": "wrong-surveyor-" + operation},
            json={"expectedRevision": 3, "reason": "A different valid subject must not claim assigned field work"})
        assert denied.status_code == 403, denied.text
        assert _business(context.bundle.engine) == before
    for operation, version, status in (("claim", 3, "CLAIMED"), ("start", 4, "IN_PROGRESS")):
        response = context.client.post(f"{BASE}/assignments/{identity}/{operation}",
            headers={**context.headers(ASSIGNEE), "Idempotency-Key": "actual-surveyor-" + operation},
            json={"expectedRevision": version, "reason": "The actual assigned account starts the field work"})
        assert response.status_code == 200, response.text
        assert response.json()["assignment"]["revision"] == version + 1
        assert response.json()["assignment"]["status"] == status
    response = context.client.post(f"{BASE}/assignments/{identity}/cancel",
        headers={**context.headers(), "Idempotency-Key": "http-cancel"},
        json={"expectedRevision": 5, "reason": "Cancel the actual field assignment with a retained reason"})
    assert response.status_code == 200, response.text
    assert response.json()["assignment"]["status"] == "CANCELLED"
    assert response.json()["assignment"]["revision"] == 6
    events = context.bundle.audit_log.list_events(tenant_id=TENANT)
    assert len([e for e in events if e.event_type == "market_survey.assignment_updated.v1"]) == 2
    assert [e.actor for e in events if e.event_type == "market_survey.assignment_claimed.v1"] == [ASSIGNEE]
    assert [e.actor for e in events if e.event_type == "market_survey.assignment_started.v1"] == [ASSIGNEE]
    assert [e.actor for e in events if e.event_type == "market_survey.assignment_cancelled.v1"] == [ACTOR]


def test_signed_http_cannot_reassign_to_a_disabled_actual_account(context):
    identity = _create(context)["assignment"]["assignment_id"]
    account = context.bundle.identity_store.find_account_by_id(ASSIGNEE)
    context.bundle.identity_store.save_account(replace(account, status="disabled"))
    before = _business(context.bundle.engine)
    response = context.client.post(f"{BASE}/assignments/{identity}/assign",
        headers={**context.headers(), "Idempotency-Key": "disabled-assignee"},
        json={"expectedRevision": 1, "assignedTo": ASSIGNEE, "reason": "Cannot reuse a disabled directory account"})
    assert response.status_code == 403, response.text
    assert _business(context.bundle.engine) == before


def test_router_captured_missing_boundary_cannot_borrow_a_later_configured_default(context, monkeypatch):
    import apps.api.oday_api.security.dependencies as dependencies

    before = _business(context.bundle.engine)
    app = FastAPI()
    app.include_router(create_canonical_survey_assignment_router(factory=context.factory,
        audit_log=context.bundle.audit_log, boundary=None), prefix="/api/v1")
    # The actual first app remains bound to its own real boundary. A new global
    # default must not upgrade the unconfigured second app's captured owner.
    monkeypatch.setattr(dependencies, "default_boundary", lambda: context.client.app.state.survey_test_boundary)
    with TestClient(app) as client:
        response = client.get(BASE + "/assignments", headers=context.headers(),
            params={"targetKind": "CANDIDATE_SITE", "targetId": "candidate-owned"})
    assert response.status_code == 503
    assert response.json() == {"detail": "SURVEY_IDENTITY_BOUNDARY_UNAVAILABLE"}
    assert _business(context.bundle.engine) == before


def test_two_real_signed_boundaries_cannot_replace_each_other_through_mutable_default(context, monkeypatch):
    import apps.api.oday_api.security.dependencies as dependencies

    key = SigningKey(kid="second-isolated-survey", algorithm="HS256", secret=b"second-isolated-survey-HMAC-key")
    boundary = AuthenticationBoundary(AuthBoundaryConfig(local_issuer="urn:odp:identity:second-survey",
        local_audiences=frozenset({"second-survey"}), local_signing_keys={key.kid: key},
        identity_store=context.bundle.identity_store, session_service=context.bundle.session_service), audit_log=context.bundle.audit_log)
    second = FastAPI()
    second.state.feature_flag_registry = context.bundle.feature_flag_registry

    @second.middleware("http")
    async def correlation(request: Request, call_next):
        request.state.correlation_id = request.headers.get("x-correlation-id", CID)
        return await call_next(request)

    second.include_router(create_canonical_survey_assignment_router(factory=context.factory,
        audit_log=context.bundle.audit_log, boundary=boundary), prefix="/api/v1")
    session = context.bundle.session_service.create_session(account_id=UUID(ACTOR), provider="local_password")
    now = datetime.now(UTC)
    token = encode_compact_jwt({"sub": ACTOR, "sid": str(session.session_id), "tenant_id": TENANT,
        "iss": "urn:odp:identity:second-survey", "aud": "second-survey", "iat": now.timestamp(),
        "exp": (now + timedelta(minutes=10)).timestamp()}, key)
    second_headers = {"Authorization": "Bearer " + token, "x-correlation-id": CID}
    monkeypatch.setattr(dependencies, "default_boundary", lambda: boundary)
    params = {"targetKind": "CANDIDATE_SITE", "targetId": "candidate-owned"}
    before = _business(context.bundle.engine)
    with TestClient(second) as client:
        assert client.get(BASE + "/assignments", headers=second_headers, params=params).status_code == 200
        assert client.get(BASE + "/assignments", headers=context.headers(), params=params).status_code == 401
        assert context.client.get(BASE + "/assignments", headers=context.headers(), params=params).status_code == 200
        assert context.client.get(BASE + "/assignments", headers=second_headers, params=params).status_code == 401
    assert _business(context.bundle.engine) == before


def test_parent_removed_after_initial_admission_preserves_object_deny_audit_without_creating_assignment(context, monkeypatch):
    from contextlib import contextmanager

    from shared.auth import Action

    service = _service(context)
    original = service.parents.guard
    after_removal = None

    @contextmanager
    def remove_before_guard(*args, **kwargs):
        nonlocal after_removal
        # Initial parent and assignee admission has completed. Remove the real
        # owned parent immediately before entering the original locked fence.
        context.listing_owner._store.delete_collection(context.listing_owner._CANDIDATES)
        after_removal = _business(context.bundle.engine)
        with original(*args, **kwargs):
            yield

    monkeypatch.setattr(service.parents, "guard", remove_before_guard)
    with pytest.raises(SurveyAssignmentNotFound):
        service.command(context.principal, operation="create", payload=_payload(),
            idempotency_key="parent-removed", correlation_id=CID)
    assert after_removal is not None and _business(context.bundle.engine) == after_removal
    denied = [e for e in context.bundle.audit_log.list_events(tenant_id=TENANT)
        if e.event_type == "security.authorization" and e.outcome == "deny"]
    assert len(denied) == 1 and denied[0].actor == ACTOR and denied[0].correlation_id == CID
    assert denied[0].action == Action.CREATE.value
    assert denied[0].resource == "listing/candidate-owned"
