from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.forecastops import (
    FeedbackStatus,
    FeedbackType,
    ForecastFeedback,
    ForecastInput,
    ForecastOpsError,
    ForecastOpsService,
    InMemoryForecastOpsRepository,
    StoreDayObservation,
)
from shared.auth import Role
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.repositories import DurableForecastOpsRepository
from tests.integration._authz import (
    FORECASTOPS_HEADERS,
    auth_headers,
)

PREDICTION_TIME = datetime(2026, 6, 27, 9, 0, tzinfo=UTC)
TENANT_ID = "tenant-test"
DATA_OWNER_HEADERS = {
    **auth_headers(Role.DATA_OWNER),
    "x-tenant-id": TENANT_ID,
}


def _create_sample_observations(
    store_id: str = "store-001",
    base_revenue: float = 100_000.0,
    days: int = 14,
) -> tuple[StoreDayObservation, ...]:
    return tuple(
        StoreDayObservation(
            store_id=store_id,
            business_date=date(2026, 6, day + 1),
            actual_revenue=base_revenue + day * 1_000.0,
            machine_cycles=int(base_revenue / 100),
            site_score_baseline_p50=100_000.0,
            source_snapshot_ids=(f"pos-202606{day + 1:02d}",),
        )
        for day in range(days)
    )


def test_context_annotation_feedback_auto_accepted_and_filters_training_and_precision() -> None:
    """ODP-FR-FCT-008 Path 1: CONTEXT_ANNOTATION.

    - Automatically accepted
    - Does not modify forecast values directly (ODP-BR-GOV-001)
    - Serves as exclusion interval for training dataset and precision calculations.
    """
    repository = InMemoryForecastOpsRepository()
    service = ForecastOpsService(repository=repository)
    observations = _create_sample_observations(days=14)

    # 1. Ingest timeseries and create initial forecast
    service.ingest_timeseries(observations, tenant_id=TENANT_ID)
    forecast_result = service.forecast(
        [
            ForecastInput(
                tenant_id=TENANT_ID,
                store_id="store-001",
                observations=observations,
                prediction_origin_time=PREDICTION_TIME,
            )
        ],
        scored_at=PREDICTION_TIME,
    )
    initial_forecast = forecast_result.forecasts[0]
    initial_p50 = initial_forecast.p50

    # 2. Submit CONTEXT_ANNOTATION for renovation period June 5 to June 8
    feedback = service.submit_feedback(
        TENANT_ID,
        store_id="store-001",
        feedback_type=FeedbackType.CONTEXT_ANNOTATION,
        target_date_start="2026-06-05",
        target_date_end="2026-06-08",
        reason="Store remodeling and adjacent road construction",
        actor="ops-manager-alice",
    )

    # Verification 1: Feedback is auto-accepted
    assert feedback.status is FeedbackStatus.ACCEPTED
    assert feedback.feedback_type is FeedbackType.CONTEXT_ANNOTATION
    assert feedback.target_date_start == date(2026, 6, 5)
    assert feedback.target_date_end == date(2026, 6, 8)

    # Verification 2: Forecast values are NOT directly changed (ODP-BR-GOV-001)
    stored_forecast = repository.latest_forecasts(TENANT_ID)[0]
    assert stored_forecast.p50 == initial_p50

    # Verification 3: Training series excludes the 4 annotated days (June 5, 6, 7, 8)
    training_series = service.get_training_series(TENANT_ID, "store-001")
    assert training_series is not None
    assert len(training_series.observations) == 10  # 14 - 4 = 10
    excluded_dates = {obs.business_date for obs in training_series.observations}
    assert date(2026, 6, 5) not in excluded_dates
    assert date(2026, 6, 6) not in excluded_dates
    assert date(2026, 6, 7) not in excluded_dates
    assert date(2026, 6, 8) not in excluded_dates

    # Verification 4: Precision evaluation excludes the annotated days
    precision_eval = service.evaluate_precision(
        TENANT_ID, "store-001", initial_forecast.forecast_output_id
    )
    assert precision_eval["observation_count"] == 10
    assert precision_eval["excluded_observation_count"] == 4
    assert precision_eval["is_within_p10_p90"] is True


def test_outcome_correction_requires_data_owner_approval_and_recalculates_forecast() -> None:
    """ODP-FR-FCT-008 Path 2: OUTCOME_CORRECTION.

    - Initial submission is pending_approval
    - Requires Data Owner approval
    - Upon approval: updates canonical data and triggers recalculation/re-forecast
    - Does NOT directly overwrite forecast or decision fields.
    """
    repository = InMemoryForecastOpsRepository()
    service = ForecastOpsService(repository=repository)
    observations = _create_sample_observations(base_revenue=50_000.0, days=7)
    service.ingest_timeseries(observations, tenant_id=TENANT_ID)

    # Initial forecast
    initial_result = service.forecast(
        [
            ForecastInput(
                tenant_id=TENANT_ID,
                store_id="store-001",
                observations=observations,
                prediction_origin_time=PREDICTION_TIME,
            )
        ],
        scored_at=PREDICTION_TIME,
    )
    initial_forecast = initial_result.forecasts[0]
    assert initial_forecast.forecast_version == 1

    # 1. Submit OUTCOME_CORRECTION: actual revenue was erroneously recorded low
    feedback = service.submit_feedback(
        TENANT_ID,
        store_id="store-001",
        feedback_type=FeedbackType.OUTCOME_CORRECTION,
        target_date_start="2026-06-07",
        target_date_end="2026-06-07",
        corrected_revenue=150_000.0,
        reason="POS terminal missed credit card batch settlement",
        actor="ops-manager-bob",
    )

    # Verification 1: Pending approval
    assert feedback.status is FeedbackStatus.PENDING_APPROVAL
    assert feedback.corrected_revenue == 150_000.0

    # Verification 2: Canonical series not modified before approval
    series_before = repository.get_series(TENANT_ID, "store-001")
    june_7_obs = next(obs for obs in series_before.observations if obs.business_date == date(2026, 6, 7))
    assert june_7_obs.actual_revenue != 150_000.0

    # 2. Approve OUTCOME_CORRECTION by Data Owner
    approved_feedback, new_forecast = service.approve_outcome_correction(
        TENANT_ID,
        feedback.feedback_id,
        actor="data-owner-claire",
        note="Confirmed with merchant bank statement",
    )

    # Verification 3: Approved status & audit fields
    assert approved_feedback.status is FeedbackStatus.APPROVED
    assert approved_feedback.approved_by == "data-owner-claire"
    assert approved_feedback.approved_at is not None

    # Verification 4: Canonical data corrected
    series_after = repository.get_series(TENANT_ID, "store-001")
    corrected_obs = next(obs for obs in series_after.observations if obs.business_date == date(2026, 6, 7))
    assert corrected_obs.actual_revenue == 150_000.0

    # Verification 5: Recalculated forecast generated as version 2
    assert new_forecast is not None
    assert new_forecast.forecast_version == 2
    assert new_forecast.p50 > initial_forecast.p50


def test_outcome_correction_rejection_flow() -> None:
    """OUTCOME_CORRECTION rejection flow."""
    repository = InMemoryForecastOpsRepository()
    service = ForecastOpsService(repository=repository)
    observations = _create_sample_observations(days=7)
    service.ingest_timeseries(observations, tenant_id=TENANT_ID)

    feedback = service.submit_feedback(
        TENANT_ID,
        store_id="store-001",
        feedback_type=FeedbackType.OUTCOME_CORRECTION,
        target_date="2026-06-05",
        corrected_revenue=999_999.0,
        reason="Suspected typo test",
        actor="ops-user",
    )
    assert feedback.status is FeedbackStatus.PENDING_APPROVAL

    rejected = service.reject_outcome_correction(
        TENANT_ID,
        feedback.feedback_id,
        actor="data-owner-claire",
        rejection_reason="Unverified claim without bank slip",
    )
    assert rejected.status is FeedbackStatus.REJECTED
    assert rejected.rejection_reason == "Unverified claim without bank slip"


def test_alert_disposition_auto_accepted_and_closes_alert() -> None:
    """ODP-FR-FCT-008 Path 3: ALERT_DISPOSITION.

    - Automatically accepted
    - Writes Alert disposition
    - Closes the alert (status='closed', closed_at set)
    - Closed alert cannot be acknowledged subsequently.
    """
    repository = InMemoryForecastOpsRepository()
    service = ForecastOpsService(repository=repository)
    observations = tuple(
        StoreDayObservation(
            store_id="store-alert-001",
            business_date=date(2026, 6, day),
            actual_revenue=30_000.0,  # low revenue to trigger RED alert
            site_score_baseline_p50=100_000.0,
        )
        for day in range(20, 27)
    )

    # 1. Forecast generates RED alert
    result = service.forecast(
        [
            ForecastInput(
                tenant_id=TENANT_ID,
                store_id="store-alert-001",
                observations=observations,
                prediction_origin_time=PREDICTION_TIME,
            )
        ],
        scored_at=PREDICTION_TIME,
    )
    alert = result.alerts[0]
    assert alert.status == "open"
    assert alert.closed_at is None
    assert alert.disposition is None

    # 2. Submit ALERT_DISPOSITION feedback
    feedback = service.submit_feedback(
        TENANT_ID,
        store_id="store-alert-001",
        feedback_type=FeedbackType.ALERT_DISPOSITION,
        alert_id=alert.alert_id,
        disposition="false_alarm_sensor_glitch",
        reason="IoT sensor offline gave zero machine cycle readings erroneously",
        actor="ops-manager-dan",
    )

    # Verification 1: Feedback auto-accepted
    assert feedback.status is FeedbackStatus.ACCEPTED
    assert feedback.disposition == "false_alarm_sensor_glitch"

    # Verification 2: Alert is closed with disposition
    updated_alert = repository.get_alert(TENANT_ID, alert.alert_id)
    assert updated_alert is not None
    assert updated_alert.status == "closed"
    assert updated_alert.closed_at is not None
    assert updated_alert.disposition == "false_alarm_sensor_glitch"
    assert updated_alert.disposition_set_by == "ops-manager-dan"

    # Verification 3: Closed alert cannot be acknowledged
    with pytest.raises(ForecastOpsError, match="closed and cannot be acknowledged"):
        service.acknowledge_alert(
            TENANT_ID, alert.alert_id, actor="ops-manager-dan", note="late ack"
        )


def test_governance_rejection_of_direct_forecast_field_overrides() -> None:
    """ODP-BR-GOV-001 critical constraint:

    Feedback must NOT directly overwrite forecast values (p10, p50, p90, trajectory_class, etc.)
    or decision fields. Direct overwrite attempts are strictly rejected.
    """
    repository = InMemoryForecastOpsRepository()
    service = ForecastOpsService(repository=repository)

    with pytest.raises(ForecastOpsError, match="ODP-BR-GOV-001 violation"):
        service.submit_feedback(
            TENANT_ID,
            store_id="store-001",
            feedback_type=FeedbackType.CONTEXT_ANNOTATION,
            reason="Illegal attempt to directly force p50 and trajectory",
            actor="attacker",
            raw_payload={
                "store_id": "store-001",
                "feedback_type": "CONTEXT_ANNOTATION",
                "reason": "bad payload",
                "p50": 999999.0,
                "trajectory_class": "growing",
            },
        )


def test_api_feedback_endpoints_and_rbac() -> None:
    """Integration test for API feedback endpoints, RBAC permissions, and security audit."""
    app = create_app()
    client = TestClient(app, headers=FORECASTOPS_HEADERS)

    # 1. Submit CONTEXT_ANNOTATION via POST /forecastops/feedbacks (requires forecastops:write)
    ctx_payload = {
        "store_id": "store-api-101",
        "feedback_type": "CONTEXT_ANNOTATION",
        "target_date_start": "2026-06-10",
        "target_date_end": "2026-06-12",
        "reason": "Water pipe maintenance on street",
        "actor": "ops-user-1",
    }
    ctx_response = client.post("/forecastops/feedbacks", json=ctx_payload)
    assert ctx_response.status_code == 201
    ctx_body = ctx_response.json()
    assert ctx_body["feedback_type"] == "context_annotation"
    assert ctx_body["status"] == "accepted"
    ctx_id = ctx_body["feedback_id"]

    # 2. Anonymous caller is denied (403)
    anon_client = TestClient(app)
    anon_response = anon_client.post("/forecastops/feedbacks", json=ctx_payload)
    assert anon_response.status_code == 403

    # 3. Submit OUTCOME_CORRECTION via POST /forecastops/feedbacks
    cor_payload = {
        "store_id": "store-api-101",
        "feedback_type": "OUTCOME_CORRECTION",
        "target_date": "2026-06-11",
        "corrected_revenue": 120_000.0,
        "reason": "Manual cash register reconciliation",
        "actor": "ops-user-1",
    }
    cor_response = client.post("/forecastops/feedbacks", json=cor_payload)
    assert cor_response.status_code == 201
    cor_body = cor_response.json()
    assert cor_body["status"] == "pending_approval"
    cor_id = cor_body["feedback_id"]

    # 4. Non-Data-Owner cannot approve (OPERATIONS_MANAGER lacks data:approve)
    deny_approve = client.post(
        f"/forecastops/feedbacks/{cor_id}/approve",
        json={"actor": "ops-user-1", "note": "trying to approve my own"},
    )
    assert deny_approve.status_code == 403

    # 5. Data Owner approves OUTCOME_CORRECTION
    data_owner_client = TestClient(app, headers=DATA_OWNER_HEADERS)
    approve_response = data_owner_client.post(
        f"/forecastops/feedbacks/{cor_id}/approve",
        json={"actor": "data-owner-sally", "note": "Verified slip"},
    )
    assert approve_response.status_code == 200
    approve_body = approve_response.json()
    assert approve_body["feedback"]["status"] == "approved"
    assert approve_body["feedback"]["approved_by"] == "data-owner-sally"

    # 6. List feedbacks via GET /forecastops/feedbacks
    list_response = client.get("/forecastops/feedbacks", params={"store_id": "store-api-101"})
    assert list_response.status_code == 200
    list_body = list_response.json()
    assert list_body["count"] == 2

    # 7. Get single feedback by ID
    get_response = client.get(f"/forecastops/feedbacks/{ctx_id}")
    assert get_response.status_code == 200
    assert get_response.json()["feedback_id"] == ctx_id


def test_durable_repository_feedback_persistence(tmp_path) -> None:
    """Test DurableForecastOpsRepository feedback persistence."""
    from shared.infrastructure.persistence.engine import SqliteEngine

    engine = SqliteEngine(tmp_path / "test_forecastops.db")
    store = SqliteDocumentStore(engine)
    repo = DurableForecastOpsRepository(store)

    fb1 = ForecastFeedback.create(
        tenant_id=TENANT_ID,
        store_id="store-durable-001",
        feedback_type=FeedbackType.CONTEXT_ANNOTATION,
        target_date="2026-06-01",
        reason="Durable test annotation",
        created_by="ops-user",
    )
    repo.save_feedback(fb1)

    loaded = repo.get_feedback(TENANT_ID, fb1.feedback_id)
    assert loaded is not None
    assert loaded.feedback_id == fb1.feedback_id
    assert loaded.reason == "Durable test annotation"
    assert loaded.status is FeedbackStatus.ACCEPTED

    all_fb = repo.list_feedbacks(TENANT_ID, store_id="store-durable-001")
    assert len(all_fb) == 1
    assert all_fb[0].feedback_id == fb1.feedback_id
