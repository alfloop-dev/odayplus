"""Default HeatZone merge/split decision policy (ODP-FR-HZ-006, ODP-SD-AMD-001 §5.2)."""

from __future__ import annotations

from datetime import UTC, datetime

from shared.governance.decision_policy import DecisionPolicy

HEATZONE_MERGE_POLICY_LABEL = "heatzone-merge-v1"
HEATZONE_MERGE_POLICY_ID = "heatzone-merge"
HEATZONE_MERGE_POLICY_VERSION = "1.0.0"
HEATZONE_MERGE_POLICY_KIND = "heatzone_merge"
HEATZONE_MERGE_POLICY_DEFAULT_TENANT = "11111111-1111-1111-1111-111111111111"

DEFAULT_HEATZONE_MERGE_PARAMETERS: dict[str, object] = {
    "min_observation_days": 180,
    "min_mature_labels": 200,
    "min_active_stores": 50,
    "min_adjacent_pairs": 30,
    "min_metro_clusters": 2,
    "min_spatial_contiguity": 0.80,
    "max_absorption_cv": 0.15,
    "max_drift_psi": 0.10,
    "max_wasserstein": 0.05,
    "min_correlation_rho": 0.75,
    "max_disconnect_index": 0.20,
    "min_split_density_ratio": 2.5,
    "min_ndcg_gain": 0.05,
    "min_cannibalization_variance_reduction": 0.20,
    "allow_cross_admin_boundary": False,
}


def default_heatzone_merge_policy(
    tenant_id: str = HEATZONE_MERGE_POLICY_DEFAULT_TENANT,
) -> DecisionPolicy:
    """Build the seeded heatzone merge/split policy for a tenant."""
    normalized_tenant_id = str(tenant_id or "").strip()
    if not normalized_tenant_id:
        raise ValueError("tenant_id is required for the heatzone merge policy")

    return DecisionPolicy(
        policy_version_id=f"{HEATZONE_MERGE_POLICY_LABEL}:{normalized_tenant_id}",
        policy_label=HEATZONE_MERGE_POLICY_LABEL,
        policy_id=HEATZONE_MERGE_POLICY_ID,
        policy_version=HEATZONE_MERGE_POLICY_VERSION,
        policy_kind=HEATZONE_MERGE_POLICY_KIND,
        tenant_id=normalized_tenant_id,
        effective_from=datetime(2026, 9, 1, tzinfo=UTC),
        parameters=dict(DEFAULT_HEATZONE_MERGE_PARAMETERS),
        declared_inputs=(
            "store_daily_performance",
            "heatzone_training_view",
            "h3_adjacency",
            "absorbed_demand",
        ),
        change_reason="熱區合併／拆分決策政策導入，依 HZ-004 實績門檻與空間異質性治理",
        approved_by="architecture_review",
        owner_role="expansion_owner",
    )


__all__ = [
    "DEFAULT_HEATZONE_MERGE_PARAMETERS",
    "HEATZONE_MERGE_POLICY_DEFAULT_TENANT",
    "HEATZONE_MERGE_POLICY_ID",
    "HEATZONE_MERGE_POLICY_KIND",
    "HEATZONE_MERGE_POLICY_LABEL",
    "HEATZONE_MERGE_POLICY_VERSION",
    "default_heatzone_merge_policy",
]
