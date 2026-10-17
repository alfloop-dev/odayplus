"""Elapsed native Growth window controls; synthetic local unit data only."""
from datetime import UTC, datetime, timedelta

import pytest

from modules.opsboard.application import growth


@pytest.fixture
def clock(monkeypatch):
    value = [datetime(2026, 10, 1, tzinfo=UTC)]
    monkeypatch.setattr(growth, "_now_iso", lambda: value[0].isoformat())
    return value


def _executed(clock, days=14):
    service = growth.GrowthService()
    action = service._find_action("growth-7005")
    action["status"] = "APPROVED"  # Explicit synthetic unit prerequisite only.
    action["observationWindowDays"] = days
    service.transition_action(action_id=action["id"], target_status="EXECUTED")
    service.transition_action(action_id=action["id"], target_status="OBSERVING")
    return service, action


@pytest.mark.parametrize("days", [1, 14, 365])
def test_execution_records_and_freezes_actual_server_window(clock, days):
    service, action = _executed(clock, days)
    assert action["executedAt"] == clock[0].isoformat()
    assert action["observationStartedAt"] == clock[0].isoformat()
    assert action["observationEndsAt"] == (clock[0] + timedelta(days=days)).isoformat()
    assert action["observationWindowFrozenDays"] == days
    assert service.get_action(action["id"])["observation"]["status"] == "OBSERVING"
    action["observationWindowDays"] = 1 if days != 1 else 365
    assert service.get_action(action["id"])["observation"]["days"] == days


@pytest.mark.parametrize("days", [0, 366, True, "14", None])
def test_invalid_window_cannot_record_execution_or_mutate_state(clock, days):
    service = growth.GrowthService()
    action = service._find_action("growth-7005")
    action.update(status="APPROVED", observationWindowDays=days)
    before = service.export_state()
    with pytest.raises(growth.GrowthPolicyError, match="VALID_OBSERVATION_WINDOW_REQUIRED"):
        service.transition_action(action_id=action["id"], target_status="EXECUTED")
    assert service.export_state() == before


def test_ready_rejects_until_exact_recorded_end_without_business_writes(clock):
    service, action = _executed(clock)
    clock[0] += timedelta(days=14) - timedelta(microseconds=1)
    before = service.export_state()
    with pytest.raises(growth.GrowthConflict, match="OBSERVATION_WINDOW_NOT_MATURE"):
        service.transition_action(action_id=action["id"], target_status="OUTCOME_READY")
    assert service.export_state() == before
    clock[0] += timedelta(microseconds=1)
    result = service.transition_action(action_id=action["id"], target_status="OUTCOME_READY")
    assert result["status"] == "OUTCOME_READY"
    assert service.get_action(action["id"])["observation"]["status"] == "MATURE"


@pytest.mark.parametrize("damage", ["missing", "naive", "bad_end", "future"])
def test_forged_ready_and_legacy_missing_window_cannot_publish_effectiveness(clock, damage):
    service, action = _executed(clock)
    action.update(status="OUTCOME_READY", observedLift=3.0, evidenceLevel="L3")
    if damage == "missing":
        action.pop("observationStartedAt")
    elif damage == "naive":
        action["observationStartedAt"] = "2026-10-01T00:00:00"
    elif damage == "bad_end":
        action["observationEndsAt"] = (clock[0] + timedelta(days=1)).isoformat()
    else:
        action["observationStartedAt"] = (clock[0] + timedelta(days=1)).isoformat()
        action["observationEndsAt"] = (clock[0] + timedelta(days=15)).isoformat()
    before = service.export_state()
    row = service.get_action(action["id"])
    assert row["observation"]["status"] == "UNAVAILABLE"
    assert row["closeoutGate"]["outcome"] == "PENDING"
    assert not row["closeoutGate"]["canClose"]
    with pytest.raises(growth.GrowthConflict, match="OBSERVATION_WINDOW_NOT_MATURE"):
        service.write_outcome(action_id=action["id"], outcome="EFFECTIVE", required_action="CLOSE",
                              observed_lift=3.0, evidence_level="L3", rationale="Synthetic gate test")
    assert service.export_state() == before


def test_status_alone_does_not_make_recent_window_effective(clock):
    service, action = _executed(clock)
    action.update(status="OUTCOME_READY", observedLift=3.0, evidenceLevel="L3")
    assert service.get_action(action["id"])["closeoutGate"]["outcome"] == "PENDING"
    clock[0] += timedelta(days=14)
    assert service.get_action(action["id"])["closeoutGate"]["outcome"] == "EFFECTIVE"
