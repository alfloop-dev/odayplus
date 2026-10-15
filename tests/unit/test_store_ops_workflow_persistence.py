"""SQLite regression of submitted workflow facts; no native action or identity is fabricated."""

from copy import deepcopy

import pytest

from modules.opsboard.application.store_ops import (
    StoreOpsConflict,
    StoreOpsLiveDataUnavailable,
    StoreOpsNotFound,
    StoreOpsPolicyError,
)
from modules.opsboard.application.store_ops_scope import binding_hash
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.engine import SqliteEngine
from tests.unit import test_store_ops_review_persistence as review_tests

case = review_tests.case
repository = review_tests.repository
service = review_tests.service
scoped = review_tests.scoped

PAYLOADS = {
    "triage": {"severity": "high", "category": "cleanliness", "evidenceStrength": "usable",
               "decision": "fastForward", "observationWindow": "Operator requests two hours",
               "needEvidence": False, "demoFastForward": True, "notes": "Retain this triage note."},
    "assign": {"ownerRoleId": "facilitiesLead", "ownerName": "Submitted assignee label",
               "slaDueAt": "2026-10-16T10:00:00+08:00", "handoffNote": "Inspect the north counter."},
    "actions": {"actionType": "remoteRestart", "title": "Requested equipment action",
                "instructions": "Review before any execution.", "checklistItems": ["Confirm incident", "Obtain authority"],
                "needEvidence": True, "requiresApproval": True, "observationWindow": "Operator-selected window",
                "remoteRestartAuditNote": "This is a plan, not a device command."},
    "field-report": {"reportedBy": "Submitted reporter label", "observedAt": "2026-10-15T11:20:30.123456+08:00",
                     "summary": "Manual observation submitted.", "checklistStatus": "blocked",
                     "attachmentNames": ["declared-only-photo.jpg"], "blocker": "Awaiting access."},
    "escalate": {"target": "growth", "urgency": "high", "reason": "Cross-team review requested.",
                 "requestedOutcome": "Assess recorded incident.", "notifyOwner": True},
    "transfer": {"targetRoleId": "supportLead", "targetOwnerName": "Submitted owner label",
                 "reason": "Service follow-up.", "handoffNote": "Retain the original observations.", "keepWatching": True},
    "camera-purpose": {"purpose": "service incident review", "cameraLocation": "Submitted location label",
                       "timeWindow": "Submitted incident interval", "retentionHours": 12,
                       "privacyAcknowledged": True, "auditNote": "Purpose-specific access review note."},
}
START = {"triage": "new", "assign": "triaged", "actions": "assigned", "field-report": "inprogress",
         "escalate": "observing", "transfer": "observing", "camera-purpose": "observing"}
FLAGS = {"triage": {"clockAdvanced": False}, "assign": {"assigneeIdentityStatus": "not_verified"},
         "actions": {"executionStatus": "not_executed", "approvalRequestStatus": "not_created"},
         "field-report": {"reporterIdentityStatus": "not_verified", "attachmentEvidenceStatus": "not_verified"},
         "escalate": {"notificationStatus": "not_sent", "routingStatus": "not_dispatched"},
         "transfer": {"assigneeIdentityStatus": "not_verified", "watchSubscriptionStatus": "not_created"},
         "camera-purpose": {}}


def prepare(engine, action):
    value = repository(engine).get_state()
    value["issues"][0]["status"] = START[action]
    if action == "camera-purpose":
        value["issues"][0]["evidenceIds"].append("recorded-camera")
        value["evidence"].append({"id": "recorded-camera", "kind": "camera", "lockedReason": "purpose required"})
    repository(engine).save_state(value)
    return value


def command(target, action, payload, key="workflow-command"):
    kwargs = {"issue_id": "review-issue", "payload": deepcopy(payload), "correlation_id": "workflow-correlation",
              "idempotency_key": key, "actor_role_id": "opsLead", "actor_name": "Caller label"}
    if action == "camera-purpose":
        return target.record_camera_purpose(**kwargs)
    return target.transition_issue(action_type=action, **kwargs)


@pytest.mark.parametrize("action", list(PAYLOADS))
def test_all_submitted_workflow_fields_survive_reopen_and_exactly_once_replay(case, action):
    engine, path = case
    original = prepare(engine, action)
    with engine.transaction():
        saved = command(service(engine), action, PAYLOADS[action])
    record = saved["issue"]["workflowRecords"][0]
    assert record["action"] == action and record["submission"] == PAYLOADS[action]
    assert record["issueId"] == "review-issue" and record["storeId"] == "review-store"
    assert record["sourceIssueUpdatedAt"] == original["issues"][0]["updatedAt"]
    assert record["evidenceIds"] == original["issues"][0]["evidenceIds"]
    assert record["actorSubjectId"] is None and record["actorEvidence"] == "caller_supplied"
    assert record["actorName"] == "Caller label"
    assert record["workflowAuditId"] == record["id"] == saved["auditEvent"]["id"]
    for name, expected in FLAGS[action].items():
        assert record[name] == expected
    event = DurableAuditLog(engine).list_events()[0]
    assert event.event_id == record["auditEventId"]
    assert event.metadata["reviewRecordHash"] == binding_hash(record)
    if action == "camera-purpose":
        assert record["submission"]["auditNote"] == PAYLOADS[action]["auditNote"]
        assert "auditNote" not in event.metadata
        assert "auditNote" not in saved["auditEvent"]["metadata"]
        assert saved["evidenceItem"]["retentionHours"] == 12
    if action == "triage":
        assert saved["issue"]["status"] == "triaged"
        assert saved["issue"]["slaDueAt"] == original["issues"][0]["slaDueAt"]
        assert saved["issue"]["createdAt"] == original["issues"][0]["createdAt"]
    assert engine.query_one("SELECT COUNT(*) AS n FROM durable_jobs")["n"] == 0
    assert "outcomeMetrics" not in saved["issue"] and "relatedGrowthId" not in saved["issue"]
    assert not repository(engine).get_state().get("attachments")
    engine.close()
    reopened = SqliteEngine(path)
    try:
        assert service(reopened).get_issue("review-issue")["workflowRecords"] == [record]
        assert service(reopened).snapshot()["issues"][0]["workflowRecords"] == [record]
        with reopened.transaction():
            replay = command(service(reopened), action, PAYLOADS[action])
        assert replay["idempotentReplay"] is True and replay["issue"]["workflowRecords"] == [record]
        assert len(DurableAuditLog(reopened).list_events()) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize("action", list(PAYLOADS))
def test_legacy_optional_inputs_remain_unset_without_fabricated_business_defaults(case, action):
    engine, _ = case
    prepare(engine, action)
    payload = {"purpose": "service incident review", "privacyAcknowledged": True} if action == "camera-purpose" else {}
    with engine.transaction():
        saved = command(service(engine), action, payload)
    submission = saved["issue"]["workflowRecords"][0]["submission"]
    assert set(submission) == set(PAYLOADS[action])
    assert all(value == payload.get(key) for key, value in submission.items())
    if action == "camera-purpose":
        assert saved["evidenceItem"]["retentionHours"] == 24  # Existing purpose policy, not a submitted value.
        assert submission["retentionHours"] is None


@pytest.mark.parametrize("action", ["transfer", "camera-purpose"])
def test_workflow_receipts_and_readback_stay_actor_tenant_and_store_scoped(case, action):
    engine, _ = case
    prepare(engine, action)
    payload = PAYLOADS[action]
    with engine.transaction():
        _, target = scoped(engine, payload)
        saved = command(target, action, payload)
    record = saved["issue"]["workflowRecords"][0]
    assert record["actorSubjectId"] == "actual-subject" and record["actorEvidence"] == "authenticated_principal"
    changed = {**payload, ("handoffNote" if action == "transfer" else "auditNote"): "Different submission"}
    _, target = scoped(engine, changed)
    with pytest.raises(StoreOpsConflict, match="different request"):
        command(target, action, changed)
    assert scoped(engine, payload, subject="another-subject")[0].get_idempotency_result("workflow-command") is None
    restricted = scoped(engine, payload, stores=frozenset())[1]
    with pytest.raises(StoreOpsNotFound):
        restricted.get_issue("review-issue")
    repository(engine, "other-tenant").save_state({**review_tests.state(), "issues": []})
    other_repo, other_service = scoped(engine, payload, tenant="other-tenant")
    assert other_repo.get_idempotency_result("workflow-command") is None
    with pytest.raises(StoreOpsNotFound):
        other_service.get_issue("review-issue")
    assert service(engine).get_issue("review-issue")["workflowRecords"] == [record]


@pytest.mark.parametrize("action", ["triage", "camera-purpose"])
@pytest.mark.parametrize("stage", ["audit", "receipt"])
def test_workflow_failure_after_real_write_rolls_back_state_audit_and_receipt(case, monkeypatch, action, stage):
    engine, _ = case
    original = prepare(engine, action)
    repo, target = scoped(engine, PAYLOADS[action])
    if stage == "audit":
        real = target._audit_log.record

        def fail(*args):
            real(*args)
            raise RuntimeError("synthetic after-write failure")

        monkeypatch.setattr(target._audit_log, "record", fail)
    else:
        real = repo.save_idempotency_result

        def fail(*args):
            real(*args)
            raise RuntimeError("synthetic after-write failure")

        monkeypatch.setattr(repo, "save_idempotency_result", fail)
    with pytest.raises(RuntimeError, match="after-write failure"), engine.transaction():
        command(target, action, PAYLOADS[action])
    assert repository(engine).get_state() == original
    assert DurableAuditLog(engine).list_events() == []
    assert scoped(engine, PAYLOADS[action])[0].get_idempotency_result("workflow-command") is None


@pytest.mark.parametrize("existing", [None, "recorded-growth-entity-id", "growth"])
def test_escalation_intent_does_not_create_or_replace_a_growth_entity_identifier(case, existing):
    engine, _ = case
    value = prepare(engine, "escalate")
    if existing is not None:
        value["issues"][0]["relatedGrowthId"] = existing
        repository(engine).save_state(value)
    with engine.transaction():
        saved = command(service(engine), "escalate", PAYLOADS["escalate"])
    assert saved["issue"].get("relatedGrowthId") == (None if existing == "growth" else existing)
    assert saved["issue"]["workflowRecords"][0]["submission"]["target"] == "growth"


@pytest.mark.parametrize("action,patch", [
    ("triage", {"demoFastForward": "true"}), ("triage", {"evidenceStrength": "verified"}),
    ("assign", {"slaDueAt": "2026-02-30T10:00:00Z"}),
    ("actions", {"checklistItems": "not a list"}), ("actions", {"requiresApproval": 1}),
    ("field-report", {"observedAt": "2026-10-15T10:00:00"}),
    ("field-report", {"attachmentNames": [42]}), ("escalate", {"notifyOwner": "true"}),
    ("transfer", {"targetOwnerName": False}), ("camera-purpose", {"privacyAcknowledged": "true"}),
    ("camera-purpose", {"retentionHours": 73}), ("camera-purpose", {"auditNote": {"text": "bad"}}),
    ("transfer", {"actorSubjectId": "forged-subject"}),
])
def test_invalid_workflow_values_are_rejected_without_silently_dropping_them(case, action, patch):
    engine, _ = case
    original = prepare(engine, action)
    with pytest.raises(StoreOpsPolicyError), engine.transaction():
        command(service(engine), action, {**PAYLOADS[action], **patch})
    assert repository(engine).get_state() == original and DurableAuditLog(engine).list_events() == []


@pytest.mark.parametrize("field,value", [("storeId", "foreign-store"), ("action", "unknown"), ("actorSubjectId", "forged")])
def test_workflow_readback_rejects_tampered_owner_action_or_actor_evidence(case, field, value):
    engine, _ = case
    prepare(engine, "transfer")
    with engine.transaction():
        command(service(engine), "transfer", PAYLOADS["transfer"])
    state = repository(engine).get_state()
    state["issues"][0]["workflowRecords"][0][field] = value
    repository(engine).save_state(state)
    with pytest.raises(StoreOpsLiveDataUnavailable):
        service(engine).get_issue("review-issue")


def test_scoped_workflow_history_cannot_be_rewritten(case):
    engine, _ = case
    prepare(engine, "transfer")
    repo, target = scoped(engine, PAYLOADS["transfer"])
    with engine.transaction():
        saved = command(target, "transfer", PAYLOADS["transfer"])
    altered = repo.get_state()
    altered["issues"][0]["workflowRecords"][0]["submission"]["reason"] = "Overwrite history"
    with pytest.raises(StoreOpsConflict, match="history cannot be rewritten"):
        repo.save_state(altered)
    assert service(engine).get_issue("review-issue")["workflowRecords"] == saved["issue"]["workflowRecords"]


@pytest.mark.parametrize("patch", [{"privacyAcknowledged": "true"}, {"retentionHours": "12"}])
def test_camera_http_contract_does_not_coerce_wrong_types_into_accepted_purpose(patch):
    from tests.contract.test_operator_api import _store_ops_client, _store_ops_headers

    client, _ = _store_ops_client()
    path = "/api/v1/operator/store-ops/issues/ISS-1024"
    headers = _store_ops_headers("camera-wrong-type")
    before = client.get(path + "/evidence", headers=headers).json()
    response = client.post(
        path + "/camera-purpose",
        json={"purpose": "service incident review", "privacyAcknowledged": True,
              "retentionHours": 12, "auditNote": "Typed API request", **patch},
        headers=headers,
    )
    assert response.status_code == 422
    assert client.get(path + "/evidence", headers=headers).json() == before
