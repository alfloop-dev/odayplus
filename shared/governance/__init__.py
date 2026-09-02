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
    "default_model_performance_drift_policy",
    "resolve_policy",
]
