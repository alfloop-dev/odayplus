"""Actual PostgreSQL review CAS/rollback/current parent controls on two engines.

Synthetic parents and people prove engineering behavior only, never human UAT.
Uses the same unmodified migration chain and owned database lifecycle as the
original canonical survey PostgreSQL controls.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace

import pytest

from modules.market_survey.application.assignment_owner import SurveyAssignmentNotFound
from modules.market_survey.infrastructure.assignment_repository import (
    SurveyOwnerUnavailable,
    SurveyRevisionConflict,
)
from modules.site_feasibility import SiteFeasibilityService
from modules.site_feasibility.domain.models import FeasibilityDecision
from shared.auth import Role
from tests.integration.test_canonical_survey_assignments_postgresql import (
    _business,
    _create,
    _observe_real_guard_transaction,
    _service,
    _wait_for_owned_backend_lock,
)
from tests.integration.test_canonical_survey_assignments_postgresql import (
    owners as owners,
)
from tests.integration.test_canonical_survey_review import _physical_attributes, _review_body
from tests.integration.test_survey_assignment_verified_content import _submit_payload

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def review(owners):
    identity = _create(owners)["assignment"]["assignment_id"]
    result = _service(owners, principal=owners.surveyor).submit_evidence(owners.surveyor,
        identity=identity, payload=_submit_payload(), idempotency_key="submit-review-pg", correlation_id=owners.cid)
    owners.identity, owners.survey_id = identity, result["surveyId"]
    owners.reviewer = replace(owners.principal, roles=frozenset({Role.SITE_REVIEWER}))
    owners.repo = owners.first.survey_assignment_repository_for_tenant(owners.tenant)
    return owners


def _review(owners, *, index=0, key="review-pg", body=None, principal=None):
    principal = principal or owners.reviewer
    return _service(owners, index, principal).review_evidence(principal, identity=owners.identity,
        payload={"surveyId": owners.survey_id, **(body or _review_body())}, idempotency_key=key, correlation_id=owners.cid)


@pytest.mark.parametrize("drainage,expected", [(True, FeasibilityDecision.FEASIBLE),
    (False, FeasibilityDecision.CONDITIONAL), (None, FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY)])
def test_structured_physical_facts_and_review_survive_two_real_engines(owners, drainage, expected):
    identity = _create(owners)["assignment"]["assignment_id"]
    attributes = _physical_attributes(drainage)
    submitted = _service(owners, principal=owners.surveyor).submit_evidence(owners.surveyor,
        identity=identity, payload=_submit_payload(attributes=attributes),
        idempotency_key="physical-submit-pg", correlation_id=owners.cid)
    repo = owners.second.survey_assignment_repository_for_tenant(owners.tenant)
    before = repo.get_evidence(submitted["surveyId"])
    assert before.attributes == attributes and before.review_status.value == "PENDING_REVIEW"
    market_context = {"context_id": "synthetic-pg-physical-context", "identity": {
        "site_id": "candidate-owned", "metadata": {"zoning": "commercial"}}}
    def evaluate(evidence):
        return SiteFeasibilityService().evaluate_feasibility("candidate-owned", market_context,
            [evidence.to_dict()], tenant_id=owners.tenant)
    assert evaluate(before).decision.recommendation is FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY
    reviewer = replace(owners.principal, roles=frozenset({Role.SITE_REVIEWER}))
    reviewed = _service(owners, 1, reviewer).review_evidence(reviewer, identity=identity,
        payload={"surveyId": submitted["surveyId"], **_review_body()},
        idempotency_key="physical-review-pg", correlation_id=owners.cid)
    assert reviewed["evidence"]["attributes"] == attributes
    after = owners.first.survey_assignment_repository_for_tenant(owners.tenant).get_evidence(before.survey_id)
    assert after.attributes == before.attributes
    assert replace(after, review_status=before.review_status, review_record=before.review_record,
                   updated_at=before.updated_at) == before
    result = evaluate(after)
    assert result.decision.recommendation is expected
    assert result.metadata["binding_recommendation_allowed"] is (expected is FeasibilityDecision.FEASIBLE)


def test_native_review_is_visible_from_another_engine_and_exact_retry_is_once(review):
    original = deepcopy(review.repo.get_evidence(review.survey_id))
    result = _review(review)
    assert result["evidence"]["revision"] == 2
    before = _business(review.first.engine)
    read = _service(review, 1, review.reviewer).evidence(review.reviewer, identity=review.identity)
    assert read == result["evidence"]
    replay = _review(review, index=1)
    assert replay["replayed"] and replay["receiptId"] == result["receiptId"]
    assert replay["auditEventIds"] == result["auditEventIds"]
    assert _business(review.first.engine) == before == _business(review.second.engine)
    actual = review.repo.get_evidence(review.survey_id)
    assert replace(actual, review_status=original.review_status, review_record=original.review_record,
                   updated_at=original.updated_at) == original


def test_two_real_writers_cannot_both_review_one_evidence_revision(review, monkeypatch):
    second = _service(review, 1, review.reviewer)
    entered, observed = _observe_real_guard_transaction(monkeypatch, second)
    def command():
        try:
            return second.review_evidence(review.reviewer, identity=review.identity, payload={"surveyId": review.survey_id, **_review_body()},
                idempotency_key="review-second-writer", correlation_id=review.cid)
        except SurveyRevisionConflict as exc:
            return exc
    with ThreadPoolExecutor(max_workers=1) as executor:
        with review.first.engine.transaction():
            assert _review(review)["evidence"]["revision"] == 2
            expected = _business(review.first.engine)
            pending = executor.submit(command)
            assert entered.wait(5)
            assert _wait_for_owned_backend_lock(review.database, observed["pid"])
            assert not pending.done()
        result = pending.result(timeout=20)
    assert isinstance(result, SurveyRevisionConflict)
    assert _business(review.first.engine) == expected == _business(review.second.engine)


def test_parent_revocation_under_lock_blocks_review_and_replay(review, monkeypatch):
    second = _service(review, 1, review.reviewer)
    entered, observed = _observe_real_guard_transaction(monkeypatch, second)
    def command():
        try:
            return second.review_evidence(review.reviewer, identity=review.identity, payload={"surveyId": review.survey_id, **_review_body()},
                idempotency_key="revoked-parent-review", correlation_id=review.cid)
        except SurveyAssignmentNotFound as exc:
            return exc
    with ThreadPoolExecutor(max_workers=1) as executor:
        with review.first.engine.transaction(), review.listing.candidate_guard("candidate-owned"), \
                review.metadata.candidate_metadata_guard("candidate-owned"):
            current = review.metadata.get_listing_metadata("listing-owned")
            review.metadata.save_listing_metadata("listing-owned", {**current, "storeId": "revoked-store"})
            expected = _business(review.first.engine)
            pending = executor.submit(command)
            assert entered.wait(5)
            assert _wait_for_owned_backend_lock(review.database, observed["pid"])
        result = pending.result(timeout=20)
    assert isinstance(result, SurveyAssignmentNotFound)
    assert _business(review.first.engine) == expected == _business(review.second.engine)


def test_audit_failure_reverts_review_receipt_and_outbox_on_both_engines(review, monkeypatch):
    before = _business(review.first.engine)
    original = review.first.audit_log.record
    def fail(event):
        if event.event_type == "market_survey.survey_reviewed.v1":
            raise RuntimeError("deliberate PostgreSQL review audit failure")
        return original(event)
    monkeypatch.setattr(review.first.audit_log, "record", fail)
    with pytest.raises(RuntimeError, match="deliberate"):
        _review(review)
    assert _business(review.first.engine) == before == _business(review.second.engine)
    monkeypatch.setattr(review.first.audit_log, "record", original)
    assert _review(review, index=1)["evidence"]["revision"] == 2


@pytest.mark.parametrize("field,value", [("attributes", {"forged": True}), ("submitter_id", "other"),
    ("observation_id", "another-observation"), ("media_attachments", ["unverified"])])
def test_review_port_cannot_overwrite_submitted_payload(review, field, value):
    before = _business(review.first.engine)
    actual = review.repo.get_evidence(review.survey_id)
    setattr(actual, field, value)
    with review.repo.command(), pytest.raises(SurveyOwnerUnavailable, match="MUTATION_INVALID"):
        review.repo.save_evidence_review(actual, expected_version=1)
    assert _business(review.first.engine) == before == _business(review.second.engine)
