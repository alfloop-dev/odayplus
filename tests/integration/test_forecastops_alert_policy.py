from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from modules.forecastops import (
    AlertLevel,
    ForecastAlertPolicyError,
    ForecastInput,
    ForecastOpsService,
    InMemoryForecastOpsRepository,
    StoreDayObservation,
    default_forecast_alert_policy,
    forecast_stores,
)
from shared.governance import (
    DecisionPolicy,
    InMemoryDecisionPolicyRepository,
    PolicyResolutionError,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
STORE_ID = "store-alert-policy-001"
V1_TIME = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
V2_TIME = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)


def _policy(
    *,
    label: str,
    version: str,
    effective_from: datetime,
    red: float,
    orange: float,
    yellow: float,
) -> DecisionPolicy:
    return DecisionPolicy(
        policy_version_id=f"{label}:{TENANT_ID}",
        policy_label=label,
        policy_id="four-light-policy",
        policy_version=version,
        policy_kind="forecast_alert",
        tenant_id=TENANT_ID,
        effective_from=effective_from,
        parameters={
            "thresholds": [
                {"level": "RED", "input": "sitescore_gap_ratio", "op": "<=", "value": red},
                {
                    "level": "ORANGE",
                    "input": "sitescore_gap_ratio",
                    "op": "<=",
                    "value": orange,
                },
                {
                    "level": "YELLOW",
                    "input": "sitescore_gap_ratio",
                    "op": "<=",
                    "value": yellow,
                },
            ],
            "data_quality_guard": {
                "max_staleness_days": 2,
                "on_violation": "SUPPRESS_HIGH_CONFIDENCE",
            },
        },
        declared_inputs=("sitescore_gap_ratio",),
        change_reason=f"alert policy {version}",
        approved_by="ops-lead",
        owner_role="ops",
    )


def _input(*, origin: datetime = V1_TIME) -> ForecastInput:
    return ForecastInput(
        tenant_id=TENANT_ID,
        store_id=STORE_ID,
        observations=(
            StoreDayObservation(
                store_id=STORE_ID,
                business_date=origin.date() - timedelta(days=1),
                actual_revenue=60_000.0,
                site_score_baseline_p50=100_000.0,
            ),
        ),
        prediction_origin_time=origin,
    )


def test_default_policy_keeps_the_existing_thresholds_and_declares_actual_input() -> None:
    policy = default_forecast_alert_policy(TENANT_ID)

    assert policy.policy_id == "four-light-policy"
    assert policy.policy_version == "1.0.0"
    assert policy.declared_inputs == ("sitescore_gap_ratio",)
    assert policy.parameters["thresholds"] == [
        {"level": "RED", "input": "sitescore_gap_ratio", "op": "<=", "value": -0.35},
        {"level": "ORANGE", "input": "sitescore_gap_ratio", "op": "<=", "value": -0.20},
        {"level": "YELLOW", "input": "sitescore_gap_ratio", "op": "<=", "value": -0.10},
    ]


def test_same_forecast_is_evaluated_by_each_policy_version_and_retained() -> None:
    policies = InMemoryDecisionPolicyRepository(
        [
            _policy(
                label="four-light-policy-v1",
                version="1.0.0",
                effective_from=V1_TIME,
                red=-0.35,
                orange=-0.20,
                yellow=-0.10,
            )
        ]
    )
    policies.supersede(
        _policy(
            label="four-light-policy-v2",
            version="2.0.0",
            effective_from=V2_TIME,
            red=-0.50,
            orange=-0.30,
            yellow=-0.10,
        )
    )
    repository = InMemoryForecastOpsRepository()
    service = ForecastOpsService(repository=repository, policy_repository=policies)

    first = service.forecast([_input()], prediction_run_id="same-forecast", scored_at=V1_TIME)
    second = service.forecast(
        [_input()], prediction_run_id="same-forecast", scored_at=V2_TIME
    )

    assert first.alerts[0].alert_level is AlertLevel.RED
    assert second.alerts[0].alert_level is AlertLevel.ORANGE
    assert first.alerts[0].policy_version == "four-light-policy-v1"
    assert second.alerts[0].policy_version == "four-light-policy-v2"
    assert first.alerts[0].policy_version_id.endswith(TENANT_ID)
    assert second.alerts[0].policy_version_id.endswith(TENANT_ID)
    assert first.alerts[0].alert_id != second.alerts[0].alert_id
    assert {alert.policy_version for alert in repository.list_alerts(TENANT_ID)} == {
        "four-light-policy-v1",
        "four-light-policy-v2",
    }


def test_missing_policy_fails_closed_before_persisting_forecast_or_alert() -> None:
    repository = InMemoryForecastOpsRepository()
    service = ForecastOpsService(
        repository=repository,
        policy_repository=InMemoryDecisionPolicyRepository(),
    )

    with pytest.raises(PolicyResolutionError, match="refusing to decide"):
        service.forecast([_input()], scored_at=V1_TIME)

    assert repository.latest_forecasts(TENANT_ID) == []
    assert repository.list_alerts(TENANT_ID) == []


def test_missing_policy_repository_is_rejected_instead_of_using_threshold_fallback() -> None:
    with pytest.raises(ForecastAlertPolicyError, match="policy_repository is required"):
        forecast_stores([_input()], scored_at=V1_TIME)


def test_stale_guard_is_part_of_policy_evaluation_and_suppresses_red() -> None:
    policies = InMemoryDecisionPolicyRepository(
        [
            _policy(
                label="four-light-policy-v1",
                version="1.0.0",
                effective_from=V1_TIME,
                red=-0.35,
                orange=-0.20,
                yellow=-0.10,
            )
        ]
    )
    repository = InMemoryForecastOpsRepository()
    service = ForecastOpsService(repository=repository, policy_repository=policies)
    stale_origin = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
    stale_input = _input(origin=stale_origin)
    stale_input = replace(
        stale_input,
        observations=(
            replace(
                stale_input.observations[0],
                business_date=stale_origin.date() - timedelta(days=3),
            ),
        ),
    )

    result = service.forecast([stale_input], scored_at=stale_origin)

    alert = result.alerts[0]
    assert alert.alert_level is AlertLevel.ORANGE
    assert alert.alert_reason_code == "data_quality_stale"
    assert alert.evidence_json["data_quality_guard"]["violated"] is True
    assert alert.evidence_json["threshold_alert_level"] == "red"
