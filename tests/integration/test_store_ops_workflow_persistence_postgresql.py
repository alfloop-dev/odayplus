"""Native PG persistence of seven submitted StoreOps workflow fact records.

Uses the existing owned, synthetic PostgreSQL tenant/store/issue materialization
and scoped HTTP fixture. Camera evidence below is explicitly synthetic metadata,
not footage. Header-derived test principals do not prove human login or BFF auth.
No device execution, notification delivery, verified assignee, measurement, or
external cloud storage is inferred from recorded intent.
"""

from copy import deepcopy
from uuid import uuid4

import pytest

from modules.opsboard.application.store_ops import DurableStoreOpsRepository
from modules.opsboard.application.store_ops_audit_delivery import COLLECTION
from modules.opsboard.application.store_ops_scope import binding_hash
from shared.infrastructure.persistence import PostgresDocumentStore
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from tests.integration import test_store_ops_scoped_postgresql as scoped_tests
from tests.integration.test_intake_registry_calendar_postgresql import _bundle

case = scoped_tests.case
BASE = scoped_tests.BASE
pytestmark = pytest.mark.requires_live_env

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

FLAGS = {"triage": {"clockAdvanced": False}, "assign": {"assigneeIdentityStatus": "not_verified"},
         "actions": {"executionStatus": "not_executed", "approvalRequestStatus": "not_created"},
         "field-report": {"reporterIdentityStatus": "not_verified", "attachmentEvidenceStatus": "not_verified"},
         "escalate": {"notificationStatus": "not_sent", "routingStatus": "not_dispatched"},
         "transfer": {"assigneeIdentityStatus": "not_verified", "watchSubscriptionStatus": "not_created"},
         "camera-purpose": {}}


def _post(client, case, action, payload, key, *, headers=None):
    return client.post(
        f"{BASE}/issues/{case['issue']}/{action}",
        json=payload,
        headers={**(headers or case["headers"]), "Idempotency-Key": key},
    )


def _prepare_via_routes(case, action):
    if action == "camera-purpose":
        # Explicit fixture materialization in its own tenant, before any command.
        # No unscoped state, production camera access, or footage is introduced.
        state = case["repo"].get_state()
        evidence_id = str(uuid4())
        state["issues"][0]["evidenceIds"].append(evidence_id)
        state["evidence"].append({
            "id": evidence_id, "kind": "camera", "lockedReason": "purpose required",
            "summary": "Synthetic camera metadata for transaction verification; no footage.",
        })
        case["repo"].save_state(state)
    client = scoped_tests._client(case["bundle"])
    prefix = (
        ("triage", {"severity": "high", "decision": "accept"}, "triaged"),
        ("assign", {"ownerRoleId": "opsLead", "ownerName": "Synthetic assigned label",
                    "slaDueAt": "2026-10-16T10:00:00Z"}, "assigned"),
        ("actions", {"actionType": "staffBriefing", "requiresApproval": False}, "inprogress"),
        ("field-report", {"checklistStatus": "complete"}, "observing"),
    )
    for step, payload, status in prefix:
        if step == action:
            break
        response = _post(client, case, step, payload, "workflow-setup-" + step)
        assert response.status_code == 200, response.text
        assert response.json()["issue"]["status"] == status
    return client


def _events(bundle, tenant):
    return {
        event.event_id: event
        for event in bundle.audit_log.list_events(tenant_id=tenant)
        if event.event_type in {
            "operator.store_ops.issue_transition", "operator.store_ops.camera_purpose",
        }
    }


def _job_count(bundle):
    return bundle.engine.query_one(
        "SELECT COUNT(*) AS count FROM durable_jobs WHERE job_type = ?",
        ("store-ops-audit-delivery",),
    )["count"]


def _receipt_key(case, action, key):
    operation = "store_ops.camera_purpose" if action == "camera-purpose" else "store_ops.transition"
    return binding_hash([case["subject"], operation, key])


@pytest.mark.parametrize("action", list(PAYLOADS))
def test_native_all_workflow_submissions_reopen_audit_receipt_and_replay_once(case, action):
    client = _prepare_via_routes(case, action)
    before = deepcopy(case["repo"].get_state())
    foreign_before = deepcopy(case["foreign_repo"].get_state())
    before_ids = set(_events(case["bundle"], case["tenant"]))
    before_jobs = _job_count(case["bundle"])
    payload = {
        **deepcopy(PAYLOADS[action]), "issueId": case["issue"], "storeId": case["store"],
        "actorName": "Forged caller label", "actorRoleId": "auditPm",
    }
    key = "native-all-fields-" + action
    response = _post(client, case, action, payload, key)
    assert response.status_code == 200, response.text
    saved = response.json()["issue"]
    record = saved["workflowRecords"][-1]
    assert record["action"] == action and record["submission"] == PAYLOADS[action]
    assert record["issueId"] == case["issue"] and record["storeId"] == case["store"]
    assert record["actorSubjectId"] == record["actorName"] == case["subject"]
    assert record["actorEvidence"] == "authenticated_principal" and record["actorRoleId"] == "opsLead"
    assert record["sourceIssueUpdatedAt"] == before["issues"][0]["updatedAt"]
    assert record["evidenceIds"] == before["issues"][0]["evidenceIds"]
    assert record["workflowAuditId"] == record["id"] == response.json()["auditEvent"]["id"]
    assert saved["workflowRecords"][:-1] == before["issues"][0].get("workflowRecords", [])
    for name, expected in FLAGS[action].items():
        assert record[name] == expected
    after_events = _events(case["bundle"], case["tenant"])
    assert set(after_events) - before_ids == {record["auditEventId"]}
    event = after_events[record["auditEventId"]]
    expected_type = "operator.store_ops.camera_purpose" if action == "camera-purpose" else "operator.store_ops.issue_transition"
    assert event.event_type == expected_type and event.actor == case["subject"]
    assert event.metadata["tenant_id"] == case["tenant"]
    assert event.metadata["actor_subject_id"] == case["subject"]
    assert event.metadata["workflowAuditId"] == record["workflowAuditId"]
    assert event.metadata["reviewRecordHash"] == binding_hash(record)
    aggregate = case["repo"].get_state()
    audit = next(row for row in aggregate["auditEvents"] if row["id"] == record["workflowAuditId"])
    assert audit["metadata"]["reviewRecordHash"] == binding_hash(record)
    assert audit["targetId"] == case["issue"]
    assert audit["action"] == ("evidence.camera_purpose.recorded" if action == "camera-purpose" else "issue." + action)
    receipt_key = _receipt_key(case, action, key)
    receipt = case["repo"].get_idempotency_result(receipt_key)
    assert receipt is not None and receipt["result"]["issue"]["workflowRecords"][-1] == record
    assert receipt["result"]["auditEvent"]["id"] == record["workflowAuditId"]
    delivery = case["repo"]._store.get(COLLECTION, event.event_id)
    assert delivery["status"] == "delivered" and delivery["event"].event_id == event.event_id
    assert _job_count(case["bundle"]) == before_jobs + 1
    assert aggregate["issues"][1] == before["issues"][1]
    assert case["foreign_repo"].get_state() == foreign_before
    assert "outcomeMetrics" not in saved and "relatedGrowthId" not in saved
    assert not aggregate.get("attachments")
    if action == "camera-purpose":
        assert record["submission"]["auditNote"] == PAYLOADS[action]["auditNote"]
        assert "auditNote" not in event.metadata
        assert "auditNote" not in audit["metadata"]
        assert "auditNote" not in response.json()["auditEvent"]["metadata"]
        assert response.json()["evidenceItem"]["retentionHours"] == 12
    if action == "triage":
        assert saved["status"] == "triaged"  # demoFastForward is not a clock or maturity bypass.
        assert saved["slaDueAt"] == before["issues"][0]["slaDueAt"]
        assert saved["createdAt"] == before["issues"][0]["createdAt"]
    client.close()
    case["bundle"].engine.close()
    reopened = _bundle(case["db"], case["monkeypatch"])
    try:
        client = scoped_tests._client(reopened)
        repo = DurableStoreOpsRepository(TenantScopedDocumentStore(PostgresDocumentStore(reopened.engine), case["tenant"]))
        read = client.get(f"{BASE}/issues/{case['issue']}", headers=case["headers"])
        assert read.status_code == 200, read.text
        assert read.json()["issue"] == saved
        assert repo.get_idempotency_result(receipt_key) == receipt
        replay = _post(client, case, action, payload, key)
        assert replay.status_code == 200, replay.text
        assert replay.json()["idempotentReplay"] is True
        assert replay.json()["issue"] == saved and replay.json()["auditEvent"]["id"] == record["workflowAuditId"]
        assert set(_events(reopened, case["tenant"])) == set(after_events)
        assert _job_count(reopened) == before_jobs + 1
        changed_field = {"triage": "notes", "assign": "handoffNote", "actions": "instructions",
                         "field-report": "summary", "escalate": "reason", "transfer": "handoffNote",
                         "camera-purpose": "auditNote"}[action]
        changed = _post(client, case, action, {**payload, changed_field: "Changed submission"}, key)
        assert changed.status_code == 409, changed.text
        restricted = {**case["headers"], "x-store-ids": case["hidden_store"], "x-region-ids": "south"}
        assert _post(client, case, action, payload, key, headers=restricted).status_code == 404
        foreign = {**case["headers"], "x-tenant-id": case["foreign_repo"]._store.tenant_id}
        assert _post(client, case, action, payload, key, headers=foreign).status_code == 404
        assert repo.get_state()["issues"][0] == saved
        assert set(_events(reopened, case["tenant"])) == set(after_events)
        assert _job_count(reopened) == before_jobs + 1
    finally:
        client.close()
        reopened.engine.close()


@pytest.mark.parametrize("action", ["triage", "camera-purpose"])
@pytest.mark.parametrize("failure_stage", ["audit", "receipt"])
def test_native_workflow_after_write_fault_rolls_back_then_same_request_commits(case, action, failure_stage):
    client = _prepare_via_routes(case, action)
    before = deepcopy(case["repo"].get_state())
    before_ids = set(_events(case["bundle"], case["tenant"]))
    before_delivery = deepcopy(case["repo"]._store.list_all(COLLECTION))
    before_jobs = _job_count(case["bundle"])
    actual_audit = case["bundle"].audit_log.record
    actual_receipt = DurableStoreOpsRepository.save_idempotency_result
    expected_type = "operator.store_ops.camera_purpose" if action == "camera-purpose" else "operator.store_ops.issue_transition"

    def fail_after_actual_audit(event):
        result = actual_audit(event)
        if event.event_type == expected_type:
            raise RuntimeError("Synthetic failure after actual workflow audit insert")
        return result

    def fail_after_actual_receipt(self, key, result):
        actual_receipt(self, key, result)
        raise RuntimeError("Synthetic failure after actual workflow receipt insert")

    if failure_stage == "audit":
        case["monkeypatch"].setattr(case["bundle"].audit_log, "record", fail_after_actual_audit)
    else:
        case["monkeypatch"].setattr(DurableStoreOpsRepository, "save_idempotency_result", fail_after_actual_receipt)
    key = "workflow-native-rollback-" + action
    payload = deepcopy(PAYLOADS[action])
    failed = _post(client, case, action, payload, key)
    assert failed.status_code == (503 if failure_stage == "audit" else 500), failed.text
    assert case["repo"].get_state() == before
    assert set(_events(case["bundle"], case["tenant"])) == before_ids
    assert case["repo"]._store.list_all(COLLECTION) == before_delivery
    assert _job_count(case["bundle"]) == before_jobs
    assert case["repo"].get_idempotency_result(_receipt_key(case, action, key)) is None
    case["monkeypatch"].setattr(case["bundle"].audit_log, "record", actual_audit)
    case["monkeypatch"].setattr(DurableStoreOpsRepository, "save_idempotency_result", actual_receipt)
    committed = _post(client, case, action, payload, key)
    assert committed.status_code == 200, committed.text
    record = committed.json()["issue"]["workflowRecords"][-1]
    assert record["action"] == action and record["submission"] == payload
    assert committed.json()["issue"]["workflowRecords"][:-1] == before["issues"][0].get("workflowRecords", [])
    after_ids = set(_events(case["bundle"], case["tenant"]))
    assert after_ids - before_ids == {record["auditEventId"]}
    receipt = case["repo"].get_idempotency_result(_receipt_key(case, action, key))
    assert receipt["result"]["issue"]["workflowRecords"][-1] == record
    replay = _post(client, case, action, payload, key)
    assert replay.status_code == 200 and replay.json()["idempotentReplay"] is True
    assert replay.json()["issue"]["workflowRecords"] == committed.json()["issue"]["workflowRecords"]
    assert set(_events(case["bundle"], case["tenant"])) == after_ids
    assert _job_count(case["bundle"]) == before_jobs + 1
    client.close()
