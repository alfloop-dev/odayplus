"""Pure MOCK call-flow contracts; no actual SQL, acceptance, reaping or IO proof.

Every owner/read/lease/IO/result boundary below is mocked. Successful mock
responses describe routing scenarios, never genuine grants or durable rows.
Root must separately verify the actual owner, SQL, providers and worker wiring.
"""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, call, create_autospec, sentinel

import pytest
from fastapi import HTTPException

from modules.external_data.application.native_intake_process import (
    NativeIORejected,
    NativeIOUnreaped,
)
from modules.external_data.application.native_parser_artifacts import (
    NativeParserArtifactUnavailable,
)
from modules.external_data.application.native_parser_output import NativeParserOutputRejected
from modules.listing.application import native_intake_parser_runtime as runtime_module
from modules.listing.application.native_intake_parser import NativeIntakeParserOwner
from modules.listing.application.native_intake_parser_policy import (
    JOB_TYPE,
    NativeParserConflict,
    NativeParserUnavailable,
)
from shared.jobs.queue import JobFenceRejectedError, JobStatus

TENANT = "eaa9709e-8a88-41e0-bd39-e01ebd19ce44"
JOB = "cd7f47b3-bde5-498a-afaf-988af7179c12"
OPEN = {"terminal": False, "result": None}


def prepared(fence):
    # Deliberately incomplete MOCK response, not a historical retry proof.
    return {**OPEN, "retry_prepared": True, "retry_receipt": {"physical_fence": fence}}


def allow(method, result=None):
    method.side_effect = None
    method.return_value = result


@pytest.fixture
def mocked_flow(monkeypatch):
    owner = Mock(spec=NativeIntakeParserOwner)
    transaction_exits = []

    @contextmanager
    def transaction(_tenant):
        yield
        if transaction_exits:
            error = transaction_exits.pop(0)
            if error is not None:
                raise error

    owner.graph = SimpleNamespace(transaction=Mock(side_effect=transaction))
    owner.engine = Mock(spec_set=["execute", "query_one"])
    owner.queue = Mock(spec_set=["update_status", "heartbeat"])
    delivery = SimpleNamespace(job_type=JOB_TYPE, job_id=JOB, correlation_id="mock-correlation",
        payload={"job_id": JOB, "tenant_id": TENANT, "attempt_token": "1." + "a" * 64},
        status=JobStatus.RUNNING, locked_by="mock-parser-worker", fence_token=9)
    policy = SimpleNamespace(sha256="b" * 64, tenant_id=TENANT)
    owner.replay.return_value = OPEN
    owner._accept.return_value = (7, policy)
    for name in ("cancel_unaccepted", "recover_expired", "settle_unavailable", "settle_permanent", "prepare_retry"):
        getattr(owner, name).side_effect = JobFenceRejectedError("MOCK_FULL_PROOF_NOT_ESTABLISHED")
    leases = Mock(spec=runtime_module.NativeParserCompositeLeases)
    leases.business_fence = 7
    lease_factory = create_autospec(runtime_module.NativeParserCompositeLeases, return_value=leases)
    io = Mock(spec=runtime_module.NativeIntakeParserIO)
    io.read_and_parse.return_value = sentinel.observed_output
    io_factory = create_autospec(runtime_module.NativeIntakeParserIO, return_value=io)
    commit = create_autospec(runtime_module.commit_parser_output, return_value=sentinel.committed_result)
    reader = Mock(spec=runtime_module.NativeParserExecutionReads)
    state = SimpleNamespace(job={"status": "RUNNING", "fence_token": 7, "attempt": 1},
        payload={"attempt_intents": []}, before=Mock(), context=SimpleNamespace(policy=policy))
    reader.historical_locked.return_value = (sentinel.physical, state.job, {}, sentinel.run, Mock())
    reader.fresh.return_value = state
    reads = create_autospec(runtime_module.NativeParserExecutionReads, return_value=reader)
    timeline = Mock()
    for name, replacement in (("NativeParserCompositeLeases", lease_factory),
            ("NativeIntakeParserIO", io_factory), ("commit_parser_output", commit),
            ("NativeParserExecutionReads", reads)):
        monkeypatch.setattr(runtime_module, name, replacement)
    for name, mock in (("owner", owner), ("parser_io", io), ("commit", commit),
            ("held_leases", leases), ("reader", reader)):
        timeline.attach_mock(mock, name)
    runtime = runtime_module.NativeIntakeParserRuntime(owner)
    return SimpleNamespace(owner=owner, delivery=delivery, policy=policy, leases=leases,
        lease_factory=lease_factory, io=io, io_factory=io_factory, commit=commit, reader=reader,
        reads=reads, state=state, timeline=timeline, runtime=runtime, transaction_exits=transaction_exits)


def assert_no_io(flow):
    flow.io_factory.assert_not_called()
    flow.io.read_and_parse.assert_not_called()
    flow.commit.assert_not_called()


def assert_no_failure_grant(flow):
    flow.owner.prepare_retry.assert_not_called()
    flow.owner.settle_permanent.assert_not_called()
    flow.owner.queue.update_status.assert_not_called()
    flow.owner.engine.execute.assert_not_called()


@pytest.mark.parametrize("path", ["capability", "permanent", "exhausted_retry"])
def test_mock_terminal_owner_can_observe_concurrent_positive_result_after_earlier_open_replay(mocked_flow, path):
    f = mocked_flow
    if path == "capability":
        error = NativeIORejected("NATIVE_GCS_HTTP_REJECTED", status=403)
        method = f.owner.settle_unavailable
    elif path == "permanent":
        error = NativeParserOutputRejected("NATIVE_PARSER_OUTPUT_REJECTED")
        method = f.owner.settle_permanent
    else:
        error = NativeIORejected("NATIVE_IO_CONNECTION_FAILED")
        f.owner.prepare_retry.side_effect = JobFenceRejectedError("NATIVE_PARSER_RETRY_EXHAUSTED")
        method = f.owner.settle_permanent
    f.io.read_and_parse.side_effect = error
    allow(method, sentinel.concurrent_committed_result)
    assert f.runtime.execute(f.delivery) is sentinel.concurrent_committed_result
    f.io.read_and_parse.assert_called_once_with()
    f.commit.assert_not_called()
    method.assert_called_once()
    f.leases.pause.assert_called_once_with()


@pytest.mark.parametrize("result", [sentinel.historical_output, None])
def test_mock_terminal_replay_needs_no_new_acceptance_or_capability(mocked_flow, result):
    f = mocked_flow
    f.owner.replay.return_value = {"terminal": True, "result": result}
    f.owner._accept.side_effect = NativeParserUnavailable("MOCK_RETIRED_CONFIGURATION")
    assert f.runtime.execute(f.delivery) is result
    f.owner._accept.assert_not_called()
    f.lease_factory.assert_not_called()
    assert_no_io(f)
    assert_no_failure_grant(f)


@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.RUNNING])
def test_mock_prepared_retry_replay_of_old_delivery_does_not_start_io(mocked_flow, status):
    f = mocked_flow
    f.delivery.status = status
    f.owner.replay.return_value = prepared(f.delivery.fence_token)
    assert f.runtime.execute(f.delivery) is None
    f.owner._accept.assert_not_called()
    assert_no_io(f)


def test_mock_new_claim_of_prepared_retry_requires_actual_acceptance_api(mocked_flow):
    f = mocked_flow
    f.owner.replay.return_value = prepared(f.delivery.fence_token - 1)
    assert f.runtime.execute(f.delivery) is sentinel.committed_result
    f.owner._accept.assert_called_once_with(f.delivery, TENANT, 1)
    f.io.read_and_parse.assert_called_once_with()
    f.commit.assert_called_once_with(f.owner, f.delivery, f.leases, sentinel.observed_output)


def test_mock_accepted_result_uses_fixed_io_and_commit_in_order(mocked_flow):
    f = mocked_flow
    assert f.runtime.execute(f.delivery) is sentinel.committed_result
    f.lease_factory.assert_called_once_with(f.owner, f.delivery, business_fence=7, policy_sha256=f.policy.sha256)
    f.io_factory.assert_called_once_with(f.owner, f.delivery, f.leases, f.policy,
        storage_factory=None, parser_process_factory=None)
    calls = f.timeline.mock_calls
    assert calls.index(call.owner._accept(f.delivery, TENANT, 1)) < calls.index(call.parser_io.read_and_parse())
    assert calls.index(call.parser_io.read_and_parse()) < calls.index(
        call.commit(f.owner, f.delivery, f.leases, sentinel.observed_output))
    f.leases.pause.assert_called_once_with()
    assert_no_failure_grant(f)


def test_mock_injected_provider_factories_are_trusted_constructor_arguments(mocked_flow):
    f = mocked_flow
    runtime = runtime_module.NativeIntakeParserRuntime(f.owner,
        storage_factory=sentinel.storage_factory, parser_process_factory=sentinel.process_factory)
    runtime.execute(f.delivery)
    f.io_factory.assert_called_once_with(f.owner, f.delivery, f.leases, f.policy,
        storage_factory=sentinel.storage_factory, parser_process_factory=sentinel.process_factory)


def test_mock_request_selected_provider_is_rejected_before_owner_or_io(mocked_flow):
    f = mocked_flow
    f.delivery.payload["provider"] = "untrusted.module:provider"
    with pytest.raises(JobFenceRejectedError):
        f.runtime.execute(f.delivery)
    f.owner.replay.assert_not_called()
    f.owner._accept.assert_not_called()
    assert_no_io(f)


def test_mock_known_acceptance_rejection_never_uses_apparently_valid_running_read(mocked_flow):
    f = mocked_flow
    error = JobFenceRejectedError("NATIVE_PARSER_ACCEPTANCE_NOT_CURRENT")
    f.owner._accept.side_effect = error
    # Even a configured mock RUNNING acceptance must not be consulted here.
    with pytest.raises(JobFenceRejectedError) as raised:
        f.runtime.execute(f.delivery)
    assert raised.value is error
    f.reads.assert_not_called()
    assert_no_io(f)
    assert_no_failure_grant(f)


@pytest.mark.parametrize("retry_claim", [False, True])
def test_mock_missing_capability_on_unaccepted_command_delegates_full_cancellation(mocked_flow, retry_claim):
    f = mocked_flow
    f.owner.replay.return_value = prepared(8) if retry_claim else OPEN
    f.owner._accept.side_effect = NativeParserUnavailable("NATIVE_PARSER_SIGNING_KEY_UNAVAILABLE")
    f.state.job["status"] = "RETRYING" if retry_claim else "QUEUED"
    allow(f.owner.cancel_unaccepted, sentinel.cancellation_receipt)
    assert f.runtime.execute(f.delivery) is None
    f.owner.cancel_unaccepted.assert_called_once_with(f.delivery, leases=None)
    f.owner.recover_expired.assert_not_called()
    f.reader.fresh.assert_not_called()
    assert_no_io(f)


@pytest.mark.parametrize("error", [
    NativeParserConflict("NATIVE_PARSER_ROOT_DEADLINE_EXPIRED"),
    JobFenceRejectedError("NATIVE_PARSER_EXECUTION_CANCELLED_OR_EXHAUSTED"),
])
@pytest.mark.parametrize("proved", [True, False])
def test_mock_original_unaccepted_cap_expiry_requires_full_cancellation_proof(mocked_flow, error, proved):
    f = mocked_flow
    f.owner._accept.side_effect = error
    f.state.job["status"] = "QUEUED"
    if proved:
        allow(f.owner.cancel_unaccepted, sentinel.cancellation_receipt)
        assert f.runtime.execute(f.delivery) is None
    else:
        # A root deadline or a generic fence rejection alone proves no expired
        # original command/source cap and cannot close a healthy command.
        with pytest.raises(type(error)) as raised:
            f.runtime.execute(f.delivery)
        assert raised.value is error
    f.owner.cancel_unaccepted.assert_called_once_with(f.delivery, leases=None)
    assert_no_io(f)
    assert_no_failure_grant(f)


def test_mock_unknown_acceptance_reply_requires_committed_history_and_fresh_reads(mocked_flow):
    f = mocked_flow
    f.owner._accept.side_effect = RuntimeError("MOCK_ACCEPTANCE_COMMIT_REPLY_LOST")
    # A different business fence tests that runtime uses the actual API read.
    f.state.job["fence_token"] = 11
    assert f.runtime.execute(f.delivery) is sentinel.committed_result
    f.reader.historical_locked.assert_called_once_with(f.delivery, TENANT, 1)
    f.reader.fresh.assert_called_once_with(f.delivery, TENANT, 1, business_fence=11)
    assert f.owner.graph.transaction.call_args_list == [call(TENANT), call(TENANT)]
    f.lease_factory.assert_called_once_with(f.owner, f.delivery, business_fence=11, policy_sha256=f.policy.sha256)
    f.owner._accept.assert_called_once()
    f.io.read_and_parse.assert_called_once()


@pytest.mark.parametrize("failure", [
    JobFenceRejectedError("MOCK_FOREIGN_FENCE"),
    NativeParserConflict("NATIVE_PARSER_ROOT_DEADLINE_EXPIRED"),
    RuntimeError("MOCK_SQL_FAILURE"),
])
def test_mock_unknown_acceptance_without_historical_proof_preserves_original(mocked_flow, failure):
    f = mocked_flow
    original = RuntimeError("MOCK_UNKNOWN_ACCEPTANCE")
    f.owner._accept.side_effect = original
    f.reader.historical_locked.side_effect = failure
    with pytest.raises(RuntimeError) as raised:
        f.runtime.execute(f.delivery)
    assert raised.value is original
    f.reader.fresh.assert_not_called()
    assert_no_io(f)
    assert_no_failure_grant(f)


@pytest.mark.parametrize("state", ["ADMITTED", "FINISHED", "ERROR"])
def test_mock_unknown_acceptance_cannot_restart_any_current_journal(mocked_flow, state):
    f = mocked_flow
    original = RuntimeError("MOCK_UNKNOWN_ACCEPTANCE")
    f.owner._accept.side_effect = original
    f.state.payload["attempt_intents"] = [{"attempt": 1, "state": state}]
    with pytest.raises(RuntimeError) as raised:
        f.runtime.execute(f.delivery)
    assert raised.value is original
    assert_no_io(f)
    assert_no_failure_grant(f)


def test_mock_unknown_acceptance_does_not_reuse_a_changed_business_state(mocked_flow):
    f = mocked_flow
    original = RuntimeError("MOCK_UNKNOWN_ACCEPTANCE")
    f.owner._accept.side_effect = original
    f.reader.fresh.return_value = SimpleNamespace(job={"status": "RETRYING"})
    with pytest.raises(RuntimeError) as raised:
        f.runtime.execute(f.delivery)
    assert raised.value is original
    assert_no_io(f)
    assert_no_failure_grant(f)


@pytest.mark.parametrize("read_index", [0, 1])
def test_mock_unknown_read_transaction_exit_never_releases_an_execution_grant(mocked_flow, read_index):
    f = mocked_flow
    original = RuntimeError("MOCK_ACCEPTANCE_REPLY_LOST")
    f.owner._accept.side_effect = original
    f.transaction_exits.extend([None] * read_index + [RuntimeError("MOCK_READ_COMMIT_REPLY_LOST")])
    with pytest.raises(RuntimeError) as raised:
        f.runtime.execute(f.delivery)
    assert raised.value is original
    assert_no_io(f)
    assert_no_failure_grant(f)


def test_mock_reconciled_acceptance_with_current_capability_loss_can_only_close(mocked_flow):
    f = mocked_flow
    f.owner._accept.side_effect = RuntimeError("MOCK_ACCEPTANCE_REPLY_LOST")
    f.reader.fresh.side_effect = NativeParserUnavailable("NATIVE_PARSER_CONFIGURATION_UNAVAILABLE")
    allow(f.owner.settle_unavailable)
    assert f.runtime.execute(f.delivery) is None
    f.owner.settle_unavailable.assert_called_once_with(f.delivery, business_fence=7, leases=None)
    f.lease_factory.assert_not_called()
    assert_no_io(f)
    assert_no_failure_grant(f)


@pytest.mark.parametrize("error", [
    NativeIORejected("NATIVE_GCS_HTTP_REJECTED", status=503),
    NativeIORejected("NATIVE_GCS_HTTP_REJECTED", status=429),
    NativeIORejected("NATIVE_IO_PARSER_DEADLINE_EXPIRED"),
])
def test_mock_typed_transient_routes_to_owner_retry_without_repeating_io(mocked_flow, error):
    f = mocked_flow
    f.io.read_and_parse.side_effect = error
    allow(f.owner.prepare_retry, sentinel.retry_receipt)
    assert f.runtime.execute(f.delivery) is None
    f.owner.prepare_retry.assert_called_once_with(f.delivery, leases=f.leases)
    f.owner.settle_permanent.assert_not_called()
    f.io.read_and_parse.assert_called_once()
    f.commit.assert_not_called()
    f.leases.pause.assert_called_once()


@pytest.mark.parametrize("error,method", [
    (NativeIORejected("NATIVE_GCS_HTTP_REJECTED", status=503), "prepare_retry"),
    (NativeParserOutputRejected("MOCK_INVALID_OUTPUT"), "settle_permanent"),
    (NativeIORejected("NATIVE_GCS_HTTP_REJECTED", status=403), "settle_unavailable"),
])
def test_mock_typed_error_without_complete_owner_journal_proof_stays_unresolved(mocked_flow, error, method):
    f = mocked_flow
    f.io.read_and_parse.side_effect = error
    # All owner methods refuse by default: a typed exception is not a fact.
    with pytest.raises(type(error)) as raised:
        f.runtime.execute(f.delivery)
    assert raised.value is error
    getattr(f.owner, method).assert_called_once()
    f.io.read_and_parse.assert_called_once()
    f.commit.assert_not_called()
    f.leases.pause.assert_called_once()


def test_mock_permanent_observation_routes_to_independent_owner_proof(mocked_flow):
    f = mocked_flow
    f.io.read_and_parse.side_effect = NativeParserOutputRejected("MOCK_INVALID_OUTPUT")
    allow(f.owner.settle_permanent)
    assert f.runtime.execute(f.delivery) is None
    f.owner.settle_permanent.assert_called_once_with(f.delivery, leases=f.leases)
    f.owner.prepare_retry.assert_not_called()
    f.commit.assert_not_called()


@pytest.mark.parametrize("error", [
    NativeParserUnavailable("NATIVE_PARSER_CONFIGURATION_UNAVAILABLE"),
    NativeParserArtifactUnavailable("MOCK_ARTIFACT_WITHDRAWN"),
    NativeIORejected("NATIVE_GCS_HTTP_REJECTED", status=401),
    NativeIORejected("NATIVE_GCS_HTTP_REJECTED", status=403),
    NativeIORejected("NATIVE_GCS_PARSER_CREDENTIAL_UNAVAILABLE"),
    NativeIORejected("NATIVE_IO_PARSER_CURRENT_CONFIG_LOST"),
    PermissionError("MOCK_REQUESTER_WITHDRAWN"), HTTPException(403),
])
def test_mock_accepted_capability_loss_uses_actual_accepted_fence_and_leases(mocked_flow, error):
    f = mocked_flow
    f.io.read_and_parse.side_effect = error
    allow(f.owner.settle_unavailable)
    assert f.runtime.execute(f.delivery) is None
    f.owner.settle_unavailable.assert_called_once_with(f.delivery, business_fence=7, leases=f.leases)
    f.owner.cancel_unaccepted.assert_not_called()
    assert_no_failure_grant(f)
    f.leases.pause.assert_called_once()


def test_mock_lease_constructor_loss_retains_accepted_fence_for_negative_proof(mocked_flow):
    f = mocked_flow
    f.lease_factory.side_effect = NativeParserUnavailable("NATIVE_PARSER_SIGNING_KEY_UNAVAILABLE")
    allow(f.owner.settle_unavailable)
    assert f.runtime.execute(f.delivery) is None
    f.owner.settle_unavailable.assert_called_once_with(f.delivery, business_fence=7, leases=None)
    f.leases.pause.assert_not_called()
    assert_no_io(f)


@pytest.mark.parametrize("error", [
    RuntimeError("MOCK_FINISH_COMMIT_REPLY_LOST"),
    RuntimeError("NativeIORejected NATIVE_IO_CHILD_FAILED is only exception text"),
    NativeParserUnavailable("NATIVE_PARSER_CURRENT_SQL_ROOT_REQUIRED"),
    NativeParserConflict("NATIVE_PARSER_ROOT_DEADLINE_EXPIRED"),
    JobFenceRejectedError("NATIVE_PARSER_INTENT_FINISH_REJECTED"),
    NativeIORejected("NATIVE_IO_UNKNOWN_FAILURE"),
    NativeIOUnreaped("NATIVE_IO_CHILD_UNREAPED"),
])
def test_mock_unknown_root_finish_or_unreaped_failure_never_becomes_parser_retry(mocked_flow, error):
    f = mocked_flow
    error.sqlstate = "40001"
    f.io.read_and_parse.side_effect = error
    with pytest.raises(type(error)) as raised:
        f.runtime.execute(f.delivery)
    assert raised.value is error
    assert_no_failure_grant(f)
    f.owner.settle_unavailable.assert_not_called()
    f.io.read_and_parse.assert_called_once()
    f.commit.assert_not_called()
    f.leases.pause.assert_called_once()
    if isinstance(error, NativeIOUnreaped):
        f.owner.replay.assert_called_once_with(f.delivery)
        f.owner.recover_expired.assert_not_called()


def test_mock_later_delivery_call_cannot_restart_a_failed_finish_on_known_rejection(mocked_flow):
    f = mocked_flow
    finish_error = RuntimeError("MOCK_FINISH_REPLY_LOST")
    rejection = JobFenceRejectedError("NATIVE_PARSER_ACCEPTANCE_NOT_CURRENT")
    f.owner._accept.side_effect = [(7, f.policy), rejection]
    f.io.read_and_parse.side_effect = finish_error
    with pytest.raises(RuntimeError) as first:
        f.runtime.execute(f.delivery)
    assert first.value is finish_error
    with pytest.raises(JobFenceRejectedError) as second:
        f.runtime.execute(f.delivery)
    assert second.value is rejection
    f.io.read_and_parse.assert_called_once()
    f.reads.assert_not_called()
    f.commit.assert_not_called()
    assert_no_failure_grant(f)


def test_mock_lost_result_commit_returns_only_authenticated_replay(mocked_flow):
    f = mocked_flow
    f.commit.side_effect = RuntimeError("MOCK_RESULT_COMMIT_REPLY_LOST")
    f.owner.replay.side_effect = [OPEN, {"terminal": True, "result": sentinel.replayed_result}]
    assert f.runtime.execute(f.delivery) is sentinel.replayed_result
    f.io.read_and_parse.assert_called_once()
    f.commit.assert_called_once()
    assert_no_failure_grant(f)
    f.leases.pause.assert_called_once()


@pytest.mark.parametrize("error", [
    RuntimeError("MOCK_RESULT_COMMIT_REPLY_LOST"),
    NativeIORejected("NATIVE_IO_CHILD_FAILED"),
    NativeParserOutputRejected("MOCK_COMMIT_VALIDATION_FAILURE"),
])
def test_mock_unproven_result_commit_cannot_repeat_io_commit_or_grant_retry(mocked_flow, error):
    f = mocked_flow
    f.commit.side_effect = error
    with pytest.raises(type(error)) as raised:
        f.runtime.execute(f.delivery)
    assert raised.value is error
    f.io.read_and_parse.assert_called_once()
    f.commit.assert_called_once()
    assert_no_failure_grant(f)


def test_mock_second_root_failure_keeps_original_commit_error(mocked_flow):
    f = mocked_flow
    original = RuntimeError("MOCK_COMMIT_REPLY_LOST")
    f.commit.side_effect = original
    f.owner.replay.side_effect = [OPEN, NativeParserConflict("NATIVE_PARSER_ROOT_DEADLINE_EXPIRED")]
    with pytest.raises(RuntimeError) as raised:
        f.runtime.execute(f.delivery)
    assert raised.value is original
    assert_no_failure_grant(f)


@pytest.mark.parametrize("proved", [True, False])
def test_mock_lost_retry_commit_requires_prepared_proof_bound_to_this_claim(mocked_flow, proved):
    f = mocked_flow
    original = NativeIORejected("NATIVE_IO_CHILD_FAILED")
    f.io.read_and_parse.side_effect = original
    f.owner.prepare_retry.side_effect = RuntimeError("MOCK_RETRY_COMMIT_REPLY_LOST")
    # An old preparation followed by this new claim does not close this call.
    f.owner.replay.side_effect = [OPEN, OPEN, prepared(9 if proved else 8)]
    if proved:
        assert f.runtime.execute(f.delivery) is None
    else:
        with pytest.raises(NativeIORejected) as raised:
            f.runtime.execute(f.delivery)
        assert raised.value is original
    f.owner._accept.assert_called_once()
    f.owner.prepare_retry.assert_called_once()
    f.owner.settle_permanent.assert_not_called()
    f.io.read_and_parse.assert_called_once()
    f.commit.assert_not_called()


@pytest.mark.parametrize("code", ["NATIVE_PARSER_RETRY_EXHAUSTED", "NATIVE_PARSER_ORIGINAL_RETRY_DEADLINE_EXHAUSTED"])
@pytest.mark.parametrize("proved", [True, False])
def test_mock_original_retry_exhaustion_needs_independent_permanent_proof(mocked_flow, code, proved):
    f = mocked_flow
    original = NativeIORejected("NATIVE_IO_CHILD_FAILED")
    f.io.read_and_parse.side_effect = original
    f.owner.prepare_retry.side_effect = JobFenceRejectedError(code)
    if proved:
        allow(f.owner.settle_permanent)
        assert f.runtime.execute(f.delivery) is None
    else:
        # Includes a live source cap too short for backoff: not proof that the
        # original command timeout/counter is exhausted or Intake may fail.
        with pytest.raises(NativeIORejected) as raised:
            f.runtime.execute(f.delivery)
        assert raised.value is original
    f.owner.settle_permanent.assert_called_once_with(f.delivery, leases=f.leases)
    f.io.read_and_parse.assert_called_once()
    f.commit.assert_not_called()


def test_mock_retry_root_failure_does_not_fall_back_to_permanent_failure(mocked_flow):
    f = mocked_flow
    original = NativeIORejected("NATIVE_IO_CHILD_FAILED")
    f.io.read_and_parse.side_effect = original
    f.owner.prepare_retry.side_effect = NativeParserConflict("NATIVE_PARSER_ROOT_DEADLINE_EXPIRED")
    with pytest.raises(NativeIORejected) as raised:
        f.runtime.execute(f.delivery)
    assert raised.value is original
    f.owner.settle_permanent.assert_not_called()


def test_mock_capability_loss_during_retry_uses_same_accepted_fence(mocked_flow):
    f = mocked_flow
    f.io.read_and_parse.side_effect = NativeIORejected("NATIVE_IO_CHILD_FAILED")
    f.owner.prepare_retry.side_effect = NativeParserUnavailable("NATIVE_PARSER_CONFIGURATION_UNAVAILABLE")
    allow(f.owner.settle_unavailable)
    assert f.runtime.execute(f.delivery) is None
    f.owner.settle_unavailable.assert_called_once_with(f.delivery, business_fence=7, leases=f.leases)
    f.owner.settle_permanent.assert_not_called()


def test_mock_reclaimed_running_job_bypasses_rejected_active_replay_to_recovery(mocked_flow):
    f = mocked_flow
    f.owner.replay.side_effect = NativeParserConflict("NATIVE_PARSER_TERMINAL_PROOF_REJECTED")
    allow(f.owner.recover_expired, sentinel.recovery_receipt)
    assert f.runtime.execute(f.delivery) is None
    f.owner.recover_expired.assert_called_once_with(f.delivery, leases=None)
    f.owner._accept.assert_not_called()
    f.reads.assert_not_called()
    f.owner.cancel_unaccepted.assert_not_called()
    f.lease_factory.assert_not_called()
    assert_no_io(f)
    assert_no_failure_grant(f)


@pytest.mark.parametrize("refusal", [
    JobFenceRejectedError("NATIVE_PARSER_RECOVERY_BUSINESS_LEASE_STILL_LIVE"),
    JobFenceRejectedError("NATIVE_PARSER_RETRY_UNRESOLVED_IO"),
    NativeParserConflict("NATIVE_PARSER_ATTEMPT_JOURNAL_REJECTED"),
    NativeParserConflict("NATIVE_PARSER_ROOT_DEADLINE_EXPIRED"),
    RuntimeError("MOCK_RECOVERY_COMMIT_REPLY_LOST"),
])
def test_mock_unproved_recovery_never_accepts_or_reaps_or_retries(mocked_flow, refusal):
    f = mocked_flow
    original = NativeParserConflict("NATIVE_PARSER_TERMINAL_PROOF_REJECTED")
    f.owner.replay.side_effect = original
    f.owner.recover_expired.side_effect = refusal
    with pytest.raises(NativeParserConflict) as raised:
        f.runtime.execute(f.delivery)
    assert raised.value is original
    f.owner._accept.assert_not_called()
    assert_no_io(f)
    assert_no_failure_grant(f)


def test_mock_lost_recovery_reply_needs_complete_terminal_replay(mocked_flow):
    f = mocked_flow
    f.owner.replay.side_effect = [NativeParserConflict("NATIVE_PARSER_TERMINAL_PROOF_REJECTED"),
        {"terminal": True, "result": None}]
    f.owner.recover_expired.side_effect = RuntimeError("MOCK_RECOVERY_COMMIT_REPLY_LOST")
    assert f.runtime.execute(f.delivery) is None
    f.owner._accept.assert_not_called()
    assert_no_io(f)


def test_mock_same_claim_expiry_requires_recovery_without_new_acceptance_grant(mocked_flow):
    f = mocked_flow
    f.owner._accept.side_effect = JobFenceRejectedError("NATIVE_PARSER_DUAL_LEASE_NOT_OWNED")
    allow(f.owner.recover_expired, sentinel.recovery_receipt)
    assert f.runtime.execute(f.delivery) is None
    f.owner.recover_expired.assert_called_once_with(f.delivery, leases=None)
    f.reads.assert_not_called()
    assert_no_io(f)


@pytest.mark.parametrize("original", [None, RuntimeError("MOCK_UNRESOLVED_IO"), NativeIOUnreaped("MOCK_UNREAPED")])
def test_mock_final_pause_failure_cannot_report_success_or_replace_pending_error(mocked_flow, original):
    f = mocked_flow
    pause_error = NativeParserConflict("NATIVE_PARSER_HEARTBEAT_NOT_JOINED")
    f.leases.pause.side_effect = pause_error
    if original is not None:
        f.io.read_and_parse.side_effect = original
    expected = pause_error if original is None else original
    with pytest.raises(type(expected)) as raised:
        f.runtime.execute(f.delivery)
    assert raised.value is expected
    f.leases.pause.assert_called_once()


def test_mock_raw_error_text_is_not_logged_or_sent_as_a_settlement_detail(mocked_flow, caplog):
    f = mocked_flow
    original = RuntimeError("MOCK_PRIVATE_INPUT_AND_CREDENTIAL_MUST_NOT_BE_PERSISTED")
    f.io.read_and_parse.side_effect = original
    with pytest.raises(RuntimeError) as raised:
        f.runtime.execute(f.delivery)
    assert raised.value is original
    assert not caplog.records
    f.owner.settle_unavailable.assert_not_called()
    assert_no_failure_grant(f)
