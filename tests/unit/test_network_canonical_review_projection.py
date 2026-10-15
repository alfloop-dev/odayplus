"""Nonempty canonical reviews read frozen reports, never fixture scores."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from modules.opsboard.application.network_reviews import NetworkReviewConflict, NetworkReviewService
from modules.sitescore.domain.scoring import Interval, SiteScoreRecommendation, SiteScoreReport
from shared.domain.models import MEASUREMENT_MEASURED, MEASUREMENT_UNMEASURED
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.repositories import (
    DurableDecisionStore,
    DurableSiteScoreRepository,
)
from shared.workflow.sitescore import DecisionAction, SiteScoreDecisionWorkflow


def report(candidate_id="synthetic-review-candidate-a", **changes):
    at = datetime(2026, 10, 13, tzinfo=UTC)
    band = Interval(100000, 150000, 200000)
    value = SiteScoreReport(
        sitescore_run_id=f"synthetic-run-{candidate_id}", candidate_site_id=candidate_id,
        target_format_code="synthetic-format", recommendation=SiteScoreRecommendation.GO,
        m1=band, m3=band, m6=band, m12=band, payback_period=Interval(24, 36, 48),
        payback_p50_months=36, rent_reasonableness=0.6, cannibalization_risk=0.2,
        comparable_stores=3, key_positive_factors=(), key_negative_factors=("recorded risk",),
        confidence=0.84, model_version="synthetic-model-a", feature_version="synthetic-view",
        feature_snapshot_time=at, prediction_origin_time=at, scored_at=at,
        source_snapshot_ids=("synthetic-snapshot-a",), heat_zone_id="synthetic-zone-a",
    )
    return replace(value, **changes)


def components(path):
    engine = SqliteEngine(path)
    store = SqliteDocumentStore(engine)
    scores = DurableSiteScoreRepository(store)
    decisions = DurableDecisionStore(store)
    workflow = SiteScoreDecisionWorkflow(store=decisions)
    service = NetworkReviewService(seed_fixtures=False, require_canonical=True,
                                   decision_repository=decisions, sitescore_repository=scores,
                                   decision_workflow=workflow)
    return engine, scores, decisions, workflow, service


def open_review(scores, workflow, value):
    saved = scores.save_report(value)
    decision = workflow.open_decision(saved, created_by="synthetic-author", tenant_id="synthetic-tenant")
    workflow.submit_for_review(decision.decision_id, submitted_by="synthetic-author")
    return saved, decision


def test_nonempty_durable_review_preserves_measurement_not_suitability_score(tmp_path):
    engine, scores, decisions, workflow, service = components(tmp_path / "review.sqlite")
    saved, decision = open_review(scores, workflow, report())
    before = engine.query("SELECT collection, doc_id, data FROM durable_documents ORDER BY collection, doc_id")
    snapshot = service.snapshot(correlation_id="synthetic-read")
    row = snapshot["reviews"][0]
    assert snapshot["source"] == "canonical"
    assert row["id"] == decision.decision_id and row["candidateId"] == saved.candidate_site_id
    assert row["reportId"] == saved.report_id and row["reportVersion"] == saved.report_version
    assert row["score"] is None and snapshot["candidates"][0]["score"] is None
    assert row["confidence"] == 0.84 and row["confidenceStatus"] == MEASUREMENT_MEASURED
    assert row["reportAvailability"] == "available"
    assert row["modelVersion"] == saved.model_version and row["datasetSnapshotId"] == "synthetic-snapshot-a"
    # This is the real producer's nonempty frontend contract, not a seed adapter.
    for key in ("eventChips", "history", "requestedBy", "payback", "m12P50", "risk", "notes"):
        assert key in row
    assert isinstance(row["risk"], str) and row["eventChips"] == []
    assert row["requestedBy"] == "synthetic-author"
    assert row["dueAt"] is None and row["fieldVisit"] is None and row["brokerContact"] is None
    assert engine.query("SELECT collection, doc_id, data FROM durable_documents ORDER BY collection, doc_id") == before
    engine.close()


def test_restart_and_newer_other_reports_do_not_replace_decision_frozen_version(tmp_path):
    path = tmp_path / "versions.sqlite"
    engine, scores, decisions, workflow, service = components(path)
    saved, decision = open_review(scores, workflow, report(confidence=0.2))
    scores.save_report(report(confidence=0.99, model_version="synthetic-new-model",
                              recommendation=SiteScoreRecommendation.REJECT))
    open_review(scores, workflow, report("synthetic-review-candidate-b", confidence=0.71))
    engine.close()
    restarted, _, _, _, service = components(path)
    row = next(r for r in service.snapshot()["reviews"] if r["id"] == decision.decision_id)
    assert row["score"] is None and row["confidence"] == 0.2
    assert row["reportId"] == saved.report_id and row["reportVersion"] == 1
    assert row["modelVersion"] == "synthetic-model-a" and row["recommendation"] == "GO"
    restarted.close()


@pytest.mark.parametrize(("status", "value", "expected"), [
    (MEASUREMENT_MEASURED, 0.0, 0.0),
    (MEASUREMENT_UNMEASURED, 0.84, None),
    (MEASUREMENT_MEASURED, float("nan"), None),
])
def test_zero_unmeasured_and_nonfinite_confidence_remain_distinct(tmp_path, status, value, expected):
    engine, scores, _, workflow, service = components(tmp_path / "confidence.sqlite")
    open_review(scores, workflow, report(confidence=value, confidence_status=status))
    row = service.snapshot()["reviews"][0]
    assert row["score"] is None and row["confidence"] == expected
    assert row["confidenceStatus"] == (MEASUREMENT_MEASURED if expected is not None else MEASUREMENT_UNMEASURED)
    engine.close()


@pytest.mark.parametrize("mismatch", ["candidate", "report", "version", "model"])
def test_conflicting_frozen_report_never_supplies_another_record_measurements(tmp_path, mismatch):
    engine, scores, decisions, workflow, service = components(tmp_path / "conflict.sqlite")
    saved, decision = open_review(scores, workflow, report())
    changes = {"candidate": {"candidate_site_id": "synthetic-other-candidate"},
               "report": {"report_id": "synthetic-other-report"},
               "version": {"report_version": saved.report_version + 1},
               "model": {"model_version": "synthetic-other-model"}}[mismatch]
    decisions.save_report(decision.decision_id, replace(saved, **changes))
    row = service.snapshot()["reviews"][0]
    assert row["reportAvailability"] == "conflict"
    assert row["score"] is None and row["confidence"] is None and row["zoneLabel"] is None
    assert row["payback"] is None and row["m12P50"] is None and row["datasetSnapshotId"] is None
    assert row["reportId"] == saved.report_id and row["reportVersion"] == saved.report_version
    engine.close()


def test_missing_report_is_not_replaced_by_latest_or_fixture_values(tmp_path):
    engine, scores, decisions, workflow, service = components(tmp_path / "missing.sqlite")
    saved, decision = open_review(scores, workflow, report())
    engine.execute("DELETE FROM durable_documents WHERE collection = ? AND doc_id = ?",
                   ("sitescore.decision_reports", decision.decision_id))
    row = service.snapshot()["reviews"][0]
    assert row["reportAvailability"] == "unavailable"
    assert row["score"] is None and row["confidence"] is None
    assert row["reportId"] == saved.report_id and row["reportVersion"] == saved.report_version
    assert row["modelVersion"] == decision.model_version
    assert row["payback"] is None and row["m12P50"] is None
    engine.close()


def test_terminal_review_projects_real_actor_reason_and_recorded_state(tmp_path):
    engine, scores, _, workflow, service = components(tmp_path / "terminal.sqlite")
    _, decision = open_review(scores, workflow, report())
    workflow.decide(decision.decision_id, action=DecisionAction.REJECT,
                    actor="synthetic-independent-reviewer", reason="recorded synthetic rejection")
    row = service.snapshot()["reviews"][0]
    assert row["canonicalDecisionStatus"] == "REJECTED" and row["status"] == "rejected"
    assert row["decision"]["decision"] == "REJECT" and row["decision"]["finalLabel"] == "Rejected"
    assert row["decision"]["reason"] == "recorded synthetic rejection"
    assert row["decision"]["decidedBy"] == "synthetic-independent-reviewer"
    assert row["decision"]["decidedAt"] == row["history"][0]["t"]
    engine.close()


def test_system_recommendation_and_revision_are_not_pending_or_terminal_decisions(tmp_path):
    engine, scores, _, workflow, service = components(tmp_path / "read-status.sqlite")
    saved = scores.save_report(report())
    decision = workflow.open_decision(saved, created_by="synthetic-author", tenant_id="synthetic-tenant")
    snapshot = service.snapshot()
    assert snapshot["counts"] == {"reviews": 1, "pending": 0, "decided": 0}
    assert snapshot["reviews"][0]["statusLabel"] == "系統建議（尚未送審）"
    workflow.submit_for_review(decision.decision_id, submitted_by="synthetic-author")
    assert service.snapshot()["counts"] == {"reviews": 1, "pending": 1, "decided": 0}
    workflow.decide(decision.decision_id, action=DecisionAction.REQUEST_REVISION,
                    actor="synthetic-independent-reviewer", reason="recorded required revision")
    snapshot = service.snapshot()
    assert snapshot["reviews"][0]["canonicalDecisionStatus"] == "DRAFT"
    assert snapshot["counts"] == {"reviews": 1, "pending": 0, "decided": 0}
    engine.close()


def decide(service, decision_id, key="synthetic-preflight-key"):
    return service.decide_review(review_id=decision_id, decision="REJECT", reason="recorded synthetic rejection",
                                 override_ack=True, actor_role_id="siteReviewer", actor_name="synthetic-reviewer",
                                 idempotency_key=key, correlation_id="synthetic-preflight")


@pytest.mark.parametrize("stage", ["system", "pending", "revision"])
@pytest.mark.parametrize("mismatch", ["missing", "candidate", "report", "version", "model"])
def test_missing_or_conflicting_report_cannot_write_any_transition(tmp_path, stage, mismatch):
    engine, scores, decisions, workflow, service = components(tmp_path / "preflight.sqlite")
    saved = scores.save_report(report())
    decision = workflow.open_decision(saved, created_by="synthetic-author", tenant_id="synthetic-tenant")
    if stage != "system":
        workflow.submit_for_review(decision.decision_id, submitted_by="synthetic-author")
    if stage == "revision":
        workflow.decide(decision.decision_id, action=DecisionAction.REQUEST_REVISION,
                        actor="synthetic-reviewer", reason="recorded required revision")
    if mismatch == "missing":
        engine.execute("DELETE FROM durable_documents WHERE collection = ? AND doc_id = ?",
                       ("sitescore.decision_reports", decision.decision_id))
    else:
        changes = {"candidate": {"candidate_site_id": "synthetic-other-candidate"},
                   "report": {"report_id": "synthetic-other-report"},
                   "version": {"report_version": saved.report_version + 1},
                   "model": {"model_version": "synthetic-other-model"}}[mismatch]
        decisions.save_report(decision.decision_id, replace(saved, **changes))
    before = engine.query("SELECT collection, doc_id, data FROM durable_documents ORDER BY collection, doc_id")
    exported_before = service.export_state()
    with pytest.raises(NetworkReviewConflict, match="report"):
        decide(service, decision.decision_id)
    assert engine.query("SELECT collection, doc_id, data FROM durable_documents ORDER BY collection, doc_id") == before
    assert service.export_state() == exported_before
    engine.close()


def test_valid_frozen_report_decision_and_replay_ignore_a_later_unavailable_report(tmp_path):
    engine, scores, _, workflow, service = components(tmp_path / "replay.sqlite")
    saved, decision = open_review(scores, workflow, report())
    scores.save_report(report(confidence=0.99, model_version="synthetic-model-v2"))
    result = decide(service, decision.decision_id)
    assert result["review"]["decision_status"] == "REJECTED" and not result["idempotentReplay"]
    assert result["review"]["report_id"] == saved.report_id and result["review"]["report_version"] == 1
    engine.execute("DELETE FROM durable_documents WHERE collection = ? AND doc_id = ?",
                   ("sitescore.decision_reports", decision.decision_id))
    before = engine.query("SELECT collection, doc_id, data FROM durable_documents ORDER BY collection, doc_id")
    replay = decide(service, decision.decision_id)
    assert replay["idempotentReplay"] and replay["review"] == result["review"]
    assert engine.query("SELECT collection, doc_id, data FROM durable_documents ORDER BY collection, doc_id") == before
    engine.close()


@pytest.mark.parametrize("value", [True, float("inf"), 10**400])
def test_invalid_recorded_confidence_remains_unknown_without_a_read_exception(tmp_path, value):
    engine, scores, _, workflow, service = components(tmp_path / "invalid-confidence.sqlite")
    open_review(scores, workflow, report(confidence=value))
    row = service.snapshot()["reviews"][0]
    assert row["confidence"] is None and row["confidenceStatus"] == MEASUREMENT_UNMEASURED
    assert row["score"] is None
    engine.close()


def test_oversized_financial_measurements_remain_unknown_without_a_read_exception(tmp_path):
    engine, scores, _, workflow, service = components(tmp_path / "invalid-financial.sqlite")
    open_review(scores, workflow, report(payback_p50_months=10**400, rent_reasonableness=10**400,
                                        cannibalization_risk=10**400, m12=Interval(1, 10**400, 10**401)))
    row = service.snapshot()["reviews"][0]
    assert all(row[key] is None for key in ("payback", "m12P50", "rentReasonableness", "cannibalization"))
    assert row["confidence"] == 0.84 and row["score"] is None
    engine.close()


# Historical unknown metadata is readable but cannot authorize a fresh write.
@pytest.mark.parametrize("changes", [
    {"model_version": None}, {"model_version": "   "},
    {"report_version": True}, {"report_id": "   "},
])
def test_matching_unknown_retained_identity_cannot_authorize_a_fresh_decision(tmp_path, changes):
    engine, scores, decisions, workflow, service = components(tmp_path / "identity.sqlite")
    saved, original = open_review(scores, workflow, report())
    retained = replace(saved, **changes)
    decisions.save_report(original.decision_id, retained)
    decisions.save_decision(replace(original, **changes))
    before = engine.query("SELECT collection, doc_id, data FROM durable_documents ORDER BY collection, doc_id")
    exported = service.export_state()
    with pytest.raises(NetworkReviewConflict):
        decide(service, original.decision_id)
    assert engine.query("SELECT collection, doc_id, data FROM durable_documents ORDER BY collection, doc_id") == before
    assert service.export_state() == exported
    engine.close()
