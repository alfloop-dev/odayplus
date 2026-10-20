"""Actual same-run parser retries; synthetic GCS and the real fixed child."""
import copy
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest

from modules.external_data.application.native_intake_process import NativeIORejected
from modules.external_data.application.native_manual_parser_process import (
    NativeManualParserChildRunner,
)
from modules.listing.application.native_intake_assisted_entry_request_proof import _object, _time
from modules.listing.application.native_intake_parser_io import NativeIntakeParserIO
from modules.listing.application.native_intake_parser_leases import NativeParserCompositeLeases
from modules.listing.application.native_intake_parser_policy import (
    POLICY_ENV,
    NativeParserConflict,
    NativeParserUnavailable,
)
from modules.listing.application.native_intake_parser_results import commit_parser_output
from modules.listing.application.native_intake_retrieval_policy import (
    canonical_json,
    canonical_sha256,
)
from shared.audit.errors import CanonicalAuditUnavailable
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobStatus
from tests.integration.test_native_intake_identity_postgresql import defer_audit
from tests.integration.test_native_intake_parser_history_postgresql import (
    retire_all_positive_settings,
)
from tests.integration.test_native_intake_parser_permanent_postgresql import (
    authority as authority,
)
from tests.integration.test_native_intake_parser_permanent_postgresql import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_native_intake_parser_permanent_postgresql import (
    identity_case as identity_case,
)
from tests.integration.test_native_intake_parser_permanent_postgresql import (
    intake_blank_db as intake_blank_db,
)
from tests.integration.test_native_intake_parser_permanent_postgresql import (
    native_api as native_api,
)
from tests.integration.test_native_intake_parser_permanent_postgresql import observe_error
from tests.integration.test_native_intake_parser_permanent_postgresql import (
    parser_admission_case as parser_admission_case,
)
from tests.integration.test_native_intake_parser_permanent_postgresql import (
    parser_evidence_fields as parser_evidence_fields,
)
from tests.integration.test_native_intake_parser_permanent_postgresql import (
    parser_io_case as parser_io_case,
)
from tests.integration.test_native_intake_parser_permanent_postgresql import (
    parser_realm as parser_realm,
)
from tests.integration.test_native_intake_parser_permanent_postgresql import (
    source_policy_case as source_policy_case,
)
from tests.integration.test_native_intake_parser_permanent_postgresql import (
    start_case as start_case,
)
from tests.integration.test_native_intake_parser_unavailable_postgresql import snapshot, withdraw

pytestmark = pytest.mark.requires_live_env


@pytest.fixture(autouse=True)
def retry_policy(parser_admission_case, request, monkeypatch):
    """Change only the synthetic policy before its first parser admission."""
    if getattr(request, "param", None) is not None:
        policy = json.loads(os.environ[POLICY_ENV])
        monkeypatch.setenv(POLICY_ENV, json.dumps({**policy, **request.param}))


def rows(case, job_id):
    owner, ids = case[:2]
    with owner.graph.transaction(ids["tenant"]):
        job = owner.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_id=?", (ids["tenant"], job_id))
        run = owner.graph._one("SELECT * FROM intake.parser_runs WHERE tenant_id=? AND parser_run_id=?",
            (ids["tenant"], job["aggregate_id"]))
        return job, run, owner.queue.get(job_id)


def transient(io, monkeypatch):
    with monkeypatch.context() as patch:
        observe_error(io, patch, "transient")


@contextmanager
def accepted_retry(case, job_id):
    owner, ids = case[:2]
    defer_audit(owner.graph)
    until = time.monotonic()+5
    while True:
        delivery = owner.queue.claim_next(worker_id="synthetic-native-parser-retry")
        if delivery is not None:
            break
        assert time.monotonic() < until, "Actual original retry backoff never became due"
        time.sleep(.025)
    assert delivery.job_id == job_id
    fence, policy = owner._accept(delivery, ids["tenant"], 1)
    leases = NativeParserCompositeLeases(owner, delivery, business_fence=fence, policy_sha256=policy.sha256)
    try:
        yield NativeIntakeParserIO(owner, delivery, leases, policy, storage_factory=case[5].factory), leases, delivery
    finally:
        leases.pause()


def assert_prepared(case, delivery, receipt, previous):
    owner = case[0]
    current = snapshot(case)
    for table in ("intake.intakes", "intake.source_snapshots", "intake.human_corrections", "identity.match_cases",
            "expansion.listings", "control_transitions", "storage_calls", "slots", "rates", "workflow.outbox_events"):
        assert current[table] == previous[table], table
    job, run, physical = rows(case, delivery.job_id)
    old_job = next(j for j in previous["workflow.jobs"] if j["job_id"] == delivery.job_id)
    old_run = previous["intake.parser_runs"][0]
    assert len(current["intake.parser_runs"]) == 1
    assert job["status"] == "RETRYING" and run["status"] == "QUEUED"
    assert run["parser_run_id"] == old_run["parser_run_id"] and run["started_at"] == old_run["started_at"]
    assert job["timeout_at"] == old_job["timeout_at"] and job["max_attempts"] == old_job["max_attempts"]
    assert job["attempt"] == old_job["attempt"] and job["fence_token"] == old_job["fence_token"]
    assert job["version"] == old_job["version"]+1 and run["version"] == old_run["version"]+1
    assert job["result"] is None and run["parsed_payload"] is None and run["normalized_payload"] is None
    assert job["lease_expires_at"] is None and run["completed_at"] is None
    assert job["payload"]["attempt_intents"] == _object(old_job["payload"])["attempt_intents"]
    assert job["payload"]["retry_receipt"] == receipt
    assert physical.status == JobStatus.QUEUED and physical.delivery_state == JobDeliveryState.RETRYING
    assert physical.locked_by is None and physical.lease_expires_at is None
    assert physical.attempts == receipt["physical_attempts"]
    assert physical.fence_token == receipt["physical_fence"] and physical.version == receipt["physical_version"]
    assert datetime.fromisoformat(receipt["available_at"])-datetime.fromisoformat(receipt["prepared_at"]) == timedelta(
        seconds=job["payload"]["admission"]["policy"]["backoff_seconds"][job["attempt"]-1])
    assert owner.replay(delivery) == {"terminal": False, "result": None, "retry_prepared": True, "retry_receipt": receipt}


@pytest.mark.parametrize("parser_evidence_fields,status,target", [
    (None, "SUCCEEDED", "MATCHING"), ("partial", "PARTIAL", "NEEDS_REVIEW")], indirect=["parser_evidence_fields"])
def test_actual_retry_keeps_same_run_original_input_and_deadline_then_commits_real_output(
        parser_io_case, monkeypatch, status, target):
    case, io, leases, delivery = parser_io_case
    owner = case[0]
    first_job, first_run, _ = rows(case, delivery.job_id)
    transient(io, monkeypatch)
    previous = snapshot(case)
    receipt = owner.prepare_retry(delivery, leases=leases)
    assert_prepared(case, delivery, receipt, previous)
    with accepted_retry(case, delivery.job_id) as (second_io, second_leases, second):
        job, run, physical = rows(case, delivery.job_id)
        assert job["attempt"] == physical.attempts == 2 and run["version"] == 4
        assert job["timeout_at"] == first_job["timeout_at"] and run["started_at"] == first_run["started_at"]
        observed = second_io.read_and_parse()
        result = commit_parser_output(owner, second, second_leases, observed)
    assert result["parser_status"] == status and result["processing_state"] == target
    job, run, physical = rows(case, delivery.job_id)
    assert run["version"] == 5 and run["parser_run_id"] == first_run["parser_run_id"]
    assert job["timeout_at"] == first_job["timeout_at"] and run["started_at"] == first_run["started_at"]
    assert len(job["payload"]["attempt_intents"]) == 5 and len(job["payload"]["attempt_history"]) == 1
    assert result["attempt_history_sha256"] == canonical_sha256(job["payload"]["attempt_history"])
    assert job["payload"]["admission"] == first_job["payload"]["admission"]
    assert job["status"] == "SUCCEEDED" and physical.status == JobStatus.SUCCEEDED
    current = snapshot(case)
    assert len(current["intake.parser_runs"]) == 1
    for table in ("intake.source_snapshots", "intake.human_corrections", "identity.match_cases", "expansion.listings"):
        assert current[table] == previous[table]
    retire_all_positive_settings(monkeypatch)
    owner.artifact_registry = None
    assert owner.replay(second) == {"terminal": True, "result": result}
    assert snapshot(case) == current


def test_two_actual_retries_use_original_backoffs_and_preserve_every_attempt(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    first = rows(case, delivery.job_id)
    transient(io, monkeypatch)
    case[0].prepare_retry(delivery, leases=leases)
    with accepted_retry(case, delivery.job_id) as (io2, leases2, delivery2):
        transient(io2, monkeypatch)
        receipt = case[0].prepare_retry(delivery2, leases=leases2)
        assert datetime.fromisoformat(receipt["available_at"])-datetime.fromisoformat(receipt["prepared_at"]) == timedelta(seconds=3)
    with accepted_retry(case, delivery.job_id) as (io3, leases3, delivery3):
        result = commit_parser_output(case[0], delivery3, leases3, io3.read_and_parse())
    job, run, physical = rows(case, delivery.job_id)
    assert job["attempt"] == physical.attempts == 3 and run["version"] == 7
    assert len(job["payload"]["attempt_history"]) == 2 and len(job["payload"]["attempt_intents"]) == 6
    assert run["started_at"] == first[1]["started_at"] and job["timeout_at"] == first[0]["timeout_at"]
    assert result["attempt_history_sha256"] == canonical_sha256(job["payload"]["attempt_history"])
    assert case[0].replay(delivery3) == {"terminal": True, "result": result}


def test_actual_failed_and_reaped_child_can_retry_the_fixed_installed_parser(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    with monkeypatch.context() as patch:
        # Explicit test seam: real process creation, failure and group reaping.
        # The command is fixed here, never selected by a job or user input.
        patch.setattr(NativeManualParserChildRunner, "_command",
            lambda self, credential_fd: [sys.executable, "-I", "-S", "-c", "raise SystemExit(2)"])
        with pytest.raises(NativeIORejected, match="^NATIVE_IO_CHILD_FAILED$"):
            io.read_and_parse()
    receipt = case[0].prepare_retry(delivery, leases=leases)
    assert receipt["failure_observation"]["code"] == "NATIVE_IO_CHILD_FAILED"
    assert receipt["quality_summary"]["reaped_operations"] == 4
    with accepted_retry(case, delivery.job_id) as (io2, leases2, delivery2):
        result = commit_parser_output(case[0], delivery2, leases2, io2.read_and_parse())
    assert result["parser_status"] == "SUCCEEDED"
    assert case[0].replay(delivery2) == {"terminal": True, "result": result}


@pytest.mark.parametrize("retry_policy", [{"max_attempts": 2, "deadline_seconds": 12, "backoff_seconds": [9]}],
    indirect=True)
def test_original_deadline_cannot_be_restarted_to_fit_another_backoff(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    transient(io, monkeypatch)
    timeout = _time(rows(case, delivery.job_id)[0]["timeout_at"])
    with case[0].graph.transaction(case[1]["tenant"]):
        remaining = (timeout-case[0].clock._clock()).total_seconds()
    # The SQL clock remains root-bound; waiting holds no SQL transaction.
    # This timer only positions the test. prepare_retry reads the real SQL
    # clock and original timeout again, with its unchanged750ms root budget.
    wait_until = time.monotonic()+max(0, remaining-4)
    while time.monotonic() < wait_until:
        time.sleep(.05)
    previous = snapshot(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_ORIGINAL_RETRY_DEADLINE_EXHAUSTED$"):
        case[0].prepare_retry(delivery, leases=leases)
    assert snapshot(case) == previous


@pytest.mark.parametrize("retry_policy", [{"max_attempts": 1, "backoff_seconds": []}], indirect=True)
def test_original_single_attempt_cap_can_fail_only_the_observed_closed_failure(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    transient(io, monkeypatch)
    previous = snapshot(case)
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_RETRY_EXHAUSTED$"):
        case[0].prepare_retry(delivery, leases=leases)
    assert snapshot(case) == previous
    assert case[0].settle_permanent(delivery, leases=leases) is None
    job = rows(case, delivery.job_id)[0]
    assert job["attempt"] == job["max_attempts"] == 1
    assert job["payload"]["permanent_failure_receipt"]["code"] == "NATIVE_PARSER_RETRY_EXHAUSTED"
    assert case[0].replay(delivery) == {"terminal": True, "result": None}


@pytest.mark.parametrize("kind", ["metadata", "unknown", "auth", "unreaped"])
def test_only_proved_closed_transient_operations_allow_retry(parser_io_case, monkeypatch, kind):
    case, io, leases, delivery = parser_io_case
    observe_error(io, monkeypatch, kind)
    previous = snapshot(case)
    with pytest.raises((NativeParserConflict, JobFenceRejectedError)):
        case[0].prepare_retry(delivery, leases=leases)
    assert snapshot(case) == previous


def test_prepared_retry_replay_is_idempotent_after_positive_settings_retire(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    transient(io, monkeypatch)
    receipt = case[0].prepare_retry(delivery, leases=leases)
    previous = snapshot(case)
    retire_all_positive_settings(monkeypatch)
    case[0].artifact_registry = None
    assert case[0].prepare_retry(delivery, leases=leases) == receipt
    assert snapshot(case) == previous


def test_actual_committed_retry_with_unknown_response_reconciles_without_duplicate_audit(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    transient(io, monkeypatch)
    original = leases.root
    @contextmanager
    def unknown_response():
        with original() as value:
            yield value
        raise OSError("Synthetic response lost after actual commit")
    with monkeypatch.context() as patch:
        patch.setattr(leases, "root", unknown_response)
        receipt = case[0].prepare_retry(delivery, leases=leases)
    previous = snapshot(case)
    assert case[0].prepare_retry(delivery, leases=leases) == receipt and snapshot(case) == previous


@pytest.mark.parametrize("failure", ["audit", "late_key", "root_expiry"])
def test_retry_preparation_rolls_back_run_jobs_and_audit_atomically(parser_io_case, monkeypatch, failure):
    case, io, leases, delivery = parser_io_case
    transient(io, monkeypatch)
    owner = case[0]
    previous = snapshot(case)
    expected = {"audit": CanonicalAuditUnavailable, "late_key": NativeParserUnavailable,
        "root_expiry": NativeParserConflict}[failure]
    if failure == "audit":
        original = owner.graph.audit_sink.persist
        def fail(event, scope):
            if event["action"] == "intake.parser_retry_prepared.v1":
                raise CanonicalAuditUnavailable("Synthetic retry audit unavailable")
            return original(event, scope)
        monkeypatch.setattr(owner.graph.audit_sink, "persist", fail)
    else:
        original = owner._confirm
        def fail(*args):
            if failure == "late_key":
                withdraw(case, "key", monkeypatch)
            else:
                args[-1]._monotonic_deadline = time.monotonic()-1
            return original(*args)
        monkeypatch.setattr(owner, "_confirm", fail)
    with pytest.raises(expected):
        owner.prepare_retry(delivery, leases=leases)
    assert snapshot(case) == previous


@pytest.mark.parametrize("kind", ["metadata", "transient"])
def test_permanent_failure_or_original_cap_exhaustion_after_retry_has_complete_history(parser_io_case, monkeypatch, kind):
    case, io, leases, delivery = parser_io_case
    first = rows(case, delivery.job_id)
    transient(io, monkeypatch)
    case[0].prepare_retry(delivery, leases=leases)
    with accepted_retry(case, delivery.job_id) as (io2, leases2, delivery2):
        if kind == "metadata":
            observe_error(io2, monkeypatch, kind)
            final, final_leases = delivery2, leases2
        else:
            transient(io2, monkeypatch)
            case[0].prepare_retry(delivery2, leases=leases2)
    if kind == "transient":
        with accepted_retry(case, delivery.job_id) as (io3, leases3, delivery3):
            transient(io3, monkeypatch)
            previous = snapshot(case)
            with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_RETRY_EXHAUSTED$"):
                case[0].prepare_retry(delivery3, leases=leases3)
            assert snapshot(case) == previous
            assert case[0].settle_permanent(delivery3, leases=leases3) is None
            final = delivery3
    else:
        assert case[0].settle_permanent(final, leases=final_leases) is None
    job, run, physical = rows(case, delivery.job_id)
    receipt = job["payload"]["permanent_failure_receipt"]
    assert receipt["code"] == ("NATIVE_PARSER_PERMANENT_FAILURE" if kind == "metadata" else "NATIVE_PARSER_RETRY_EXHAUSTED")
    assert receipt["quality_summary"]["error_operations"] == job["attempt"]
    assert receipt["attempt_history_sha256"] == canonical_sha256(job["payload"]["attempt_history"])
    assert run["started_at"] == first[1]["started_at"] and job["timeout_at"] == first[0]["timeout_at"]
    assert job["status"] == run["status"] == "FAILED" and physical.status == JobStatus.FAILED
    target = next(i for i in snapshot(case)["intake.intakes"] if i["intake_id"] == run["intake_id"])
    assert target["processing_state"] == "FAILED"
    retire_all_positive_settings(monkeypatch)
    case[0].artifact_registry = None
    assert case[0].replay(final) == {"terminal": True, "result": None}


def test_authority_loss_after_actual_retry_preserves_intake_and_authenticates_all_errors(parser_io_case, monkeypatch):
    case, io, leases, delivery = parser_io_case
    transient(io, monkeypatch)
    case[0].prepare_retry(delivery, leases=leases)
    with accepted_retry(case, delivery.job_id) as (_, leases2, delivery2):
        withdraw(case, "key", monkeypatch)
        previous = snapshot(case)
        assert case[0].settle_unavailable(delivery2, business_fence=leases2.business_fence, leases=leases2) is None
    current = snapshot(case)
    assert current["intake.intakes"] == previous["intake.intakes"]
    receipt = rows(case, delivery.job_id)[0]["payload"]["negative_receipt"]
    assert receipt["quality_summary"]["error_operations"] == 1
    assert "attempt_history_sha256" in receipt
    assert case[0].replay(delivery2) == {"terminal": True, "result": None}


@pytest.mark.parametrize("tamper", ["missing_attempt", "due", "audit_bool", "old_unreaped", "old_extra"])
def test_forged_prior_attempt_is_rejected_before_next_storage_operation(parser_io_case, monkeypatch, tamper):
    case, io, leases, delivery = parser_io_case
    transient(io, monkeypatch)
    case[0].prepare_retry(delivery, leases=leases)
    with accepted_retry(case, delivery.job_id) as (io2, _, delivery2):
        job = rows(case, delivery.job_id)[0]
        payload = copy.deepcopy(job["payload"])
        if tamper == "missing_attempt":
            payload["attempt_history"] = []
        elif tamper == "due":
            payload["attempt_history"][0]["retry_receipt"]["available_at"] = payload["attempt_history"][0]["retry_receipt"]["prepared_at"]
        elif tamper == "old_unreaped":
            payload["attempt_intents"][0]["process_state"] = "UNKNOWN"
        elif tamper == "old_extra":
            payload["attempt_intents"].insert(1, copy.deepcopy(payload["attempt_intents"][0]))
        with case[2].connect() as connection:
            if tamper == "audit_bool":
                ident = payload["attempt_history"][0]["retry_receipt"]["audit_event_id"]
                assert connection.execute("UPDATE audit.audit_events SET after_value=jsonb_set(after_value,'{intake_mutated}','0'::jsonb) "
                    "WHERE audit_event_id=%s", (ident,)).rowcount == 1
            else:
                assert connection.execute("UPDATE workflow.jobs SET payload=%s::jsonb WHERE job_id=%s",
                    (canonical_json(payload), delivery2.job_id)).rowcount == 1
        previous = snapshot(case)
        with pytest.raises(NativeParserConflict):
            io2.read_and_parse()
        assert snapshot(case) == previous
