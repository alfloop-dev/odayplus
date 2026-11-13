"""Real accepted lease/command expiry, current owner fences and full history."""
import copy
import json
import time
from contextlib import contextmanager
from datetime import timedelta

import pytest

from modules.listing.application.identity_execution import IdentityExecutionService
from modules.listing.application.native_intake_assisted_entry_request_proof import _object, _time
from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from modules.listing.application.native_intake_retrieval_policy import canonical_json
from shared.audit.errors import CanonicalAuditUnavailable
from shared.jobs.queue import JobFenceRejectedError, JobStatus
from tests.integration.test_assisted_listing_identity import _execute as execute_seed_identity
from tests.integration.test_native_intake_identity_postgresql import defer_audit
from tests.integration.test_native_intake_parser_admission_postgresql import claimed
from tests.integration.test_native_intake_parser_budget_postgresql import wait_outside_sql
from tests.integration.test_native_intake_parser_cancellation_postgresql import (
    assert_cancelled,
    prepared_claim,
)
from tests.integration.test_native_intake_parser_history_postgresql import (
    retire_all_positive_settings,
)
from tests.integration.test_native_intake_parser_permanent_postgresql import observe_error
from tests.integration.test_native_intake_parser_retry_postgresql import (
    accepted_retry,
    rows,
    transient,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    intake_blank_db as intake_blank_db,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    parser_admission_case as parser_admission_case,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    parser_evidence_fields as parser_evidence_fields,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    parser_io_case as parser_io_case,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    parser_realm as parser_realm,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    retry_policy as retry_policy,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_parser_retry_postgresql import (
    start_case as start_case,
)
from tests.integration.test_native_intake_parser_unavailable_postgresql import (
    assert_preserved,
    snapshot,
    withdraw,
)

pytestmark = pytest.mark.requires_live_env
FAST = {"max_attempts": 2, "deadline_seconds": 4, "backoff_seconds": [1]}


@pytest.fixture(autouse=True)
def short_source_authority(parser_admission_case, request):
    """Freeze a genuinely short source approval before parser admission."""
    if getattr(request, "param", None) is not True:
        return
    case = parser_admission_case
    with case[2].connect() as connection:
        assert connection.execute("UPDATE intake.source_registry SET review_expires_at=clock_timestamp()+interval '8 seconds',"
            "assisted_entry_review_expires_at=clock_timestamp()+interval '8 seconds',version=version+1 "
            "WHERE source_id=(SELECT source_id FROM intake.intakes WHERE tenant_id=%s AND intake_id=%s)",
            (case[1]["tenant"], case[3]["intake_id"])).rowcount == 1


def settle_seed(case):
    """Finish the independent real fixture job before its own45s expiry."""
    owner, ids = case[:2]
    seed = execute_seed_identity(case[6][0])
    assert seed["status"] == "EXECUTED" and seed["job_id"] == ids["job"]
    with owner.graph.transaction(ids["tenant"]):
        row = owner.graph._one("SELECT status,result FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], ids["job"]))
        assert row["status"] == "SUCCEEDED" and row["result"] == seed
        current = IdentityExecutionService(owner.graph)._delivery(ids["tenant"], ids["decision"], case[6][0][2])
        owner.queue.update_status(current.job_id, JobStatus.SUCCEEDED,
            expected_version=current.version, fence_token=current.fence_token)


def expire(case, delivery, *, source=False):
    job = rows(case, delivery.job_id)[0]
    until = job["payload"]["admission"]["binding"]["source_valid_until"] if source else job["timeout_at"]
    return wait_outside_sql(case, _time(until))


def assert_recovered(case, delivery, previous, receipt, *, mode, operations, errors=0):
    owner, ids = case[:2]
    current = snapshot(case)
    assert_preserved(previous, current)
    old = next(j for j in previous["workflow.jobs"] if j["job_id"] == delivery.job_id)
    prior_run = previous["intake.parser_runs"][0]
    job, run, physical = rows(case, delivery.job_id)
    assert job["status"] == run["status"] == "CANCELLED" and physical.status == JobStatus.CANCELLED
    assert job["result"] is None and job["lease_expires_at"] is None and job["next_attempt_at"] is None
    assert job["attempt"] == old["attempt"] >= 1 and job["fence_token"] == old["fence_token"]+1
    assert job["version"] == old["version"]+1 and run["version"] == prior_run["version"]+1
    assert job["max_attempts"] == old["max_attempts"] and job["timeout_at"] == old["timeout_at"]
    assert run["started_at"] == prior_run["started_at"] and run["started_at"] is not None
    assert run["parsed_payload"] is None and run["normalized_payload"] is None
    errors_value = json.loads(run["validation_errors"]) if isinstance(run["validation_errors"], str) else run["validation_errors"]
    assert _object(run["field_confidence"]) == {} and errors_value == []
    old_payload = _object(old["payload"])
    assert job["payload"]["attempt_intents"] == old_payload["attempt_intents"]
    assert job["payload"]["acceptance_receipt"] == old_payload["acceptance_receipt"]
    assert receipt["ownership_mode"] == mode and receipt["cause_error_class"] is None
    assert receipt["business_lease_expires_at"] == _time(old["lease_expires_at"]).isoformat()
    assert receipt["quality_summary"] == {"recorded_operations": operations, "finished_operations": operations-errors,
        "error_operations": errors, "reaped_operations": operations, "unresolved_operations": 0, "output_committed": False}
    assert physical.fence_token == receipt["physical_fence"] and physical.attempts == receipt["physical_attempts"]
    assert physical.locked_by is None and physical.lease_expires_at is None and physical.delivery_state is None
    added = [e for e in current["workflow.outbox_events"] if e["event_id"] not in {
        p["event_id"] for p in previous["workflow.outbox_events"]}]
    assert len(added) == 1 and added[0]["event_type"] == "parser.run_completed"
    assert owner.graph.audit_sink.writer.verify_chain(ids["tenant"]).ok
    assert owner.replay(delivery) == {"terminal": True, "result": None}
    assert owner.recover_expired(delivery) == receipt
    assert snapshot(case) == current
    return current


@pytest.mark.parametrize("retry_policy", [FAST], indirect=True)
@pytest.mark.parametrize("outcome", ["empty", "finished", "transient"])
def test_actual_original_command_expiry_closes_same_accepted_claim_without_new_attempt(
        parser_io_case, monkeypatch, outcome, record_property):
    case, io, leases, delivery = parser_io_case
    if outcome == "finished":
        io.read_and_parse()
    elif outcome == "transient":
        transient(io, monkeypatch)
    leases.pause()
    record_property("actual_original_command_wait_seconds", expire(case, delivery))
    retire_all_positive_settings(monkeypatch)
    case[0].artifact_registry = None
    previous = snapshot(case)
    receipt = case[0].recover_expired(delivery)
    assert receipt["cause_code"] == "ORIGINAL_COMMAND_EXPIRED"
    current = assert_recovered(case, delivery, previous, receipt, mode="SAME_ACCEPTED_CLAIM",
        operations={"empty": 0, "finished": 4, "transient": 1}[outcome], errors=int(outcome == "transient"))
    with pytest.raises(JobFenceRejectedError):
        leases._renew()
    assert snapshot(case) == current


@pytest.mark.parametrize("short_source_authority", [True], indirect=True)
@pytest.mark.parametrize("finished", [False, True])
def test_actual_frozen_source_expiry_can_close_same_accepted_owner_without_mutating_intake(
        parser_io_case, finished, record_property):
    case, io, leases, delivery = parser_io_case
    if finished:
        io.read_and_parse()
    leases.pause()
    record_property("actual_frozen_source_wait_seconds", expire(case, delivery, source=True))
    job = rows(case, delivery.job_id)[0]
    with case[0].graph.transaction(case[1]["tenant"]):
        assert case[0].clock._clock() < _time(job["timeout_at"])
    previous = snapshot(case)
    receipt = case[0].recover_expired(delivery)
    assert receipt["cause_code"] == "ORIGINAL_SOURCE_AUTHORITY_EXPIRED"
    assert_recovered(case, delivery, previous, receipt, mode="SAME_ACCEPTED_CLAIM", operations=4 if finished else 0)


@pytest.mark.parametrize("short_source_authority", [True], indirect=True)
def test_actual_frozen_source_expiry_cancels_initial_without_fictitious_acceptance(parser_admission_case):
    case = parser_admission_case
    _, delivery = claimed(case)
    expire(case, delivery, source=True)
    previous = snapshot(case)
    receipt = case[0].cancel_unaccepted(delivery)
    assert receipt["cause_code"] == "ORIGINAL_SOURCE_AUTHORITY_EXPIRED"
    assert_cancelled(case, delivery, previous, receipt)


@pytest.mark.parametrize("short_source_authority", [True], indirect=True)
def test_actual_frozen_source_expiry_cancels_prepared_retry_without_accepting_another_attempt(parser_io_case, monkeypatch):
    case, io, leases, first = parser_io_case
    delivery, _ = prepared_claim(case, io, leases, first, monkeypatch)
    expire(case, delivery, source=True)
    previous = snapshot(case)
    receipt = case[0].cancel_unaccepted(delivery)
    assert receipt["cause_code"] == "ORIGINAL_SOURCE_AUTHORITY_EXPIRED"
    assert_cancelled(case, delivery, previous, receipt, prepared=True)


@pytest.mark.parametrize("retry_policy", [{"max_attempts": 3, "deadline_seconds": 8, "backoff_seconds": [1, 2]}], indirect=True)
def test_actual_second_accepted_attempt_recovers_with_entire_original_retry_history(parser_io_case, monkeypatch):
    case, io, leases, first = parser_io_case
    transient(io, monkeypatch)
    case[0].prepare_retry(first, leases=leases)
    with accepted_retry(case, first.job_id) as (current_io, current_leases, delivery):
        current_io.read_and_parse()
        current_leases.pause()
        expire(case, delivery)
        previous = snapshot(case)
        receipt = case[0].recover_expired(delivery)
        assert receipt["attempt"] == 2 and receipt["cause_code"] == "ORIGINAL_COMMAND_EXPIRED"
        assert_recovered(case, delivery, previous, receipt, mode="SAME_ACCEPTED_CLAIM", operations=5, errors=1)
        retire_all_positive_settings(monkeypatch)
        case[0].artifact_registry = None
        assert case[0].replay(first) == {"terminal": True, "result": None}


@pytest.mark.parametrize("outcome", ["empty", "finished", "transient", "unreaped"])
def test_real_physical_reclaim_requires_old_business_expiry_and_all_operations_closed(
        parser_io_case, monkeypatch, outcome, record_property):
    case, io, leases, old = parser_io_case
    owner = case[0]
    settle_seed(case)
    if outcome == "finished":
        io.read_and_parse()
    elif outcome != "empty":
        observe_error(io, monkeypatch, outcome)
    leases.pause()
    actual = rows(case, old.job_id)[2]
    elapsed = wait_outside_sql(case, actual.lease_expires_at)
    record_property("actual_original_physical_lease_wait_seconds", elapsed)
    defer_audit(owner.graph)
    delivery = owner.queue.claim_next(worker_id="synthetic-native-parser-expired-recovery")
    assert delivery.job_id == old.job_id and delivery.fence_token > old.fence_token
    assert delivery.attempts == actual.attempts+1
    previous = snapshot(case)
    with pytest.raises(JobFenceRejectedError, match="NATIVE_PARSER_CANCEL_PHYSICAL_NOT_OWNED"):
        owner.recover_expired(old)
    with pytest.raises(JobFenceRejectedError, match="NATIVE_PARSER_DUAL_LEASE_NOT_OWNED"):
        owner._accept(delivery, case[1]["tenant"], 1)
    assert snapshot(case) == previous
    retire_all_positive_settings(monkeypatch)
    owner.artifact_registry = None
    if outcome == "unreaped":
        assert len(previous["slots"]) == 1
        with pytest.raises(NativeParserConflict, match="NATIVE_PARSER_ATTEMPT_JOURNAL_REJECTED"):
            owner.recover_expired(delivery)
        assert snapshot(case) == previous
    else:
        receipt = owner.recover_expired(delivery)
        assert receipt["cause_code"] == "EXPIRED_ACCEPTED_BUSINESS_LEASE"
        assert_recovered(case, delivery, previous, receipt, mode="RECLAIMED_PHYSICAL",
            operations={"empty": 0, "finished": 4, "transient": 1}[outcome], errors=int(outcome == "transient"))


def test_real_new_physical_claim_cannot_take_a_synthetically_still_live_business_lease(parser_io_case, record_property):
    case, _, leases, old = parser_io_case
    owner, ids = case[:2]
    settle_seed(case)
    leases.pause()
    # Negative counterexample only: never use this synthetic extension to
    # accept/recover or claim a passing positive ownership witness.
    with case[2].connect() as connection:
        connection.execute("UPDATE workflow.jobs SET lease_expires_at=clock_timestamp()+interval '100 seconds' "
            "WHERE tenant_id=%s AND job_id=%s", (ids["tenant"], old.job_id))
    record_property("actual_original_physical_lease_wait_seconds", wait_outside_sql(case, old.lease_expires_at))
    defer_audit(owner.graph)
    delivery = owner.queue.claim_next(worker_id="synthetic-native-parser-live-business-refusal")
    assert delivery.job_id == old.job_id and delivery.fence_token > old.fence_token
    previous = snapshot(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_RECOVERY_BUSINESS_LEASE_STILL_LIVE$"):
        owner.recover_expired(delivery)
    assert snapshot(case) == previous


@pytest.mark.parametrize("loss", [None, "key", "human"])
def test_healthy_original_clock_and_live_business_lease_cannot_be_called_expired(parser_io_case, monkeypatch, loss):
    case, _, leases, delivery = parser_io_case
    leases.pause()
    if loss:
        withdraw(case, loss, monkeypatch)
    previous = snapshot(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_RECOVERY_CAUSE_REQUIRED$"):
        case[0].recover_expired(delivery)
    assert snapshot(case) == previous


def test_unaccepted_command_is_not_an_accepted_recovery(parser_admission_case):
    case = parser_admission_case
    _, delivery = claimed(case)
    previous = snapshot(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_RECOVERY_ACCEPTED_COMMAND_REQUIRED$"):
        case[0].recover_expired(delivery)
    assert snapshot(case) == previous


@pytest.mark.parametrize("retry_policy", [FAST], indirect=True)
@pytest.mark.parametrize("failure", ["audit", "root_expiry", "physical_version"])
def test_recovery_failures_roll_back_run_fence_jobs_and_outbox(parser_io_case, monkeypatch, failure):
    case, _, leases, delivery = parser_io_case
    owner = case[0]
    leases.pause()
    expire(case, delivery)
    previous = snapshot(case)
    original = owner.graph.audit_sink.persist
    def conflict(event, scope):
        value = original(event, scope)
        if event["action"] == "parser.run_completed.v1":
            if failure == "audit":
                raise CanonicalAuditUnavailable("Synthetic recovery audit unavailable")
            if failure == "root_expiry":
                time.sleep(.8)
            else:
                physical = owner.queue.get(delivery.job_id)
                owner.queue.heartbeat(physical.job_id, expected_version=physical.version, fence_token=physical.fence_token)
        return value
    monkeypatch.setattr(owner.graph.audit_sink, "persist", conflict)
    expected = CanonicalAuditUnavailable if failure == "audit" else NativeParserConflict if failure == "root_expiry" else JobFenceRejectedError
    with pytest.raises(expected):
        owner.recover_expired(delivery)
    assert snapshot(case) == previous


@pytest.mark.parametrize("retry_policy", [FAST], indirect=True)
def test_actual_unreaped_operation_survives_original_command_expiry_unsettled(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    observe_error(io, monkeypatch, "unreaped")
    leases.pause()
    expire(case, delivery)
    previous = snapshot(case)
    assert len(previous["slots"]) == 1
    with pytest.raises(NativeParserConflict, match="NATIVE_PARSER_ATTEMPT_JOURNAL_REJECTED"):
        case[0].recover_expired(delivery)
    assert snapshot(case) == previous


@pytest.mark.parametrize("retry_policy", [FAST], indirect=True)
def test_lost_actual_recovery_commit_response_reconciles_current_terminal_without_reacceptance(parser_io_case, monkeypatch):
    case, _, leases, delivery = parser_io_case
    owner = case[0]
    leases.pause()
    expire(case, delivery)
    previous = snapshot(case)
    transaction, lost = owner.graph.transaction, False
    @contextmanager
    def lose(tenant):
        nonlocal lost
        with transaction(tenant):
            yield
        if not lost:
            lost = True
            raise OSError("Synthetic lost SQL response after actual recovery COMMIT")
    monkeypatch.setattr(owner.graph, "transaction", lose)
    receipt = owner.recover_expired(delivery)
    assert lost
    assert_recovered(case, delivery, previous, receipt, mode="SAME_ACCEPTED_CLAIM", operations=0)


@pytest.mark.parametrize("retry_policy", [FAST], indirect=True)
@pytest.mark.parametrize("tamper", ["false_attempt", "accepted_fence", "worker", "mode", "business_lease", "cause", "start", "journal", "event"])
def test_recovery_terminal_proof_rejects_coherently_changed_receipt_audit_and_independent_facts(parser_io_case, monkeypatch, tamper):
    case, _, leases, delivery = parser_io_case
    leases.pause()
    expire(case, delivery)
    receipt = case[0].recover_expired(delivery)
    changes = {"false_attempt": {"attempt": False}, "accepted_fence": {"accepted_physical_fence": receipt["accepted_physical_fence"]+1},
        "worker": {"worker": "synthetic-other-worker"}, "mode": {"ownership_mode": "RECLAIMED_PHYSICAL"},
        "business_lease": {"business_lease_expires_at": (_time(receipt["first_started_at"])-timedelta(seconds=1)).isoformat()},
        "cause": {"cause_code": "EXPIRED_ACCEPTED_BUSINESS_LEASE"}, "start": {"first_started_at": receipt["settled_at"]},
        "journal": {"journal_sha256": "0"*64}}
    job = rows(case, delivery.job_id)[0]
    with case[2].connect() as connection:
        if tamper == "event":
            connection.execute("UPDATE workflow.outbox_events SET aggregate_version=aggregate_version+1 WHERE tenant_id=%s AND event_id=%s",
                (case[1]["tenant"], receipt["parser_completed_event_id"]))
        else:
            altered = {**copy.deepcopy(receipt), **changes[tamper]}
            connection.execute("UPDATE workflow.jobs SET payload=%s::jsonb WHERE tenant_id=%s AND job_id=%s",
                (canonical_json({**job["payload"], "cancellation_receipt": altered}), case[1]["tenant"], delivery.job_id))
            connection.execute("UPDATE audit.audit_events SET after_value=%s::jsonb WHERE tenant_id=%s AND audit_event_id=%s",
                (canonical_json({**altered, "status": "CANCELLED", "parser_run_status": "CANCELLED", "intake_mutated": False}),
                    case[1]["tenant"], receipt["audit_event_id"]))
    retire_all_positive_settings(monkeypatch)
    case[0].artifact_registry = None
    previous = snapshot(case)
    with pytest.raises(NativeParserConflict):
        case[0].replay(delivery)
    with pytest.raises(NativeParserConflict):
        case[0].recover_expired(delivery)
    assert snapshot(case) == previous
