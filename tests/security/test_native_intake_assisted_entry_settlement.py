"""Settlement orchestration only; current SQL ownership is covered by PG cases."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from modules.external_data.application.native_intake_process import (
    NativeIORejected,
    NativeIOUnreaped,
)
from modules.listing.application import native_intake_assisted_entry_runtime as runtime_module
from modules.listing.application.native_intake_assisted_entry_execution import (
    NativeAssistedExecutionReads,
)
from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryUnavailable,
)
from modules.listing.application.native_intake_assisted_entry_runtime import (
    NativeIntakeAssistedEntryRuntime,
)


class Leases:
    business_fence = 17

    @contextmanager
    def root(self):
        yield


@pytest.fixture
def settlement_case(monkeypatch):
    owner = SimpleNamespace(graph=None, engine=None, queue=None,
        delivery_identity=lambda _: ("3fc06c20-5718-41b8-bb17-39f433a8e2c1", 1))
    runtime, calls = NativeIntakeAssistedEntryRuntime(owner), []

    def negative(delivery, error, *, business_fence, leases):
        calls.append((delivery, error, business_fence, leases))
        return "owned-negative-attempted"

    runtime.settlement = SimpleNamespace(replay=lambda *_, **__: {"terminal": False, "result": None}, negative=negative)
    # Simulate a positive read that happened BEFORE a later authority change.
    # This is deliberately not evidence of current SQL permission or ownership.
    monkeypatch.setattr(NativeAssistedExecutionReads, "retry_context", lambda *_, **__: SimpleNamespace(
        job={"status": "RUNNING", "attempt": 1, "max_attempts": 3},
        physical=SimpleNamespace(attempts=1, max_retries=3)))

    def no_retry(*_, **__):
        raise AssertionError("A retired cleanup authority must not use the earlier retry context")

    monkeypatch.setattr(runtime_module, "prepare_assisted_retry", no_retry)
    return runtime, calls, Leases(), object()


@pytest.mark.parametrize("error", [NativeAssistedEntryUnavailable("SYNTHETIC_CONFIG_RETIRED"),
    PermissionError("SYNTHETIC_ACCOUNT_DISABLED"), HTTPException(401), HTTPException(403)])
def test_authority_lost_at_cleanup_attempts_only_fresh_fenced_negative_settlement(settlement_case, error):
    runtime, calls, leases, delivery = settlement_case

    def cleanup():
        raise error

    result = runtime._settle(delivery, leases, NativeIORejected("SYNTHETIC_TEMPORARY_IO_FAILURE", status=503),
        io=SimpleNamespace(terminal_attempted=False, cleanup_known_unreferenced=cleanup))
    assert result == "owned-negative-attempted" and calls == [(delivery, error, leases.business_fence, leases)]


@pytest.mark.parametrize("error", [HTTPException(503), NativeIOUnreaped("SYNTHETIC_UNREAPED")])
def test_cleanup_outage_or_unreaped_child_cannot_be_claimed_as_closed_work(settlement_case, error):
    runtime, calls, leases, delivery = settlement_case

    def cleanup():
        raise error

    with pytest.raises(type(error)) as raised:
        runtime._settle(delivery, leases, NativeIORejected("SYNTHETIC_TEMPORARY_IO_FAILURE", status=503),
            io=SimpleNamespace(terminal_attempted=False, cleanup_known_unreferenced=cleanup))
    assert raised.value is error and calls == []
