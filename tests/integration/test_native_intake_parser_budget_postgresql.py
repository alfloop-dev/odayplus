"""Actual original parser time/physical caps; no synthetic claim counters."""
import copy
import time
from contextlib import contextmanager

import pytest

from modules.listing.application.identity_execution import IdentityExecutionService
from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_assisted_entry_request_proof import _time
from modules.listing.application.native_intake_parser_io import NativeIntakeParserIO
from modules.listing.application.native_intake_parser_leases import NativeParserCompositeLeases
from modules.listing.application.native_intake_parser_policy import (
    NativeParserConflict,
    NativeParserUnavailable,
)
from modules.listing.application.native_intake_retrieval_policy import canonical_json
from shared.audit.errors import CanonicalAuditUnavailable
from shared.jobs.queue import JobFenceRejectedError, JobStatus
from tests.integration.test_assisted_listing_identity import _execute as execute_seed_identity
from tests.integration.test_native_intake_identity_postgresql import defer_audit
from tests.integration.test_native_intake_parser_admission_postgresql import claimed
from tests.integration.test_native_intake_parser_history_postgresql import (
    retire_all_positive_settings,
)
from tests.integration.test_native_intake_parser_permanent_postgresql import (
    assert_failure,
    observe_error,
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
from tests.integration.test_native_intake_parser_unavailable_postgresql import snapshot, withdraw

pytestmark = pytest.mark.requires_live_env
TIME_POLICY = {"max_attempts": 2, "deadline_seconds": 12, "backoff_seconds": [9]}


def wait_outside_sql(case, until, *, leave_seconds=0):
    owner, ids = case[:2]
    with owner.graph.transaction(ids["tenant"]):
        remaining = (until-owner.clock._clock()).total_seconds()
    started = time.monotonic()
    wake = started+max(0, remaining-leave_seconds)+(.02 if leave_seconds == 0 else 0)
    while time.monotonic() < wake:
        time.sleep(.05)
    return time.monotonic()-started


def near_original_timeout(case, delivery):
    timeout = _time(rows(case, delivery.job_id)[0]["timeout_at"])
    wait_outside_sql(case, timeout, leave_seconds=4)


@pytest.mark.parametrize("retry_policy", [{"max_attempts": 2, "backoff_seconds": [1]}], indirect=True)
def test_actual_unaccepted_claim_expiry_exhausts_physical_cap_before_business_cap(
        parser_admission_case, monkeypatch, record_property):
    case = parser_admission_case
    owner, ids = case[:2]
    # The shared graph fixture owns a separate already-approved identity job.
    # Finish it through its actual owner before waiting45s; otherwise that
    # earlier physical lease also expires and legitimately wins claim_next.
    seed = execute_seed_identity(case[6][0])
    assert seed["status"] == "EXECUTED" and seed["job_id"] == ids["job"]
    with owner.graph.transaction(ids["tenant"]):
        job = owner.graph._one("SELECT status,result FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
            (ids["tenant"], ids["job"]))
        assert job["status"] == "SUCCEEDED" and job["result"] == seed
        # The graph repository committed real business success. Its caller
        # completes transport separately, using the actual existing claim.
        current = IdentityExecutionService(owner.graph)._delivery(ids["tenant"], ids["decision"], case[6][0][2])
        owner.queue.update_status(current.job_id, JobStatus.SUCCEEDED,
            expected_version=current.version, fence_token=current.fence_token)
    assert owner.queue.get(ids["job"]).status == JobStatus.SUCCEEDED
    record_property("settled_independent_seed_identity_job_id", ids["job"])
    _, stale = claimed(case)
    record_property("actual_parser_job_id", stale.job_id)
    initial = rows(case, stale.job_id)
    assert initial[0]["attempt"] == 0 and initial[1]["started_at"] is None
    assert stale.attempts == 1 and stale.max_retries == 2
    assert (stale.lease_expires_at-stale.heartbeat_at).total_seconds() == 45
    elapsed = wait_outside_sql(case, stale.lease_expires_at)
    record_property("actual_unaccepted_claim_expiry_wait_seconds", elapsed)
    defer_audit(owner.graph)
    delivery = owner.queue.claim_next(worker_id="synthetic-native-parser-actual-reclaim")
    record_property("actual_reclaimed_job_id", delivery.job_id if delivery else None)
    assert delivery.job_id == stale.job_id and delivery.attempts == 2
    assert delivery.fence_token == stale.fence_token+1
    previous = snapshot(case)
    with pytest.raises(JobFenceRejectedError):
        owner._accept(stale, ids["tenant"], 1)
    assert snapshot(case) == previous
    fence, policy = owner._accept(delivery, ids["tenant"], 1)
    leases = NativeParserCompositeLeases(owner, delivery, business_fence=fence, policy_sha256=policy.sha256)
    try:
        io = NativeIntakeParserIO(owner, delivery, leases, policy, storage_factory=case[5].factory)
        transient(io, monkeypatch)
        job, run, physical = rows(case, delivery.job_id)
        assert job["attempt"] == 1 < job["max_attempts"] == 2
        assert physical.attempts == physical.max_retries == 2
        assert job["timeout_at"] == initial[0]["timeout_at"]
        previous = snapshot(case)
        with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_RETRY_EXHAUSTED$"):
            owner.prepare_retry(delivery, leases=leases)
        assert snapshot(case) == previous
        assert owner.settle_permanent(delivery, leases=leases) is None
        receipt = assert_failure(case, delivery, previous, operations=1)
        assert receipt["cause_code"] == "ORIGINAL_PARSER_RETRY_CAP_EXHAUSTED"
        assert receipt["attempt"] == 1 and receipt["physical_attempts"] == 2
        assert rows(case, delivery.job_id)[1]["started_at"] == run["started_at"]
        retire_all_positive_settings(monkeypatch)
        owner.artifact_registry = None
        assert owner.replay(delivery) == {"terminal": True, "result": None}
    finally:
        leases.pause()


@pytest.mark.parametrize("retry_policy", [TIME_POLICY], indirect=True)
@pytest.mark.parametrize("kind,operations,cause", [("transient", 1, "ORIGINAL_PARSER_RETRY_TIME_EXHAUSTED"),
    ("metadata", 2, "OBSERVED_PERMANENT_PARSER_FAILURE")])
def test_actual_closed_failure_uses_original_time_cap_without_reset_or_fabricated_attempt(
        parser_io_case, monkeypatch, kind, operations, cause):
    case, io, leases, delivery = parser_io_case
    owner = case[0]
    original = rows(case, delivery.job_id)
    observe_error(io, monkeypatch, kind)
    near_original_timeout(case, delivery)
    previous = snapshot(case)
    if kind == "transient":
        with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_ORIGINAL_RETRY_DEADLINE_EXHAUSTED$"):
            owner.prepare_retry(delivery, leases=leases)
        assert snapshot(case) == previous
    assert owner.settle_permanent(delivery, leases=leases) is None
    receipt = assert_failure(case, delivery, previous, operations=operations)
    assert receipt["cause_code"] == cause
    job, run, physical = rows(case, delivery.job_id)
    assert job["attempt"] == physical.attempts == 1 < job["max_attempts"] == physical.max_retries == 2
    assert job["timeout_at"] == original[0]["timeout_at"] and run["started_at"] == original[1]["started_at"]
    assert job["payload"]["admission"] == original[0]["payload"]["admission"]
    retire_all_positive_settings(monkeypatch)
    owner.artifact_registry = None
    current = snapshot(case)
    assert owner.replay(delivery) == {"terminal": True, "result": None}
    assert owner.settle_permanent(delivery, leases=leases) is None and snapshot(case) == current


@pytest.mark.parametrize("retry_policy", [TIME_POLICY], indirect=True)
@pytest.mark.parametrize("kind", ["empty", "healthy", "auth", "unknown", "unreaped"])
def test_near_timeout_alone_does_not_authorize_failing_the_intake(parser_io_case, monkeypatch, kind):
    case, io, leases, delivery = parser_io_case
    if kind == "healthy":
        io.read_and_parse()
    elif kind != "empty":
        observe_error(io, monkeypatch, kind)
    near_original_timeout(case, delivery)
    previous = snapshot(case)
    with pytest.raises((NativeParserConflict, JobFenceRejectedError)):
        case[0].settle_permanent(delivery, leases=leases)
    assert snapshot(case) == previous


@pytest.mark.parametrize("retry_policy", [TIME_POLICY], indirect=True)
@pytest.mark.parametrize("loss", ["key", "source"])
def test_original_time_exhaustion_does_not_override_lost_current_authority(parser_io_case, monkeypatch, loss):
    case, io, leases, delivery = parser_io_case
    transient(io, monkeypatch)
    near_original_timeout(case, delivery)
    withdraw(case, loss, monkeypatch)
    previous = snapshot(case)
    with pytest.raises((NativeParserUnavailable, NativeAssistedEntryConflict)):
        case[0].settle_permanent(delivery, leases=leases)
    assert snapshot(case) == previous
    assert case[0].settle_unavailable(delivery, business_fence=leases.business_fence, leases=leases) is None
    current = snapshot(case)
    assert current["intake.intakes"] == previous["intake.intakes"]
    assert case[0].replay(delivery) == {"terminal": True, "result": None}


@pytest.mark.parametrize("retry_policy", [TIME_POLICY], indirect=True)
@pytest.mark.parametrize("failure", ["audit", "late_key", "root_expiry"])
def test_time_exhaustion_terminal_rolls_back_every_write_on_audit_or_final_guard_failure(
        parser_io_case, monkeypatch, failure):
    case, io, leases, delivery = parser_io_case
    owner = case[0]
    transient(io, monkeypatch)
    near_original_timeout(case, delivery)
    previous = snapshot(case)
    if failure == "audit":
        original = owner.graph.audit_sink.persist
        def reject(event, scope):
            if event["action"] == "intake.processing_failed.v1":
                raise CanonicalAuditUnavailable("Synthetic time-exhaustion audit unavailable")
            return original(event, scope)
        monkeypatch.setattr(owner.graph.audit_sink, "persist", reject)
        expected = CanonicalAuditUnavailable
    else:
        original = owner._confirm
        def reject(*args):
            if failure == "late_key":
                withdraw(case, "key", monkeypatch)
            else:
                args[-1]._monotonic_deadline = time.monotonic()-1
            return original(*args)
        monkeypatch.setattr(owner, "_confirm", reject)
        expected = NativeParserUnavailable if failure == "late_key" else NativeParserConflict
    with pytest.raises(expected):
        owner.settle_permanent(delivery, leases=leases)
    assert snapshot(case) == previous


@pytest.mark.parametrize("retry_policy", [TIME_POLICY], indirect=True)
def test_unknown_response_after_actual_time_exhaustion_commit_is_history_only(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    transient(io, monkeypatch)
    near_original_timeout(case, delivery)
    previous = snapshot(case)
    original = leases.root
    @contextmanager
    def response_lost():
        with original() as value:
            yield value
        raise OSError("Synthetic response loss after actual time-exhaustion commit")
    with monkeypatch.context() as patch:
        patch.setattr(leases, "root", response_lost)
        assert case[0].settle_permanent(delivery, leases=leases) is None
    assert_failure(case, delivery, previous, operations=1)
    current = snapshot(case)
    retire_all_positive_settings(monkeypatch)
    case[0].artifact_registry = None
    assert case[0].settle_permanent(delivery, leases=leases) is None and snapshot(case) == current


@pytest.mark.parametrize("retry_policy", [TIME_POLICY], indirect=True)
@pytest.mark.parametrize("tamper", ["cause", "backoff", "timeout"])
def test_time_exhaustion_history_must_prove_the_original_budget_and_actual_cause(parser_io_case, monkeypatch, tamper):
    case, io, leases, delivery = parser_io_case
    transient(io, monkeypatch)
    near_original_timeout(case, delivery)
    assert case[0].settle_permanent(delivery, leases=leases) is None
    job = rows(case, delivery.job_id)[0]
    payload = copy.deepcopy(job["payload"])
    if tamper == "cause":
        payload["permanent_failure_receipt"]["cause_code"] = "ORIGINAL_PARSER_RETRY_CAP_EXHAUSTED"
    elif tamper == "backoff":
        payload["admission"]["policy"]["backoff_seconds"] = [1]
    with case[2].connect() as connection:
        if tamper == "timeout":
            assert connection.execute("UPDATE workflow.jobs SET timeout_at=timeout_at+interval '1 minute' WHERE job_id=%s",
                (delivery.job_id,)).rowcount == 1
        else:
            assert connection.execute("UPDATE workflow.jobs SET payload=%s::jsonb WHERE job_id=%s",
                (canonical_json(payload), delivery.job_id)).rowcount == 1
    previous = snapshot(case)
    retire_all_positive_settings(monkeypatch)
    case[0].artifact_registry = None
    with pytest.raises(NativeParserConflict):
        case[0].replay(delivery)
    assert snapshot(case) == previous
