"""Both SQL timeouts share the same freshly measured remaining budget."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from modules.listing.application.native_intake_retrieval import NativeIntakeRetrievalOwner
from modules.listing.application.native_intake_retrieval_policy import NativeIntakeRetrievalConflict
from modules.listing.application.native_intake_source_policy import (
    NativeIntakeSourcePolicyConflict,
    NativeIntakeSourcePolicyOwner,
    NativeIntakeSourcePolicyUnavailable,
)


class RecordingEngine:
    """Canned driver results only; actual clock/GUC effects have SQL tests."""
    dialect = "postgresql"

    def __init__(self, error=None):
        self.calls = []
        self.error = error
        self._local = SimpleNamespace(connection=object())
        self.remaining_ms, self.settings = 749, []

    def query_one(self, sql, params=()):
        self.calls.append((sql, params))
        if sql == "SELECT clock_timestamp() AS now":
            return {"now": datetime.now(UTC)}
        if self.error is not None:
            raise self.error
        if sql.startswith("WITH observed AS MATERIALIZED"):
            assert "clock_timestamp()" in sql
            assert "set_config('statement_timeout'" in sql and "set_config('lock_timeout'" in sql
            value = str(self.remaining_ms) + "ms" if self.remaining_ms >= 1 else None
            if value is not None:
                self.settings.append((value, value))
            return {"remaining_ms": self.remaining_ms, "statement_timeout": value, "lock_timeout": value}
        self.settings.append(params)
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
    bound = datetime.now(UTC) + timedelta(milliseconds=750)
    owner._admission_budget(bound)
    if isinstance(owner, NativeIntakeSourcePolicyOwner):
        assert len(owner.engine.calls) == 1
        assert owner.engine.calls[0][1] == (bound.isoformat(),)
    else:
        assert len(owner.engine.calls) == 2
        assert owner.engine.calls[1][0] == f"SELECT set_config('{first}',?,true), set_config('{second}',?,true)"
    values, = owner.engine.settings
    assert len(values) == 2 and values[0] == values[1]
    assert values[0].endswith("ms") and 1 <= int(values[0][:-2]) < 750


def test_each_boundary_still_reads_the_current_database_clock(owner_case):
    owner, _, _, _ = owner_case
    bound = datetime.now(UTC) + timedelta(milliseconds=750)
    owner._admission_budget(bound)
    owner._admission_budget(bound)
    if isinstance(owner, NativeIntakeSourcePolicyOwner):
        assert len(owner.engine.calls) == 2
        assert all("SELECT clock_timestamp() AS observed_at" in sql for sql, _ in owner.engine.calls)
        assert all(params == (bound.isoformat(),) for _, params in owner.engine.calls)
    else:
        assert len(owner.engine.calls) == 4
        assert [sql for sql, _ in owner.engine.calls[::2]] == ["SELECT clock_timestamp() AS now"] * 2


def test_expired_bound_rejects_before_any_timeout_setting(owner_case):
    owner, conflict_type, _, _ = owner_case
    owner.engine.remaining_ms = -1
    with pytest.raises(conflict_type, match="ADMISSION_DEADLINE_EXPIRED"):
        owner._admission_budget(datetime.now(UTC) - timedelta(seconds=1))
    assert len(owner.engine.calls) == 1 and owner.engine.settings == []
    if not isinstance(owner, NativeIntakeSourcePolicyOwner):
        assert owner.engine.calls[0][0] == "SELECT clock_timestamp() AS now"


def test_timeout_setting_exception_identity_is_preserved(owner_case):
    owner, _, _, _ = owner_case
    original = RuntimeError("setting failed")
    owner.engine.error = original
    with pytest.raises(RuntimeError) as caught:
        owner._admission_budget(datetime.now(UTC) + timedelta(milliseconds=750))
    assert caught.value is original


@pytest.mark.parametrize("change", ["non_postgres", "no_local", "no_connection"])
def test_source_policy_budget_requires_owned_root_before_any_pool_query(change):
    owner = object.__new__(NativeIntakeSourcePolicyOwner)
    owner.engine = RecordingEngine()
    if change == "non_postgres":
        owner.engine.dialect = "sqlite"
    elif change == "no_local":
        owner.engine._local = None
    else:
        owner.engine._local.connection = None
    with pytest.raises(NativeIntakeSourcePolicyUnavailable, match="^NATIVE_SOURCE_POLICY_CURRENT_SQL_ROOT_REQUIRED$"):
        owner._admission_budget(datetime.now(UTC) + timedelta(milliseconds=750))
    assert owner.engine.calls == owner.engine.settings == []
