"""Both SQL timeouts share the same freshly measured remaining budget."""
from datetime import UTC, datetime, timedelta

import pytest

from modules.listing.application.native_intake_retrieval import NativeIntakeRetrievalOwner
from modules.listing.application.native_intake_retrieval_policy import NativeIntakeRetrievalConflict
from modules.listing.application.native_intake_source_policy import (
    NativeIntakeSourcePolicyConflict,
    NativeIntakeSourcePolicyOwner,
)


class RecordingEngine:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def query_one(self, sql, params=()):
        self.calls.append((sql, params))
        if sql == "SELECT clock_timestamp() AS now":
            return {"now": datetime.now(UTC)}
        if self.error is not None:
            raise self.error
        return {}


@pytest.fixture(params=[
    (NativeIntakeSourcePolicyOwner, NativeIntakeSourcePolicyConflict, "statement_timeout", "lock_timeout"),
    (NativeIntakeRetrievalOwner, NativeIntakeRetrievalConflict, "lock_timeout", "statement_timeout"),
])
def owner_case(request):
    owner_type, conflict_type, first, second = request.param
    owner = object.__new__(owner_type)
    owner.engine = RecordingEngine()
    return owner, conflict_type, first, second


def test_both_timeouts_are_set_once_using_the_same_remaining_value(owner_case):
    owner, _, first, second = owner_case
    owner._admission_budget(datetime.now(UTC) + timedelta(milliseconds=750))
    assert len(owner.engine.calls) == 2
    sql, values = owner.engine.calls[1]
    assert sql == f"SELECT set_config('{first}',?,true), set_config('{second}',?,true)"
    assert len(values) == 2 and values[0] == values[1]
    assert values[0].endswith("ms") and 1 <= int(values[0][:-2]) < 750


def test_each_boundary_still_reads_the_current_database_clock(owner_case):
    owner, _, _, _ = owner_case
    bound = datetime.now(UTC) + timedelta(milliseconds=750)
    owner._admission_budget(bound)
    owner._admission_budget(bound)
    assert len(owner.engine.calls) == 4
    assert [sql for sql, _ in owner.engine.calls[::2]] == ["SELECT clock_timestamp() AS now"] * 2


def test_expired_bound_rejects_before_any_timeout_setting(owner_case):
    owner, conflict_type, _, _ = owner_case
    with pytest.raises(conflict_type, match="ADMISSION_DEADLINE_EXPIRED"):
        owner._admission_budget(datetime.now(UTC) - timedelta(seconds=1))
    assert [sql for sql, _ in owner.engine.calls] == ["SELECT clock_timestamp() AS now"]


def test_timeout_setting_exception_identity_is_preserved(owner_case):
    owner, _, _, _ = owner_case
    original = RuntimeError("setting failed")
    owner.engine.error = original
    with pytest.raises(RuntimeError) as caught:
        owner._admission_budget(datetime.now(UTC) + timedelta(milliseconds=750))
    assert caught.value is original
