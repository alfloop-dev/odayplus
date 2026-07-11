from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.sitescore import (
    InMemorySiteScoreRepository,
    SiteScoreFeatureInput,
    SiteScoreRecommendation,
    SiteScoreReportService,
    run_sitescore_batch_score,
    score_sites,
)
from shared.audit import InMemoryAuditLog
from shared.workflow.sitescore import (
    CandidateSiteRealizationHook,
    DecisionAction,
    DecisionStatus,
    SiteScoreDecisionError,
    SiteScoreDecisionWorkflow,
)
from tests.integration._authz import SITESCORE_HEADERS

SNAPSHOT_TIME = datetime(2026, 6, 27, 8, 0, tzinfo=UTC)
PREDICTION_TIME = datetime(2026, 6, 27, 9, 0, tzinfo=UTC)

GO_SITE = SiteScoreFeatureInput(
    candidate_site_id="CS-GO-001",
    feature_snapshot_time=SNAPSHOT_TIME,
    heat_zone_id="heatzone:h3r9_go",
    heat_zone_score=82.0,
    monthly_rent=60_000.0,
    area_ping=25.0,
    comparable_store_count=5,
    comparable_monthly_revenue_p50=480_000.0,
    buildout_capex=2_500_000.0,
    gross_margin_ratio=0.60,
    average_confidence=0.92,
    data_quality_score=0.95,
    source_snapshot_ids=("listing-20260627", "store-20260627"),
)
REJECT_SITE = SiteScoreFeatureInput(
    candidate_site_id="CS-REJ-002",
    feature_snapshot_time=SNAPSHOT_TIME,
    heat_zone_score=40.0,
    monthly_rent=300_000.0,
    area_ping=20.0,
    comparable_store_count=4,
    comparable_monthly_revenue_p50=200_000.0,
    buildout_capex=2_500_000.0,
    gross_margin_ratio=0.55,
    average_confidence=0.85,
    data_quality_score=0.9,
    source_snapshot_ids=("listing-20260627",),
)
INVESTIGATE_SITE = SiteScoreFeatureInput(
    candidate_site_id="CS-INV-003",
    feature_snapshot_time=SNAPSHOT_TIME,
    heat_zone_score=70.0,
    monthly_rent=55_000.0,
    area_ping=24.0,
    comparable_store_count=0,
    comparable_monthly_revenue_p50=0.0,
    average_confidence=0.6,
    data_quality_score=0.8,
    source_snapshot_ids=("listing-20260627",),
)


def test_score_sites_recommends_go_reject_and_investigate() -> None:
    reports = score_sites(
        [GO_SITE, REJECT_SITE, INVESTIGATE_SITE],
        prediction_origin_time=PREDICTION_TIME,
        scored_at=PREDICTION_TIME,
    )
    by_id = {report.candidate_site_id: report for report in reports}

    go = by_id["CS-GO-001"]
    assert go.recommendation is SiteScoreRecommendation.GO
    # Intervals are well ordered across every horizon.
    for interval in (go.m1, go.m3, go.m6, go.m12, go.payback_period):
        assert interval.p10 <= interval.p50 <= interval.p90
    # Revenue ramps up across horizons.
    assert go.m1.p50 < go.m3.p50 < go.m6.p50 < go.m12.p50
    assert go.payback_p50_months < 36.0
    assert "payback_within_target" in go.key_positive_factors

    assert by_id["CS-REJ-002"].recommendation is SiteScoreRecommendation.REJECT
    assert by_id["CS-INV-003"].recommendation is SiteScoreRecommendation.INVESTIGATE
    assert "no_comparable_evidence" in by_id["CS-INV-003"].key_negative_factors


def test_summary_dict_matches_report_contract_fields() -> None:
    report = score_sites([GO_SITE], scored_at=PREDICTION_TIME)[0]
    summary = report.to_summary_dict()
    for key in (
        "recommendation",
        "m1",
        "m3",
        "m6",
        "m12",
        "paybackPeriod",
        "rentReasonableness",
        "cannibalizationRisk",
        "comparableStores",
        "keyPositiveFactors",
        "keyNegativeFactors",
        "modelVersion",
        "featureSnapshotTime",
    ):
        assert key in summary
    assert set(summary["m12"]) == {"p10", "p50", "p90"}


def test_repository_versions_reports_and_preserves_history() -> None:
    repository = InMemorySiteScoreRepository()
    service = SiteScoreReportService(repository=repository)

    first = service.score_candidates([GO_SITE], scored_at=PREDICTION_TIME)[0]
    second = service.score_candidates([GO_SITE], scored_at=PREDICTION_TIME)[0]

    assert first.report_version == 1
    assert second.report_version == 2
    assert first.report_id != second.report_id
    assert repository.latest("CS-GO-001").report_version == 2
    assert [r.report_version for r in repository.history("CS-GO-001")] == [1, 2]
    assert repository.get_report(first.report_id) is first


def test_batch_worker_scores_and_persists_versioned_reports() -> None:
    repository = InMemorySiteScoreRepository()
    result = run_sitescore_batch_score(
        job_id="ss-job-1",
        features=[GO_SITE, REJECT_SITE],
        prediction_origin_time=PREDICTION_TIME,
        repository=repository,
    )
    assert result.job_id == "ss-job-1"
    assert result.status == "succeeded"
    assert len(result.reports) == 2
    assert {r.report_version for r in result.reports} == {1}
    assert result.to_dict()["summaries"][0]["candidateSiteId"] == "CS-GO-001"


def test_decision_workflow_closed_loop_with_realization_and_audit() -> None:
    audit_log = InMemoryAuditLog()
    hook = CandidateSiteRealizationHook()
    workflow = SiteScoreDecisionWorkflow(audit_log=audit_log, hooks=[hook])
    repository = InMemorySiteScoreRepository()
    report = SiteScoreReportService(repository=repository).score_candidates(
        [GO_SITE], scored_at=PREDICTION_TIME
    )[0]

    decision = workflow.open_decision(report, created_by="analyst-a")
    assert decision.status is DecisionStatus.SYSTEM_RECOMMENDED
    assert decision.recommendation is SiteScoreRecommendation.GO

    decision = workflow.submit_for_review(decision.decision_id, submitted_by="analyst-a")
    assert decision.status is DecisionStatus.PENDING_REVIEW

    # High-risk approval requires a reason — never optimistic.
    with pytest.raises(SiteScoreDecisionError):
        workflow.decide(decision.decision_id, action=DecisionAction.APPROVE, actor="director-b")

    outcome = workflow.decide(
        decision.decision_id,
        action=DecisionAction.APPROVE,
        actor="director-b",
        reason="商圈需求充足、回本期符合標準",
    )
    assert outcome.decision.status is DecisionStatus.APPROVED
    assert outcome.decision.decision_id == decision.decision_id
    assert outcome.audit_event_id
    assert len(outcome.realization_events) == 1

    # Inputs are frozen on approval: snapshot ids, model version, policy version.
    event = outcome.realization_events[0]
    assert event.model_version == report.model_version
    assert event.policy_version == decision.policy_version
    assert event.input_snapshot_ids == report.source_snapshot_ids
    assert event.feature_snapshot_time == report.feature_snapshot_time

    realized = hook.get("CS-GO-001")
    assert realized is not None
    assert realized.site_status == "approved"
    assert realized.baseline_trajectory == report.baseline_trajectory()

    approve_events = [e for e in audit_log.list_events() if e.action == "approve"]
    assert len(approve_events) == 1
    assert approve_events[0].metadata["reason"] == "商圈需求充足、回本期符合標準"
    assert approve_events[0].metadata["realized_sites"] == 1


def test_decision_workflow_reject_and_request_revision_paths() -> None:
    workflow = SiteScoreDecisionWorkflow()
    repository = InMemorySiteScoreRepository()
    report = SiteScoreReportService(repository=repository).score_candidates(
        [GO_SITE], scored_at=PREDICTION_TIME
    )[0]

    rejected = workflow.open_decision(report, created_by="analyst-a")
    workflow.submit_for_review(rejected.decision_id, submitted_by="analyst-a")
    reject_outcome = workflow.decide(
        rejected.decision_id, action=DecisionAction.REJECT, actor="director-b", reason="租金過高"
    )
    assert reject_outcome.decision.status is DecisionStatus.REJECTED
    assert reject_outcome.realization_events == ()

    revised = workflow.open_decision(report, created_by="analyst-a")
    workflow.submit_for_review(revised.decision_id, submitted_by="analyst-a")
    revise_outcome = workflow.decide(
        revised.decision_id, action=DecisionAction.REQUEST_REVISION, actor="director-b"
    )
    assert revise_outcome.decision.status is DecisionStatus.DRAFT
    # A returned decision can be resubmitted for review.
    resubmitted = workflow.submit_for_review(revised.decision_id, submitted_by="analyst-a")
    assert resubmitted.status is DecisionStatus.PENDING_REVIEW


def test_sitescore_api_scores_reports_and_runs_decision_loop() -> None:
    client = TestClient(create_app(), headers=SITESCORE_HEADERS)

    score_response = client.post(
        "/sitescore/score-jobs",
        json={
            "prediction_origin_time": PREDICTION_TIME.isoformat(),
            "features": [
                {
                    "candidate_site_id": "CS-API-001",
                    "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
                    "heat_zone_score": 82,
                    "monthly_rent": 60_000,
                    "area_ping": 25,
                    "comparable_store_count": 5,
                    "comparable_monthly_revenue_p50": 480_000,
                    "buildout_capex": 2_500_000,
                    "gross_margin_ratio": 0.6,
                    "average_confidence": 0.92,
                    "data_quality_score": 0.95,
                    "source_snapshot_ids": ["listing-20260627"],
                }
            ],
        },
        headers={"x-correlation-id": "corr-ss-1", "Idempotency-Key": "ss-idem-1"},
    )
    assert score_response.status_code == 202
    body = score_response.json()
    assert body["created"] is True
    assert body["correlation_id"] == "corr-ss-1"
    assert body["summaries"][0]["recommendation"] == "GO"
    report_id = body["reports"][0]["report_id"]

    # Idempotent replay returns the same job without re-scoring (version stays 1).
    replay = client.post(
        "/sitescore/score-jobs",
        json={"features": []},
        headers={"x-correlation-id": "corr-ss-1", "Idempotency-Key": "ss-idem-1"},
    )
    assert replay.json()["created"] is False
    assert replay.json()["reports"][0]["report_id"] == report_id

    listing = client.get("/sitescore/reports")
    assert listing.json()["count"] == 1
    detail = client.get("/sitescore/reports/CS-API-001")
    assert detail.json()["version_count"] == 1

    open_response = client.post(
        "/sitescore/decisions",
        json={"report_id": report_id, "created_by": "analyst-a"},
    )
    assert open_response.status_code == 201
    decision_id = open_response.json()["decision_id"]
    assert open_response.json()["decision_status"] == "PENDING_REVIEW"

    # Approval without a reason is rejected (high risk, not optimistic).
    missing_reason = client.post(
        f"/sitescore/decisions/{decision_id}/decision",
        json={"action": "APPROVE", "actor": "director-b"},
    )
    assert missing_reason.status_code == 422

    approve = client.post(
        f"/sitescore/decisions/{decision_id}/decision",
        json={"action": "APPROVE", "actor": "director-b", "reason": "符合展店標準"},
    )
    assert approve.status_code == 200
    approve_body = approve.json()
    assert approve_body["decision_status"] == "APPROVED"
    assert len(approve_body["realization_events"]) == 1

    realized = client.get("/sitescore/realized")
    assert realized.json()["count"] == 1
    assert realized.json()["items"][0]["candidate_site_id"] == "CS-API-001"
    assert realized.json()["items"][0]["site_status"] == "approved"

    audit = client.get("/audit/events", params={"correlation_id": "corr-ss-1"})
    assert any(event["action"] == "run_model" for event in audit.json()["events"])


def test_sitescore_prediction_run_replay() -> None:
    client = TestClient(create_app(), headers=SITESCORE_HEADERS)
    score_response = client.post(
        "/sitescore/score-jobs",
        json={
            "prediction_origin_time": PREDICTION_TIME.isoformat(),
            "features": [
                {
                    "candidate_site_id": "CS-REPLAY-001",
                    "feature_snapshot_time": SNAPSHOT_TIME.isoformat(),
                    "heat_zone_score": 85,
                    "monthly_rent": 50_000,
                    "area_ping": 25,
                    "comparable_store_count": 5,
                    "comparable_monthly_revenue_p50": 450_000,
                }
            ],
        },
    )
    assert score_response.status_code == 202
    body = score_response.json()
    report = body["reports"][0]
    sitescore_run_id = report["sitescore_run_id"]
    
    # 1. Fetch sitescore run by ID
    run_response = client.get(f"/sitescore/runs/{sitescore_run_id}")
    assert run_response.status_code == 200
    run_body = run_response.json()
    assert run_body["candidate_site_id"] == "CS-REPLAY-001"
    prediction_run_id = run_body["prediction_run_id"]
    assert prediction_run_id.startswith("pred-run-sitescore-")

    # 2. Fetch prediction run by ID
    pred_run_response = client.get(f"/sitescore/prediction-runs/{prediction_run_id}")
    assert pred_run_response.status_code == 200
    pred_run_body = pred_run_response.json()
    assert pred_run_body["prediction_run"]["prediction_run_id"] == prediction_run_id
    assert len(pred_run_body["predictions"]) == 1
    assert pred_run_body["predictions"][0]["entity_id"] == "CS-REPLAY-001"

