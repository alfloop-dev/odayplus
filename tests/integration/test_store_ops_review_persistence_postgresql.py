"""Native PostgreSQL review persistence via the existing scoped HTTP fixture.

The original fixture owns synthetic native tenants, stores and scoped API
principals. This proves neither human login/BFF cookies nor external publication.
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


def _post(client, case, action, payload, key, *, headers=None):
    return client.post(
        f"{BASE}/issues/{case['issue']}/{action}", json=payload,
        headers={**(headers or case["headers"]), "Idempotency-Key": key},
    )


def _observe_through_routes(case):
    client = scoped_tests._client(case["bundle"])
    for action, payload, expected in (
        ("triage", {"severity": "high", "decision": "accept"}, "triaged"),
        ("assign", {"ownerRoleId": "opsLead", "ownerName": "Synthetic assigned operator",
                    "slaDueAt": "2026-10-16T10:00:00Z"}, "assigned"),
        ("actions", {"actionType": "staffBriefing", "title": "Review recorded incident",
                     "instructions": "Record a local manual observation.",
                     "checklistItems": [], "requiresApproval": False}, "inprogress"),
        ("field-report", {"reportedBy": "Synthetic assigned operator",
                          "observedAt": "2026-10-15T10:00:00Z",
                          "summary": "Explicit synthetic manual report for persistence verification.",
                          "checklistStatus": "complete", "attachmentNames": []}, "observing"),
    ):
        response = _post(client, case, action, payload, "review-setup-" + action)
        assert response.status_code == 200, response.text
        assert response.json()["issue"]["status"] == expected
    return client


def _reply(case):
    return {
        "issueId": case["issue"], "storeId": case["store"], "channel": "customerService",
        "decision": "approve", "draftReply": "We have recorded this incident for review.",
        "reviewerNote": "Explicit synthetic reviewer text.", "publishAfterApproval": True,
        "actorName": "forged caller label", "actorRoleId": "auditPm",
    }


def _business_events(bundle, tenant):
    return [event for event in bundle.audit_log.list_events(tenant_id=tenant)
            if event.event_type == "operator.store_ops.issue_transition"]


def _delivery_job_count(bundle):
    return bundle.engine.query_one(
        "SELECT COUNT(*) AS count FROM durable_jobs WHERE job_type = ?",
        ("store-ops-audit-delivery",),
    )["count"]


def test_native_review_routes_reopen_exact_audit_receipts_and_recheck_current_scope(case):
    client = _observe_through_routes(case)
    hidden_before = deepcopy(case["repo"].get_state()["issues"][1])
    foreign_before = deepcopy(case["foreign_repo"].get_state())
    reply_payload = _reply(case)
    replied = _post(client, case, "reply-review", reply_payload, "native-reply")
    assert replied.status_code == 200, replied.text
    reply = replied.json()["issue"]["replyReviews"][0]
    assert reply["submission"] == {
        "channel": "customerService", "decision": "approve",
        "draftReply": "We have recorded this incident for review.",
        "reviewerNote": "Explicit synthetic reviewer text.", "publishAfterApproval": True,
    }
    assert reply["deliveryStatus"] == "not_sent"
    outcome_payload = {
        "issueId": case["issue"], "storeId": case["store"], "outcome": "effective",
        "impactSummary": "Manual assessment recorded; no measured effect is asserted.",
        "evidenceSummary": "Synthetic incident text reviewed.", "closeIssue": True,
    }
    completed = _post(client, case, "outcome", outcome_payload, "native-outcome")
    assert completed.status_code == 200, completed.text
    saved_issue = completed.json()["issue"]
    outcome = saved_issue["outcomeReviews"][0]
    assert saved_issue["status"] == "closed" and "outcomeMetrics" not in saved_issue
    assert outcome["submission"] == {
        "outcome": "effective", "impactSummary": outcome_payload["impactSummary"],
        "evidenceSummary": outcome_payload["evidenceSummary"], "closeIssue": True,
        "followUpTarget": None, "followUpAction": None,
    }
    workflow = {row["id"]: row for row in case["repo"].get_state()["auditEvents"]}
    events = {event.event_id: event for event in _business_events(case["bundle"], case["tenant"])}
    assert len(events) == 6
    for record, response in ((reply, replied), (outcome, completed)):
        assert record["issueId"] == case["issue"] and record["storeId"] == case["store"]
        assert record["actorSubjectId"] == record["actorName"] == case["subject"]
        assert record["actorEvidence"] == "authenticated_principal" and record["actorRoleId"] == "opsLead"
        assert record["id"] == record["workflowAuditId"] == response.json()["auditEvent"]["id"]
        assert record["evidenceIds"] == []  # This fixture has no measurements or evidence rows.
        actual = events[record["auditEventId"]]
        assert actual.actor == case["subject"] and actual.metadata["tenant_id"] == case["tenant"]
        assert actual.metadata["actor_subject_id"] == case["subject"]
        assert actual.metadata["workflowAuditId"] == record["workflowAuditId"]
        assert actual.metadata["reviewRecordHash"] == binding_hash(record)
        assert workflow[record["workflowAuditId"]]["metadata"]["reviewRecordHash"] == binding_hash(record)
        delivery = case["repo"]._store.get(COLLECTION, actual.event_id)
        assert delivery["status"] == "delivered" and delivery["event"].event_id == actual.event_id
    assert case["repo"].get_state()["issues"][1] == hidden_before
    assert case["foreign_repo"].get_state() == foreign_before
    original_ids = set(events)
    original_job_count = _delivery_job_count(case["bundle"])
    case["bundle"].engine.close()
    reopened = _bundle(case["db"], case["monkeypatch"])
    try:
        client = scoped_tests._client(reopened)
        repo = DurableStoreOpsRepository(TenantScopedDocumentStore(PostgresDocumentStore(reopened.engine), case["tenant"]))
        read = client.get(f"{BASE}/issues/{case['issue']}", headers=case["headers"])
        assert read.status_code == 200, read.text
        assert read.json()["issue"] == saved_issue
        for action, payload, key in (("reply-review", reply_payload, "native-reply"),
                                     ("outcome", outcome_payload, "native-outcome")):
            replay = _post(client, case, action, payload, key)
            assert replay.status_code == 200, replay.text
            assert replay.json()["idempotentReplay"] is True
            assert replay.json()["issue"] == saved_issue
        changed = _post(client, case, "reply-review", {**reply_payload, "draftReply": "Changed text"}, "native-reply")
        assert changed.status_code == 409
        new_actor = {**case["headers"], "x-subject-id": str(uuid4())}
        assert _post(client, case, "outcome", outcome_payload, "native-outcome", headers=new_actor).status_code == 409
        narrowed = {**case["headers"], "x-store-ids": case["hidden_store"], "x-region-ids": "south"}
        assert _post(client, case, "outcome", outcome_payload, "native-outcome", headers=narrowed).status_code == 404
        foreign = {**case["headers"], "x-tenant-id": case["foreign_repo"]._store.tenant_id}
        assert _post(client, case, "outcome", outcome_payload, "native-outcome", headers=foreign).status_code == 404
        assert {event.event_id for event in _business_events(reopened, case["tenant"])} == original_ids
        assert _delivery_job_count(reopened) == original_job_count
        assert repo.get_state()["issues"][0] == saved_issue
    finally:
        reopened.engine.close()


@pytest.mark.parametrize("failure_stage", ["audit", "receipt"])
def test_native_review_write_failure_rolls_back_then_same_request_commits(case, failure_stage):
    client = _observe_through_routes(case)
    before = deepcopy(case["repo"].get_state())
    before_ids = {event.event_id for event in _business_events(case["bundle"], case["tenant"])}
    before_delivery = deepcopy(case["repo"]._store.list_all(COLLECTION))
    before_jobs = _delivery_job_count(case["bundle"])
    real_record = case["bundle"].audit_log.record
    real_save = DurableStoreOpsRepository.save_idempotency_result

    def fail_after_actual_audit(event):
        result = real_record(event)
        if event.event_type == "operator.store_ops.issue_transition":
            raise RuntimeError("Synthetic failure after actual review audit insert")
        return result

    def fail_after_actual_receipt(self, key, result):
        real_save(self, key, result)
        raise RuntimeError("Synthetic failure after actual review receipt insert")

    if failure_stage == "audit":
        case["monkeypatch"].setattr(case["bundle"].audit_log, "record", fail_after_actual_audit)
    else:
        case["monkeypatch"].setattr(DurableStoreOpsRepository, "save_idempotency_result", fail_after_actual_receipt)
    key = "native-review-rollback"
    payload = _reply(case)
    failed = _post(client, case, "reply-review", payload, key)
    assert failed.status_code == (503 if failure_stage == "audit" else 500), failed.text
    assert case["repo"].get_state() == before
    assert {event.event_id for event in _business_events(case["bundle"], case["tenant"])} == before_ids
    assert case["repo"]._store.list_all(COLLECTION) == before_delivery
    assert _delivery_job_count(case["bundle"]) == before_jobs
    receipt_key = binding_hash([case["subject"], "store_ops.transition", key])
    assert case["repo"].get_idempotency_result(receipt_key) is None
    case["monkeypatch"].setattr(case["bundle"].audit_log, "record", real_record)
    case["monkeypatch"].setattr(DurableStoreOpsRepository, "save_idempotency_result", real_save)
    committed = _post(client, case, "reply-review", payload, key)
    assert committed.status_code == 200, committed.text
    record = committed.json()["issue"]["replyReviews"][0]
    assert record["submission"]["draftReply"] == payload["draftReply"]
    after_ids = {event.event_id for event in _business_events(case["bundle"], case["tenant"])}
    assert after_ids - before_ids == {record["auditEventId"]}
    replay = _post(client, case, "reply-review", payload, key)
    assert replay.status_code == 200 and replay.json()["idempotentReplay"] is True
    assert replay.json()["issue"]["replyReviews"] == [record]
    assert {event.event_id for event in _business_events(case["bundle"], case["tenant"])} == after_ids
    assert _delivery_job_count(case["bundle"]) == before_jobs + 1
