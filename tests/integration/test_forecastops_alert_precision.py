from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.forecastops import (
    AlertLevel,
    ForecastInput,
    ForecastOpsService,
    InMemoryForecastOpsRepository,
    StoreDayObservation,
    default_forecast_alert_policy,
)
from shared.governance import InMemoryDecisionPolicyRepository
from tests.integration._authz import FORECASTOPS_HEADERS

TENANT_ID = "tenant-test"
PREDICTION_TIME = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)


def _policy_repository() -> InMemoryDecisionPolicyRepository:
    return InMemoryDecisionPolicyRepository([default_forecast_alert_policy(TENANT_ID)])


def _app(repository: InMemoryForecastOpsRepository | None = None):
    return create_app(
        forecastops_repository=repository or InMemoryForecastOpsRepository(),
        forecastops_policy_repository=_policy_repository(),
    )


def test_forecastops_service_backfill_and_evaluate_alert_precision() -> None:
    repository = InMemoryForecastOpsRepository()
    service = ForecastOpsService(
        repository=repository,
        policy_repository=_policy_repository(),
    )

    # 1. Store 1: Red alert generated on June 1
    obs_store_1 = tuple(
        StoreDayObservation(
            store_id="store-prec-1",
            business_date=date(2026, 5, 25 + d),
            actual_revenue=60_000.0,
            site_score_baseline_p50=100_000.0,
        )
        for d in range(7)
    )
    result = service.forecast(
        [
            ForecastInput(
                tenant_id=TENANT_ID,
                store_id="store-prec-1",
                observations=obs_store_1,
                prediction_origin_time=PREDICTION_TIME,
            )
        ],
        scored_at=PREDICTION_TIME,
    )
    alert = result.alerts[0]
    assert alert.alert_level is AlertLevel.RED
    assert alert.disposition is None

    # 2. Ingest subsequent timeseries: on June 8, revenue drops (deterioration)
    future_obs = [
        StoreDayObservation(
            store_id="store-prec-1",
            business_date=date(2026, 6, 1),
            actual_revenue=80_000.0,
            site_score_baseline_p50=100_000.0,
        ),
        StoreDayObservation(
            store_id="store-prec-1",
            business_date=date(2026, 6, 8),
            actual_revenue=55_000.0,  # gap = -0.45 <= -0.35 (RED threshold)
            site_score_baseline_p50=100_000.0,
        ),
    ]
    service.ingest_timeseries(future_obs, tenant_id=TENANT_ID)

    # 3. Run backfill
    backfill_result = service.backfill_alert_precision(
        TENANT_ID,
        store_id="store-prec-1",
    )
    assert backfill_result["updated_count"] == 1
    metrics = backfill_result["metrics"]
    assert metrics["true_positive_count"] == 1
    assert metrics["false_positive_count"] == 0
    assert metrics["precision"] == 1.0
    assert metrics["mean_lead_time_days"] == 7.0

    # 4. Verify stored alert
    stored_alert = repository.get_alert(TENANT_ID, alert.alert_id)
    assert stored_alert is not None
    assert stored_alert.disposition == "TRUE_POSITIVE"
    assert stored_alert.lead_time_days == 7

    # 5. evaluate_alert_precision returns matching metrics
    eval_metrics = service.evaluate_alert_precision(TENANT_ID)
    assert eval_metrics["precision"] == 1.0
    assert eval_metrics["mean_lead_time_days"] == 7.0


def test_api_alert_precision_endpoints() -> None:
    repository = InMemoryForecastOpsRepository()
    service = ForecastOpsService(
        repository=repository,
        policy_repository=_policy_repository(),
    )
    app = create_app(
        forecastops_repository=repository,
        forecastops_policy_repository=_policy_repository(),
    )
    client = TestClient(app, headers=FORECASTOPS_HEADERS)

    # 1. Forecast generates an alert
    obs = tuple(
        StoreDayObservation(
            store_id="store-api-prec-1",
            business_date=date(2026, 5, 25 + d),
            actual_revenue=60_000.0,
            site_score_baseline_p50=100_000.0,
        )
        for d in range(7)
    )
    service.forecast(
        [
            ForecastInput(
                tenant_id=TENANT_ID,
                store_id="store-api-prec-1",
                observations=obs,
                prediction_origin_time=PREDICTION_TIME,
            )
        ],
        scored_at=PREDICTION_TIME,
    )

    # Add 30 days of observations showing recovery (no deterioration) -> should backfill to FALSE_POSITIVE
    healthy_obs = [
        StoreDayObservation(
            store_id="store-api-prec-1",
            business_date=date(2026, 6, d),
            actual_revenue=95_000.0,
            site_score_baseline_p50=100_000.0,
        )
        for d in range(1, 31)
    ]
    service.ingest_timeseries(healthy_obs, tenant_id=TENANT_ID)

    # 2. Trigger backfill via POST /forecastops/alerts/backfill-precision
    backfill_resp = client.post(
        "/forecastops/alerts/backfill-precision",
        json={"store_id": "store-api-prec-1", "evaluation_horizon_days": 28},
    )
    assert backfill_resp.status_code == 200
    b_body = backfill_resp.json()
    assert b_body["updated_count"] == 1
    assert b_body["metrics"]["false_positive_count"] == 1
    assert b_body["metrics"]["precision"] == 0.0

    # 3. Query GET /forecastops/alerts/precision
    prec_resp = client.get("/forecastops/alerts/precision")
    assert prec_resp.status_code == 200
    p_body = prec_resp.json()
    assert p_body["false_positive_count"] == 1
    assert p_body["total_alerts"] == 1
