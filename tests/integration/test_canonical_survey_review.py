"""Signed HTTP review of actual canonical evidence; all parents are synthetic.

These controls use the real SQL, identity, receipt and audit owners. They do not
assert real field work, production model admission, financial approval or UAT.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from modules.market_survey.domain.models import PromotionStatus
from modules.market_survey.infrastructure.assignment_repository import SurveyRevisionConflict
from shared.auth import Role
from tests.integration.test_canonical_survey_assignments import (
    ACTOR,
    ASSIGNEE,
    BASE,
    CID,
    TENANT,
    _business,
    _payload,
)
from tests.integration.test_canonical_survey_assignments import (
    context as context,
)
from tests.integration.test_survey_assignment_verified_content import _submit_payload


@pytest.fixture
def review(context):
    made = context.client.post(BASE + "/assignments", json=_payload(),
        headers={**context.headers(), "Idempotency-Key": "create-review-task"})
    assert made.status_code == 201, made.text
    identity = made.json()["assignment"]["assignment_id"]
    submitted = context.client.post(f"{BASE}/assignments/{identity}/submit", json=_submit_payload(),
        headers={**context.headers(ASSIGNEE), "Idempotency-Key": "submit-review-task"})
    assert submitted.status_code == 200, submitted.text
    context.bundle.identity_store.set_account_roles(ACTOR, [Role.SITE_REVIEWER])
    context.identity = identity
    context.repo = context.bundle.survey_assignment_repository_for_tenant(TENANT)
    context.survey_id = submitted.json()["surveyId"]
    return context


def _review_body(**changes):
    return {"expectedRevision": 1, "decision": "APPROVED",
        "reason": "Independently inspected the submitted synthetic field evidence",
        "reviewChecklist": {"checkedOriginalObservation": True},
        "conditions": [], **changes}


def _review(review, *, body=None, key="review-once", subject=ACTOR):
    return review.client.post(f"{BASE}/assignments/{review.identity}/review",
        json={"surveyId": review.survey_id, **(body or _review_body())},
        headers={**review.headers(subject), "Idempotency-Key": key})


def _evidence(review, subject=ACTOR):
    return review.client.get(f"{BASE}/assignments/{review.identity}/evidence",
        headers=review.headers(subject))


@pytest.mark.parametrize("decision", ["APPROVED", "REJECTED", "NEEDS_REVISION"])
def test_current_independent_reviewer_can_save_native_outcome_without_listing_update(review, decision):
    before = deepcopy(review.repo.get_evidence(review.survey_id))
    read = _evidence(review)
    assert read.status_code == 200
    assert read.json()["revision"] == 1
    assert read.json()["canRequestReview"] is True
    assert _evidence(review, ASSIGNEE).json()["canRequestReview"] is False
    response = _review(review, body=_review_body(decision=decision))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["evidence"]["revision"] == 2
    assert result["evidence"]["review_status"] == decision
    record = result["evidence"]["review_record"]
    assert record["reviewer_id"] == ACTOR
    assert record["review_comment"] == _review_body()["reason"]
    assert record["review_checklist"] == _review_body()["reviewChecklist"]
    assert result["auditEventIds"] and result["receiptId"] and not result["replayed"]
    after = review.repo.get_evidence(review.survey_id)
    assert replace(after, review_status=before.review_status, review_record=before.review_record,
                   updated_at=before.updated_at) == before
    assert after.promotion_status == PromotionStatus.NOT_PROMOTED
    assert _evidence(review).json()["review_record"] == record


def test_exact_retry_keeps_one_review_and_changed_input_or_stale_version_conflicts(review):
    first = _review(review)
    assert first.status_code == 200, first.text
    frozen = _business(review.bundle.engine)
    replay = _review(review)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert {k: v for k, v in first.json().items() if k != "replayed"} == {
        k: v for k, v in replay.json().items() if k != "replayed"}
    assert _business(review.bundle.engine) == frozen
    assert _review(review, body=_review_body(decision="REJECTED")).status_code == 409
    assert _review(review, key="another-review").status_code == 409
    assert _business(review.bundle.engine) == frozen


@pytest.mark.parametrize("subject,roles", [(ASSIGNEE, [Role.SITE_REVIEWER]), (ACTOR, [Role.EXPANSION_USER])])
def test_self_review_and_actual_nonreviewer_roles_cannot_write(review, subject, roles):
    review.bundle.identity_store.set_account_roles(subject, roles)
    frozen = _business(review.bundle.engine)
    assert _review(review, subject=subject).status_code == 403
    assert _business(review.bundle.engine) == frozen


@pytest.mark.parametrize("claim,value", [("reviewer_id", ACTOR), ("tenant_id", TENANT),
    ("reviewer_roles", ["PLATFORM_ADMIN"]), ("expectedRevision", True),
    ("decision", "PENDING_REVIEW"), ("reason", " "), ("conditions", [""])])
def test_review_does_not_accept_caller_authority_or_invalid_command(review, claim, value):
    frozen = _business(review.bundle.engine)
    assert _review(review, body=_review_body(**{claim: value})).status_code == 422
    assert _business(review.bundle.engine) == frozen


@pytest.mark.parametrize("revocation", ["role", "parent"])
def test_replay_still_requires_current_role_and_canonical_parent(review, revocation):
    assert _review(review).status_code == 200
    if revocation == "role":
        review.bundle.identity_store.set_account_roles(ACTOR, [Role.EXPANSION_USER])
    else:
        review.metadata.save_listing_metadata("listing-owned", {"tenantId": "foreign-tenant"})
    frozen = _business(review.bundle.engine)
    assert _review(review).status_code == (403 if revocation == "role" else 404)
    assert _business(review.bundle.engine) == frozen


def test_review_audit_failure_rolls_back_evidence_receipt_and_outbox_then_same_key_recovers(review, monkeypatch):
    original = review.bundle.audit_log.record
    def fail(event):
        if event.event_type == "market_survey.survey_reviewed.v1":
            raise RuntimeError("deliberate owned review audit failure")
        return original(event)
    frozen = _business(review.bundle.engine)
    monkeypatch.setattr(review.bundle.audit_log, "record", fail)
    assert _review(review).status_code == 503
    assert _business(review.bundle.engine) == frozen
    monkeypatch.setattr(review.bundle.audit_log, "record", original)
    assert _review(review).status_code == 200


def test_insertion_api_remains_immutable_after_review(review):
    assert _review(review).status_code == 200
    value = review.repo.get_evidence(review.survey_id)
    with review.repo.command(), pytest.raises(SurveyRevisionConflict, match="IMMUTABLE"):
        review.repo.save_evidence(value)


def test_direct_owner_does_not_borrow_roles_from_body(review):
    principal = replace(review.principal, roles=frozenset({Role.EXPANSION_USER}))
    service = review.factory(principal, correlation_id=CID)
    with pytest.raises(PermissionError):
        service.review_evidence(principal, identity=review.identity, payload={"surveyId": review.survey_id, **_review_body()},
            idempotency_key="direct-review", correlation_id=CID)


def test_review_pins_the_exact_survey_not_only_a_reusable_revision_number(review):
    frozen = _business(review.bundle.engine)
    assert _review(review, body=_review_body(surveyId="another-survey")).status_code == 409
    assert _business(review.bundle.engine) == frozen


@pytest.mark.parametrize("flag", ["is_retracted", "is_superseded"])
def test_review_and_exact_replay_refuse_current_retracted_or_superseded_evidence(review, flag):
    assert _review(review).status_code == 200
    value, revision = review.repo.get_evidence_versioned(review.survey_id)
    # Deliberate storage corruption models a later native lifecycle owner;
    # no HTTP endpoint is granted authority to change submitted payload here.
    setattr(value, flag, True)
    assert review.repo._store.compare_and_put(review.repo.EVIDENCE, review.survey_id,
        value, expected_version=revision) == revision + 1
    frozen = _business(review.bundle.engine)
    assert _review(review).status_code == 409
    assert _business(review.bundle.engine) == frozen
