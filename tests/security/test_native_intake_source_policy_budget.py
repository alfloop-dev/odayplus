"""Pure deadline arithmetic; no SQL producer, root or authority proof.

The simulated clocks exercise charging an already-spent interval and guard
latency. Actual SQL/owner controls belong to the genuine integration fixtures.
"""
from datetime import UTC, datetime, timedelta

import pytest

from modules.listing.application import native_intake_source_policy_budget as budgets
from modules.listing.application.native_intake_source_policy import NativeIntakeSourcePolicyConflict

EXPIRED = "^NATIVE_SOURCE_POLICY_ADMISSION_DEADLINE_EXPIRED$"
EPOCH = datetime(2026, 10, 18, tzinfo=UTC)


class Timer:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now


class ObservedClock:
    def __init__(self, timer, observed, *, query_delay=0.0, guard_delay=0.0):
        self.timer, self.observed = timer, observed
        self.query_delay, self.guard_delay = query_delay, guard_delay
        self.guarded = []
        self.clock_calls = 0

    def _clock(self):
        self.clock_calls += 1
        self.timer.now += self.query_delay
        return self.observed

    def _admission_budget(self, deadline):
        self.guarded.append(deadline)
        self.timer.now += self.guard_delay


def _timer(monkeypatch):
    timer = Timer()
    monkeypatch.setattr(budgets.time, "monotonic", timer.monotonic)
    return timer


def test_adopting_spent_root_never_starts_another_750ms(monkeypatch):
    timer = _timer(monkeypatch)
    deadline = EPOCH + timedelta(milliseconds=750)
    source = ObservedClock(timer, EPOCH + timedelta(milliseconds=400), query_delay=0.1)
    before = budgets.NativeSourcePolicyAdmissionBudget(source, sql_deadline=deadline)
    # Only250ms remains after the100ms clock query, not a fresh750ms.
    timer.now += 0.251
    with pytest.raises(NativeIntakeSourcePolicyConflict, match=EXPIRED):
        before()
    assert before.deadline == deadline and source.guarded == []


def test_live_callbacks_always_pass_the_same_original_SQL_deadline(monkeypatch):
    timer = _timer(monkeypatch)
    deadline = EPOCH + timedelta(milliseconds=750)
    source = ObservedClock(timer, EPOCH + timedelta(milliseconds=400), query_delay=0.1)
    before = budgets.NativeSourcePolicyAdmissionBudget(source, sql_deadline=deadline)
    timer.now += 0.05
    before()
    timer.now += 0.05
    before()
    assert source.guarded == [deadline, deadline]
    assert before.deadline == deadline and source.clock_calls == 1


def test_expiry_during_the_actual_guard_cannot_return_a_live_callback(monkeypatch):
    timer = _timer(monkeypatch)
    deadline = EPOCH + timedelta(milliseconds=750)
    source = ObservedClock(timer, EPOCH + timedelta(milliseconds=500),
                           query_delay=0.1, guard_delay=0.2)
    before = budgets.NativeSourcePolicyAdmissionBudget(source, sql_deadline=deadline)
    with pytest.raises(NativeIntakeSourcePolicyConflict, match=EXPIRED):
        before()
    assert source.guarded == [deadline]


def test_clock_observation_time_is_charged_before_any_fact_can_be_read(monkeypatch):
    timer = _timer(monkeypatch)
    source = ObservedClock(timer, EPOCH + timedelta(milliseconds=400), query_delay=0.4)
    with pytest.raises(NativeIntakeSourcePolicyConflict, match=EXPIRED):
        budgets.NativeSourcePolicyAdmissionBudget(source, sql_deadline=EPOCH + timedelta(milliseconds=750))
    assert source.guarded == []


@pytest.mark.parametrize("remaining", [0, -0.001, -5])
def test_expired_existing_SQL_deadline_is_not_replaced(monkeypatch, remaining):
    timer = _timer(monkeypatch)
    source = ObservedClock(timer, EPOCH)
    with pytest.raises(NativeIntakeSourcePolicyConflict, match=EXPIRED):
        budgets.NativeSourcePolicyAdmissionBudget(source, sql_deadline=EPOCH + timedelta(seconds=remaining))
    assert source.guarded == []


@pytest.mark.parametrize("remaining", [0.751, 40, 45])
def test_a_business_lease_cannot_be_substituted_for_the_admission_root(monkeypatch, remaining):
    timer = _timer(monkeypatch)
    source = ObservedClock(timer, EPOCH)
    with pytest.raises(NativeIntakeSourcePolicyConflict, match="^NATIVE_SOURCE_POLICY_DEADLINE_NOT_ADMISSION_ROOT$"):
        budgets.NativeSourcePolicyAdmissionBudget(source, sql_deadline=EPOCH + timedelta(seconds=remaining))
    assert source.guarded == []


@pytest.mark.parametrize("deadline", [None, "2026-10-18T00:00:00Z", True, EPOCH.replace(tzinfo=None)])
def test_only_an_explicit_timezone_aware_existing_deadline_is_accepted(monkeypatch, deadline):
    timer = _timer(monkeypatch)
    source = ObservedClock(timer, EPOCH)
    with pytest.raises(ValueError, match="^An existing timezone-aware source-policy SQL root deadline is required$"):
        budgets.NativeSourcePolicyAdmissionBudget(source, sql_deadline=deadline)
    assert source.clock_calls == 0 and source.guarded == []


def test_actual_clock_errors_are_not_relabelled_as_successful_observations(monkeypatch):
    _timer(monkeypatch)
    error = RuntimeError("Synthetic unavailable SQL clock")

    class UnavailableClock:
        def _clock(self):
            raise error

    with pytest.raises(RuntimeError) as caught:
        budgets.NativeSourcePolicyAdmissionBudget(UnavailableClock(), sql_deadline=EPOCH)
    assert caught.value is error
