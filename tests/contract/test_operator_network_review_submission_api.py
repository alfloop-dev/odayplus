"""Canonical saved-report submit uses durable actor-bound ownership, no inference."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from tests.integration.test_operator_canonical_wiring import CanonicalHarness, _seed_candidate
from tests.unit.test_network_canonical_review_projection import report

BASE = "/api/v1/operator/network-reviews/candidates"
HEADERS = {
    "x-subject-id": "submit-author",
    "x-roles": "expansion_user",
    "x-operator-role": "expansion-staff",
    "x-tenant-id": "tenant-a",
    "If-Actor-Match": "submit-author",
    "Idempotency-Key": "submit-key",
    "X-Correlation-Id": "submit-correlation",
}


def seeded(tmp_path):
    harness = CanonicalHarness(tmp_path / "submit.sqlite")
    candidate = _seed_candidate(harness, "tenant-a")
    saved = harness.sitescore("tenant-a").save_report(
        replace(report(), candidate_site_id=candidate)
    )
    return harness, candidate, saved


def test_actual_saved_report_has_readonly_actor_bound_submission_context(tmp_path):
    harness, candidate, saved = seeded(tmp_path)
    try:
        with TestClient(harness.app()) as client:
            response = client.get(f"{BASE}/{candidate}/submission-context", headers=HEADERS)
            assert response.status_code == 200, response.text
            context = response.json()
            assert context["source"] == "canonical"
            assert context["canSubmit"] is True
            assert context["actorSubjectId"] == "submit-author"
            assert context["target"]["expectedReportId"] == saved.report_id
            assert context["target"]["expectedReportVersion"] == saved.report_version
            assert context["target"]["expectedDecisionVersion"] is None
            assert harness.decisions("tenant-a").list_decisions() == []
            assert harness.runtime.calls == []
    finally:
        harness.close()


def test_actual_submit_persists_own_decision_approval_candidate_and_receipt(tmp_path):
    harness, candidate, saved = seeded(tmp_path)
    try:
        with TestClient(harness.app()) as client:
            response = client.get(f"{BASE}/{candidate}/submission-context", headers=HEADERS)
            assert response.status_code == 200, response.text
            body = {
                **response.json()["target"],
                "expectedActorSubjectId": "submit-author",
                "expectedTenantId": "tenant-a",
                "submissionSummary": "actual saved report ready for human review",
                "reviewerGroupId": response.json()["reviewerGroups"][0]["id"],
            }
            posted = client.post(f"{BASE}/{candidate}/submit", headers=HEADERS, json=body)
            assert posted.status_code == 200, posted.text
            value = posted.json()
            records = value["records"]
            decision = harness.decisions("tenant-a").get_decision(records["decisionId"])
            assert decision.status.value == "PENDING_REVIEW"
            assert decision.version == 2 and decision.created_by == "submit-author"
            assert records["approvalId"] and records["approvalId"] != decision.decision_id
            assert (
                decision.report_id == saved.report_id
                and decision.report_version == saved.report_version
            )
            actual = harness.listing("tenant-a").list_candidates()[0]
            assert actual.review_id == decision.decision_id
            assert value["commandReceipt"]["actorSubjectId"] == "submit-author"
            assert value["commandReceipt"]["tenantId"] == "tenant-a"
            assert value["commandReceipt"]["idempotencyKey"] == "submit-key"
            assert harness.runtime.calls == []
    finally:
        harness.close()


def form(context):
    return {
        **context["target"],
        "expectedActorSubjectId": HEADERS["x-subject-id"],
        "expectedTenantId": "tenant-a",
        "submissionSummary": "Explicit current report and saved candidate submission summary",
        "reviewerGroupId": context["reviewerGroups"][0]["id"],
    }


@pytest.mark.parametrize("field", ["submissionSummary", "reviewerGroupId"])
def test_required_submission_fields_are_not_filled_from_actor_or_default_group(tmp_path, field):
    harness, candidate, _ = seeded(tmp_path)
    try:
        with TestClient(harness.app()) as client:
            context = client.get(f"{BASE}/{candidate}/submission-context", headers=HEADERS).json()
            body = form(context)
            body.pop(field)
            before = harness.decisions("tenant-a").list_decisions()
            response = client.post(f"{BASE}/{candidate}/submit", headers=HEADERS, json=body)
            assert response.status_code == 422
            assert harness.decisions("tenant-a").list_decisions() == before
    finally:
        harness.close()


@pytest.mark.parametrize(
    "change", ["bodyactor", "headeractor", "tenant", "group", "digest", "reportversion"]
)
def test_body_intent_catalog_and_actual_physical_context_conflicts_create_no_decision(
    tmp_path, change
):
    harness, candidate, _ = seeded(tmp_path)
    try:
        with TestClient(harness.app()) as client:
            context = client.get(f"{BASE}/{candidate}/submission-context", headers=HEADERS).json()
            body = form(context)
            headers = dict(HEADERS)
            if change == "bodyactor":
                body["expectedActorSubjectId"] = "other-actor"
            if change == "headeractor":
                headers["If-Actor-Match"] = "other-actor"
            if change == "tenant":
                body["expectedTenantId"] = "other-tenant"
            if change == "group":
                body["reviewerGroupId"] = "client-created-approval-grant"
            if change == "digest":
                body["expectedCandidateDigest"] = "f" * 64
            if change == "reportversion":
                body["expectedReportVersion"] += 1
            response = client.post(f"{BASE}/{candidate}/submit", headers=headers, json=body)
            assert response.status_code == 409, response.text
            assert harness.decisions("tenant-a").list_decisions() == []
            assert harness.runtime.calls == []
    finally:
        harness.close()


def test_pending_context_is_true_recovery_and_different_payload_cannot_rewrite_summary(tmp_path):
    harness, candidate, _ = seeded(tmp_path)
    try:
        with TestClient(harness.app()) as client:
            original = client.get(f"{BASE}/{candidate}/submission-context", headers=HEADERS).json()
            body = form(original)
            created = client.post(f"{BASE}/{candidate}/submit", headers=HEADERS, json=body)
            assert created.status_code == 200, created.text
            pending = client.get(f"{BASE}/{candidate}/submission-context", headers=HEADERS).json()
            assert pending["canSubmit"] is False and pending["blockCode"] == "ALREADY_PENDING"
            assert pending["pendingReview"]["decisionId"] == created.json()["records"]["decisionId"]
            assert pending["pendingReview"]["verifiedSubmitterSubjectId"] == HEADERS["x-subject-id"]
            assert pending["pendingReview"]["submissionSummary"] == body["submissionSummary"]
            changed = {
                **form(pending),
                "submissionSummary": "new text cannot pretend applied to pending review",
            }
            rejected = client.post(
                f"{BASE}/{candidate}/submit",
                headers={**HEADERS, "Idempotency-Key": "different-key"},
                json=changed,
            )
            assert rejected.status_code == 409, rejected.text
            actual = harness.decisions("tenant-a").get_decision(
                created.json()["records"]["decisionId"]
            )
            assert actual.submission_summary == body["submissionSummary"] and actual.version == 2
            replay = client.post(f"{BASE}/{candidate}/submit", headers=HEADERS, json=body)
            assert replay.status_code == 200 and replay.json()["idempotentReplay"] is True
            assert replay.json()["records"] == created.json()["records"]
    finally:
        harness.close()
