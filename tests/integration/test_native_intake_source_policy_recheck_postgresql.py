"""Real requests and default workers, using only synthetic current grants."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import MethodType

import pytest

from modules.listing.application.native_intake_source_policy_recheck import (
    POLICY_ENV,
    SIGNING_KEY_ENV,
    NativeIntakeSourcePolicyRecheckOwner,
)
from modules.listing.domain.identity_graph import IdentityGraphError
from shared.audit.errors import CanonicalAuditUnavailable
from shared.auth import Role
from shared.infrastructure.persistence.factory import PersistenceBundle
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus
from tests.integration.test_native_intake_requester_authority_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_source_policy_postgresql import (
    drive_upstream,
    native_row,
    sql_patch,
)
from tests.integration.test_native_intake_waiting_cancel_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_waiting_cancel_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_waiting_cancel_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_waiting_cancel_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_waiting_cancel_postgresql import (
    start_case as start_case,
)
from tests.integration.test_native_intake_waiting_cancel_postgresql import state
from tests.integration.test_native_intake_waiting_cancel_postgresql import (
    waiting as waiting,
)
from tests.security.test_native_intake_source_policy import policy

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def recheck(waiting, authority, source_policy_case, monkeypatch):
    start, prior = waiting
    graph, ids, _, _ = start[0]
    assert graph is authority[0]
    start[1][1]["principal"] = replace(start[1][1]["principal"], roles=frozenset({Role.DATA_OWNER}))
    monkeypatch.setenv(POLICY_ENV, json.dumps({**policy(ids["tenant"]), "policy_id": "synthetic-native-recheck"}))
    monkeypatch.setenv(SIGNING_KEY_ENV, "synthetic-separate-recheck-capability-" * 2)
    start[2].canonical_intake_source_policy_recheck = MethodType(PersistenceBundle.canonical_intake_source_policy_recheck, start[2])
    return waiting, NativeIntakeSourcePolicyRecheckOwner(graph), source_policy_case


def request(case, *, key="synthetic-native-policy-recheck-001", version=None, **body):
    (start, prior), _, _ = case
    return start[1][0].post(f'/api/v1/canonical-intakes/{prior["intake_id"]}/source-policy-rechecks',
        headers={"Idempotency-Key": key, "If-Match": f'W/"{version or prior["version"]}"'},
        json={"reason": "Recheck the current synthetic source policy", **body})


def drive(case, response):
    _, owner, upstream = case
    start = case[0][0]
    receipt = owner.admit_event(start[0][1]["tenant"], response.json()["outbox_event_id"])
    return drive_upstream(upstream, receipt, owner)


@pytest.mark.parametrize(("outcome", "target"), [
    ("assisted", "AWAITING_ASSISTED_ENTRY"), ("waiting", "AWAITING_SOURCE_AUTHORIZATION"),
    ("retrieval", "RETRIEVING"), ("blocked", "QUARANTINED"),
])
def test_actual_default_workers_recheck_current_policy_without_granting_access(recheck, outcome, target):
    (start, prior), owner, upstream = recheck
    graph, ids, _, db = start[0]
    if outcome == "assisted":
        with db.connect() as conn:
            conn.execute("UPDATE intake.source_registry SET assisted_entry_approval_ref='synthetic-manual-approval',"
                "assisted_entry_approved_by=%s,assisted_entry_review_expires_at=clock_timestamp()+interval '1 hour',version=version+1 "
                "WHERE source_id='synthetic-feed'", (ids["reviewer"],))
    elif outcome in {"retrieval", "blocked"}:
        sql_patch(upstream, "retrieval_mode='APPROVED_RETRIEVAL'" if outcome == "retrieval" else "retrieval_mode='SOURCE_BLOCKED'")
    original = native_row(start, prior["intake_id"])
    response = request(recheck)
    assert response.status_code == 202, response.text
    receipt = response.json()
    assert receipt["status"] == "REQUESTED" and receipt["version"] == prior["version"]
    assert response.headers["etag"] == f'W/"{prior["version"]}"'
    assert native_row(start, prior["intake_id"]) == original
    with graph.transaction(ids["tenant"]):
        assert graph.engine.query_one("SELECT job_id FROM workflow.jobs WHERE tenant_id=? AND aggregate_type='intake_policy_recheck'",
                                      (ids["tenant"],)) is None
    duplicate = request(recheck)
    assert duplicate.status_code == 202 and duplicate.json() == receipt
    assert duplicate.headers["Idempotency-Replayed"] == "true"
    assert request(recheck, key="synthetic-distinct-pending-recheck-001").status_code == 409
    checked = drive(recheck, response)
    assert checked["processing_state"] == "CHECKING_SOURCE_POLICY" and checked["version"] == prior["version"] + 1
    assert checked["requested_by"] == ids["actor"] and checked["authority"]["role"] == "data_owner"
    assert native_row(start, prior["intake_id"])["source_policy_state"] == "AUTH_REQUIRED"
    delivery = graph.queue.get(checked["job_id"])
    assert delivery.status == JobStatus.SUCCEEDED and delivery.delivery_state is None
    assert owner.execute(delivery) == checked
    source = upstream[1]
    admitted = source.admit_event(ids["tenant"], checked["outbox_event_id"])
    result = drive_upstream(upstream, admitted, source)
    assert result["processing_state"] == target and result["version"] == prior["version"] + 2
    if outcome in {"assisted", "waiting"}:
        assert result["policy_decision"]["effective_policy"] == "AUTH_REQUIRED"
    assert owner.execute(delivery) == checked
    assert request(recheck).json() == receipt


def test_repeated_wait_cycles_keep_a_direct_root_and_constant_producer_depth(recheck, monkeypatch):
    (start, prior), _, upstream = recheck
    graph, ids, _, _ = start[0]
    original = NativeIntakeSourcePolicyRecheckOwner._committed_result
    depth, maximum, calls = 0, 0, 0

    def bounded(self, *args, **kwargs):
        nonlocal depth, maximum, calls
        depth += 1
        maximum = max(maximum, depth)
        calls += 1
        try:
            return original(self, *args, **kwargs)
        finally:
            depth -= 1

    monkeypatch.setattr(NativeIntakeSourcePolicyRecheckOwner, "_committed_result", bounded)
    version, roots = prior["version"], []
    for index in range(3):
        response = request(recheck, key=f"synthetic-repeated-recheck-{index:03d}", version=version)
        assert response.status_code == 202, response.text
        checked = drive(recheck, response)
        roots.append(checked["root_identity_event_id"])
        source = upstream[1]
        admitted = list(source.admit_partition(ids["tenant"], limit=1))
        assert len(admitted) == 1
        result = drive_upstream(upstream, admitted[0], source)
        assert result["processing_state"] == "AWAITING_SOURCE_AUTHORIZATION"
        version = result["version"]
    assert len(set(roots)) == 1 and maximum == 1 and calls > 3
    assert native_row(start, prior["intake_id"])["version"] == prior["version"] + 6


@pytest.mark.parametrize("cancel_first", [False, True])
def test_revoked_requester_or_cancelled_intake_fails_recheck_and_keeps_wait_cancellable(recheck, cancel_first):
    (start, prior), owner, upstream = recheck
    graph, ids, _, db = start[0]
    response = request(recheck)
    assert response.status_code == 202, response.text
    admitted = list(owner.admit_partition(ids["tenant"], limit=1))
    assert len(admitted) == 1
    def cancel():
        return start[1][0].post(f'/api/v1/canonical-intakes/{prior["intake_id"]}/cancel',
            headers={"Idempotency-Key": "synthetic-recheck-independent-cancel", "If-Match": f'W/"{prior["version"]}"'},
            json={"reason": "Cancel this synthetic waiting request"})
    if cancel_first:
        assert cancel().status_code == 200
    else:
        with db.connect() as conn:
            conn.execute("DELETE FROM identity.account_roles WHERE account_id=%s", (ids["actor"],))
    from apps.worker.oday_worker.main import ODayWorker
    from tests.integration.test_native_intake_identity_postgresql import defer_audit

    defer_audit(graph)
    assert ODayWorker(persistence=start[2], heartbeat_interval_seconds=0.01).run_once() is True
    delivery = graph.queue.get(admitted[0]["job_id"])
    assert delivery.status == JobStatus.FAILED and delivery.delivery_state == JobDeliveryState.DEAD_LETTER
    with graph.transaction(ids["tenant"]):
        job = graph.engine.query_one("SELECT status,result FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                                     (ids["tenant"], delivery.job_id))
        assert job["status"] == "FAILED" and job["result"] is None
    row = native_row(start, prior["intake_id"])
    assert row["processing_state"] == ("CANCELLED" if cancel_first else "AWAITING_SOURCE_AUTHORIZATION")
    assert row["version"] == prior["version"] + int(cancel_first) and row["source_policy_state"] == "AUTH_REQUIRED"
    if not cancel_first:
        with db.connect() as conn:
            conn.execute("INSERT INTO identity.account_roles (account_id,role,granted_by) VALUES (%s,'data_owner','synthetic-fixture')",
                         (ids["actor"],))
        assert request(recheck, key="synthetic-retry-after-revoked-worker").status_code == 202
        assert cancel().status_code == 200


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("revocation", ["http_role", "sql_role", "sql_scope", "disabled", "module", "clearance"])
def test_http_request_and_historical_replay_reauthorize_current_identity(recheck, replay, revocation):
    (start, _), _, _ = recheck
    _, ids, _, db = start[0]
    if replay:
        assert request(recheck).status_code == 202
    if revocation == "http_role":
        caller = start[1][1]
        caller["principal"] = replace(caller["principal"], roles=frozenset({Role.PLATFORM_ADMIN}))
    else:
        with db.connect() as conn:
            sql = {
                "sql_role": "DELETE FROM identity.account_roles WHERE account_id=%s",
                "sql_scope": "DELETE FROM identity.account_scopes WHERE account_id=%s",
                "disabled": "UPDATE identity.accounts SET status='disabled' WHERE account_id=%s",
                "module": "UPDATE identity.account_scopes SET modules='[\"priceops\"]'::jsonb WHERE account_id=%s",
                "clearance": "UPDATE identity.account_scopes SET clearance='PUBLIC' WHERE account_id=%s",
            }[revocation]
            conn.execute(sql, (ids["actor"],))
    before = state(recheck[0])
    response = request(recheck)
    assert response.status_code == 403, response.text
    assert state(recheck[0]) == before


@pytest.mark.parametrize("defect", ["actor", "approval", "credentials", "stale_version", "reason_change"])
def test_untrusted_request_fields_and_intent_conflicts_never_admit_work(recheck, defect):
    body = {"actor_subject_id": "synthetic-forged"} if defect == "actor" else {
        "assisted_entry_approval_ref": "synthetic-forged"} if defect == "approval" else {
        "reason": "Authorization: Bearer synthetic-secret-credential"} if defect == "credentials" else {}
    if defect == "reason_change":
        assert request(recheck).status_code == 202
        body = {"reason": "A different synthetic command intent"}
    before = state(recheck[0])
    response = request(recheck, version=999 if defect == "stale_version" else None, **body)
    assert response.status_code == (409 if defect in {"stale_version", "reason_change"} else 422), response.text
    assert "synthetic-secret-credential" not in response.text and "synthetic-forged" not in response.text
    assert state(recheck[0]) == before


@pytest.mark.parametrize("fault", ["audit", "outbox", "receipt"])
def test_request_audit_outbox_and_durable_receipt_roll_back_together(recheck, monkeypatch, fault):
    (start, _), _, _ = recheck
    graph = start[0][0]
    def fail(*args, **kwargs):
        raise CanonicalAuditUnavailable("Synthetic atomic-request failure")
    if fault == "audit":
        monkeypatch.setattr(graph.audit_sink, "persist", fail)
    elif fault == "outbox":
        monkeypatch.setattr(type(graph), "_outbox", fail)
    else:
        monkeypatch.setattr(start[1][2], "flush", fail)
    before = state(recheck[0])
    response = request(recheck)
    assert response.status_code == 503, response.text
    assert state(recheck[0]) == before


def test_competing_request_keys_commit_only_one_active_outbox_request(recheck):
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda key: request(recheck, key=key), [
            "synthetic-racing-request-key-001", "synthetic-racing-request-key-002"]))
    assert sorted(r.status_code for r in responses) == [202, 409], [r.text for r in responses]
    (start, prior), _, _ = recheck
    graph, ids, _, _ = start[0]
    with graph.transaction(ids["tenant"]):
        rows = graph.engine.query("SELECT event_id FROM workflow.outbox_events WHERE tenant_id=? "
            "AND aggregate_type='intake_policy_recheck' AND event_type='intake.source_policy_recheck_requested'", (ids["tenant"],))
        assert len(rows) == 1
    assert native_row(start, prior["intake_id"])["version"] == prior["version"]


def claimed_recheck(case):
    from tests.integration.test_native_intake_identity_postgresql import defer_audit

    response = request(case)
    assert response.status_code == 202, response.text
    (start, _), owner, _ = case
    graph, ids, _, _ = start[0]
    admitted = owner.admit_event(ids["tenant"], response.json()["outbox_event_id"])
    defer_audit(graph)
    delivery = graph.queue.claim_next(worker_id="synthetic-fenced-recheck-worker")
    assert delivery.job_id == admitted["job_id"]
    return response.json(), admitted, delivery


@pytest.mark.parametrize("tamper", ["request_digest", "root_event", "request_audit", "admission_audit", "input", "version", "expired", "fence"])
def test_changed_proof_input_or_delivery_cannot_advance_wait(recheck, tamper):
    receipt, admitted, delivery = claimed_recheck(recheck)
    (start, prior), owner, _ = recheck
    graph, ids, _, _ = start[0]
    with graph.transaction(ids["tenant"]):
        if tamper == "request_digest":
            graph.engine.execute("UPDATE workflow.outbox_events SET payload=jsonb_set(payload,'{payload,input_sha256}',to_jsonb(?::text)) "
                                 "WHERE tenant_id=? AND event_id=?", ("0" * 64, ids["tenant"], receipt["outbox_event_id"]))
        elif tamper == "root_event":
            graph.engine.execute("UPDATE workflow.outbox_events SET payload=jsonb_set(payload,'{payload,root_identity_event_id}',to_jsonb(?::text)) "
                                 "WHERE tenant_id=? AND event_id=?", ("malformed-root", ids["tenant"], receipt["outbox_event_id"]))
        elif tamper in {"request_audit", "admission_audit"}:
            audit_id = receipt["audit_event_id"] if tamper == "request_audit" else admitted["audit_event_id"]
            graph.engine.execute("UPDATE audit.audit_events SET after_value=jsonb_set(after_value,'{intake_id}',to_jsonb(?::text)) "
                                 "WHERE tenant_id=? AND audit_event_id=?", ("synthetic-tampered", ids["tenant"], audit_id))
        elif tamper == "input":
            graph.engine.execute("UPDATE intake.intakes SET original_url='https://synthetic.example/changed-input' WHERE tenant_id=? AND intake_id=?",
                                 (ids["tenant"], prior["intake_id"]))
        elif tamper == "version":
            graph.engine.execute("UPDATE intake.intakes SET version=version+1 WHERE tenant_id=? AND intake_id=?", (ids["tenant"], prior["intake_id"]))
        elif tamper == "expired":
            graph.engine.execute("UPDATE workflow.jobs SET timeout_at=clock_timestamp()-interval '1 second' WHERE tenant_id=? AND job_id=?",
                                 (ids["tenant"], delivery.job_id))
        else:
            graph.engine.execute("UPDATE durable_jobs SET fence_token=fence_token+1 WHERE job_id=?", (delivery.job_id,))
    before = state(recheck[0])
    with pytest.raises((IdentityGraphError, ValueError, JobFenceRejectedError)):
        owner.execute(delivery)
    assert state(recheck[0]) == before
    assert native_row(start, prior["intake_id"])["processing_state"] == "AWAITING_SOURCE_AUTHORIZATION"


@pytest.mark.parametrize("fault", ["audit", "outbox", "physical_success"])
def test_worker_result_and_both_jobs_roll_back_with_transition_and_audit(recheck, monkeypatch, fault):
    _, _, delivery = claimed_recheck(recheck)
    (start, prior), owner, _ = recheck
    graph = start[0][0]
    before = state(recheck[0])
    def fail(*args, **kwargs):
        raise CanonicalAuditUnavailable("Synthetic atomic-worker failure")
    with monkeypatch.context() as patch:
        if fault == "audit":
            patch.setattr(graph.audit_sink, "persist", fail)
        elif fault == "outbox":
            patch.setattr(type(graph), "_outbox", fail)
        else:
            original = graph.queue.update_status
            def physical_failure(*args, **kwargs):
                original(*args, **kwargs)
                raise CanonicalAuditUnavailable("Synthetic failure after physical status write")
            patch.setattr(graph.queue, "update_status", physical_failure)
        with pytest.raises(CanonicalAuditUnavailable):
            owner.execute(delivery)
    assert state(recheck[0]) == before
    assert native_row(start, prior["intake_id"])["version"] == prior["version"]
    assert owner.execute(delivery)["version"] == prior["version"] + 1
