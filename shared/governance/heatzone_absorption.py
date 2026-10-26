"""Governing policy for HZ-004 absorption measurement (ODP-FR-HZ-004, ODP-FR-HZ-006).

Kept apart from the merge/split policy on purpose. Merge/split is judged
*against* absorption history, so the version that decides a merge and the
version that measured the outcomes it reasons from must be separately
identifiable: a threshold change in one has to be readable in the audit trail
without being mistaken for a change in the other.
"""

from __future__ import annotations

from datetime import UTC, datetime

from shared.governance.decision_policy import DecisionPolicy

HEATZONE_ABSORPTION_POLICY_LABEL = "heatzone-absorption-v1"
HEATZONE_ABSORPTION_POLICY_ID = "heatzone-absorption"
HEATZONE_ABSORPTION_POLICY_VERSION = "1.0.0"
HEATZONE_ABSORPTION_POLICY_KIND = "heatzone_absorption"
HEATZONE_ABSORPTION_POLICY_DEFAULT_TENANT = "11111111-1111-1111-1111-111111111111"

DEFAULT_HEATZONE_ABSORPTION_PARAMETERS: dict[str, object] = {
    # A store below this many observed days is still ramping, and its revenue
    # is not evidence of what the zone absorbs.
    "min_observation_days": 180,
    # Below this share of demand met, the zone is not "served" -- it is served
    # badly, which is a different finding and a different action.
    "under_realized_ratio": 0.35,
    # A declared start date is a claim, not an observation; admitting one is a
    # governance decision rather than a code default.
    "allow_declared_start": False,
    "allow_low_confidence_start": False,
    "allow_unknown_confidence_start": False,
}


def default_heatzone_absorption_policy(
    tenant_id: str = HEATZONE_ABSORPTION_POLICY_DEFAULT_TENANT,
) -> DecisionPolicy:
    """Build the seeded HZ-004 absorption policy for a tenant."""
    normalized_tenant_id = str(tenant_id or "").strip()
    if not normalized_tenant_id:
        raise ValueError("tenant_id is required for the heatzone absorption policy")

    return DecisionPolicy(
        policy_version_id=f"{HEATZONE_ABSORPTION_POLICY_LABEL}:{normalized_tenant_id}",
        policy_label=HEATZONE_ABSORPTION_POLICY_LABEL,
        policy_id=HEATZONE_ABSORPTION_POLICY_ID,
        policy_version=HEATZONE_ABSORPTION_POLICY_VERSION,
        policy_kind=HEATZONE_ABSORPTION_POLICY_KIND,
        tenant_id=normalized_tenant_id,
        effective_from=datetime(2026, 9, 1, tzinfo=UTC),
        parameters=dict(DEFAULT_HEATZONE_ABSORPTION_PARAMETERS),
        declared_inputs=(
            "store_daily_performance",
            "operational_start_observation",
            "absorbed_demand",
        ),
        change_reason="熱區需求吸收實績量測政策導入，作為 merge／split 的證據來源",
        approved_by="architecture_review",
        owner_role="expansion_owner",
    )


__all__ = [
    "DEFAULT_HEATZONE_ABSORPTION_PARAMETERS",
    "HEATZONE_ABSORPTION_POLICY_DEFAULT_TENANT",
    "HEATZONE_ABSORPTION_POLICY_ID",
    "HEATZONE_ABSORPTION_POLICY_KIND",
    "HEATZONE_ABSORPTION_POLICY_LABEL",
    "HEATZONE_ABSORPTION_POLICY_VERSION",
    "default_heatzone_absorption_policy",
]
