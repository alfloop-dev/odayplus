from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from modules.forecastops import (
    AlertLevel,
    ForecastInput,
    StoreDayObservation,
    forecast_stores,
)
from shared.infrastructure.persistence.decision_policy import SqlDecisionPolicyRepository


class _Engine:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def query_one(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        self.calls.append((sql, params))
        return self.row


def test_sql_decision_policy_repository_resolves_registry_row() -> None:
    tenant_id = "11111111-1111-1111-1111-111111111111"
    at = datetime(2026, 9, 1, tzinfo=UTC)
    engine = _Engine(
        {
            "policy_version_id": f"four-light-policy-v1:{tenant_id}",
            "policy_label": "four-light-policy-v1",
            "policy_id": "four-light-policy",
            "policy_version": "1.0.0",
            "policy_kind": "forecast_alert",
            "tenant_id": tenant_id,
            "effective_from": "2026-09-01T00:00:00+00:00",
            "effective_to": None,
            "change_reason": "mechanism導入",
            "rollback_policy_version": None,
            "parameters": json.dumps(
                {
                    "thresholds": [
                        {"level": "RED", "input": "sitescore_gap_ratio", "op": "<=", "value": -0.35},
                        {"level": "ORANGE", "input": "sitescore_gap_ratio", "op": "<=", "value": -0.20},
                        {"level": "YELLOW", "input": "sitescore_gap_ratio", "op": "<=", "value": -0.10},
                    ]
                }
            ),
            "declared_inputs": json.dumps(["sitescore_gap_ratio"]),
            "approved_by": "architecture_owner",
            "owner_role": "ops",
        }
    )

    repo = SqlDecisionPolicyRepository(engine)
    policy = repo.find_effective(
        policy_kind="forecast_alert",
        tenant_id=tenant_id,
        at=at,
    )

    assert policy is not None
    assert policy.policy_version_id == f"four-light-policy-v1:{tenant_id}"
    assert policy.parameters["thresholds"][0]["value"] == -0.35
    assert policy.declared_inputs == ("sitescore_gap_ratio",)
    assert engine.calls[0][1] == ("forecast_alert", tenant_id, at, at)

    # Verify that the resolved policy successfully evaluates a forecast in the domain evaluator
    forecast_input = ForecastInput(
        tenant_id=tenant_id,
        store_id="store-sql-001",
        observations=(
            StoreDayObservation(
                store_id="store-sql-001",
                business_date=date(2026, 8, 31),
                actual_revenue=60_000.0,
                site_score_baseline_p50=100_000.0,
            ),
        ),
        prediction_origin_time=at,
    )
    forecasts, alerts, _ = forecast_stores(
        [forecast_input],
        scored_at=at,
        policy_repository=repo,
    )
    assert len(forecasts) == 1
    assert len(alerts) == 1
    assert alerts[0].alert_level is AlertLevel.RED
    assert alerts[0].policy_version == "four-light-policy-v1"
    assert alerts[0].policy_version_id == f"four-light-policy-v1:{tenant_id}"


