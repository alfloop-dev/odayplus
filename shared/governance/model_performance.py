"""Versioned performance-drift policy used by production model validation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from shared.governance.decision_policy import DecisionPolicy

MODEL_PERFORMANCE_DRIFT_POLICY_KIND = "model_performance_drift"
MODEL_PERFORMANCE_DRIFT_POLICY_ID = "model-performance-drift-policy"
MODEL_PERFORMANCE_DRIFT_POLICY_LABEL = "model-performance-drift-policy-v1"
MODEL_PERFORMANCE_DRIFT_POLICY_VERSION = "1.0.0"
# Production model aliases are global.  The canonical deployment tenant is the
# registry scope for this platform-wide model quality policy.
MODEL_PERFORMANCE_DRIFT_POLICY_TENANT_ID = "00000000-0000-0000-0000-000000000001"

# Absolute limits remain model-specific in the policy data.  The training
# spec's caller thresholds may only make these gates stricter; the policy's
# degradation limits are always retained by effective_thresholds().
MODEL_PERFORMANCE_METRIC_THRESHOLDS: dict[str, dict[str, dict[str, Any]]] = {
    "forecast_revenue_interval": {
        "normalized_mae": {
            "max_value": 0.35,
            "max_degradation": 0.05,
            "higher_is_better": False,
        },
        "p80_coverage": {
            "min_value": 0.65,
            "max_degradation": 0.05,
            "higher_is_better": True,
        },
    },
    "dealroom_avm": {
        "normalized_mae": {
            "max_value": 0.30,
            "max_degradation": 0.05,
            "higher_is_better": False,
        },
        "p80_coverage": {
            "min_value": 0.70,
            "max_degradation": 0.05,
            "higher_is_better": True,
        },
    },
    "listing_property_avm": {
        "normalized_mae": {
            "max_value": 0.30,
            "max_degradation": 0.05,
            "higher_is_better": False,
        },
        "p80_coverage": {
            "min_value": 0.70,
            "max_degradation": 0.05,
            "higher_is_better": True,
        },
    },
    "sitescore_propensity": {
        "normalized_mae": {
            "max_value": 0.25,
            "max_degradation": 0.05,
            "higher_is_better": False,
        },
        "p80_coverage": {
            "min_value": 0.70,
            "max_degradation": 0.05,
            "higher_is_better": True,
        },
    },
    "heatzone_priority": {
        "normalized_mae": {
            "max_value": 0.30,
            "max_degradation": 0.05,
            "higher_is_better": False,
        },
        "p80_coverage": {
            "min_value": 0.65,
            "max_degradation": 0.05,
            "higher_is_better": True,
        },
    },
    "avm_liquidity": {
        "normalized_mae": {
            "max_value": 0.45,
            "max_degradation": 0.05,
            "higher_is_better": False,
        },
        "observed_event_rate": {
            "min_value": 0.02,
            "higher_is_better": True,
        },
    },
}


def default_model_performance_drift_policy(
    tenant_id: str = MODEL_PERFORMANCE_DRIFT_POLICY_TENANT_ID,
) -> DecisionPolicy:
    """Build the seeded model-performance policy for a registry tenant."""

    normalized_tenant_id = str(tenant_id or "").strip()
    if not normalized_tenant_id:
        raise ValueError("tenant_id is required for the model performance policy")
    return DecisionPolicy(
        policy_version_id=f"{MODEL_PERFORMANCE_DRIFT_POLICY_LABEL}:{normalized_tenant_id}",
        policy_label=MODEL_PERFORMANCE_DRIFT_POLICY_LABEL,
        policy_id=MODEL_PERFORMANCE_DRIFT_POLICY_ID,
        policy_version=MODEL_PERFORMANCE_DRIFT_POLICY_VERSION,
        policy_kind=MODEL_PERFORMANCE_DRIFT_POLICY_KIND,
        tenant_id=normalized_tenant_id,
        effective_from=datetime(2026, 9, 1, tzinfo=UTC),
        parameters={
            "metric_thresholds_by_model": {
                model_name: {
                    metric_name: dict(config)
                    for metric_name, config in metric_configs.items()
                }
                for model_name, metric_configs in MODEL_PERFORMANCE_METRIC_THRESHOLDS.items()
            }
        },
        declared_inputs=("metrics", "baseline_metrics"),
        change_reason="Bind production model validation and baseline drift to versioned release gates",
        approved_by="architecture_owner",
        owner_role="model-risk-owner",
    )


__all__ = [
    "MODEL_PERFORMANCE_DRIFT_POLICY_ID",
    "MODEL_PERFORMANCE_DRIFT_POLICY_KIND",
    "MODEL_PERFORMANCE_DRIFT_POLICY_LABEL",
    "MODEL_PERFORMANCE_DRIFT_POLICY_TENANT_ID",
    "MODEL_PERFORMANCE_DRIFT_POLICY_VERSION",
    "MODEL_PERFORMANCE_METRIC_THRESHOLDS",
    "default_model_performance_drift_policy",
]
