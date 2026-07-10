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
    build_store_timeseries,
    run_forecastops_batch_forecast,
)
from tests.integration._authz import FORECASTOPS_HEADERS

PREDICTION_TIME = datetime(2026, 6, 27, 9, 0, tzinfo=UTC)


def _observation(day: int, revenue: float, baseline: float = 100_000.0) -> StoreDayObservation:
    return StoreDayObservation(
        store_id="store-001",
        business_date=date(2026, 6, day),
        actual_revenue=revenue,
        machine_cycles=int(revenue / 100),
        site_score_baseline_p50=baseline,
        source_snapshot_ids=(f"pos-202606{day:02d}",),
    )


def test_store_timeseries_view_groups_and_orders_observations() -> None:
    series = build_store_timeseries(
        [
            {"store_id": "store-b", "business_date": "2026-06-02", "actual_revenue": 90_000},
            {"store_id": "store-a", "business_date": "2026-06-02", "actual_revenue": 80_000},
            {"store_id": "store-a", "business_date": "2026-06-01", "actual_revenue": 70_000},
        ]
    )

    assert [item.store_id for item in series] == ["store-a", "store-b"]
    assert [point.business_date.isoformat() for point in series[0].observations] == [
        "2026-06-01",
        "2026-06-02",
    ]
    assert series[0].to_dict()["feature_version"] == "store-machine-timeseries-view-v1"


def test_forecast_job_emits_four_light_alerts_and_handoffs() -> None:
    repository = InMemoryForecastOpsRepository()
    service = ForecastOpsService(repository=repository)
    observations = tuple(_observation(day, 80_000 - day * 2_000) for day in range(20, 27))

    result = service.forecast(
        [
            ForecastInput(
                store_id="store-001",
                observations=observations,
                prediction_origin_time=PREDICTION_TIME,
            )
        ],
        scored_at=PREDICTION_TIME,
    )

    forecast = result.forecasts[0]
    assert forecast.forecast_version == 1
    assert forecast.p10 <= forecast.p50 <= forecast.p90
    assert forecast.w4.p10 <= forecast.w4.p50 <= forecast.w4.p90
    assert forecast.w8.p10 <= forecast.w8.p50 <= forecast.w8.p90
    assert forecast.w12.p10 <= forecast.w12.p50 <= forecast.w12.p90
    assert forecast.w24.p10 <= forecast.w24.p50 <= forecast.w24.p90
    assert forecast.trajectory_class == "declining"
    assert forecast.sitescore_gap_ratio == -0.72

    alert = result.alerts[0]
    assert alert.alert_level is AlertLevel.RED
    assert alert.alert_reason_code == "sitescore_gap"
    assert alert.evidence_json["policy_version"] == "four-light-policy-v1"

    handoff = result.handoffs[0]
    assert handoff.alert_id == alert.alert_id
    assert handoff.intervention_type == "maintenance"
    assert handoff.eligibility_status == "manual_review"
    assert "inspect_machine_uptime" in handoff.action_set_json["recommended_actions"]


def test_batch_worker_persists_versions() -> None:
    repository = InMemoryForecastOpsRepository()
    inputs = [
        {
            "store_id": "store-001",
            "prediction_origin_time": PREDICTION_TIME.isoformat(),
            "observations": [
                {
                    "business_date": "2026-06-26",
                    "actual_revenue": 88_000,
                    "site_score_baseline_p50": 100_000,
                }
            ],
        }
    ]

    first = run_forecastops_batch_forecast(
        inputs=inputs, job_id="forecast-job-1", repository=repository
    )
    second = run_forecastops_batch_forecast(
        inputs=inputs, job_id="forecast-job-2", repository=repository
    )

    assert first.job_id == "forecast-job-1"
    assert first.status == "succeeded"
    assert first.to_dict()["forecasts"][0]["forecast_version"] == 1
    assert set(first.to_dict()["forecasts"][0]["forecast_bands"]) == {"w4", "w8", "w12", "w24"}
    assert second.to_dict()["forecasts"][0]["forecast_version"] == 2


def test_forecastops_api_runs_alert_handoff_loop_and_is_idempotent() -> None:
    client = TestClient(create_app(), headers=FORECASTOPS_HEADERS)
    payload = {
        "prediction_origin_time": PREDICTION_TIME.isoformat(),
        "inputs": [
            {
                "store_id": "store-api-001",
                "observations": [
                    {
                        "business_date": "2026-06-25",
                        "actual_revenue": 120_000,
                        "site_score_baseline_p50": 120_000,
                        "source_snapshot_ids": ["pos-20260625"],
                    },
                    {
                        "business_date": "2026-06-26",
                        "actual_revenue": 90_000,
                        "site_score_baseline_p50": 120_000,
                        "source_snapshot_ids": ["pos-20260626"],
                    },
                ],
            }
        ],
    }

    response = client.post(
        "/forecastops/forecast-jobs",
        json=payload,
        headers={"x-correlation-id": "corr-forecast-1", "Idempotency-Key": "fo-idem-1"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["created"] is True
    assert body["correlation_id"] == "corr-forecast-1"
    assert set(body["forecasts"][0]["forecast_bands"]) == {"w4", "w8", "w12", "w24"}
    assert body["alerts"][0]["alert_level"] == "orange"
    assert body["handoffs"][0]["store_id"] == "store-api-001"
    assert body["handoffs"][0]["intervention_type"] == "promotion"
    job_id = body["job_id"]

    replay = client.post(
        "/forecastops/forecast-jobs",
        json=payload,
        headers={"x-correlation-id": "corr-forecast-1", "Idempotency-Key": "fo-idem-1"},
    )
    assert replay.json()["created"] is False
    assert replay.json()["job_id"] == job_id
    assert replay.json()["forecasts"][0]["forecast_version"] == 1

    forecasts = client.get("/forecastops/forecasts")
    assert forecasts.json()["count"] == 1
    assert forecasts.json()["items"][0]["forecast_version"] == 1
    alerts = client.get("/forecastops/alerts", params={"level": "orange"})
    assert alerts.json()["count"] == 1
    handoffs = client.get("/forecastops/intervention-handoffs")
    assert handoffs.json()["count"] == 1

    audit = client.get("/audit/events", params={"correlation_id": "corr-forecast-1"})
    assert any(event["event_type"] == "forecastops.forecasted.v1" for event in audit.json()["events"])


def test_forecastops_prediction_run_replay() -> None:
    client = TestClient(create_app(), headers=FORECASTOPS_HEADERS)
    payload = {
        "prediction_origin_time": PREDICTION_TIME.isoformat(),
        "inputs": [
            {
                "store_id": "store-replay-001",
                "observations": [
                    {
                        "business_date": "2026-06-25",
                        "actual_revenue": 100_000,
                        "site_score_baseline_p50": 100_000,
                    }
                ],
            }
        ],
    }
    response = client.post(
        "/forecastops/forecast-jobs",
        json=payload,
    )
    assert response.status_code == 202
    body = response.json()
    forecast = body["forecasts"][0]
    forecast_output_id = forecast["forecast_output_id"]
    prediction_run_id = forecast["prediction_run_id"]
    assert prediction_run_id.startswith("pred-run-forecast-")

    # 1. Fetch prediction run by ID
    pred_run_response = client.get(f"/forecastops/prediction-runs/{prediction_run_id}")
    assert pred_run_response.status_code == 200
    pred_run_body = pred_run_response.json()
    assert pred_run_body["prediction_run"]["prediction_run_id"] == prediction_run_id
    assert len(pred_run_body["predictions"]) == 1
    assert pred_run_body["predictions"][0]["entity_id"] == "store-replay-001"

    # 2. Fetch canonical forecast output by ID
    output_response = client.get(f"/forecastops/forecast-outputs/{forecast_output_id}")
    assert output_response.status_code == 200
    output_body = output_response.json()
    assert output_body["store_id"] == "store-replay-001"
    assert output_body["prediction_run_id"] == prediction_run_id

