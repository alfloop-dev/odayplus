"""Integration tests for Market Survey Workflow (ODP-SURVEY-001).

Contract: `odayplus.survey-workflow.v2`.
Requires: `odayplus.market-data-facade.v2`, `emgi.field-survey.v1`.

Acceptance Criteria:
1. Own assignment, reviewer separation, correction, expiry and promotion.
2. Treat platform survey ingestion as evidence, not automatic ground truth.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.market_survey import create_market_survey_router
from modules.external_data.application.market_data_facade import MarketDataFacade
from modules.external_data.infrastructure.data_platform_client import (
    DataPlatformClient,
    InMemoryDataPlatformTransport,
)
from modules.market_survey import (
    REQUIRED_EVIDENCE_CONTRACT,
    REQUIRED_FACADE_CONTRACT,
    SURVEY_WORKFLOW_CONTRACT,
    SURVEY_WORKFLOW_VERSION,
    AssignmentStatus,
    EvidenceReviewStatus,
    FieldSurveyEvidence,
    InMemorySurveyRepository,
    MarketSurveyService,
    MediaAttachment,
    MediaKind,
    PlatformSurveyFacadeAdapter,
    PromotionRecord,
    PromotionStatus,
    SurveyAssignment,
    SurveyAuthorizationError,
    SurveyErrorCode,
    SurveyLifecycleKind,
    SurveyLocation,
    SurveyNotFoundError,
    SurveyPromotionStateMachine,
    SurveyRepository,
    SurveyReviewRecord,
    SurveyReviewStateMachine,
    SurveyStateConflictError,
    SurveyType,
    SurveyValidationError,
    TargetEntityKind,
    run_survey_expiry_sweep,
)
from packages.oday_data_product_contracts_client.models.field_survey import (
    FieldSurveyDocument,
    FieldSurveyObservation,
    ReviewStatus,
    SurveyLocation as PlatformSurveyLocation,
    SurveyReview as PlatformSurveyReview,
    SurveyType as PlatformSurveyType,
    TargetEntityKind as PlatformTargetEntityKind,
)
from shared.audit import InMemoryAuditLog
from shared.auth import Principal, Role, Scope
from tests.integration._authz import auth_headers

NOW = datetime(2026, 8, 20, 10, 0, 0, tzinfo=UTC)
FUTURE_EXPIRY = NOW + timedelta(days=7)
PAST_EXPIRY = NOW - timedelta(hours=2)


@pytest.fixture
def repo() -> InMemorySurveyRepository:
    return InMemorySurveyRepository()


@pytest.fixture
def audit_log() -> InMemoryAuditLog:
    return InMemoryAuditLog()


@pytest.fixture
def service(repo: InMemorySurveyRepository, audit_log: InMemoryAuditLog) -> MarketSurveyService:
    return MarketSurveyService(repository=repo, audit_log=audit_log)


@pytest.fixture
def client(service: MarketSurveyService, repo: InMemorySurveyRepository, audit_log: InMemoryAuditLog) -> TestClient:
    app = FastAPI(title="Survey Workflow Test App")
    router = create_market_survey_router(service=service, repository=repo, audit_log=audit_log)
    app.include_router(router)
    return TestClient(app)


def test_contract_metadata_and_versions() -> None:
    assert SURVEY_WORKFLOW_CONTRACT == "odayplus.survey-workflow.v2"
    assert SURVEY_WORKFLOW_VERSION == "2.0.0"
    assert REQUIRED_FACADE_CONTRACT == "odayplus.market-data-facade.v2"
    assert REQUIRED_EVIDENCE_CONTRACT == "emgi.field-survey.v1"


def test_assignment_lifecycle_and_submission(service: MarketSurveyService) -> None:
    asgn = service.create_assignment(
        tenant_id="tenant-alpha",
        campaign_id="cmp-taipei-2026",
        target_entity_id="site-xinyi-001",
        target_entity_kind=TargetEntityKind.CANDIDATE_SITE,
        survey_type=SurveyType.PHYSICAL_FEASIBILITY,
        expires_at=FUTURE_EXPIRY,
        created_by="ops-manager-1",
        instructions={"checklist": ["verify_hvac", "check_power_phase"]},
        now=NOW,
    )
    assert asgn.status == AssignmentStatus.UNASSIGNED
    assert asgn.assigned_to is None

    asgn = service.assign_survey(
        asgn.assignment_id,
        assigned_to="surveyor-john",
        assigned_by="ops-manager-1",
        tenant_id="tenant-alpha",
        now=NOW,
    )
    assert asgn.status == AssignmentStatus.ASSIGNED
    assert asgn.assigned_to == "surveyor-john"

    asgn = service.claim_assignment(asgn.assignment_id, actor_id="surveyor-john", tenant_id="tenant-alpha", now=NOW)
    assert asgn.status == AssignmentStatus.CLAIMED

    asgn = service.start_survey(asgn.assignment_id, actor_id="surveyor-john", tenant_id="tenant-alpha", now=NOW)
    assert asgn.status == AssignmentStatus.IN_PROGRESS

    asgn, evidence = service.submit_survey(
        asgn.assignment_id,
        actor_id="surveyor-john",
        location=SurveyLocation(latitude=25.033, longitude=121.565, address="Songren Rd, Xinyi District"),
        attributes={"frontage_meters": 12.5, "ceiling_height_meters": 3.8, "power_capacity_amps": 200},
        media_attachments=[
            MediaAttachment(
                blob_id="blob-photo-1",
                captured_at=NOW.isoformat(),
                media_id="media-001",
                media_kind=MediaKind.PHOTO,
                sha256="abc123sha",
                storage_uri="gs://oday-media/xinyi-front.jpg",
                caption="Storefront facade",
            )
        ],
        confidence=0.98,
        tenant_id="tenant-alpha",
        now=NOW,
    )
    assert asgn.status == AssignmentStatus.SUBMITTED
    assert asgn.survey_id == evidence.survey_id

    # Invariant: Evidence is PENDING_REVIEW, NOT ground truth
    assert evidence.review_status == EvidenceReviewStatus.PENDING_REVIEW
    assert evidence.promotion_status == PromotionStatus.NOT_PROMOTED
    assert evidence.submitter_id == "surveyor-john"
    assert evidence.location.latitude == 25.033


def test_assignment_expiry_and_overdue_sweep(service: MarketSurveyService) -> None:
    asgn = service.create_assignment(
        tenant_id="tenant-beta",
        campaign_id="cmp-kaohsiung",
        target_entity_id="site-zuoying-002",
        target_entity_kind=TargetEntityKind.STORE,
        survey_type=SurveyType.STORE_AUDIT,
        expires_at=PAST_EXPIRY,
        created_by="ops-manager-2",
        now=PAST_EXPIRY - timedelta(days=1),
    )

    sweep_result = run_survey_expiry_sweep(service, tenant_id="tenant-beta", now=NOW)
    assert sweep_result.expired_count >= 1
    assert asgn.assignment_id in sweep_result.expired_assignment_ids

    updated = service.get_assignment(asgn.assignment_id, tenant_id="tenant-beta")
    assert updated is not None
    assert updated.status == AssignmentStatus.EXPIRED

    with pytest.raises(SurveyStateConflictError) as exc_info:
        service.claim_assignment(asgn.assignment_id, actor_id="surveyor-bob", tenant_id="tenant-beta", now=NOW)
    assert exc_info.value.code == SurveyErrorCode.ASSIGNMENT_EXPIRED

    with pytest.raises(SurveyStateConflictError) as exc_info2:
        service.submit_survey(
            asgn.assignment_id,
            actor_id="surveyor-bob",
            location=SurveyLocation(latitude=22.68, longitude=120.30),
            tenant_id="tenant-beta",
            now=NOW,
        )
    assert exc_info2.value.code == SurveyErrorCode.ASSIGNMENT_EXPIRED

    reopened = service.assign_survey(
        asgn.assignment_id,
        assigned_to="surveyor-bob",
        assigned_by="ops-manager-2",
        expires_at=FUTURE_EXPIRY,
        tenant_id="tenant-beta",
        now=NOW,
    )
    assert reopened.status == AssignmentStatus.ASSIGNED
    assert reopened.expires_at == FUTURE_EXPIRY.isoformat()


def test_platform_observation_ingested_as_evidence_not_ground_truth(service: MarketSurveyService) -> None:
    platform_obs = FieldSurveyObservation(
        blob_id="blob-emgi-001",
        campaign_id="cmp-platform-taichung",
        effective_from=NOW.isoformat(),
        location=PlatformSurveyLocation(latitude=24.1477, longitude=120.6736, address="Taiwan Blvd, Taichung"),
        observation_id="obs-platform-999",
        review=PlatformSurveyReview(
            review_status=ReviewStatus.PENDING_REVIEW,
            reviewed_at=NOW.isoformat(),
            reviewer_id="external-field-agent",
        ),
        scope_principal_id="principal-corp",
        submission_id="sub-001",
        submitted_at=NOW.isoformat(),
        submitter_id="field-surveyor-ext",
        survey_id="srv-platform-001",
        survey_type=PlatformSurveyType.CANDIDATE_SITE,
        surveyed_at=NOW.isoformat(),
        target_entity_id="candidate-site-taichung-007",
        target_entity_kind=PlatformTargetEntityKind.CANDIDATE_SITE,
        attributes={"pedestrian_count_peak_hour": 1420, "competitor_count_500m": 3},
        confidence=0.92,
    )

    evidence = service.ingest_platform_observation(
        platform_obs,
        tenant_id="tenant-gamma",
        now=NOW,
    )

    # Invariant: Observation enters odayplus as unreviewed, unpromoted evidence
    assert evidence.survey_id == "srv-platform-001"
    assert evidence.observation_id == "obs-platform-999"
    assert evidence.review_status == EvidenceReviewStatus.PENDING_REVIEW
    assert evidence.promotion_status == PromotionStatus.NOT_PROMOTED
    assert evidence.is_superseded is False
    assert evidence.is_retracted is False
    assert evidence.attributes["pedestrian_count_peak_hour"] == 1420


def test_platform_document_ingestion_and_retraction(service: MarketSurveyService) -> None:
    transport = InMemoryDataPlatformTransport()
    client_dp = DataPlatformClient(transport=transport)
    facade = MarketDataFacade(client=client_dp, enforce_auth=False)
    adapter = PlatformSurveyFacadeAdapter(service=service, facade=facade)

    raw_doc = {
        "contract_id": "emgi.field-survey.v1",
        "field_surveys": [
            {
                "blob_id": "blob-doc-1",
                "campaign_id": "cmp-doc-test",
                "effective_from": NOW.isoformat(),
                "location": {"latitude": 25.04, "longitude": 121.55, "address": "Dunhua S. Rd"},
                "observation_id": "obs-doc-101",
                "review": {
                    "review_status": "PENDING_REVIEW",
                    "reviewed_at": NOW.isoformat(),
                    "reviewer_id": "agent-001",
                },
                "scope_principal_id": "principal-test",
                "submission_id": "sub-101",
                "submitted_at": NOW.isoformat(),
                "submitter_id": "surveyor-alice",
                "survey_id": "srv-doc-101",
                "survey_type": "PHYSICAL_FEASIBILITY",
                "surveyed_at": NOW.isoformat(),
                "target_entity_id": "prop-101",
                "target_entity_kind": "PROPERTY",
                "attributes": {"usable_area_sqm": 120.0},
            },
            {
                "blob_id": "blob-doc-2",
                "campaign_id": "cmp-doc-test",
                "effective_from": NOW.isoformat(),
                "location": {"latitude": 25.05, "longitude": 121.56},
                "observation_id": "obs-doc-102",
                "review": {
                    "review_status": "PENDING_REVIEW",
                    "reviewed_at": NOW.isoformat(),
                    "reviewer_id": "agent-001",
                },
                "scope_principal_id": "principal-test",
                "submission_id": "sub-102",
                "submitted_at": NOW.isoformat(),
                "submitter_id": "surveyor-bob",
                "survey_id": "srv-doc-102",
                "survey_type": "STORE_AUDIT",
                "surveyed_at": NOW.isoformat(),
                "target_entity_id": "store-102",
                "target_entity_kind": "STORE",
                "attributes": {"compliance_score": 94},
            },
        ],
        "retractions": [
            {"observation_id": "obs-doc-101", "reason": "Property boundary measurement error"}
        ],
    }

    transport.store_document("emgi.field-survey.v1", "fs-doc-001", raw_doc)
    ingested = adapter.fetch_and_ingest_document("fs-doc-001", tenant_id="tenant-alpha")
    assert len(ingested) == 2

    ev1 = service.get_survey("srv-doc-101", tenant_id="tenant-alpha")
    assert ev1 is not None
    assert ev1.is_retracted is True
    assert ev1.retraction_reason == "Property boundary measurement error"

    ev2 = service.get_survey("srv-doc-102", tenant_id="tenant-alpha")
    assert ev2 is not None
    assert ev2.is_retracted is False


def test_reviewer_separation_self_review_denied(service: MarketSurveyService) -> None:
    asgn = service.create_assignment(
        tenant_id="tenant-alpha",
        campaign_id="cmp-hsinchu",
        target_entity_id="site-science-park-01",
        target_entity_kind=TargetEntityKind.CANDIDATE_SITE,
        survey_type=SurveyType.PHYSICAL_FEASIBILITY,
        expires_at=FUTURE_EXPIRY,
        created_by="ops-lead",
        assigned_to="surveyor-claire",
        now=NOW,
    )
    _, evidence = service.submit_survey(
        asgn.assignment_id,
        actor_id="surveyor-claire",
        location=SurveyLocation(latitude=24.78, longitude=120.99),
        tenant_id="tenant-alpha",
        now=NOW,
    )

    # Submitter attempting self-review is DENIED
    with pytest.raises(SurveyAuthorizationError) as exc_info:
        service.review_survey(
            evidence.survey_id,
            decision=EvidenceReviewStatus.APPROVED,
            reviewer_id="surveyor-claire",
            reviewer_roles=["SITE_REVIEWER"],
            tenant_id="tenant-alpha",
            now=NOW,
        )
    assert exc_info.value.code == SurveyErrorCode.SELF_REVIEW_DENIED

    # Unauthorized role is DENIED
    with pytest.raises(SurveyAuthorizationError) as exc_info2:
        service.review_survey(
            evidence.survey_id,
            decision=EvidenceReviewStatus.APPROVED,
            reviewer_id="random-user",
            reviewer_roles=["ANALYST_ONLY"],
            tenant_id="tenant-alpha",
            now=NOW,
        )
    assert exc_info2.value.code == SurveyErrorCode.UNAUTHORIZED_REVIEWER

    # Distinct authorized reviewer is APPROVED
    reviewed = service.review_survey(
        evidence.survey_id,
        decision=EvidenceReviewStatus.APPROVED,
        reviewer_id="manager-david",
        reviewer_roles=["SITE_REVIEWER", "OPERATIONS_MANAGER"],
        review_comment="Site physical dimensions verified against cadastral maps",
        review_checklist={"dimensions_verified": True, "power_phase_ok": True},
        tenant_id="tenant-alpha",
        now=NOW,
    )
    assert reviewed.review_status == EvidenceReviewStatus.APPROVED
    assert reviewed.review_record is not None
    assert reviewed.review_record.reviewer_id == "manager-david"


def test_survey_correction_and_lineage(service: MarketSurveyService) -> None:
    asgn = service.create_assignment(
        tenant_id="tenant-alpha",
        campaign_id="cmp-tainan",
        target_entity_id="site-anping-01",
        target_entity_kind=TargetEntityKind.CANDIDATE_SITE,
        survey_type=SurveyType.PHYSICAL_FEASIBILITY,
        expires_at=FUTURE_EXPIRY,
        created_by="ops-1",
        assigned_to="surveyor-eva",
        now=NOW,
    )
    _, initial_evidence = service.submit_survey(
        asgn.assignment_id,
        actor_id="surveyor-eva",
        location=SurveyLocation(latitude=22.99, longitude=120.16),
        attributes={"frontage_meters": 8.0, "floor_area_sqm": 85.0},
        tenant_id="tenant-alpha",
        now=NOW,
    )
    service.review_survey(
        initial_evidence.survey_id,
        decision=EvidenceReviewStatus.APPROVED,
        reviewer_id="reviewer-frank",
        tenant_id="tenant-alpha",
        now=NOW,
    )

    corrected_evidence, correction_rec = service.correct_survey(
        initial_evidence.survey_id,
        corrected_by="surveyor-eva",
        reason="Laser remeasurement included rear storage annex",
        delta_attributes={"floor_area_sqm": 105.0},
        lifecycle_kind=SurveyLifecycleKind.CORRECTION,
        tenant_id="tenant-alpha",
        now=NOW + timedelta(days=1),
    )

    assert corrected_evidence.replaces_survey_id == initial_evidence.survey_id
    assert corrected_evidence.attributes["floor_area_sqm"] == 105.0
    assert corrected_evidence.attributes["frontage_meters"] == 8.0
    assert corrected_evidence.review_status == EvidenceReviewStatus.PENDING_REVIEW
    assert corrected_evidence.is_superseded is False

    original_updated = service.get_survey(initial_evidence.survey_id, tenant_id="tenant-alpha")
    assert original_updated is not None
    assert original_updated.is_superseded is True

    with pytest.raises(SurveyStateConflictError) as exc_info:
        service.review_survey(
            initial_evidence.survey_id,
            decision=EvidenceReviewStatus.APPROVED,
            reviewer_id="reviewer-frank",
            tenant_id="tenant-alpha",
            now=NOW + timedelta(days=2),
        )
    assert exc_info.value.code == SurveyErrorCode.SUPERSEDED_EVIDENCE

    service.review_survey(
        corrected_evidence.survey_id,
        decision=EvidenceReviewStatus.APPROVED,
        reviewer_id="reviewer-frank",
        review_comment="Approved remeasured floor area",
        tenant_id="tenant-alpha",
        now=NOW + timedelta(days=2),
    )

    lineage = service.get_survey_lineage(corrected_evidence.survey_id, tenant_id="tenant-alpha")
    assert lineage["survey"]["survey_id"] == corrected_evidence.survey_id
    assert len(lineage["corrections"]) == 1
    assert lineage["corrections"][0]["reason"] == "Laser remeasurement included rear storage annex"
    assert len(lineage["ancestry"]) == 1
    assert lineage["ancestry"][0]["survey_id"] == initial_evidence.survey_id


def test_governed_promotion_to_candidate_site(service: MarketSurveyService) -> None:
    promoted_records: list[PromotionRecord] = []
    service.promotion_hooks.append(lambda rec: promoted_records.append(rec))

    asgn = service.create_assignment(
        tenant_id="tenant-alpha",
        campaign_id="cmp-expansion-2026",
        target_entity_id="candidate-site-99",
        target_entity_kind=TargetEntityKind.CANDIDATE_SITE,
        survey_type=SurveyType.PHYSICAL_FEASIBILITY,
        expires_at=FUTURE_EXPIRY,
        created_by="expansion-manager",
        assigned_to="surveyor-gina",
        now=NOW,
    )
    _, evidence = service.submit_survey(
        asgn.assignment_id,
        actor_id="surveyor-gina",
        location=SurveyLocation(latitude=25.045, longitude=121.52),
        attributes={"pedestrian_score": 88, "rent_estimate": 150000},
        tenant_id="tenant-alpha",
        now=NOW,
    )

    with pytest.raises(SurveyValidationError) as exc_info:
        service.promote_survey(
            evidence.survey_id,
            promoted_by="expansion-manager",
            target_entity_type="candidate_site",
            tenant_id="tenant-alpha",
            now=NOW,
        )
    assert exc_info.value.code == SurveyErrorCode.NOT_APPROVED_FOR_PROMOTION

    service.review_survey(
        evidence.survey_id,
        decision=EvidenceReviewStatus.APPROVED,
        reviewer_id="expansion-director",
        review_comment="Site meets all feasibility criteria",
        tenant_id="tenant-alpha",
        now=NOW,
    )

    promo_rec = service.promote_survey(
        evidence.survey_id,
        promoted_by="expansion-manager",
        target_entity_type="candidate_site",
        target_entity_ref="cs-draft-99-final",
        promotion_payload={"target_brand": "ODay Coffee", "priority": "HIGH"},
        tenant_id="tenant-alpha",
        now=NOW + timedelta(hours=1),
    )

    assert promo_rec.survey_id == evidence.survey_id
    assert promo_rec.target_entity_ref == "cs-draft-99-final"
    assert len(promoted_records) == 1

    promoted_evidence = service.get_survey(evidence.survey_id, tenant_id="tenant-alpha")
    assert promoted_evidence is not None
    assert promoted_evidence.promotion_status == PromotionStatus.PROMOTED
    assert promoted_evidence.promoted_by == "expansion-manager"
    assert promoted_evidence.promoted_target_ref == "cs-draft-99-final"


def test_tenant_isolation_and_audit_trail(service: MarketSurveyService, audit_log: InMemoryAuditLog) -> None:
    asgn1 = service.create_assignment(
        tenant_id="tenant-1",
        campaign_id="cmp-1",
        target_entity_id="site-1",
        target_entity_kind=TargetEntityKind.CANDIDATE_SITE,
        survey_type=SurveyType.PHYSICAL_FEASIBILITY,
        expires_at=FUTURE_EXPIRY,
        created_by="user-t1",
        assigned_to="surveyor-t1",
        now=NOW,
    )
    _, ev1 = service.submit_survey(
        asgn1.assignment_id,
        actor_id="surveyor-t1",
        location=SurveyLocation(latitude=25.0, longitude=121.0),
        tenant_id="tenant-1",
        now=NOW,
    )

    assert service.get_assignment(asgn1.assignment_id, tenant_id="tenant-2") is None
    assert service.get_survey(ev1.survey_id, tenant_id="tenant-2") is None
    assert len(service.list_assignments(tenant_id="tenant-2")) == 0
    assert len(service.list_surveys(tenant_id="tenant-2")) == 0

    with pytest.raises(SurveyNotFoundError):
        service.review_survey(
            ev1.survey_id,
            decision=EvidenceReviewStatus.APPROVED,
            reviewer_id="reviewer-t2",
            tenant_id="tenant-2",
            now=NOW,
        )

    audit_events = audit_log.list_events(tenant_id="tenant-1")
    actions = {e.action for e in audit_events}
    assert {"create_assignment", "submit_survey"} <= actions


def test_fastapi_http_survey_workflow(client: TestClient) -> None:
    headers_staff = {
        **auth_headers(Role.EXPANSION_USER, subject="surveyor-helen"),
        "x-tenant-id": "tenant-http",
    }
    headers_manager = {
        **auth_headers(Role.SITE_REVIEWER, Role.EXPANSION_USER, subject="manager-ian"),
        "x-tenant-id": "tenant-http",
    }

    # 1. POST /market-survey/assignments
    res_create = client.post(
        "/market-survey/assignments",
        json={
            "campaign_id": "cmp-http-test",
            "target_entity_id": "site-http-101",
            "target_entity_kind": "CANDIDATE_SITE",
            "survey_type": "PHYSICAL_FEASIBILITY",
            "expires_at": FUTURE_EXPIRY.isoformat(),
            "created_by": "manager-ian",
            "assigned_to": "surveyor-helen",
            "instructions": {"focus": "foot_traffic"},
        },
        headers=headers_manager,
    )
    assert res_create.status_code == 201
    asgn_data = res_create.json()
    asgn_id = asgn_data["assignment_id"]
    assert asgn_data["status"] == "ASSIGNED"
    assert asgn_data["contract"] == SURVEY_WORKFLOW_CONTRACT

    # 2. GET /market-survey/assignments
    res_list = client.get("/market-survey/assignments", headers=headers_staff)
    assert res_list.status_code == 200
    assert res_list.json()["count"] >= 1

    # 3. POST /market-survey/assignments/{id}/claim
    res_claim = client.post(
        f"/market-survey/assignments/{asgn_id}/claim",
        json={"actor_id": "surveyor-helen"},
        headers=headers_staff,
    )
    assert res_claim.status_code == 200
    assert res_claim.json()["status"] == "CLAIMED"

    # 4. POST /market-survey/assignments/{id}/submit
    res_submit = client.post(
        f"/market-survey/assignments/{asgn_id}/submit",
        json={
            "actor_id": "surveyor-helen",
            "location": {"latitude": 25.04, "longitude": 121.56, "address": "Civic Blvd"},
            "attributes": {"frontage": 10.0, "traffic_count": 800},
            "media_attachments": [
                {
                    "blob_id": "blob-101",
                    "captured_at": NOW.isoformat(),
                    "media_id": "med-101",
                    "media_kind": "PHOTO",
                    "sha256": "sha101",
                    "storage_uri": "gs://media/101.jpg",
                }
            ],
            "confidence": 0.95,
        },
        headers=headers_staff,
    )
    assert res_submit.status_code == 200
    sub_data = res_submit.json()
    survey_id = sub_data["evidence"]["survey_id"]
    assert sub_data["evidence"]["review_status"] == "PENDING_REVIEW"

    # 5. POST /market-survey/surveys/{id}/review with self-review -> 403 FORBIDDEN
    res_self_review = client.post(
        f"/market-survey/surveys/{survey_id}/review",
        json={
            "decision": "APPROVED",
            "reviewer_id": "surveyor-helen",
        },
        headers=headers_staff,
    )
    assert res_self_review.status_code == 403
    assert res_self_review.json()["detail"]["code"] == "SELF_REVIEW_DENIED"

    # 6. POST /market-survey/surveys/{id}/review with distinct reviewer -> 200 OK
    res_review_ok = client.post(
        f"/market-survey/surveys/{survey_id}/review",
        json={
            "decision": "APPROVED",
            "reviewer_id": "manager-ian",
            "review_comment": "Verified and approved",
        },
        headers=headers_manager,
    )
    assert res_review_ok.status_code == 200
    assert res_review_ok.json()["review_status"] == "APPROVED"

    # 7. POST /market-survey/surveys/{id}/promote -> 200 OK
    res_promote = client.post(
        f"/market-survey/surveys/{survey_id}/promote",
        json={
            "promoted_by": "manager-ian",
            "target_entity_type": "candidate_site",
            "target_entity_ref": "candidate-http-101",
        },
        headers=headers_manager,
    )
    assert res_promote.status_code == 200
    assert res_promote.json()["status"] == "PROMOTED"

    # 8. GET /market-survey/surveys/{id}/lineage -> 200 OK
    res_lineage = client.get(f"/market-survey/surveys/{survey_id}/lineage", headers=headers_manager)
    assert res_lineage.status_code == 200
    lineage = res_lineage.json()
    assert lineage["survey"]["survey_id"] == survey_id
    assert lineage["promotion"]["target_entity_ref"] == "candidate-http-101"
    assert lineage["contract"] == SURVEY_WORKFLOW_CONTRACT
