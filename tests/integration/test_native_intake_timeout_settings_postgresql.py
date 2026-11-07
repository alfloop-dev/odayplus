"""Real SQL timeout side effects still consume the original 750ms root."""
from datetime import timedelta

import pytest

from modules.listing.application.native_intake_pending_commands import NativeIntakeCommandBudget
from modules.listing.application.native_intake_retrieval import NativeIntakeRetrievalOwner
from modules.listing.application.native_intake_retrieval_policy import NativeIntakeRetrievalConflict
from modules.listing.application.native_intake_source_policy import (
    NativeIntakeSourcePolicyConflict,
    NativeIntakeSourcePolicyOwner,
)
from shared.infrastructure.persistence.postgresql import PostgresEngine

pytestmark = pytest.mark.requires_live_env


@pytest.mark.parametrize("owner_type", [NativeIntakeSourcePolicyOwner, NativeIntakeRetrievalOwner])
def test_actual_both_timeouts_decrease_under_one_unchanged_root(intake_blank_db, owner_type, record_property):
    engine = PostgresEngine(intake_blank_db.url(), bootstrap=False, validate_schema=False)
    owner = object.__new__(owner_type)
    owner.engine = engine

    def settings():
        return engine.query_one("SELECT current_setting('statement_timeout') AS statement, "
            "current_setting('lock_timeout') AS lock")

    try:
        with engine.transaction():
            baseline = settings()
        with engine.transaction():
            engine.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            before = NativeIntakeCommandBudget(owner)
            deadline, monotonic_deadline = before.deadline, before._monotonic_deadline
            before()
            first_settings = settings()
            assert first_settings["statement"] == first_settings["lock"]
            first = first_settings["statement"]
            assert first.endswith("ms") and 1 <= int(first[:-2]) < 750
            engine.query_one("SELECT pg_sleep(0.015)")
            before()
            second_settings = settings()
            assert second_settings["statement"] == second_settings["lock"]
            second = second_settings["statement"]
            assert second.endswith("ms") and 1 <= int(second[:-2]) < int(first[:-2])
            assert (before.deadline, before._monotonic_deadline) == (deadline, monotonic_deadline)
            assert engine.query_one("SHOW transaction_isolation")["transaction_isolation"] == "serializable"
        with engine.transaction():
            assert settings() == baseline, "SET LOCAL must not escape its original transaction"
        record_property("first_both_timeouts", first)
        record_property("second_both_timeouts", second)
        record_property("original_root_deadlines_unchanged", "true")
    finally:
        engine.close()


@pytest.mark.parametrize("owner_type,conflict_type", [
    (NativeIntakeSourcePolicyOwner, NativeIntakeSourcePolicyConflict),
    (NativeIntakeRetrievalOwner, NativeIntakeRetrievalConflict)])
def test_actual_expired_sql_bound_preserves_both_local_timeouts_and_original_deadlines(
        intake_blank_db, owner_type, conflict_type):
    engine = PostgresEngine(intake_blank_db.url(), bootstrap=False, validate_schema=False)
    owner = object.__new__(owner_type)
    owner.engine = engine

    def settings():
        return engine.query_one("SELECT current_setting('statement_timeout') AS statement, "
            "current_setting('lock_timeout') AS lock")

    try:
        with engine.transaction():
            baseline = settings()
        with engine.transaction():
            engine.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            before = NativeIntakeCommandBudget(owner)
            original = before.deadline, before._monotonic_deadline
            before()
            current = settings()
            assert current["statement"] == current["lock"]
            assert 1 <= int(current["statement"][:-2]) < 750
            with pytest.raises(conflict_type, match="ADMISSION_DEADLINE_EXPIRED"):
                owner._admission_budget(owner._clock() - timedelta(seconds=1))
            assert settings() == current
            assert (before.deadline, before._monotonic_deadline) == original
        with engine.transaction():
            assert settings() == baseline
    finally:
        engine.close()
