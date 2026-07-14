"""Operator Console modular route sub-packages.

Each sub-module owns one domain fleet (shell, issues, approvals, evidence,
seed).  The parent operator.py assembles them via compose_operator_router().
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
