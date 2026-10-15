"""Owned SQLite native decision entry: physical context, real owner and replay."""

from dataclasses import asdict, replace

import pytest
from fastapi.testclient import TestClient

from modules.opsboard.application.operator_live_repository import OperatorLiveRepository
from tests.integration.test_network_review_canonical_atomicity import (
    business_bytes as durable_bytes,
)
from tests.integration.test_network_review_submission_atomicity import (
    AUTHOR,
    CHECKER,
    TENANT,
    arguments,
    invoke,
    setup,
)

BASE = "/api/v1/operator/network-reviews"
HEADERS = {
    "x-subject-id": CHECKER.subject_id,
    "x-roles": "site_reviewer",
    "x-operator-role": "expansion-manager",
    "x-tenant-id": TENANT,
    "If-Actor-Match": CHECKER.subject_id,
    "Idempotency-Key": "owned-bound-decision",
    "X-Correlation-Id": "00000000-0000-4000-8000-000000000123",
}


def pending(tmp_path):
    harness, resolver, candidate, _, submission = setup(tmp_path)
    result = invoke(resolver, "submit_review", **arguments(submission))
    return harness, resolver, candidate, result["records"]["decisionId"]


def body(context, action="GO"):
    return {
        **context["target"],
        "expectedActorSubjectId": CHECKER.subject_id,
        "expectedTenantId": TENANT,
        "decision": action,
        "reason": "Separate reviewer reads the retained report and records the decision",
        "conditions": "Lease and construction conditions must be confirmed"
        if action == "WAIT"
        else "",
        "requiredData": ["inspection"] if action == "RETURN" else [],
        "overrideAck": True,
    }


def context_read(client, candidate, decision, headers=None):
    return client.get(
        f"{BASE}/{decision}/decision-context",
        params={"candidateId": candidate},
        headers=headers or HEADERS,
    )


def test_today_real_pending_producer_enters_exact_native_review_instead_of_approval_alias(tmp_path):
    harness, _, candidate, decision = pending(tmp_path)
    try:
        saved = harness.decisions(TENANT).get_decision(decision)
        row = OperatorLiveRepository(harness.bundle)._sitescore_approvals([saved])[0]
        assert row["target"] == {
            "workspace": "network",
            "tab": "review",
            "entityId": candidate,
            "candidateId": candidate,
            "reviewId": decision,
        }
        assert saved.approval_id != row["id"] and row["id"] == decision
    finally:
        harness.close()


@pytest.mark.parametrize("action", ["GO", "WAIT", "RETURN", "REJECT"])
def test_current_context_decides_exact_owner_and_replays_same_original_version_without_writes(
    tmp_path, action
):
    harness, _, candidate, decision = pending(tmp_path)
    try:
        with TestClient(harness.app()) as client:
            before_context = durable_bytes(harness.bundle.engine)
            read = context_read(client, candidate, decision)
            assert read.status_code == 200, read.text
            context = read.json()
            assert context["canDecide"] and context["actorSubjectId"] == CHECKER.subject_id
            assert durable_bytes(harness.bundle.engine) == before_context
            payload = body(context, action)
            headers = {**HEADERS, "If-Match": f'W/"{context["target"]["expectedDecisionVersion"]}"'}
            response = client.post(f"{BASE}/{decision}/decision", headers=headers, json=payload)
            assert response.status_code == 200, response.text
            receipt = response.json()
            assert receipt["review"]["version"] == context["target"]["expectedDecisionVersion"] + 1
            assert receipt["review"]["network_action"] == action
            assert receipt["review"]["approval_id"] == context["target"]["expectedApprovalId"]
            assert receipt["commandContext"]["request"] == payload
            assert receipt["commandContext"]["actorSubjectId"] == CHECKER.subject_id
            current = client.get(BASE, headers=HEADERS).json()
            row = next(row for row in current["reviews"] if row["id"] == decision)
            assert (
                row["decision"]["decision"] == action
                and row["decision"]["decidedBy"] == CHECKER.subject_id
            )
            terminal = durable_bytes(harness.bundle.engine)
            replay = client.post(f"{BASE}/{decision}/decision", headers=headers, json=payload)
            assert replay.status_code == 200 and replay.json()["idempotentReplay"] is True
            assert durable_bytes(harness.bundle.engine) == terminal
            assert harness.runtime.calls == []
    finally:
        harness.close()


@pytest.mark.parametrize(
    "change, expected",
    [
        ("missing-version", 428),
        ("bad-header-version", 409),
        ("body-actor", 409),
        ("header-actor", 409),
        ("tenant", 409),
        ("candidate", 409),
        ("digest", 409),
        ("report", 409),
        ("report-version", 409),
        ("approval", 409),
        ("decision-version", 409),
        ("reason", 422),
        ("risk-ack", 422),
        ("forged-actor-field", 422),
    ],
)
def test_refused_context_and_policy_leave_entire_physical_graph_unchanged(
    tmp_path, change, expected
):
    harness, _, candidate, decision = pending(tmp_path)
    try:
        with TestClient(harness.app()) as client:
            context = context_read(client, candidate, decision).json()
            payload = body(context, "REJECT" if change == "risk-ack" else "GO")
            headers = {**HEADERS, "If-Match": f'W/"{context["target"]["expectedDecisionVersion"]}"'}
            if change == "missing-version":
                headers.pop("If-Match")
            if change == "bad-header-version":
                headers["If-Match"] = 'W/"999"'
            if change == "body-actor":
                payload["expectedActorSubjectId"] = "other"
            if change == "header-actor":
                headers["If-Actor-Match"] = "other"
            if change == "tenant":
                payload["expectedTenantId"] = "other"
            if change == "candidate":
                payload["expectedCandidateId"] = "other"
            if change == "digest":
                payload["expectedCandidateDigest"] = "0" * 64
            if change == "report":
                payload["expectedReportId"] = "other"
            if change == "report-version":
                payload["expectedReportVersion"] += 1
            if change == "approval":
                payload["expectedApprovalId"] = decision
            if change == "decision-version":
                payload["expectedDecisionVersion"] += 1
                headers["If-Match"] = f'W/"{payload["expectedDecisionVersion"]}"'
            if change == "reason":
                payload["reason"] = ""
            if change == "risk-ack":
                payload["overrideAck"] = False
            if change == "forged-actor-field":
                payload["actorName"] = "invented approver"
            before = durable_bytes(harness.bundle.engine)
            response = client.post(f"{BASE}/{decision}/decision", headers=headers, json=payload)
            assert response.status_code == expected, response.text
            assert durable_bytes(harness.bundle.engine) == before
    finally:
        harness.close()


def test_current_proposer_is_readable_but_cannot_decide_and_scope_replay_is_refused(tmp_path):
    harness, _, candidate, decision = pending(tmp_path)
    try:
        with TestClient(harness.app()) as client:
            author_headers = {
                **HEADERS,
                "x-subject-id": AUTHOR.subject_id,
                "If-Actor-Match": AUTHOR.subject_id,
            }
            context = context_read(client, candidate, decision, author_headers).json()
            assert context["canDecide"] is False and "proposer" in context["refusalReason"]
            context = context_read(client, candidate, decision).json()
            payload = body(context)
            headers = {**HEADERS, "If-Match": f'W/"{payload["expectedDecisionVersion"]}"'}
            posted = client.post(f"{BASE}/{decision}/decision", headers=headers, json=payload)
            assert posted.status_code == 200, posted.text
            before = durable_bytes(harness.bundle.engine)
            denied = client.post(
                f"{BASE}/{decision}/decision",
                headers={**headers, "x-roles": "expansion_user"},
                json=payload,
            )
            assert denied.status_code == 403 and durable_bytes(harness.bundle.engine) == before
            conflict = client.post(
                f"{BASE}/{decision}/decision",
                headers=headers,
                json={**payload, "reason": "Different decision reason under same key"},
            )
            assert conflict.status_code == 409 and durable_bytes(harness.bundle.engine) == before
    finally:
        harness.close()


@pytest.mark.parametrize(
    "field, value",
    [
        ("model_version", ""),
        ("model_version", " "),
        ("report_id", ""),
        ("report_id", " "),
        ("report_version", 0),
        ("report_version", True),
        ("report_version", "1"),
    ],
)
def test_context_never_advertises_decidable_legacy_identity_that_fresh_owner_refuses(
    tmp_path, field, value
):
    harness, _, candidate, decision = pending(tmp_path)
    try:
        with TestClient(harness.app()) as client:
            original = context_read(client, candidate, decision)
            assert original.status_code == 200, original.text
            payload = body(original.json())
            repository = harness.decisions(TENANT)
            legacy_decision = repository.get_decision(decision)
            legacy_report = repository.get_report(decision)
            # Explicit malformed historical fixture; no repair, backfill or invented model admission.
            repository.save_decision(replace(legacy_decision, **{field: value}))
            repository._store.put(
                repository._REPORTS, decision, replace(legacy_report, **{field: value})
            )
            before = durable_bytes(harness.bundle.engine)
            context = context_read(client, candidate, decision)
            assert context.status_code == 409, context.text
            assert "RECORDED_REVIEW_CONTEXT_REQUIRED" in context.text
            assert durable_bytes(harness.bundle.engine) == before
            headers = {**HEADERS, "If-Match": f'W/"{payload["expectedDecisionVersion"]}"'}
            refused = client.post(f"{BASE}/{decision}/decision", headers=headers, json=payload)
            assert refused.status_code == 409, refused.text
            assert durable_bytes(harness.bundle.engine) == before
    finally:
        harness.close()


def returned_pending_with_newer_report(tmp_path):
    from tests.unit.test_network_canonical_review_projection import report

    harness, resolver, candidate, decision = pending(tmp_path)
    first = harness.decisions(TENANT).get_report(decision)
    returned = invoke(
        resolver,
        "decide_review",
        review_id=decision,
        decision="RETURN",
        reason="Return the original frozen report for lease and inspection evidence",
        required_data=["lease", "inspection"],
        conditions="",
        override_ack=False,
        actor_role_id="site_reviewer",
        actor_name=CHECKER.subject_id,
        principal=CHECKER,
        idempotency_key="original-return",
        correlation_id="00000000-0000-4000-8000-000000000301",
    )
    old_approval_id = returned["records"]["approvalId"]
    latest = harness.sitescore(TENANT).save_report(
        replace(report(), candidate_site_id=candidate, model_version="later-synthetic-saved-model")
    )
    submission = invoke(resolver, "submission_context", candidate_id=candidate, principal=AUTHOR)
    resumed = invoke(
        resolver,
        "submit_review",
        **arguments(submission, idempotency_key="return-resubmit-current"),
    )
    assert resumed["records"]["decisionId"] == decision
    assert (
        resumed["records"]["reportId"] == first.report_id
        and resumed["records"]["reportVersion"] == 1
    )
    assert latest.report_version == 2 and resumed["records"]["approvalId"] != old_approval_id
    return harness, resolver, candidate, decision, first, latest, old_approval_id


@pytest.mark.parametrize("action", ["GO", "WAIT", "RETURN", "REJECT"])
def test_current_resubmitted_owner_decides_frozen_v1_preserving_latest_v2_and_prior_returned_signature(
    tmp_path, action
):
    harness, _, candidate, decision, first, latest, old_approval_id = (
        returned_pending_with_newer_report(tmp_path)
    )
    try:
        service = harness.bundle.network_review_resolver()._factory(None, TENANT)
        approvals = service._decision_workflow._approval_store
        old_signature = asdict(approvals.get(old_approval_id))
        latest_before = harness.sitescore(TENANT).latest(candidate).to_dict()
        with TestClient(harness.app()) as client:
            read = context_read(client, candidate, decision)
            assert read.status_code == 200, read.text
            context = read.json()
            assert context["canDecide"] is True
            assert context["target"]["expectedReportId"] == first.report_id
            assert context["target"]["expectedReportVersion"] == 1
            assert context["target"]["expectedDecisionVersion"] == 4
            payload = body(context, action)
            headers = {**HEADERS, "If-Match": 'W/"4"'}
            posted = client.post(f"{BASE}/{decision}/decision", headers=headers, json=payload)
            assert posted.status_code == 200, posted.text
            value = posted.json()
            assert (
                value["review"]["version"] == 5 and value["review"]["report_id"] == first.report_id
            )
            assert (
                value["review"]["report_version"] == 1
                and value["review"]["network_action"] == action
            )
            assert value["review"]["created_by"] == AUTHOR.subject_id
            assert asdict(approvals.get(old_approval_id)) == old_signature
            assert harness.sitescore(TENANT).latest(candidate).to_dict() == latest_before
            assert harness.sitescore(TENANT).latest(candidate).report_id == latest.report_id
            assert harness.decisions(TENANT).get_report(decision).to_dict() == first.to_dict()
            after = client.get(BASE, headers=HEADERS).json()
            row = next(row for row in after["reviews"] if row["id"] == decision)
            assert row["reportVersion"] == 1 and row["decision"]["decision"] == action
            assert row["decision"]["decidedBy"] == CHECKER.subject_id
            before_replay = durable_bytes(harness.bundle.engine)
            replay = client.post(f"{BASE}/{decision}/decision", headers=headers, json=payload)
            assert replay.status_code == 200 and replay.json()["idempotentReplay"] is True
            assert durable_bytes(harness.bundle.engine) == before_replay
            assert harness.runtime.calls == []
    finally:
        harness.close()


@pytest.mark.parametrize(
    "change", ["creator", "tenant", "latest-report-as-frozen", "old-approval-as-current"]
)
def test_resubmitted_frozen_owner_rejects_foreign_actor_or_wrong_current_identity_without_domain_writes(
    tmp_path, change
):
    harness, _, candidate, decision, _, latest, old_approval_id = (
        returned_pending_with_newer_report(tmp_path)
    )
    try:
        with TestClient(harness.app()) as client:
            context = context_read(client, candidate, decision).json()
            payload = body(context)
            headers = {**HEADERS, "If-Match": 'W/"4"'}
            expected = 409
            if change == "creator":
                headers.update(
                    {"x-subject-id": AUTHOR.subject_id, "If-Actor-Match": AUTHOR.subject_id}
                )
                payload["expectedActorSubjectId"] = AUTHOR.subject_id
                expected = 403
            if change == "tenant":
                payload["expectedTenantId"] = "other-tenant"
            if change == "latest-report-as-frozen":
                payload.update(
                    expectedReportId=latest.report_id, expectedReportVersion=latest.report_version
                )
            if change == "old-approval-as-current":
                payload["expectedApprovalId"] = old_approval_id
            before = durable_bytes(harness.bundle.engine)
            refused = client.post(f"{BASE}/{decision}/decision", headers=headers, json=payload)
            assert refused.status_code == expected, refused.text
            assert durable_bytes(harness.bundle.engine) == before
            assert harness.decisions(TENANT).get_decision(decision).status.value == "PENDING_REVIEW"
    finally:
        harness.close()


@pytest.mark.parametrize("malformed", [0, True, 2.0, "2"])
def test_original_valid_context_cannot_transition_a_later_malformed_stored_decision_version(
    tmp_path, malformed
):
    harness, _, candidate, decision = pending(tmp_path)
    try:
        with TestClient(harness.app()) as client:
            original = context_read(client, candidate, decision)
            assert original.status_code == 200, original.text
            payload = body(original.json())
            repository = harness.decisions(TENANT)
            repository.save_decision(replace(repository.get_decision(decision), version=malformed))
            before = durable_bytes(harness.bundle.engine)
            refreshed = context_read(client, candidate, decision)
            assert refreshed.status_code == 409, refreshed.text
            assert durable_bytes(harness.bundle.engine) == before
            posted = client.post(
                f"{BASE}/{decision}/decision",
                headers={**HEADERS, "If-Match": 'W/"2"'},
                json=payload,
            )
            assert posted.status_code == 409, posted.text
            assert durable_bytes(harness.bundle.engine) == before
            assert harness.decisions(TENANT).get_decision(decision).status.value == "PENDING_REVIEW"
    finally:
        harness.close()


def test_authorized_original_receipt_replay_precedes_fresh_recorded_identity_guard_without_rewriting_corruption(
    tmp_path,
):
    harness, _, candidate, decision = pending(tmp_path)
    try:
        with TestClient(harness.app()) as client:
            original = context_read(client, candidate, decision).json()
            payload = body(original)
            headers = {**HEADERS, "If-Match": 'W/"2"'}
            posted = client.post(f"{BASE}/{decision}/decision", headers=headers, json=payload)
            assert posted.status_code == 200, posted.text
            repository = harness.decisions(TENANT)
            saved = repository.get_decision(decision)
            repository.save_decision(replace(saved, version=float(saved.version)))
            before = durable_bytes(harness.bundle.engine)
            replay = client.post(f"{BASE}/{decision}/decision", headers=headers, json=payload)
            assert replay.status_code == 200 and replay.json()["idempotentReplay"] is True
            assert replay.json()["review"] == posted.json()["review"]
            assert durable_bytes(harness.bundle.engine) == before
            denied = client.post(
                f"{BASE}/{decision}/decision",
                headers={**headers, "x-roles": "expansion_user"},
                json=payload,
            )
            assert denied.status_code == 403 and durable_bytes(harness.bundle.engine) == before
    finally:
        harness.close()
