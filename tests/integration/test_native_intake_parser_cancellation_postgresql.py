"""Real SQL unaccepted/prepared cancellation; no fabricated accepted attempts."""
import copy
import time
from contextlib import contextmanager

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
from tests.integration.test_native_intake_parser_history_postgresql import (
    retire_all_positive_settings,
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
from tests.integration.test_native_intake_parser_retry_postgresql import rows, transient
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


def prepared_claim(case, io, leases, delivery, monkeypatch):
    transient(io, monkeypatch)
    receipt = case[0].prepare_retry(delivery, leases=leases)
    defer_audit(case[0].graph)
    until = time.monotonic()+5
    while True:
        current = case[0].queue.claim_next(worker_id="synthetic-native-parser-cancellation")
        if current is not None:
            assert current.job_id == delivery.job_id
            return current, receipt
        assert time.monotonic() < until
        time.sleep(.025)


def assert_cancelled(case, delivery, previous, receipt, *, prepared=False):
    owner, ids = case[:2]
    current = snapshot(case)
    assert_preserved(previous, current)
    job, run, physical = rows(case, delivery.job_id)
    old_job = next(j for j in previous["workflow.jobs"] if j["job_id"] == delivery.job_id)
    old_run = previous["intake.parser_runs"][0]
    assert job["status"] == run["status"] == "CANCELLED" and physical.status == JobStatus.CANCELLED
    assert job["result"] is None and job["lease_expires_at"] is None and job["next_attempt_at"] is None
    assert job["payload"]["cancellation_receipt"] == receipt
    assert job["attempt"] == old_job["attempt"] == (1 if prepared else 0)
    assert job["fence_token"] == old_job["fence_token"]+1 and job["version"] == old_job["version"]+1
    assert job["timeout_at"] == old_job["timeout_at"] and job["max_attempts"] == old_job["max_attempts"]
    assert run["started_at"] == old_run["started_at"] and run["version"] == old_run["version"]+1
    assert (run["started_at"] is None) is (not prepared)
    assert run["parsed_payload"] is None and run["normalized_payload"] is None
    assert job["payload"]["attempt_intents"] == _object(old_job["payload"])["attempt_intents"]
    assert physical.locked_by is None and physical.lease_expires_at is None and physical.delivery_state is None
    assert receipt["physical_attempts"] == physical.attempts == (2 if prepared else 1)
    events = [e for e in current["workflow.outbox_events"] if e["event_id"] not in {
        p["event_id"] for p in previous["workflow.outbox_events"]}]
    assert len(events) == 1 and events[0]["event_type"] == "parser.run_completed"
    assert owner.graph.audit_sink.writer.verify_chain(ids["tenant"]).ok
    assert owner.replay(delivery) == {"terminal": True, "result": None}
    assert owner.cancel_unaccepted(delivery) == receipt
    assert snapshot(case) == current
    return current


@pytest.mark.parametrize("loss", ["key", "rotated_key", "policy", "storage", "realm", "token", "registry", "human", "source", "release"])
def test_initial_cancellation_has_no_fictitious_acceptance_or_start(parser_admission_case, monkeypatch, loss):
    case = parser_admission_case
    _, delivery = claimed(case)
    withdraw(case, loss, monkeypatch)
    previous = snapshot(case)
    receipt = case[0].cancel_unaccepted(delivery)
    assert receipt["acceptance_audit_event_id"] is None and receipt["retry_audit_event_id"] is None
    assert receipt["quality_summary"]["recorded_operations"] == 0
    current = assert_cancelled(case, delivery, previous, receipt)
    retire_all_positive_settings(monkeypatch)
    case[0].artifact_registry = None
    assert case[0].replay(delivery) == {"terminal": True, "result": None}
    assert case[0].cancel_unaccepted(delivery) == receipt and snapshot(case) == current


@pytest.mark.parametrize("loss", ["key", "token", "registry", "human", "source"])
def test_prepared_retry_cancellation_keeps_actual_first_attempt_and_its_closed_error(parser_io_case, monkeypatch, loss):
    case, io, leases, first = parser_io_case
    delivery, retry = prepared_claim(case, io, leases, first, monkeypatch)
    withdraw(case, loss, monkeypatch)
    previous = snapshot(case)
    receipt = case[0].cancel_unaccepted(delivery)
    assert receipt["retry_audit_event_id"] == retry["audit_event_id"]
    assert receipt["quality_summary"]["recorded_operations"] == receipt["quality_summary"]["error_operations"] == 1
    current = assert_cancelled(case, delivery, previous, receipt, prepared=True)
    retire_all_positive_settings(monkeypatch)
    case[0].artifact_registry = None
    assert case[0].replay(first) == {"terminal": True, "result": None}
    assert snapshot(case) == current


@pytest.mark.parametrize("retry_policy", [{"max_attempts": 2, "deadline_seconds": 6, "backoff_seconds": [1]}], indirect=True)
def test_actual_original_timeout_can_cancel_without_restarting_budget_or_accepting_retry(
        parser_io_case, monkeypatch, record_property):
    case, io, leases, delivery = parser_io_case
    delivery, _ = prepared_claim(case, io, leases, delivery, monkeypatch)
    elapsed = wait_outside_sql(case, _time(rows(case, delivery.job_id)[0]["timeout_at"]))
    record_property("actual_original_deadline_wait_seconds", elapsed)
    previous = snapshot(case)
    receipt = case[0].cancel_unaccepted(delivery)
    assert receipt["cause_code"] == "ORIGINAL_COMMAND_EXPIRED" and receipt["cause_error_class"] is None
    assert_cancelled(case, delivery, previous, receipt, prepared=True)


@pytest.mark.parametrize("retry_policy", [{"max_attempts": 2, "deadline_seconds": 6, "backoff_seconds": [1]}], indirect=True)
def test_actual_initial_original_timeout_retains_no_started_at(parser_admission_case, record_property):
    case = parser_admission_case
    _, delivery = claimed(case)
    elapsed = wait_outside_sql(case, _time(rows(case, delivery.job_id)[0]["timeout_at"]))
    record_property("actual_original_deadline_wait_seconds", elapsed)
    previous = snapshot(case)
    receipt = case[0].cancel_unaccepted(delivery)
    assert receipt["cause_code"] == "ORIGINAL_COMMAND_EXPIRED" and receipt["cause_error_class"] is None
    assert_cancelled(case, delivery, previous, receipt)


def test_healthy_initial_command_is_not_a_cancellation_cause(parser_admission_case):
    case = parser_admission_case
    _, delivery = claimed(case)
    previous = snapshot(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_CAPABILITY_STILL_CURRENT$"):
        case[0].cancel_unaccepted(delivery)
    assert snapshot(case) == previous


def test_healthy_prepared_retry_is_not_a_cancellation_cause(parser_io_case, monkeypatch):
    case, io, leases, first = parser_io_case
    delivery, _ = prepared_claim(case, io, leases, first, monkeypatch)
    previous = snapshot(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_CAPABILITY_STILL_CURRENT$"):
        case[0].cancel_unaccepted(delivery)
    assert snapshot(case) == previous


@pytest.mark.parametrize("closed", [False, True])
def test_accepted_attempt_requires_separate_recovery_and_never_loses_its_operations(parser_io_case, monkeypatch, closed):
    case, io, leases, delivery = parser_io_case
    if closed:
        io.read_and_parse()
    else:
        io._intent("gcs_bucket", data=None)
    leases.pause()
    withdraw(case, "key", monkeypatch)
    previous = snapshot(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_CANCEL_UNACCEPTED_COMMAND_REQUIRED$"):
        case[0].cancel_unaccepted(delivery)
    assert snapshot(case) == previous


@pytest.mark.parametrize("retry_policy", [{"max_attempts": 1, "backoff_seconds": []}], indirect=True)
def test_real_unaccepted_reclaim_beyond_original_physical_cap_cancels_without_business_attempt(
        parser_admission_case, record_property):
    case = parser_admission_case
    owner, ids = case[:2]
    seed = execute_seed_identity(case[6][0])
    assert seed["status"] == "EXECUTED" and seed["job_id"] == ids["job"]
    with owner.graph.transaction(ids["tenant"]):
        job = owner.graph._one("SELECT status,result FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], ids["job"]))
        assert job["status"] == "SUCCEEDED" and job["result"] == seed
        current = IdentityExecutionService(owner.graph)._delivery(ids["tenant"], ids["decision"], case[6][0][2])
        owner.queue.update_status(current.job_id, JobStatus.SUCCEEDED,
            expected_version=current.version, fence_token=current.fence_token)
    _, first = claimed(case)
    elapsed = wait_outside_sql(case, first.lease_expires_at)
    record_property("actual_original_physical_lease_wait_seconds", elapsed)
    defer_audit(owner.graph)
    delivery = owner.queue.claim_next(worker_id="synthetic-native-parser-cancel-reclaim")
    assert delivery.job_id == first.job_id and delivery.attempts == 2 and delivery.max_retries == 1
    previous = snapshot(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_CANCEL_PHYSICAL_NOT_OWNED$"):
        owner.cancel_unaccepted(first)
    assert snapshot(case) == previous
    receipt = owner.cancel_unaccepted(delivery)
    assert receipt["cause_code"] == "ORIGINAL_PHYSICAL_CAP_EXHAUSTED" and receipt["cause_error_class"] is None
    assert receipt["physical_attempts"] == 2 and receipt["attempt"] == 0 and receipt["first_started_at"] is None
    current = snapshot(case)
    assert_preserved(previous, current)
    assert owner.replay(delivery) == {"terminal": True, "result": None}


def test_old_physical_owner_cannot_cancel_new_prepared_claim(parser_io_case, monkeypatch):
    case, io, leases, first = parser_io_case
    prepared_claim(case, io, leases, first, monkeypatch)
    withdraw(case, "key", monkeypatch)
    previous = snapshot(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_CANCEL_PHYSICAL_NOT_OWNED$"):
        case[0].cancel_unaccepted(first)
    assert snapshot(case) == previous


def test_untyped_cancel_flag_is_not_a_human_withdrawal_journal(parser_admission_case, monkeypatch):
    case = parser_admission_case
    _, delivery = claimed(case)
    withdraw(case, "key", monkeypatch)
    with case[2].connect() as connection:
        connection.execute("UPDATE workflow.jobs SET cancellation_requested_at=clock_timestamp() WHERE tenant_id=%s AND job_id=%s",
            (case[1]["tenant"], delivery.job_id))
    previous = snapshot(case)
    with pytest.raises(JobFenceRejectedError, match="NATIVE_PARSER_CANCEL_UNACCEPTED_COMMAND_REQUIRED"):
        case[0].cancel_unaccepted(delivery)
    assert snapshot(case) == previous


@pytest.mark.parametrize("failure", ["audit", "root_expiry", "physical_version"])
def test_cancellation_audit_root_and_delivery_version_failures_are_atomic(parser_admission_case, monkeypatch, failure):
    case = parser_admission_case
    owner = case[0]
    _, delivery = claimed(case)
    withdraw(case, "key", monkeypatch)
    previous = snapshot(case)
    original = owner.graph.audit_sink.persist
    def conflict(event, scope):
        value = original(event, scope)
        if event["action"] == "parser.run_completed.v1":
            if failure == "audit":
                raise CanonicalAuditUnavailable("Synthetic cancellation audit unavailable")
            if failure == "root_expiry":
                time.sleep(.8)
            else:
                current = owner.queue.get(delivery.job_id)
                owner.queue.heartbeat(current.job_id, expected_version=current.version, fence_token=current.fence_token)
        return value
    monkeypatch.setattr(owner.graph.audit_sink, "persist", conflict)
    expected = CanonicalAuditUnavailable if failure == "audit" else NativeParserConflict if failure == "root_expiry" else JobFenceRejectedError
    with pytest.raises(expected):
        owner.cancel_unaccepted(delivery)
    assert snapshot(case) == previous


def test_lost_successful_cancellation_commit_response_reconciles_full_actual_terminal(parser_admission_case, monkeypatch):
    case = parser_admission_case
    owner = case[0]
    _, delivery = claimed(case)
    withdraw(case, "key", monkeypatch)
    previous = snapshot(case)
    transaction = owner.graph.transaction
    lost = False
    @contextmanager
    def lose(tenant):
        nonlocal lost
        with transaction(tenant):
            yield
        if not lost:
            lost = True
            raise OSError("Synthetic lost response after real SQL COMMIT")
    monkeypatch.setattr(owner.graph, "transaction", lose)
    receipt = owner.cancel_unaccepted(delivery)
    assert lost
    assert_cancelled(case, delivery, previous, receipt)


@pytest.mark.parametrize("tamper", ["false_attempt", "fence", "timeout", "start", "run_version", "quality", "cause", "event"])
def test_terminal_cancellation_rejects_tampered_receipt_audit_or_event(parser_admission_case, monkeypatch, tamper):
    case = parser_admission_case
    _, delivery = claimed(case)
    withdraw(case, "key", monkeypatch)
    receipt = case[0].cancel_unaccepted(delivery)
    changes = {"false_attempt": {"attempt": False}, "fence": {"business_fence_after": receipt["business_fence_after"]+1},
        "timeout": {"timeout_at": receipt["settled_at"]}, "start": {"first_started_at": receipt["settled_at"]},
        "run_version": {"run_version_before": 0}, "quality": {"quality_summary": {
            **receipt["quality_summary"], "recorded_operations": False}}, "cause": {"cause_code": "ORIGINAL_COMMAND_EXPIRED"}}
    job = rows(case, delivery.job_id)[0]
    if tamper != "event":
        altered = {**copy.deepcopy(receipt), **changes[tamper]}
        payload = {**job["payload"], "cancellation_receipt": altered}
        with case[2].connect() as connection:
            connection.execute("UPDATE workflow.jobs SET payload=%s::jsonb WHERE tenant_id=%s AND job_id=%s",
                (canonical_json(payload), case[1]["tenant"], delivery.job_id))
            # Match the receipt in its audit as well: independent facts and
            # exact JSON types, rather than receipt/audit equality alone, reject it.
            connection.execute("UPDATE audit.audit_events SET after_value=%s::jsonb WHERE tenant_id=%s AND audit_event_id=%s",
                (canonical_json({**altered, "status": "CANCELLED", "parser_run_status": "CANCELLED", "intake_mutated": False}),
                    case[1]["tenant"], receipt["audit_event_id"]))
    else:
        with case[2].connect() as connection:
            connection.execute("UPDATE workflow.outbox_events SET aggregate_version=aggregate_version+1 WHERE tenant_id=%s AND event_id=%s",
                (case[1]["tenant"], receipt["parser_completed_event_id"]))
    retire_all_positive_settings(monkeypatch)
    case[0].artifact_registry = None
    previous = snapshot(case)
    with pytest.raises(NativeParserConflict):
        case[0].replay(delivery)
    with pytest.raises(NativeParserConflict):
        case[0].cancel_unaccepted(delivery)
    assert snapshot(case) == previous
