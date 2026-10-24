"""Real native source-policy transactions with synthetic explicit approvals."""

import json
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import MethodType
from uuid import NAMESPACE_DNS, uuid4, uuid5

import pytest

from apps.worker.oday_worker.main import ODayWorker
from modules.listing.application.native_intake_source_policy import (
    NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE,
    POLICY_ENV,
    SIGNING_KEY_ENV,
    NativeIntakeSourcePolicyConflict,
    NativeIntakeSourcePolicyOwner,
    NativeIntakeSourcePolicyUnavailable,
    NativeSourcePolicyReevaluationRequired,
    native_intake_source_policy_policy,
    run_native_intake_source_policy_delivery,
)
from modules.listing.application.native_intake_url_identity import NATIVE_URL_RULE_VERSION
from shared.audit.errors import CanonicalAuditUnavailable
from shared.domain.events import validate_event
from shared.infrastructure.persistence.factory import PersistenceBundle
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus
from tests.integration.test_native_intake_identity_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_identity_postgresql import (
    defer_audit,
    graph_rows,
    native_row,
    phase_snapshot,
    submitted,
)
from tests.integration.test_native_intake_identity_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_identity_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_identity_postgresql import (
    start_case as start_case,
)
from tests.security.test_native_intake_source_policy import policy

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def source_policy_case(identity_case, monkeypatch, record_property):
    start, _ = identity_case
    graph, ids, _, _ = start[0]
    monkeypatch.setenv(POLICY_ENV, json.dumps({**policy(ids["tenant"]), "policy_id": "synthetic-native-source-policy"}))
    monkeypatch.setenv(SIGNING_KEY_ENV, "synthetic-source-policy-separate-key-" * 2)
    start[2].canonical_intake_source_policy = MethodType(PersistenceBundle.canonical_intake_source_policy, start[2])
    # Apply the additive AUTH routing migration; immutable baseline stays intact.
    with start[0][3].connect() as conn:
        conn.execute((Path(__file__).resolve().parents[2] /
            "infra/db/migrations/000034_native_intake_auth_wait.sql").read_text())
    owner = NativeIntakeSourcePolicyOwner(graph)
    owner.record_upstream_retry = record_property
    return identity_case, owner


def drive_upstream(case, receipt, owner):
    """Consume only the upstream command's own durable bounded retry schedule."""
    identity, source_owner = case
    start, _ = identity
    graph, ids, _, _ = start[0]
    for _ in range(3):
        defer_audit(graph)
        assert ODayWorker(persistence=start[2], heartbeat_interval_seconds=0.01).run_once() is True
        delivery = graph.queue.get(receipt["job_id"])
        if delivery.status == JobStatus.SUCCEEDED:
            return owner.execute(delivery)
        with graph.transaction(ids["tenant"]):
            job = graph.engine.query_one("SELECT status,attempt,max_attempts,last_error FROM workflow.jobs "
                "WHERE tenant_id=? AND job_id=?", (ids["tenant"], delivery.job_id))
            error = json.loads(job["last_error"]) if isinstance(job["last_error"], str) else job["last_error"]
            assert delivery.status == JobStatus.QUEUED and delivery.delivery_state == JobDeliveryState.RETRYING, job
            assert job["status"] == "RETRYING" and 0 < job["attempt"] < job["max_attempts"] <= 3, job
            assert error["error_class"] in {"SerializationFailure", "DeadlockDetected", "CanonicalAuditUnavailable"}, error
            source_owner.record_upstream_retry("actual_upstream_retry", json.dumps({"job_type": delivery.job_type, **job, "last_error": error}))
            graph.engine.query_one("SELECT pg_sleep(GREATEST(0.0,EXTRACT(EPOCH FROM (available_at-clock_timestamp())))) "
                                   "FROM durable_jobs WHERE job_id=?", (delivery.job_id,))
    raise AssertionError("The upstream native command did not commit within its declared attempt budget")


def prepare(case, outcome="retrieval", *, admit=True):
    identity, owner = case
    start, identity_owner = identity
    graph, ids, _, db = start[0]
    submission = submitted(start, "URL")
    native_start = start[3].admit_event(ids["tenant"], submission["outbox_event_id"])
    started = drive_upstream(case, native_start, start[3])
    assert started["processing_state"] == "CHECKING_IDENTITY"
    with db.connect() as conn:
        conn.execute("UPDATE intake.source_registry SET allowed_hosts=ARRAY['synthetic.example'],"
            "canonicalization_rule_version=%s,retrieval_mode='APPROVED_RETRIEVAL',legal_approval_ref='synthetic-legal',"
            "license_approval_ref='synthetic-license',review_expires_at=clock_timestamp()+interval '1 hour',"
            "kill_switch=false,production_enabled=true,version=2 WHERE source_id='synthetic-feed'", (NATIVE_URL_RULE_VERSION,))
        conn.execute("UPDATE expansion.listings SET canonical_url_sha256=%s WHERE listing_id=%s", ("a"*64, ids["listing"]))
    prior = identity_owner.admit_event(ids["tenant"], started["outbox_event_id"])
    result = drive_upstream(case, prior, identity_owner)
    assert result["processing_state"] == "CHECKING_SOURCE_POLICY"
    with db.connect() as conn:
        conn.execute("UPDATE intake.source_registry SET version=3,requests_per_minute=5,concurrent_requests=1 "
                     "WHERE source_id='synthetic-feed'")
        if outcome != "retrieval":
            conn.execute("UPDATE intake.source_registry SET retrieval_mode=%s WHERE source_id='synthetic-feed'",
                         ("ASSISTED_ENTRY_ONLY" if outcome == "assisted" else "SOURCE_BLOCKED",))
    receipt = owner.admit_event(ids["tenant"], result["outbox_event_id"]) if admit else None
    return submission, result, receipt


def claim(case, receipt):
    graph = case[0][0][0][0]
    defer_audit(graph)
    delivery = graph.queue.claim_next(worker_id="synthetic-native-source-policy-worker")
    assert delivery.job_id == receipt["job_id"] and delivery.job_type == NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE
    return delivery


@pytest.mark.parametrize(("outcome", "target", "effective", "permission", "action"), [
    ("retrieval", "RETRIEVING", "APPROVED_RETRIEVAL", "source.retrieve", "intake.retrieval_approved.v1"),
    ("assisted", "AWAITING_ASSISTED_ENTRY", "ASSISTED_ENTRY_ONLY", "intake.assisted_entry", "intake.assisted_entry_required.v1"),
    ("blocked", "QUARANTINED", "SOURCE_BLOCKED", "intake.quarantine", "intake.quarantined.v1"),
])
def test_http_start_identity_and_actual_default_source_policy_worker(source_policy_case, outcome, target, effective, permission, action):
    identity, owner = source_policy_case
    start, _ = identity
    graph, ids, _, db = start[0]
    submission, producer, receipt = prepare(source_policy_case, outcome)
    before = native_row(start, submission["intake_id"])
    before_graph = graph_rows(identity)
    assert owner.admit_event(ids["tenant"], producer["outbox_event_id"]) == receipt
    defer_audit(graph)
    assert ODayWorker(persistence=start[2], heartbeat_interval_seconds=0.01).run_once() is True
    delivery = graph.queue.get(receipt["job_id"])
    with graph.transaction(ids["tenant"]):
        job = graph.engine.query_one("SELECT status,last_error FROM workflow.jobs WHERE job_id=?", (receipt["job_id"],))
    assert delivery.status == JobStatus.SUCCEEDED, job
    result = owner.execute(delivery)
    assert result["processing_state"] == target and result["version"] == 4
    proof = result["policy_decision"]
    assert proof["source_version"] == 3 and proof["effective_policy"] == effective
    assert proof["source_snapshot"]["version"] == 3 and proof["host_match_count"] == 1
    assert proof["accepted_worker"] and proof["accepted_attempt"] == 1
    after = native_row(start, submission["intake_id"])
    assert after["processing_state"] == target and after["source_policy_state"] == effective and after["version"] == 4
    assert all(after[k] == before[k] for k in before if k not in {"processing_state", "source_policy_state", "version", "last_transition_at"})
    assert graph_rows(identity) == before_graph
    assert owner.execute(delivery) == result
    assert list(owner.admit_partition(ids["tenant"], limit=5)) == []
    assert owner.admit_event(ids["tenant"], producer["outbox_event_id"]) == receipt
    with db.connect() as conn:
        row = conn.execute("SELECT permission,reason_code,source_policy_version,service_principal,actor_subject_id "
            "FROM intake.intake_stage_transitions WHERE transition_id=%s", (result["transition_id"],)).fetchone()
        assert row == (permission, proof["reason_code"], 3, NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE, None)
        audit = conn.execute("SELECT action FROM audit.audit_events WHERE audit_event_id=%s", (result["audit_event_id"],)).fetchone()
        assert audit == (action,)
        event = conn.execute("SELECT payload FROM workflow.outbox_events WHERE event_id=%s", (result["outbox_event_id"],)).fetchone()[0]
        assert validate_event(event) == [] and event["causation_id"] == producer["outbox_event_id"]
        assert set(event["payload"]) == {"intake_id", "from_state", "to_state", "transition_id", "version", "occurred_at"}
        assert event["payload"]["from_state"] == "CHECKING_SOURCE_POLICY" and event["payload"]["to_state"] == target
    assert graph.audit_sink.writer.verify_chain(ids["tenant"]).ok
    final_snapshot = phase_snapshot(identity)
    assert owner.execute(delivery) == result
    assert phase_snapshot(identity) == final_snapshot


def sql_patch(case, set_clause, params=()):
    db = case[0][0][0][3]
    with db.connect() as conn:
        conn.execute("UPDATE intake.source_registry SET " + set_clause + " WHERE source_id='synthetic-feed'", params)


@pytest.mark.parametrize(("patch", "effective"), [
    ("kill_switch=true", "SOURCE_BLOCKED"), ("production_enabled=false", "SOURCE_BLOCKED"),
    ("retrieval_mode='POLICY_UNKNOWN'", "POLICY_UNKNOWN"),
    ("legal_approval_ref=NULL", "POLICY_UNKNOWN"), ("legal_approval_ref=' '", "POLICY_UNKNOWN"),
    ("license_approval_ref=NULL", "POLICY_UNKNOWN"), ("review_expires_at=NULL", "POLICY_UNKNOWN"),
    ("review_expires_at=clock_timestamp()-interval '1 second'", "POLICY_UNKNOWN"),
    ("requests_per_minute=0", "POLICY_UNKNOWN"), ("concurrent_requests=0", "POLICY_UNKNOWN"),
    ("canonicalization_rule_version='v1.0'", "POLICY_UNKNOWN"),
    ("allowed_hosts=ARRAY['other.example']", "POLICY_UNKNOWN"),
])
def test_current_registry_denials_commit_real_quarantine_without_source_use(source_policy_case, patch, effective):
    identity, owner = source_policy_case
    start, _ = identity
    graph, ids, _, _ = start[0]
    submission, _, receipt = prepare(source_policy_case)
    sql_patch(source_policy_case, patch)
    before_graph = graph_rows(identity)
    result = owner.execute(claim(source_policy_case, receipt))
    assert result["processing_state"] == "QUARANTINED"
    assert result["policy_decision"]["effective_policy"] == effective
    assert result["policy_decision"]["valid_until"] is None
    assert native_row(start, submission["intake_id"])["source_policy_state"] == effective
    assert graph_rows(identity) == before_graph
    with graph.transaction(ids["tenant"]):
        assert graph.engine.query_one("SELECT count(*) AS n FROM intake.source_snapshots WHERE intake_id=?", (submission["intake_id"],))["n"] == 0


@pytest.mark.parametrize("mode", ["retrieval", "assisted"])
def test_sentinel_owner_is_not_current_human_approval(source_policy_case, mode):
    _, owner = source_policy_case
    _, _, receipt = prepare(source_policy_case, mode)
    sql_patch(source_policy_case, "policy_owner_subject_id=%s", (str(uuid5(NAMESPACE_DNS, "system")),))
    result = owner.execute(claim(source_policy_case, receipt))
    assert result["processing_state"] == "QUARANTINED"
    assert result["policy_decision"]["reason_code"] == "SOURCE_APPROVAL_UNVERIFIED"


@pytest.mark.parametrize("condition", ["plain", "kill", "missing_approval"])
def test_AUTH_selected_destination_is_durable_without_source_use(source_policy_case, condition):
    identity, owner = source_policy_case
    start, _ = identity
    graph, ids, _, db = start[0]
    submission, _, receipt = prepare(source_policy_case)
    sql_patch(source_policy_case, "retrieval_mode='AUTH_REQUIRED'" +
              (",kill_switch=true" if condition == "kill" else ",legal_approval_ref=NULL" if condition == "missing_approval" else ""))
    before_graph = graph_rows(identity)
    result = run_native_intake_source_policy_delivery(claim(source_policy_case, receipt), owner)
    target = "AWAITING_SOURCE_AUTHORIZATION" if condition == "plain" else "QUARANTINED"
    assert result["processing_state"] == target
    row = native_row(start, submission["intake_id"])
    assert row["processing_state"] == target and row["version"] == 4
    assert graph.queue.get(receipt["job_id"]).status == JobStatus.SUCCEEDED
    assert graph_rows(identity) == before_graph
    assert owner.execute(graph.queue.get(receipt["job_id"])) == result
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM intake.intake_stage_transitions WHERE job_id=%s", (receipt["job_id"],)).fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM intake.source_snapshots WHERE intake_id=%s", (submission["intake_id"],)).fetchone() == (0,)
        assert conn.execute("SELECT status FROM workflow.jobs WHERE job_id=%s", (receipt["job_id"],)).fetchone() == ("SUCCEEDED",)
        event = conn.execute("SELECT payload FROM workflow.outbox_events WHERE event_id=%s", (result["outbox_event_id"],)).fetchone()[0]
        assert validate_event(event) == [] and event["payload"]["to_state"] == target
        action = "intake.source_authorization_required.v1" if condition == "plain" else "intake.quarantined.v1"
        assert conn.execute("SELECT action FROM audit.audit_events WHERE audit_event_id=%s", (result["audit_event_id"],)).fetchone() == (action,)
    assert list(owner.admit_partition(ids["tenant"], limit=5)) == []
    assert graph.audit_sink.writer.verify_chain(ids["tenant"]).ok


@pytest.mark.parametrize("approval", ["current", "absent", "blank", "expired", "missing_approver", "system_approver"])
def test_AUTH_default_worker_requires_distinct_current_manual_approval(source_policy_case, approval):
    identity, owner = source_policy_case
    start, _ = identity
    graph, ids, _, db = start[0]
    submission, _, receipt = prepare(source_policy_case)
    sql_patch(source_policy_case, "retrieval_mode='AUTH_REQUIRED',assisted_entry_approval_ref=%s,"
        "assisted_entry_approved_by=%s,assisted_entry_review_expires_at=clock_timestamp()+interval '1 hour'",
        ("synthetic-manual-approval", ids["actor"]))
    patch = {"absent": "assisted_entry_approval_ref=NULL", "blank": "assisted_entry_approval_ref=' '",
             "expired": "assisted_entry_review_expires_at=clock_timestamp()-interval '1 second'",
             "missing_approver": "assisted_entry_approved_by=NULL",
             "system_approver": "assisted_entry_approved_by='" + str(uuid5(NAMESPACE_DNS, "system")) + "'"}
    if approval != "current":
        sql_patch(source_policy_case, patch[approval])
    before_graph = graph_rows(identity)
    defer_audit(graph)
    assert ODayWorker(persistence=start[2], heartbeat_interval_seconds=0.01).run_once() is True
    delivery = graph.queue.get(receipt["job_id"])
    assert delivery.status == JobStatus.SUCCEEDED
    result = owner.execute(delivery)
    target = "AWAITING_ASSISTED_ENTRY" if approval == "current" else "AWAITING_SOURCE_AUTHORIZATION"
    assert result["processing_state"] == target
    assert result["policy_decision"]["effective_policy"] == "AUTH_REQUIRED"
    assert native_row(start, submission["intake_id"])["source_policy_state"] == "AUTH_REQUIRED"
    assert graph_rows(identity) == before_graph
    assert graph.audit_sink.writer.verify_chain(ids["tenant"]).ok
    # A later source update is not a resume or a replacement for its receipt.
    sql_patch(source_policy_case, "retrieval_mode='APPROVED_RETRIEVAL',version=version+1")
    before = phase_snapshot(identity)
    assert owner.execute(delivery) == result and phase_snapshot(identity) == before
    assert list(owner.admit_partition(ids["tenant"], limit=5)) == []
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM intake.source_snapshots WHERE intake_id=%s", (submission["intake_id"],)).fetchone() == (0,)


@pytest.mark.parametrize("cas", ["intake", "business"])
def test_separate_manual_approval_expiry_at_actual_final_CAS_rolls_back_then_waits(source_policy_case, monkeypatch, cas):
    identity, owner = source_policy_case
    start, _ = identity
    graph, ids, _, db = start[0]
    monkeypatch.setenv(POLICY_ENV, json.dumps({**policy(ids["tenant"]), "max_attempts": 3, "backoff_seconds": [0.001, 0.001]}))
    submission, _, receipt = prepare(source_policy_case)
    sql_patch(source_policy_case, "retrieval_mode='AUTH_REQUIRED',assisted_entry_approval_ref='synthetic-manual-approval',"
        "assisted_entry_approved_by=%s,assisted_entry_review_expires_at=clock_timestamp()+interval '2 seconds'", (ids["actor"],))
    delivery = claim(source_policy_case, receipt)
    original, observed = graph.engine.execute, []

    def expire(sql, params=()):
        matches = (sql.startswith("UPDATE intake.intakes SET processing_state=") if cas == "intake"
                   else sql.startswith("UPDATE workflow.jobs SET status='SUCCEEDED'"))
        if matches and not observed:
            observed.append(graph.engine.query_one("SELECT pg_backend_pid() AS pid"))
            graph.engine.query_one("SELECT pg_sleep(GREATEST(0.0,EXTRACT(EPOCH FROM "
                "((SELECT assisted_entry_review_expires_at FROM intake.source_registry "
                "WHERE source_id='synthetic-feed')-clock_timestamp())))+0.02)")
        return original(sql, params)

    monkeypatch.setattr(graph.engine, "execute", expire)
    before = phase_snapshot(identity)
    with pytest.raises(NativeSourcePolicyReevaluationRequired) as error:
        owner.execute(delivery)
    assert observed and phase_snapshot(identity) == before
    assert owner.settle_failure(delivery, error.value) is None
    with graph.transaction(ids["tenant"]):
        graph.engine.query_one("SELECT pg_sleep(GREATEST(0.0,EXTRACT(EPOCH FROM (available_at-clock_timestamp())))) "
                               "FROM durable_jobs WHERE job_id=?", (receipt["job_id"],))
    result = owner.execute(claim(source_policy_case, receipt))
    assert result["processing_state"] == "AWAITING_SOURCE_AUTHORIZATION"
    assert result["policy_decision"]["accepted_attempt"] == 2
    assert native_row(start, submission["intake_id"])["version"] == 4
    with db.connect() as conn:
        assert conn.execute("SELECT count(*),min(to_state) FROM intake.intake_stage_transitions WHERE job_id=%s",
                            (receipt["job_id"],)).fetchone() == (1, "AWAITING_SOURCE_AUTHORIZATION")


@pytest.mark.parametrize("missing", ["manual_approval_columns", "waiting_state"])
def test_unmigrated_AUTH_schema_cannot_admit_any_new_work(source_policy_case, missing):
    identity, owner = source_policy_case
    graph, ids, _, db = identity[0][0]
    _, producer, _ = prepare(source_policy_case, admit=False)
    with db.connect() as conn:
        if missing == "manual_approval_columns":
            conn.execute("ALTER TABLE intake.source_registry DROP COLUMN assisted_entry_approved_by")
        else:
            conn.execute("ALTER TABLE intake.intakes DROP CONSTRAINT intakes_processing_state_check")
    before = phase_snapshot(identity)
    with pytest.raises(NativeIntakeSourcePolicyUnavailable, match="^NATIVE_SOURCE_POLICY_CURRENT_SCHEMA_REQUIRED$"):
        owner.admit_event(ids["tenant"], producer["outbox_event_id"])
    assert phase_snapshot(identity) == before


@pytest.mark.parametrize("change", ["worker", "fence", "payload", "policy", "version", "scope", "url", "canonical", "cancel", "expired_lease", "foreign_root"])
def test_current_ownership_inputs_and_policy_changes_have_zero_business_effect(source_policy_case, monkeypatch, change):
    identity, owner = source_policy_case
    start, _ = identity
    graph, ids, _, _ = start[0]
    submission, _, receipt = prepare(source_policy_case)
    delivery = claim(source_policy_case, receipt)
    if change == "worker":
        delivery = replace(delivery, locked_by="synthetic-another-worker")
    elif change == "fence":
        delivery = replace(delivery, fence_token=delivery.fence_token+1)
    elif change == "payload":
        delivery = replace(delivery, payload={**delivery.payload, "permission": "source.retrieve"})
    elif change == "policy":
        monkeypatch.setenv(POLICY_ENV, json.dumps({**policy(ids["tenant"]), "version": 2}))
    elif change != "foreign_root":
        changes = {
            "version": ("UPDATE intake.intakes SET version=version+1 WHERE intake_id=?", (submission["intake_id"],)),
            "scope": ("UPDATE intake.intakes SET brand_id=? WHERE intake_id=?", (str(uuid4()), submission["intake_id"])),
            "url": ("UPDATE intake.intakes SET original_url='https://synthetic.example/changed' WHERE intake_id=?", (submission["intake_id"],)),
            "canonical": ("UPDATE intake.intakes SET canonical_url='https://synthetic.example/changed' WHERE intake_id=?", (submission["intake_id"],)),
            "cancel": ("UPDATE workflow.jobs SET cancellation_requested_at=clock_timestamp() WHERE job_id=?", (receipt["job_id"],)),
            "expired_lease": ("UPDATE durable_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=?", (receipt["job_id"],)),
        }
        with graph.transaction(ids["tenant"]):
            graph.engine.execute(*changes[change])
    before = phase_snapshot(identity)
    if change == "foreign_root":
        with graph.transaction(str(uuid4())), pytest.raises(PermissionError):
            owner.execute(delivery)
    else:
        with pytest.raises((NativeIntakeSourcePolicyConflict, JobFenceRejectedError)):
            owner.execute(delivery)
    assert phase_snapshot(identity) == before


@pytest.mark.parametrize("mode", ["retrieval", "assisted", "blocked"])
@pytest.mark.parametrize("failure", ["audit", "outbox", "result", "transport"])
def test_any_phase_metadata_failure_rolls_back_all_four_axes(source_policy_case, monkeypatch, mode, failure):
    identity, owner = source_policy_case
    graph = identity[0][0][0]
    _, _, receipt = prepare(source_policy_case, mode)
    delivery = claim(source_policy_case, receipt)
    before = phase_snapshot(identity)

    def fail(*_args, **_kwargs):
        raise CanonicalAuditUnavailable("Synthetic source-policy metadata outage")

    if failure == "audit":
        original = graph.audit_sink.persist

        def audit(record, scope):
            if record["action"] in {"intake.retrieval_approved.v1", "intake.assisted_entry_required.v1", "intake.quarantined.v1"}:
                original(record, scope)
                return fail()
            return original(record, scope)

        monkeypatch.setattr(graph.audit_sink, "persist", audit)
    elif failure == "outbox":
        original = graph._outbox

        def outbox(event):
            original(event)
            return fail()

        monkeypatch.setattr(graph, "_outbox", outbox)
    elif failure == "transport":
        original = graph.queue.update_status

        def transport(*args, **kwargs):
            original(*args, **kwargs)
            return fail()

        monkeypatch.setattr(graph.queue, "update_status", transport)
    else:
        original = graph.engine.execute

        def execute(sql, params=()):
            result = original(sql, params)
            if "UPDATE workflow.jobs SET status='SUCCEEDED'" in sql:
                return fail()
            return result

        monkeypatch.setattr(graph.engine, "execute", execute)
    with pytest.raises(CanonicalAuditUnavailable):
        owner.execute(delivery)
    assert phase_snapshot(identity) == before


def test_unknown_commit_reply_recovers_the_real_policy_decision(source_policy_case, monkeypatch):
    identity, owner = source_policy_case
    graph = identity[0][0][0]
    _, _, receipt = prepare(source_policy_case)
    delivery = claim(source_policy_case, receipt)
    original, once = graph.transaction, [True]

    @contextmanager
    def lost_reply(tenant):
        with original(tenant):
            yield
        if once[0]:
            once[0] = False
            raise OSError("Synthetic lost policy commit reply")

    monkeypatch.setattr(graph, "transaction", lost_reply)
    result = run_native_intake_source_policy_delivery(delivery, owner)
    assert result["processing_state"] == "RETRIEVING" and graph.queue.get(delivery.job_id).status == JobStatus.SUCCEEDED
    before = phase_snapshot(identity)
    assert owner.execute(delivery) == result and phase_snapshot(identity) == before


def test_outer_root_retains_rollback_authority_over_policy_and_delivery(source_policy_case):
    identity, owner = source_policy_case
    graph, ids, _, _ = identity[0][0]
    _, _, receipt = prepare(source_policy_case)
    delivery = claim(source_policy_case, receipt)
    before = phase_snapshot(identity)
    with pytest.raises(RuntimeError, match="Synthetic outer rollback"):
        with graph.transaction(ids["tenant"]):
            assert owner.execute(delivery)["processing_state"] == "RETRIEVING"
            raise RuntimeError("Synthetic outer rollback")
    assert phase_snapshot(identity) == before


@pytest.mark.parametrize("field", ["accepted_worker", "accepted_delivery_fence", "fence_token", "result", "permission"])
def test_terminal_proof_does_not_adopt_mutable_job_or_transition_claims(source_policy_case, field):
    identity, owner = source_policy_case
    graph, ids, _, db = identity[0][0]
    _, _, receipt = prepare(source_policy_case)
    result = owner.execute(claim(source_policy_case, receipt))
    with db.connect() as conn:
        if field == "permission":
            conn.execute("UPDATE intake.intake_stage_transitions SET permission='intake.process' WHERE transition_id=%s", (result["transition_id"],))
        elif field == "fence_token":
            conn.execute("UPDATE workflow.jobs SET fence_token=fence_token+1 WHERE job_id=%s", (receipt["job_id"],))
        elif field == "result":
            conn.execute("UPDATE workflow.jobs SET result=jsonb_set(result,'{policy_decision,reason_code}','\"invented\"') WHERE job_id=%s", (receipt["job_id"],))
        else:
            value = '"another-worker"' if field == "accepted_worker" else str(result["policy_decision"][field]+1)
            conn.execute("UPDATE workflow.jobs SET payload=jsonb_set(payload,%s,%s::jsonb) WHERE job_id=%s", ([field], value, receipt["job_id"]))
    before = phase_snapshot(identity)
    with pytest.raises(JobFenceRejectedError):
        owner.execute(graph.queue.get(receipt["job_id"]))
    assert phase_snapshot(identity) == before


@pytest.mark.parametrize("mode", ["retry", "exhausted", "revoked", "third_attempt", "three_exhausted"])
def test_real_attempt_budget_never_extends_admission_deadline(source_policy_case, monkeypatch, mode):
    identity, owner = source_policy_case
    start, _ = identity
    graph, ids, _, _ = start[0]
    config = {**policy(ids["tenant"]), "backoff_seconds": [0.001]}
    if mode == "exhausted":
        config.update(max_attempts=1, backoff_seconds=[])
    elif mode in {"third_attempt", "three_exhausted"}:
        config.update(max_attempts=3, backoff_seconds=[0.001, 0.001])
    monkeypatch.setenv(POLICY_ENV, json.dumps(config))
    submission, _, receipt = prepare(source_policy_case)
    delivery = claim(source_policy_case, receipt)
    original, remaining = graph.audit_sink.persist, [3 if mode == "three_exhausted" else 2 if mode == "third_attempt" else 1]

    def fail_audit(record, scope):
        if record["action"] == "intake.retrieval_approved.v1" and remaining[0]:
            remaining[0] -= 1
            raise CanonicalAuditUnavailable("Synthetic transient source-policy audit outage")
        return original(record, scope)

    monkeypatch.setattr(graph.audit_sink, "persist", fail_audit)
    if mode == "revoked":
        monkeypatch.setenv(POLICY_ENV, json.dumps({**config, "tenant_ids": [str(uuid4())]}))
    assert run_native_intake_source_policy_delivery(delivery, owner) is None
    assert native_row(start, submission["intake_id"])["processing_state"] == "CHECKING_SOURCE_POLICY"
    if mode not in {"exhausted", "revoked"}:
        for attempt in range(2, config["max_attempts"]+1):
            result = run_native_intake_source_policy_delivery(claim(source_policy_case, receipt), owner)
            if result is not None:
                assert result["policy_decision"]["accepted_attempt"] == attempt
                break
    with graph.transaction(ids["tenant"]):
        row = graph.engine.query_one("SELECT status,attempt,timeout_at FROM workflow.jobs WHERE job_id=?", (receipt["job_id"],))
    assert row["timeout_at"] == receipt["timeout_at"]
    assert row["status"] == ("FAILED" if mode in {"exhausted", "revoked", "three_exhausted"} else "SUCCEEDED")
    assert row["attempt"] == (1 if mode in {"exhausted", "revoked"} else config["max_attempts"])


def test_partition_CLI_uses_the_actual_source_policy_factory(source_policy_case, monkeypatch, capsys):
    import apps.api.server as server
    from apps.worker.oday_worker import native_intake_source_policy as cli

    identity, _ = source_policy_case
    start, _ = identity
    _, ids, _, db = start[0]
    _, _, _ = prepare(source_policy_case, admit=False)
    monkeypatch.setattr(server, "bootstrap_runtime", lambda: start[2])
    monkeypatch.setattr(sys, "argv", ["native-source-policy", "--tenant-id", ids["tenant"], "--limit", "5"])
    capsys.readouterr()
    cli.main()
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(lines) == 1 and lines[0]["phase"] == "native_source_policy_only"
    receipt = lines[0]["admission_receipt"]
    with db.connect() as conn:
        assert conn.execute("SELECT job_type,status FROM workflow.jobs WHERE job_id=%s", (receipt["job_id"],)).fetchone() == (
            NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE, "QUEUED")
        assert conn.execute("SELECT processing_state FROM intake.intakes WHERE intake_id=%s", (receipt["intake_id"],)).fetchone() == (
            "CHECKING_SOURCE_POLICY",)


def expiry_hook(case, monkeypatch, cas, extra=None):
    identity, _ = case
    graph, ids, _, _ = identity[0][0]
    sql_patch(case, "review_expires_at=clock_timestamp()+interval '2 seconds'")
    original, observed = graph.engine.execute, []

    def execute(sql, params=()):
        match = (sql.startswith("UPDATE intake.intakes SET processing_state=") if cas == "intake"
                 else sql.startswith("UPDATE workflow.jobs SET status='SUCCEEDED'"))
        if match and not observed:
            clock = graph.engine.query_one("SELECT clock_timestamp() AS now,pg_backend_pid() AS pid")
            observed.append(clock)
            graph.engine.query_one("SELECT pg_sleep(GREATEST(0.0,EXTRACT(EPOCH FROM "
                "((SELECT review_expires_at FROM intake.source_registry WHERE source_id='synthetic-feed')-clock_timestamp())))+0.02)")
            if extra is not None:
                extra(original, graph, ids)
        return original(sql, params)

    monkeypatch.setattr(graph.engine, "execute", execute)
    return observed


@pytest.mark.parametrize("cas", ["intake", "business"])
def test_real_DB_expiry_at_each_final_CAS_retries_then_commits_one_quarantine(source_policy_case, monkeypatch, record_property, cas):
    identity, owner = source_policy_case
    start, _ = identity
    graph, ids, _, db = start[0]
    monkeypatch.setenv(POLICY_ENV, json.dumps({**policy(ids["tenant"]), "max_attempts": 3, "backoff_seconds": [0.001, 0.001]}))
    submission, _, receipt = prepare(source_policy_case)
    delivery = claim(source_policy_case, receipt)
    observed = expiry_hook(source_policy_case, monkeypatch, cas)
    before = phase_snapshot(identity)
    with pytest.raises(NativeSourcePolicyReevaluationRequired, match="^NATIVE_SOURCE_POLICY_PROOF_CHANGED$") as error:
        owner.execute(delivery)
    assert observed and phase_snapshot(identity) == before
    record_property("expiry_CAS", cas)
    record_property("actual_postgresql_backend_pid", observed[0]["pid"])
    assert owner.settle_failure(delivery, error.value) is None
    with db.connect() as conn:
        status = conn.execute("SELECT status,attempt,timeout_at FROM workflow.jobs WHERE job_id=%s", (receipt["job_id"],)).fetchone()
        assert status[0:2] == ("RETRYING", 1) and status[2].isoformat() == receipt["timeout_at"]
    assert native_row(start, submission["intake_id"])["processing_state"] == "CHECKING_SOURCE_POLICY"
    retry = claim(source_policy_case, receipt)
    assert retry.fence_token > delivery.fence_token
    result = owner.execute(retry)
    assert result["processing_state"] == "QUARANTINED" and result["policy_decision"]["accepted_attempt"] == 2
    assert result["policy_decision"]["valid_until"] is None
    with db.connect() as conn:
        assert conn.execute("SELECT count(*),min(to_state),max(to_state) FROM intake.intake_stage_transitions WHERE job_id=%s",
                            (receipt["job_id"],)).fetchone() == (1, "QUARANTINED", "QUARANTINED")
    assert owner.execute(graph.queue.get(receipt["job_id"])) == result


@pytest.mark.parametrize("cas", ["intake", "business"])
@pytest.mark.parametrize("conflict", ["version", "stage", "policy_state", "deleted", "cancel", "business_fence", "budget", "lease", "max_attempts"])
def test_expiry_plus_any_other_failed_axis_is_never_source_only_retry(source_policy_case, monkeypatch, cas, conflict):
    identity, owner = source_policy_case
    graph, ids, _, _ = identity[0][0]
    submission, _, receipt = prepare(source_policy_case)
    delivery = claim(source_policy_case, receipt)

    def extra(execute, _graph, _ids):
        changes = {
            "version": ("UPDATE intake.intakes SET version=version+1 WHERE intake_id=?", (submission["intake_id"],)),
            "stage": ("UPDATE intake.intakes SET processing_state='FAILED' WHERE intake_id=?", (submission["intake_id"],)),
            "policy_state": ("UPDATE intake.intakes SET source_policy_state='AUTH_REQUIRED' WHERE intake_id=?", (submission["intake_id"],)),
            "deleted": ("UPDATE intake.intakes SET deleted_at=clock_timestamp() WHERE intake_id=?", (submission["intake_id"],)),
            "cancel": ("UPDATE workflow.jobs SET cancellation_requested_at=clock_timestamp() WHERE job_id=?", (receipt["job_id"],)),
            "business_fence": ("UPDATE workflow.jobs SET fence_token=fence_token+1 WHERE job_id=?", (receipt["job_id"],)),
            "budget": ("UPDATE workflow.jobs SET timeout_at=clock_timestamp()-interval '1 second' WHERE job_id=?", (receipt["job_id"],)),
            "lease": ("UPDATE durable_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=?", (receipt["job_id"],)),
            "max_attempts": ("UPDATE workflow.jobs SET max_attempts=max_attempts+1 WHERE job_id=?", (receipt["job_id"],)),
        }
        execute(*changes[conflict])

    observed = expiry_hook(source_policy_case, monkeypatch, cas, extra)
    before = phase_snapshot(identity)
    with pytest.raises((NativeIntakeSourcePolicyConflict, JobFenceRejectedError)) as error:
        owner.execute(delivery)
    assert not isinstance(error.value, NativeSourcePolicyReevaluationRequired)
    assert observed and phase_snapshot(identity) == before


def test_two_real_connections_reserve_one_occurrence_job(source_policy_case, record_property):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from modules.listing.application.intake_audit import CanonicalIntakeAuditSink
    from shared.audit.canonical_delivery import CanonicalAuditDelivery
    from shared.audit.canonical_postgresql import CanonicalPostgresAuditLog
    from shared.infrastructure.persistence.identity_graph_sql import (
        CanonicalIdentityGraphRepository,
    )
    from shared.infrastructure.persistence.job_queue import DurableJobQueue
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    identity, owner = source_policy_case
    graph, ids, _, db = identity[0][0]
    _, producer, _ = prepare(source_policy_case, admit=False)
    barrier = threading.Barrier(2)

    def attempt(active_graph, active_owner):
        pid = None
        try:
            with active_graph.transaction(ids["tenant"]):
                pid = active_graph.engine.query_one("SELECT pg_backend_pid() AS pid")["pid"]
                barrier.wait(timeout=5)
                receipt = active_owner.admit_event(ids["tenant"], producer["outbox_event_id"])
            return pid, receipt, None, None
        except Exception as error:
            return pid, None, getattr(error, "sqlstate", type(error).__name__), getattr(getattr(error, "diag", None), "constraint_name", None)

    # Engine roots hold their process lock through commit. Independent engines
    # let the original barrier exercise the database reservation race.
    reopened = PostgresEngine(db.url(), bootstrap=False, validate_schema=False)
    try:
        queue = DurableJobQueue(reopened)
        writer = CanonicalPostgresAuditLog(reopened)
        sink = CanonicalIntakeAuditSink(writer, graph.audit_sink.retention,
            CanonicalAuditDelivery(writer, queue, graph.audit_sink.delivery.sink))
        fresh_graph = CanonicalIdentityGraphRepository(reopened, queue, sink)
        fresh_owner = NativeIntakeSourcePolicyOwner(fresh_graph)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [f.result(timeout=8) for f in [
                pool.submit(attempt, graph, owner), pool.submit(attempt, fresh_graph, fresh_owner)]]
    finally:
        reopened.close()
    assert len({r[0] for r in results}) == 2 and all(r[0] for r in results)
    accepted = [r[1] for r in results if r[1] is not None]
    assert accepted and all(r == accepted[0] for r in accepted), results
    assert all(r[2] in {None, "40001", "40P01", "23505"} for r in results), results
    for r in results:
        if r[2] == "23505":
            assert r[3] in {"jobs_pkey", "jobs_tenant_id_job_type_idempotency_key_key"}
    record_property("actual_postgresql_backend_pids", ",".join(str(r[0]) for r in results))
    record_property("reservation_conflicts", json.dumps([{"sqlstate": r[2], "constraint": r[3]} for r in results]))
    assert owner.admit_event(ids["tenant"], producer["outbox_event_id"]) == accepted[0]
    with graph.transaction(ids["tenant"]):
        assert len(graph.engine.query("SELECT job_id FROM workflow.jobs WHERE tenant_id=? AND job_type=?", (ids["tenant"], NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE))) == 1


def test_nested_producer_reads_recompute_remaining_admission_budget(source_policy_case, monkeypatch, record_property):
    import time
    from concurrent.futures import ThreadPoolExecutor

    identity, owner = source_policy_case
    graph, ids, _, db = identity[0][0]
    _, producer, _ = prepare(source_policy_case, admit=False)
    before = phase_snapshot(identity)
    original, slow, caps = graph.engine.query_one, [True], []

    def query(sql, params=()):
        if sql.startswith("SELECT payload FROM workflow.jobs WHERE") and slow[0]:
            slow[0] = False
            original("SELECT pg_sleep(0.4)")
        if sql.startswith("SELECT after_value FROM audit.audit_events"):
            cap = original("SHOW statement_timeout")["statement_timeout"]
            caps.append(float(cap[:-2]) if cap.endswith("ms") else float(cap[:-1])*1000)
        return original(sql, params)

    monkeypatch.setattr(graph.engine, "query_one", query)

    def attempt():
        began, pid = time.monotonic(), None
        try:
            with graph.transaction(ids["tenant"]):
                pid = original("SELECT pg_backend_pid() AS pid")["pid"]
                owner.admit_event(ids["tenant"], producer["outbox_event_id"])
            return None, time.monotonic()-began, pid
        except Exception as error:
            return error, time.monotonic()-began, pid

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.connect() as blocker, blocker.transaction():
            blocker_pid = blocker.execute("SELECT pg_backend_pid()").fetchone()[0]
            blocker.execute("LOCK TABLE audit.audit_events IN ACCESS EXCLUSIVE MODE")
            error, elapsed, worker_pid = pool.submit(attempt).result(timeout=4)
    assert worker_pid and worker_pid != blocker_pid
    assert not slow[0] and caps and 1 <= caps[0] < 350, caps
    assert getattr(error, "sqlstate", None) in {"55P03", "57014"}, error
    assert elapsed < 2 and phase_snapshot(identity) == before
    record_property("actual_postgresql_backend_pids", f"{blocker_pid},{worker_pid}")
    record_property("remaining_admission_statement_timeout_ms", caps[0])
    record_property("elapsed_admission_seconds", elapsed)


def test_source_lock_wait_uses_the_remaining_command_deadline(source_policy_case, monkeypatch, record_property):
    import time
    from concurrent.futures import ThreadPoolExecutor

    identity, owner = source_policy_case
    start, _ = identity
    graph, ids, _, db = start[0]
    monkeypatch.setenv(POLICY_ENV, json.dumps({**policy(ids["tenant"]), "deadline_seconds": 2}))
    submission, _, receipt = prepare(source_policy_case)
    delivery = claim(source_policy_case, receipt)

    def attempt():
        began, pid = time.monotonic(), None
        try:
            with graph.transaction(ids["tenant"]):
                pid = graph.engine.query_one("SELECT pg_backend_pid() AS pid")["pid"]
                owner.execute(delivery)
            return None, time.monotonic()-began, pid
        except Exception as error:
            return error, time.monotonic()-began, pid

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.connect() as blocker, blocker.transaction():
            blocker_pid = blocker.execute("SELECT pg_backend_pid()").fetchone()[0]
            blocker.execute("SELECT source_id FROM intake.source_registry WHERE source_id='synthetic-feed' FOR UPDATE")
            error, elapsed, worker_pid = pool.submit(attempt).result(timeout=5)
    assert worker_pid and worker_pid != blocker_pid
    assert getattr(error, "sqlstate", None) in {"55P03", "57014"}, error
    assert elapsed < 5
    record_property("actual_postgresql_backend_pids", f"{blocker_pid},{worker_pid}")
    record_property("remaining_command_wait_seconds", elapsed)
    assert owner.settle_failure(delivery, error) is None
    assert native_row(start, submission["intake_id"])["processing_state"] == "CHECKING_SOURCE_POLICY"
    assert graph.queue.get(receipt["job_id"]).status == JobStatus.FAILED


def test_fresh_engine_reads_a_policy_receipt_after_current_source_changes(source_policy_case):
    from modules.listing.application.intake_audit import CanonicalIntakeAuditSink
    from shared.audit.canonical_delivery import CanonicalAuditDelivery
    from shared.audit.canonical_postgresql import CanonicalPostgresAuditLog
    from shared.infrastructure.persistence.identity_graph_sql import (
        CanonicalIdentityGraphRepository,
    )
    from shared.infrastructure.persistence.job_queue import DurableJobQueue
    from shared.infrastructure.persistence.postgresql import PostgresEngine
    from tests.integration.test_canonical_intakes_postgresql import snapshot

    identity, owner = source_policy_case
    graph, ids, old_delivery, db = identity[0][0]
    _, _, receipt = prepare(source_policy_case)
    result = owner.execute(claim(source_policy_case, receipt))
    sql_patch(source_policy_case, "kill_switch=true,version=version+1")
    before = snapshot(identity[0][0])
    graph.engine.close()
    reopened = PostgresEngine(db.url(), bootstrap=False, validate_schema=False)
    try:
        queue = DurableJobQueue(reopened)
        writer = CanonicalPostgresAuditLog(reopened)
        sink = CanonicalIntakeAuditSink(writer, graph.audit_sink.retention,
            CanonicalAuditDelivery(writer, queue, graph.audit_sink.delivery.sink))
        fresh_graph = CanonicalIdentityGraphRepository(reopened, queue, sink)
        assert NativeIntakeSourcePolicyOwner(fresh_graph).execute(queue.get(receipt["job_id"])) == result
        assert snapshot((fresh_graph, ids, old_delivery, db)) == before
        assert writer.verify_chain(ids["tenant"]).ok
    finally:
        reopened.close()


def test_native_worker_renews_before_SQL_without_racing_policy_audit(source_policy_case, monkeypatch):
    import threading
    import time

    identity, owner = source_policy_case
    start, _ = identity
    graph = start[0][0]
    _, _, receipt = prepare(source_policy_case)
    renewals = []
    original_heartbeat, original_audit = graph.queue.heartbeat, graph.audit_sink.persist

    def heartbeat(*args, **kwargs):
        version = original_heartbeat(*args, **kwargs)
        renewals.append((threading.get_ident(), graph.queue.get(receipt["job_id"])))
        return version

    def audit(record, scope):
        if record["action"] == "intake.retrieval_approved.v1":
            assert len(renewals) == 1 and renewals[0][0] == threading.get_ident()
            physical = graph.queue.get(receipt["job_id"])
            assert physical.lease_expires_at == renewals[0][1].lease_expires_at
            assert physical.version == renewals[0][1].version
            time.sleep(0.15)  # The real root outlasts fifteen configured renewal intervals.
            assert len(renewals) == 1
        return original_audit(record, scope)

    monkeypatch.setattr(graph.queue, "heartbeat", heartbeat)
    monkeypatch.setattr(graph.audit_sink, "persist", audit)
    defer_audit(graph)
    assert ODayWorker(persistence=start[2], heartbeat_interval_seconds=0.01).run_once() is True
    assert len(renewals) == 1 and graph.queue.get(receipt["job_id"]).status == JobStatus.SUCCEEDED
    assert owner.execute(graph.queue.get(receipt["job_id"]))["processing_state"] == "RETRIEVING"


@pytest.mark.parametrize("field", ["actor_ref", "causation_id", "event_version", "aggregate_version", "payload_version", "to_state", "from_state"])
def test_a_typed_or_mutated_producer_event_is_not_a_new_policy_grant(source_policy_case, field):
    identity, owner = source_policy_case
    graph, ids, _, db = identity[0][0]
    _, producer, _ = prepare(source_policy_case, admit=False)
    with db.connect() as conn:
        event = conn.execute("SELECT payload FROM workflow.outbox_events WHERE event_id=%s", (producer["outbox_event_id"],)).fetchone()[0]
        if field == "to_state":
            event["payload"]["to_state"] = "READY"
        elif field == "from_state":
            event["payload"]["from_state"] = "QUARANTINED"
        elif field == "payload_version":
            event["payload"]["version"] = str(event["payload"]["version"])
        else:
            event[field] = ("native-intake-start" if field == "actor_ref" else str(uuid4()) if field == "causation_id"
                            else True if field == "event_version" else str(event[field]))
        conn.execute("UPDATE workflow.outbox_events SET payload=%s::jsonb WHERE event_id=%s", (json.dumps(event), producer["outbox_event_id"]))
    before = phase_snapshot(identity)
    with pytest.raises((NativeIntakeSourcePolicyConflict, JobFenceRejectedError)):
        owner.admit_event(ids["tenant"], producer["outbox_event_id"])
    assert phase_snapshot(identity) == before


@pytest.mark.parametrize("field", ["input_sha256", "event_sha256", "policy_rule_sha256", "source_id", "canonical_url_sha256"])
def test_mutable_admission_payload_cannot_replace_immutable_source_policy_provenance(source_policy_case, field):
    identity, owner = source_policy_case
    graph, ids, _, db = identity[0][0]
    _, _, receipt = prepare(source_policy_case)
    delivery = claim(source_policy_case, receipt)
    with db.connect() as conn:
        conn.execute("UPDATE workflow.jobs SET payload=jsonb_set(payload,%s,%s::jsonb) WHERE job_id=%s",
            ([field], json.dumps("another-source" if field == "source_id" else "0"*64), receipt["job_id"]))
    before = phase_snapshot(identity)
    with pytest.raises(NativeIntakeSourcePolicyConflict, match="ADMISSION_AUDIT_CONFLICT"):
        owner.execute(delivery)
    assert phase_snapshot(identity) == before


@pytest.mark.parametrize("field", ["actor", "scope"])
@pytest.mark.parametrize("coordinated", [False, True])
def test_original_submission_claims_remain_bound_through_identity_and_policy(source_policy_case, field, coordinated):
    from modules.listing.application.native_intake_identity import NativeIntakeIdentityConflict

    identity, owner = source_policy_case
    graph, ids, _, db = identity[0][0]
    submission, producer, _ = prepare(source_policy_case, admit=False)
    replacement = str(uuid4())
    with db.connect() as conn:
        event = conn.execute("SELECT payload FROM workflow.outbox_events WHERE event_id=%s", (submission["outbox_event_id"],)).fetchone()[0]
        if field == "actor":
            event["actor_ref"] = replacement
            event["payload"]["submitter_subject_id"] = replacement
        else:
            event["payload"]["scope"]["region_id"] = replacement
        conn.execute("UPDATE workflow.outbox_events SET payload=%s::jsonb WHERE event_id=%s", (json.dumps(event), submission["outbox_event_id"]))
        if coordinated:
            conn.execute("UPDATE intake.intakes SET " + ("submitter_subject_id" if field == "actor" else "region_id") + "=%s WHERE intake_id=%s",
                         (replacement, submission["intake_id"]))
    before = phase_snapshot(identity)
    with pytest.raises((NativeIntakeSourcePolicyConflict, NativeIntakeIdentityConflict)) as error:
        owner.admit_event(ids["tenant"], producer["outbox_event_id"])
    assert str(error.value) in {"NATIVE_SOURCE_POLICY_INTAKE_BINDING_CONFLICT", "NATIVE_IDENTITY_ORIGINAL_SUBMISSION_PROOF_CONFLICT"}
    assert phase_snapshot(identity) == before


def test_current_global_ambiguity_including_an_unsupported_second_source_quarantines(source_policy_case):
    identity, owner = source_policy_case
    graph, ids, _, db = identity[0][0]
    _, _, receipt = prepare(source_policy_case)
    with db.connect() as conn:
        conn.execute("INSERT INTO intake.source_registry (source_id,display_name,allowed_hosts,canonicalization_rule_version,"
            "retrieval_mode,policy_owner_subject_id) VALUES ('synthetic-policy-other','Synthetic other',ARRAY['SYNTHETIC.EXAMPLE'],'v1.0','POLICY_UNKNOWN',%s)",
            (ids["actor"],))
    result = owner.execute(claim(source_policy_case, receipt))
    proof = result["policy_decision"]
    assert result["processing_state"] == "QUARANTINED" and proof["host_match_count"] == 2
    assert proof["source_id"] == "synthetic-feed" and proof["reason_code"] == "SOURCE_ASSOCIATION_UNVERIFIED"


def test_same_URL_different_native_intakes_have_distinct_occurrence_decisions(source_policy_case):
    identity, owner = source_policy_case
    graph, ids, _, _ = identity[0][0]
    results = []
    for _ in range(2):
        submitted, producer, receipt = prepare(source_policy_case)
        result = owner.execute(claim(source_policy_case, receipt))
        assert owner.admit_event(ids["tenant"], producer["outbox_event_id"]) == receipt
        results.append((submitted, receipt, result))
    assert len({r[0]["intake_id"] for r in results}) == 2
    assert len({r[1]["job_id"] for r in results}) == 2
    assert len({r[2]["policy_decision"]["policy_decision_id"] for r in results}) == 2
    with graph.transaction(ids["tenant"]):
        rows = graph.engine.query("SELECT idempotency_key FROM workflow.jobs WHERE tenant_id=? AND job_type=?",
                                  (ids["tenant"], NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE))
    assert len(rows) == 2 and len({r["idempotency_key"] for r in rows}) == 2


def test_real_source_update_before_row_acceptance_is_serialized_then_rechecked(source_policy_case, monkeypatch, record_property):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    identity, owner = source_policy_case
    start, _ = identity
    graph, ids, _, db = start[0]
    configured = native_intake_source_policy_policy()
    monkeypatch.setenv(POLICY_ENV, json.dumps({**configured, "backoff_seconds": [0.2]}))
    submission, _, receipt = prepare(source_policy_case)
    delivery = claim(source_policy_case, receipt)
    original, entered, worker_pids = graph.engine.query, threading.Event(), []

    def query(sql, params=()):
        if sql.startswith("SELECT source_id FROM intake.source_registry s"):
            entered.set()
        return original(sql, params)

    monkeypatch.setattr(graph.engine, "query", query)

    def attempt():
        try:
            with graph.transaction(ids["tenant"]):
                worker_pids.append(graph.engine.query_one("SELECT pg_backend_pid() AS pid")["pid"])
                return owner.execute(delivery), None
        except Exception as error:
            return None, error

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.connect() as blocker, blocker.transaction():
            blocker_pid = blocker.execute("SELECT pg_backend_pid()").fetchone()[0]
            blocker.execute("UPDATE intake.source_registry SET kill_switch=true,version=version+1 WHERE source_id='synthetic-feed'")
            future = pool.submit(attempt)
            assert entered.wait(3) and worker_pids
            deadline, waiting = time.monotonic()+3, False
            while time.monotonic() < deadline:
                row = blocker.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", (worker_pids[0],)).fetchone()
                if row == ("Lock",):
                    waiting = True
                    break
                time.sleep(0.01)
            assert waiting, "The independent source writer must cause a real PostgreSQL lock wait"
        result, error = future.result(timeout=6)
    assert result is None and getattr(error, "sqlstate", None) == "40001", error
    assert worker_pids[0] != blocker_pid
    record_property("actual_postgresql_backend_pids", f"{blocker_pid},{worker_pids[0]}")
    record_property("actual_source_conflict_sqlstate", error.sqlstate)
    assert native_row(start, submission["intake_id"])["processing_state"] == "CHECKING_SOURCE_POLICY"
    assert owner.settle_failure(delivery, error) is None
    with graph.transaction(ids["tenant"]):
        retry = graph.engine.query_one(
            "SELECT j.status AS business_status,j.attempt,j.max_attempts,j.next_attempt_at,j.timeout_at,"
            "d.status AS delivery_status,d.delivery_state,d.available_at,clock_timestamp() AS db_now,"
            "d.available_at>clock_timestamp() AS not_yet_due FROM workflow.jobs j "
            "JOIN durable_jobs d ON d.job_id=j.job_id::text WHERE j.tenant_id=? AND j.job_id=?",
            (ids["tenant"], receipt["job_id"]))
    assert retry["business_status"] == "RETRYING" and retry["attempt"] == 1 < retry["max_attempts"]
    assert retry["delivery_status"] == JobStatus.QUEUED.value and retry["delivery_state"] == JobDeliveryState.RETRYING.value
    record_property("actual_retry_schedule_before_claim", json.dumps(retry, default=str))
    with graph.transaction(ids["tenant"]):
        graph.engine.query_one(
            "SELECT pg_sleep(GREATEST(0.0,EXTRACT(EPOCH FROM (available_at-clock_timestamp())))) "
            "FROM durable_jobs WHERE job_id=?", (receipt["job_id"],))
        ready = graph.engine.query_one(
            "SELECT available_at,clock_timestamp() AS db_now,available_at<=clock_timestamp() AS due "
            "FROM durable_jobs WHERE job_id=?", (receipt["job_id"],))
    record_property("actual_retry_schedule_after_wait", json.dumps(ready, default=str))
    assert ready["due"] is True
    committed = owner.execute(claim(source_policy_case, receipt))
    assert committed["processing_state"] == "QUARANTINED"
    assert committed["policy_decision"]["source_version"] == 4 and committed["policy_decision"]["source_snapshot"]["kill_switch"] is True


def test_cancel_before_acceptance_settles_both_jobs_without_a_policy_decision(source_policy_case):
    identity, owner = source_policy_case
    start, _ = identity
    graph, ids, _, db = start[0]
    submission, _, receipt = prepare(source_policy_case)
    delivery = claim(source_policy_case, receipt)
    with db.connect() as conn:
        conn.execute("UPDATE workflow.jobs SET cancellation_requested_at=clock_timestamp() WHERE job_id=%s", (receipt["job_id"],))
    before = native_row(start, submission["intake_id"])
    assert run_native_intake_source_policy_delivery(delivery, owner) is None
    assert native_row(start, submission["intake_id"]) == before
    assert graph.queue.get(receipt["job_id"]).status == JobStatus.CANCELLED
    with db.connect() as conn:
        assert conn.execute("SELECT status,result FROM workflow.jobs WHERE job_id=%s", (receipt["job_id"],)).fetchone() == ("CANCELLED", None)
        assert conn.execute("SELECT count(*) FROM intake.intake_stage_transitions WHERE job_id=%s", (receipt["job_id"],)).fetchone() == (0,)


@pytest.mark.parametrize("failure", ["audit", "transport"])
def test_admission_failure_after_real_writes_rolls_back_the_reservation(source_policy_case, monkeypatch, failure):
    identity, owner = source_policy_case
    graph, ids, _, _ = identity[0][0]
    _, producer, _ = prepare(source_policy_case, admit=False)
    before = phase_snapshot(identity)
    if failure == "audit":
        original = graph.audit_sink.persist

        def persist(record, scope):
            original(record, scope)
            if record["action"] == "intake.source_policy_evaluation_requested.v1":
                raise CanonicalAuditUnavailable("Synthetic post-write admission audit failure")

        monkeypatch.setattr(graph.audit_sink, "persist", persist)
    else:
        original = graph.queue.enqueue

        def enqueue(request, **kwargs):
            result = original(request, **kwargs)
            if request.job_type == NATIVE_INTAKE_SOURCE_POLICY_JOB_TYPE:
                raise CanonicalAuditUnavailable("Synthetic post-write admission transport failure")
            return result

        monkeypatch.setattr(graph.queue, "enqueue", enqueue)
    with pytest.raises(CanonicalAuditUnavailable):
        owner.admit_event(ids["tenant"], producer["outbox_event_id"])
    assert phase_snapshot(identity) == before


@pytest.mark.parametrize("constant", ["NATIVE_URL_RULE_SOURCE_SHA256", "POLICY_RULE_SOURCE_SHA256"])
@pytest.mark.parametrize("terminal", [False, True])
def test_code_drift_cannot_grant_pending_work_but_preserves_historical_receipts(source_policy_case, monkeypatch, constant, terminal):
    from modules.listing.application import native_intake_source_policy as module

    identity, owner = source_policy_case
    _, _, receipt = prepare(source_policy_case)
    delivery = claim(source_policy_case, receipt)
    result = owner.execute(delivery) if terminal else None
    before = phase_snapshot(identity)
    monkeypatch.setattr(module, constant, "0"*64)
    if terminal:
        assert owner.execute(graph_delivery := owner.queue.get(delivery.job_id)) == result
        assert graph_delivery.status == JobStatus.SUCCEEDED
    else:
        with pytest.raises(NativeIntakeSourcePolicyConflict, match="ATTEMPT_NOT_ADMITTED"):
            owner.execute(delivery)
    assert phase_snapshot(identity) == before


@pytest.mark.parametrize("schema", ["old_unique", "missing_history"])
@pytest.mark.parametrize("operation", ["admit", "execute"])
def test_current_actual_schema_is_required_for_both_admission_and_execution(source_policy_case, schema, operation):
    from modules.listing.application.native_intake_source_policy import (
        NativeIntakeSourcePolicyUnavailable,
    )

    identity, owner = source_policy_case
    graph, ids, _, db = identity[0][0]
    _, producer, receipt = prepare(source_policy_case, admit=operation == "execute")
    delivery = claim(source_policy_case, receipt) if operation == "execute" else None
    with db.connect() as conn:
        if schema == "old_unique":
            conn.execute("CREATE UNIQUE INDEX ux_intakes_exact_url_active ON intake.intakes "
                         "(tenant_id,source_id,canonical_url_sha256) WHERE canonical_url_sha256 IS NOT NULL")
        else:
            conn.execute("DROP INDEX intake.ix_intakes_canonical_url_history")
    before = phase_snapshot(identity)
    with pytest.raises(NativeIntakeSourcePolicyUnavailable, match="CURRENT_SCHEMA_REQUIRED"):
        if operation == "admit":
            owner.admit_event(ids["tenant"], producer["outbox_event_id"])
        else:
            owner.execute(delivery)
    assert phase_snapshot(identity) == before


def test_new_upstream_driver_consumes_a_real_induced_serialization_retry(source_policy_case, monkeypatch, record_property):
    from concurrent.futures import ThreadPoolExecutor

    from modules.listing.application.native_intake_start import NativeIntakeStartOwner

    identity, source_owner = source_policy_case
    graph, _, _, _ = identity[0][0]
    # The actual default worker remains in use at 10ms. A separate real
    # connection deliberately updates its physical lease after the SQL root
    # has read it, independently of the worker's pre-command lease handoff.
    original, once, pids, retries = NativeIntakeStartOwner._committed_result, [True], [], []
    record = source_owner.record_upstream_retry

    def observed(name, value):
        retries.append(json.loads(value))
        record(name, value)

    source_owner.record_upstream_retry = observed

    def committed(self, delivery, tenant, generation):
        result = original(self, delivery, tenant, generation)
        if result is None and once[0]:
            once[0] = False
            assert self.engine is graph.engine and self.queue is graph.queue
            pids.append(graph.engine.query_one("SELECT pg_backend_pid() AS pid")["pid"])

            def renew():
                with graph.transaction(tenant):
                    pid = graph.engine.query_one("SELECT pg_backend_pid() AS pid")["pid"]
                    current = graph.queue.get(delivery.job_id)
                    graph.queue.heartbeat(delivery.job_id, expected_version=current.version, fence_token=current.fence_token)
                    return pid

            with ThreadPoolExecutor(max_workers=1) as pool:
                pids.append(pool.submit(renew).result(timeout=3))
        return result

    monkeypatch.setattr(NativeIntakeStartOwner, "_committed_result", committed)
    _, _, receipt = prepare(source_policy_case)
    assert len(pids) == 2 and pids[0] != pids[1]
    assert any(r["job_type"] == "native-intake-start" and r["last_error"]["error_class"] == "SerializationFailure" for r in retries)
    record_property("actual_upstream_serialization_backend_pids", ",".join(str(p) for p in pids))
    assert source_owner.execute(claim(source_policy_case, receipt))["processing_state"] == "RETRIEVING"


@pytest.mark.parametrize("terminal", [False, True])
def test_legacy_30_second_jobs_are_readable_only_as_history_under_current_policy(source_policy_case, monkeypatch, terminal):
    from modules.listing.application import native_intake_source_policy as module

    identity, owner = source_policy_case
    start, _ = identity
    graph, ids, _, db = start[0]
    configured = native_intake_source_policy_policy()
    # Construct a genuine SQL receipt under the previously accepted 30s
    # policy. The implementation differs only in this configuration cap.
    with monkeypatch.context() as legacy:
        legacy.setattr(module, "MAX_COMMAND_SECONDS", 30)
        legacy.setenv(POLICY_ENV, json.dumps({**configured, "deadline_seconds": 30}))
        submission, _, receipt = prepare(source_policy_case)
        delivery = claim(source_policy_case, receipt)
        result = owner.execute(delivery) if terminal else None
    before = native_row(start, submission["intake_id"])
    if terminal:
        snapshot = phase_snapshot(identity)
        assert owner.execute(graph.queue.get(receipt["job_id"])) == result
        assert phase_snapshot(identity) == snapshot
    else:
        assert run_native_intake_source_policy_delivery(delivery, owner) is None
        assert graph.queue.get(receipt["job_id"]).status == JobStatus.FAILED
        with db.connect() as connection:
            failed = connection.execute("SELECT status,result,last_error FROM workflow.jobs WHERE job_id=%s",
                                        (receipt["job_id"],)).fetchone()
            assert failed[:2] == ("FAILED", None)
            assert failed[2]["code"] == "NATIVE_SOURCE_POLICY_ATTEMPT_NOT_ADMITTED"
            assert connection.execute("SELECT count(*) FROM intake.intake_stage_transitions WHERE job_id=%s",
                                      (receipt["job_id"],)).fetchone() == (0,)
    assert native_row(start, submission["intake_id"]) == before
