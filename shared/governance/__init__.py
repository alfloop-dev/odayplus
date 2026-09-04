"""Cross-cutting governance primitives.

Decision policies live here rather than inside any one module because
ODP-SA-07 §8 applies platform-wide: ForecastOps four-light thresholds,
HeatZone merge rules, SiteScore recommendation bands and PriceOps exploration
gates are all policy data, and all of them must be resolvable to the version
that governed a given decision.
"""

from shared.governance.decision_policy import (
    DecisionPolicy,
    DecisionPolicyRepository,
    InMemoryDecisionPolicyRepository,
    PolicyIdentityError,
    PolicyResolutionError,
    PolicySupersedeError,
    resolve_policy,
)
from shared.governance.model_performance import (
    MODEL_PERFORMANCE_DRIFT_POLICY_ID,
    MODEL_PERFORMANCE_DRIFT_POLICY_KIND,
    MODEL_PERFORMANCE_DRIFT_POLICY_LABEL,
    MODEL_PERFORMANCE_DRIFT_POLICY_TENANT_ID,
    MODEL_PERFORMANCE_DRIFT_POLICY_VERSION,
    MODEL_PERFORMANCE_METRIC_THRESHOLDS,
    default_model_performance_drift_policy,
)
from shared.governance.netplan_disclosure import (
    NETPLAN_ACKNOWLEDGEABLE_CONSTRAINT_CLASSES,
    NETPLAN_ACKNOWLEDGEMENT_ROLES,
    NETPLAN_DISCLOSURE_POLICY_ID,
    NETPLAN_DISCLOSURE_POLICY_KIND,
    NETPLAN_DISCLOSURE_POLICY_LABEL,
    NETPLAN_DISCLOSURE_POLICY_VERSION,
    NETPLAN_REQUIRED_CONSTRAINT_CLASSES,
    DisclosureEvaluation,
    NetPlanDisclosurePolicyError,
    default_netplan_disclosure_policy,
    evaluate_disclosure,
    role_is_authorized,
)

__all__ = [
    "DecisionPolicy",
    "DecisionPolicyRepository",
    "InMemoryDecisionPolicyRepository",
    "PolicyIdentityError",
    "PolicyResolutionError",
    "PolicySupersedeError",
    "MODEL_PERFORMANCE_DRIFT_POLICY_ID",
    "MODEL_PERFORMANCE_DRIFT_POLICY_KIND",
    "MODEL_PERFORMANCE_DRIFT_POLICY_LABEL",
    "MODEL_PERFORMANCE_DRIFT_POLICY_TENANT_ID",
    "MODEL_PERFORMANCE_DRIFT_POLICY_VERSION",
    "MODEL_PERFORMANCE_METRIC_THRESHOLDS",
    "NETPLAN_ACKNOWLEDGEABLE_CONSTRAINT_CLASSES",
    "NETPLAN_ACKNOWLEDGEMENT_ROLES",
    "NETPLAN_DISCLOSURE_POLICY_ID",
    "NETPLAN_DISCLOSURE_POLICY_KIND",
    "NETPLAN_DISCLOSURE_POLICY_LABEL",
    "NETPLAN_DISCLOSURE_POLICY_VERSION",
    "NETPLAN_REQUIRED_CONSTRAINT_CLASSES",
    "DisclosureEvaluation",
    "NetPlanDisclosurePolicyError",
    "default_model_performance_drift_policy",
    "default_netplan_disclosure_policy",
    "evaluate_disclosure",
    "resolve_policy",
    "role_is_authorized",
]
