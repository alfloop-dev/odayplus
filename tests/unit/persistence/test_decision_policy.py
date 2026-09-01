from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

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
                        {"level": "RED", "value": -0.35},
                        {"level": "ORANGE", "value": -0.20},
                        {"level": "YELLOW", "value": -0.10},
                    ]
                }
            ),
            "declared_inputs": json.dumps(["sitescore_gap_ratio"]),
            "approved_by": "architecture_owner",
            "owner_role": "ops",
        }
    )

    policy = SqlDecisionPolicyRepository(engine).find_effective(
        policy_kind="forecast_alert",
        tenant_id=tenant_id,
        at=at,
    )

    assert policy is not None
    assert policy.policy_version_id == f"four-light-policy-v1:{tenant_id}"
    assert policy.parameters["thresholds"][0]["value"] == -0.35
    assert policy.declared_inputs == ("sitescore_gap_ratio",)
    assert engine.calls[0][1] == ("forecast_alert", tenant_id, at, at)
