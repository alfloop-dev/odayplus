"""Local lease-bound guards only; these tests establish no SQL/IO authority."""
import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from modules.external_data.application.native_intake_process import NativeIORejected
from modules.listing.application.native_intake_parser_leases import (
    NativeParserCompositeLeases,
    NativeParserLeaseTimeBound,
)
from modules.listing.application.native_intake_parser_policy import (
    JOB_TYPE,
    POLICY_ENV,
    REALM_ENV,
    SIGNING_KEY_ENV,
    STORAGE_ENV,
    NativeParserAuthenticator,
    NativeParserConflict,
    native_intake_parser_policy,
)
from shared.jobs.queue import JobFenceRejectedError, JobRecord
from tests.security.test_native_intake_parser_policy import TENANT
from tests.security.test_native_intake_parser_policy import configured as configured


@pytest.fixture
def local_controller(configured):
    owner = SimpleNamespace(graph=object(), engine=object(), queue=object(),
        auth=NativeParserAuthenticator(), artifact_registry=object())
    job_id = str(uuid4())
    item = JobRecord(JOB_TYPE, {"job_id": job_id, "tenant_id": TENANT,
        "attempt_token": owner.auth.sign(job_id, TENANT, 1)}, str(uuid4()), job_id=job_id)
    policy = native_intake_parser_policy(TENANT)
    return NativeParserCompositeLeases(owner, item, business_fence=2, policy_sha256=policy.sha256)


def recorded_bound(controller, *, seconds=30):
    # Synthetic time-bound record for guard unit controls, not a mocked SQL
    # success, accepted attempt, current human/source grant or actual IO.
    at = datetime.now(UTC) + timedelta(seconds=seconds)
    controller._proof = NativeParserLeaseTimeBound(2, 3, at, at, at, at, time.monotonic() + seconds)
    controller._stop.clear()


def test_current_key_and_policy_alone_cannot_start_io_before_committed_renewal(local_controller):
    with pytest.raises(NativeIORejected, match="^NATIVE_IO_PARSER_DUAL_LEASE_LOST$"):
        local_controller.guard()


@pytest.mark.parametrize("loss", ["expired", "stopped", "renewal_failed", "no_bound"])
def test_time_bound_or_controller_failure_always_closes_the_guard(local_controller, loss):
    recorded_bound(local_controller, seconds=-1 if loss == "expired" else 30)
    if loss == "stopped":
        local_controller._stop.set()
    elif loss == "renewal_failed":
        local_controller._failure = RuntimeError("Synthetic unknown renewal outcome")
    elif loss == "no_bound":
        local_controller._proof = None
    with pytest.raises(NativeIORejected, match="^NATIVE_IO_PARSER_DUAL_LEASE_LOST$"):
        local_controller.guard()


@pytest.mark.parametrize("loss", [POLICY_ENV, STORAGE_ENV, REALM_ENV, SIGNING_KEY_ENV,
    "changed_key", "changed_policy", "registry"])
def test_current_purpose_configuration_loss_is_observed_during_a_live_time_bound(local_controller, configured, monkeypatch, loss):
    recorded_bound(local_controller)
    local_controller.guard()
    if loss == "changed_key":
        monkeypatch.setenv(SIGNING_KEY_ENV, "different-current-parser-key-" * 2)
    elif loss == "changed_policy":
        monkeypatch.setenv(POLICY_ENV, json.dumps({**configured[POLICY_ENV], "version": 2}))
    elif loss == "registry":
        local_controller.owner.artifact_registry = None
    else:
        monkeypatch.delenv(loss)
    with pytest.raises(NativeIORejected, match="^NATIVE_IO_PARSER_CURRENT_CONFIG_LOST$"):
        local_controller.guard()


@pytest.mark.parametrize("value", [True, False, 0, -1, 10.1, float("nan"), float("inf"), "3"])
def test_io_deadline_cannot_expand_the_original_process_cap(local_controller, value):
    recorded_bound(local_controller)
    with pytest.raises(NativeIORejected, match="^NATIVE_IO_PARSER_DEADLINE_REJECTED$"):
        local_controller.io_deadline(maximum_seconds=value)


def test_actual_monotonic_child_bound_is_capped_and_pause_removes_the_old_record(local_controller):
    recorded_bound(local_controller)
    start = time.monotonic()
    bound = local_controller.io_deadline(maximum_seconds=3)
    end = time.monotonic()
    assert start < bound <= end + 3 and bound < local_controller._proof.monotonic_io_bound
    local_controller.pause()
    assert local_controller._proof is None
    with pytest.raises(NativeIORejected):
        local_controller.guard()


def test_unknown_start_renewal_outcome_never_opens_the_guard(local_controller, monkeypatch):
    def unknown():
        # Even an accidentally left local record cannot bypass the stopped
        # controller when the actual SQL renewal has no committed result.
        recorded_bound(local_controller)
        local_controller._stop.set()
        raise RuntimeError("Synthetic renewal commit response lost")

    monkeypatch.setattr(local_controller, "_renew", unknown)
    with pytest.raises(RuntimeError, match="Synthetic renewal commit response lost"):
        local_controller.start()
    assert local_controller._thread is None
    with pytest.raises(NativeIORejected):
        local_controller.guard()


def test_a_heartbeat_that_cannot_join_retains_its_handle_and_blocks_sql_cleanup(local_controller):
    joins = []
    thread = SimpleNamespace(ident=1, join=lambda timeout: joins.append(timeout), is_alive=lambda: True)
    local_controller._thread = thread
    recorded_bound(local_controller)
    with pytest.raises(NativeParserConflict, match="^NATIVE_PARSER_HEARTBEAT_NOT_JOINED$"):
        local_controller.pause()
    assert local_controller._thread is thread and joins == [1]
    with pytest.raises(NativeIORejected):
        local_controller.guard()


@pytest.mark.parametrize("fence,policy", [(True, "a" * 64), (0, "a" * 64), (1, "a" * 64),
    (2**63, "a" * 64), (2, "z" * 64), (2, "a" * 63)])
def test_constructor_refuses_unbounded_or_untyped_attempt_binding(local_controller, fence, policy):
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_LEASE_BINDING_REQUIRED$"):
        NativeParserCompositeLeases(local_controller.owner, local_controller.delivery,
            business_fence=fence, policy_sha256=policy)
