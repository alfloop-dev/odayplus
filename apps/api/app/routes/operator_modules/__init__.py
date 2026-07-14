"""Operator Console modular route sub-packages.

Each sub-module owns one domain fleet (shell, issues, approvals, evidence,
seed).  The parent operator.py assembles them inside create_operator_router(),
passing auth guards to every sub-router that owns a write endpoint.

Composition contract:
  - shell      → read-only; no permission guard required.
  - issues     → write guard: require_permission("intervention", CREATE).
  - approvals  → write guard: require_permission("intervention", APPROVE).
  - evidence   → write guard: require_permission("intervention", CREATE).
  - seed       → no auth guard (dev/test only).

All guards are wired at composition time in operator.py — sub-routers must
never be included_router'd without their auth guard.
"""

from .approvals import create_approvals_sub_router
from .evidence import create_evidence_sub_router
from .issues import create_issues_sub_router
from .seed import create_seed_sub_router
from .shell import create_shell_sub_router

__all__ = [
    "create_shell_sub_router",
    "create_issues_sub_router",
    "create_approvals_sub_router",
    "create_evidence_sub_router",
    "create_seed_sub_router",
]
