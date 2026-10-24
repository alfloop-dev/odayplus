"""Real SQLite review persistence and scoped repository contracts; not PostgreSQL/auth-login proof."""

from __future__ import annotations

import copy

import pytest

from modules.opsboard.application.store_ops import (
    DurableStoreOpsRepository,
    StoreOpsConflict,
    StoreOpsLiveDataUnavailable,
    StoreOpsNotFound,
    StoreOpsPolicyError,
    StoreOpsService,
)
from modules.opsboard.application.store_ops_scope import ScopedStoreOpsRepository, binding_hash
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore

OUTCOME = {
    "issueId": "review-issue", "storeId": "review-store", "outcome": "effective",
    "impactSummary": "Operator observed a shorter queue.",
    "evidenceSummary": "Manual observation only; no causal estimate.", "closeIssue": False,
}
REPLY = {
    "issueId": "review-issue", "storeId": "review-store", "channel": "google",
    "decision": "approve", "draftReply": "We have recorded your feedback.",
    "reviewerNote": "Reviewed the wording.", "publishAfterApproval": True,
}


def state():
    return {
        "stores": [{"id": "review-store", "name": "Recorded store", "lights": {}}],
        "issues": [{
            "id": "review-issue", "storeId": "review-store", "storeName": "Recorded store",
            "title": "Recorded incident", "status": "observing", "severity": "medium",
            "source": "csCase", "ownerRoleId": "opsLead", "ownerName": "Operator",
            "updatedAt": "2026-10-14T10:00:00+00:00", "createdAt": "2026-10-14T09:00:00+00:00",
            "slaDueAt": "2026-10-16T10:00:00+00:00", "evidenceIds": ["review-evidence"],
            "summary": "Actual recorded text in this local persistence test.",
        }],
        "evidence": [{"id": "review-evidence", "kind": "csCase", "summary": "Recorded evidence"}],
        "auditEvents": [], "attachments": [], "nextAuditOrdinal": 1,
    }


def repository(engine, tenant="review-tenant"):
    return DurableStoreOpsRepository(TenantScopedDocumentStore(SqliteDocumentStore(engine), tenant))


@pytest.fixture
def case(tmp_path):
    path = tmp_path / "reviews.sqlite3"
    engine = SqliteEngine(path)
    repository(engine).save_state(state())
    try:
        yield engine, path
    finally:
        engine.close()


def service(engine):
    return StoreOpsService(repository(engine), audit_log=DurableAuditLog(engine))


def command(target, action, payload, key="review-command"):
    return target.transition_issue(
        issue_id="review-issue", action_type=action, payload=copy.deepcopy(payload),
        correlation_id="review-correlation", idempotency_key=key,
        actor_role_id="opsLead", actor_name="Caller label",
    )


@pytest.mark.parametrize("action,payload,history", [
    ("outcome", OUTCOME, "outcomeReviews"), ("reply-review", REPLY, "replyReviews"),
])
def test_review_content_survives_reopen_and_links_real_audit_and_receipt(case, action, payload, history):
    engine, path = case
    with engine.transaction():
        first = command(service(engine), action, payload)
    record = first["issue"][history][0]
    expected = {key: value for key, value in payload.items() if key not in {"issueId", "storeId"}}
    assert {key: record["submission"][key] for key in expected} == expected
    assert record["issueId"] == "review-issue" and record["storeId"] == "review-store"
    assert record["actorSubjectId"] is None and record["actorEvidence"] == "caller_supplied"
    assert record["sourceIssueUpdatedAt"] == "2026-10-14T10:00:00+00:00"
    assert record["evidenceIds"] == ["review-evidence"]
    assert record["workflowAuditId"] == first["auditEvent"]["id"] == record["id"]
    audits = DurableAuditLog(engine).list_events(correlation_id="review-correlation")
    assert len(audits) == 1 and audits[0].event_id == record["auditEventId"]
    assert audits[0].metadata["reviewRecordHash"] == binding_hash(record)
    assert audits[0].actor == "Caller label"
    assert "outcomeMetrics" not in first["issue"]
    if action == "reply-review":
        assert record["deliveryStatus"] == "not_sent"
    engine.close()
    reopened = SqliteEngine(path)
    try:
        target = service(reopened)
        assert target.get_issue("review-issue")[history] == [record]
        assert target.issue_evidence("review-issue")["issue"][history] == [record]
        with reopened.transaction():
            replay = command(target, action, payload)
        assert replay["idempotentReplay"] is True
        assert replay["issue"][history] == [record]
        assert len(DurableAuditLog(reopened).list_events()) == 1
    finally:
        reopened.close()


def scoped(engine, payload, *, subject="actual-subject", stores=frozenset({"review-store"}), tenant="review-tenant"):
    repo = ScopedStoreOpsRepository(
        repository(engine, tenant), store_ids=stores, subject_id=subject,
        scope_hash=binding_hash([tenant, subject, sorted(stores)]),
        request_hash=binding_hash(payload), operation="store_ops.transition",
    )
    return repo, StoreOpsService(repo, audit_log=DurableAuditLog(engine), actor_subject_id=subject)


def test_scoped_review_replay_binds_payload_actor_scope_and_current_tenant(case):
    engine, _ = case
    with engine.transaction():
        _, target = scoped(engine, REPLY)
        saved = command(target, "reply-review", REPLY)
    record = saved["issue"]["replyReviews"][0]
    assert record["actorSubjectId"] == "actual-subject"
    assert record["actorEvidence"] == "authenticated_principal"
    assert DurableAuditLog(engine).list_events()[0].actor == "actual-subject"
    _, target = scoped(engine, REPLY)
    assert command(target, "reply-review", REPLY)["idempotentReplay"] is True
    changed = {**REPLY, "draftReply": "Different submitted text"}
    _, target = scoped(engine, changed)
    with pytest.raises(StoreOpsConflict, match="different request"):
        command(target, "reply-review", changed)
    # Same key under a different subject cannot return the first subject's receipt.
    other_repo, _ = scoped(engine, REPLY, subject="different-subject")
    assert other_repo.get_idempotency_result("review-command") is None
    _, restricted = scoped(engine, REPLY, stores=frozenset())
    with pytest.raises(StoreOpsNotFound):
        restricted.get_issue("review-issue")  # The live router performs this check before receipt lookup.
    repository(engine, "other-tenant").save_state({**state(), "issues": []})
    other_repo, rotated = scoped(engine, REPLY, tenant="other-tenant")
    assert other_repo.get_idempotency_result("review-command") is None
    with pytest.raises(StoreOpsNotFound):
        rotated.get_issue("review-issue")
    assert service(engine).get_issue("review-issue")["replyReviews"] == [record]


@pytest.mark.parametrize("action,patch", [
    ("outcome", {"outcome": "unknown"}),
    ("outcome", {"impactSummary": "  "}),
    ("outcome", {"closeIssue": "false"}),
    ("outcome", {"outcome": "ineffective", "closeIssue": True}),
    ("outcome", {"outcomeMetrics": [{"label": "Invented", "before": "0", "after": "100"}]}),
    ("outcome", {"observedAt": "2026-02-30"}),
    ("reply-review", {"channel": "unsupported"}),
    ("reply-review", {"decision": "return", "reviewerNote": ""}),
    ("reply-review", {"draftReply": " "}),
    ("reply-review", {"publishAfterApproval": "true"}),
    ("reply-review", {"decision": "reject", "publishAfterApproval": True}),
    ("reply-review", {"actorSubjectId": "forged-subject"}),
])
def test_invalid_or_unproduced_review_fields_do_not_mutate_or_audit(case, action, patch):
    engine, _ = case
    before = repository(engine).get_state()
    payload = {**(OUTCOME if action == "outcome" else REPLY), **patch}
    with pytest.raises(StoreOpsPolicyError), engine.transaction():
        command(service(engine), action, payload)
    assert repository(engine).get_state() == before
    assert DurableAuditLog(engine).list_events() == []
    assert repository(engine).get_idempotency_result("review-command") is None


@pytest.mark.parametrize("field,value", [("issueId", "another-issue"), ("storeId", "another-store")])
def test_review_rejects_mismatched_source_identifiers(case, field, value):
    engine, _ = case
    before = repository(engine).get_state()
    with pytest.raises(StoreOpsConflict, match="does not match"), engine.transaction():
        command(service(engine), "reply-review", {**REPLY, field: value})
    assert repository(engine).get_state() == before
    assert DurableAuditLog(engine).list_events() == []


def test_recorded_measurements_are_preserved_but_not_inferred_from_operator_judgment(case):
    engine, _ = case
    original = repository(engine).get_state()
    measurements = [{"label": "Recorded observation", "before": "source before", "after": "source after"}]
    original["issues"][0]["outcomeMetrics"] = measurements
    repository(engine).save_state(original)
    with engine.transaction():
        command(service(engine), "outcome", OUTCOME)
    assert service(engine).get_issue("review-issue")["outcomeMetrics"] == measurements


def test_later_review_appends_and_scoped_repository_rejects_history_rewrite(case):
    engine, _ = case
    with engine.transaction():
        _, target = scoped(engine, REPLY)
        first = command(target, "reply-review", REPLY)
    later = {**REPLY, "draftReply": "Revised wording", "reviewerNote": "Second review"}
    with engine.transaction():
        repo, target = scoped(engine, later)
        second = command(target, "reply-review", later, "second-review")
    history = second["issue"]["replyReviews"]
    assert history[0] == first["issue"]["replyReviews"][0] and len(history) == 2
    assert history[1]["submission"]["draftReply"] == "Revised wording"
    assert history[0]["auditEventId"] != history[1]["auditEventId"]
    rewrite = repo.get_state()
    rewrite["issues"][0]["replyReviews"][0]["submission"]["draftReply"] = "Tampered history"
    with pytest.raises(StoreOpsConflict, match="history cannot be rewritten"):
        repo.save_state(rewrite)
    assert service(engine).get_issue("review-issue")["replyReviews"] == history


def test_receipt_write_failure_rolls_back_review_state_and_durable_audit(case, monkeypatch):
    engine, _ = case
    repo, target = scoped(engine, REPLY)
    before = repository(engine).get_state()

    def fail_receipt(*args):
        raise RuntimeError("synthetic receipt write failure")

    monkeypatch.setattr(repo, "save_idempotency_result", fail_receipt)
    with pytest.raises(RuntimeError, match="receipt write failure"), engine.transaction():
        command(target, "reply-review", REPLY)
    assert repository(engine).get_state() == before
    assert DurableAuditLog(engine).list_events() == []
    assert scoped(engine, REPLY)[0].get_idempotency_result("review-command") is None


@pytest.mark.parametrize("corruption", ["foreign_store", "invalid_timestamp", "changed_content"])
def test_readback_refuses_corrupt_or_mismatched_persisted_review(case, corruption):
    engine, _ = case
    with engine.transaction():
        command(service(engine), "reply-review", REPLY)
    corrupted = repository(engine).get_state()
    record = corrupted["issues"][0]["replyReviews"][0]
    if corruption == "foreign_store":
        record["storeId"] = "different-store"
    elif corruption == "invalid_timestamp":
        record["sourceIssueUpdatedAt"] = "2026-02-30T10:00:00+00:00"
    else:
        record["submission"]["draftReply"] = "Changed outside the recorded command"
    repository(engine).save_state(corrupted)
    with pytest.raises(StoreOpsLiveDataUnavailable):
        service(engine).get_issue("review-issue")
    with pytest.raises(StoreOpsLiveDataUnavailable):
        service(engine).snapshot()
