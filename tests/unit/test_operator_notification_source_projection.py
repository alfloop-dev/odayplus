"""Pure projection controls: real owner timestamps, no fabricated health/clock data."""
from datetime import UTC, datetime
from types import SimpleNamespace

from modules.forecastops.domain.forecasting import Alert, AlertLevel
from modules.opsboard.application.operator_live_repository import OperatorLiveRepository
from modules.opsboard.application.store_ops_materializer import project_store_ops_state


def test_alert_notification_preserves_actual_owner_timestamp_and_closed_state() -> None:
    opened = datetime(2026, 10, 15, 8, tzinfo=UTC)
    projector = OperatorLiveRepository(SimpleNamespace(mode="memory"))
    alert = Alert(
        alert_id="actual-owner-alert", tenant_id="tenant-a", store_id="store-a",
        alert_level=AlertLevel.RED, alert_reason_code="actual-owner-reason",
        evidence_json={}, opened_at=opened, policy_id="owner-policy", policy_version="owner-v1",
    )
    state = project_store_ops_state(
        stores=[{"store_id": "store-a", "store_name": "Source store", "tenant_id": "tenant-a"}],
        alerts_by_store={"store-a": [alert]}, existing_state=None,
    )
    rows = projector._alert_notifications([alert], state["issues"])
    assert len(rows) == 1
    assert rows[0]["category"] == "alert"
    assert rows[0]["occurredAt"] == opened.isoformat()
    assert rows[0]["target"]["entityId"] == state["issues"][0]["id"]
    assert rows[0]["target"]["entityId"] != alert.alert_id
    assert rows[0]["target"]["tab"] == "forecastOps"
    assert rows[0]["sourceDetail"]["state"] == "available"
    issue = state["issues"][0]
    for candidates in ([], [{**issue, "storeId": "other-store"}],
                       [issue, {**issue, "id": "second-ambiguous-issue"}]):
        unresolved = projector._alert_notifications([alert], candidates)[0]
        assert unresolved["sourceDetail"]["state"] == "unavailable"
        assert "entityId" not in unresolved["target"]
        assert unresolved["target"]["workspace"] == "store"
        assert unresolved["sourceEntity"]["id"] == alert.alert_id
    assert alert.opened_at == opened
    assert projector._alert_notifications([Alert(**{**alert.__dict__, "status": "closed"})]) == []


def test_ingestion_notification_uses_only_supplied_completion_time_and_no_fake_categories() -> None:
    projector = OperatorLiveRepository(SimpleNamespace(mode="memory"))
    completed = datetime(2026, 10, 15, 9, tzinfo=UTC)
    # The live repository accepts persisted owner mappings as well as typed records.
    record = {"run_id": "actual-run", "status": "failed", "completed_at": completed, "message": "source failure"}
    rows = projector._ingestion_notifications([record])
    assert rows[0]["category"] == "job"
    assert rows[0]["occurredAt"] == completed.isoformat()
    assert rows[0]["sourceEntity"]["id"] == "actual-run"
    assert rows[0]["sourceDetail"] == {
        "state": "unavailable", "reasonCode": "INGESTION_RUN_DETAIL_NOT_IMPLEMENTED",
    }
    assert rows[0]["target"] == {"workspace": "govern"}
    missing = projector._ingestion_notifications([{**record, "completed_at": None}])
    assert missing[0]["occurredAt"] is None
    assert projector._ingestion_notifications([{**record, "status": "success"}]) == []
    assert record["completed_at"] == completed
