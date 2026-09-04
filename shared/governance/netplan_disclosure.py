"""Versioned policy for approving a network plan whose constraints are not all modelled.

`NetPlanConstraints.unmodelled_classes()` already says which of the eight
ODP-FR-NET-002 hard-constraint classes a solve never tested the plan against.
Saying it is not the same as acting on it: until this module, `decide()` read
that list and approved anyway, so a plan validated only against capital was
approvable on exactly the same terms as one validated against all eight.

This policy is the rule that turns the disclosure into a decision. It is
registry data (`policy_kind = 'netplan_action'`), not a constant, because which
classes may be waived is a governance judgement that changes on a different
clock than the solver does -- and because ODP-AC-BR-004 requires a historical
approval to name the policy version that let it through.

**The split between blocking and acknowledgeable is not a severity ranking.**
It is a question about the model:

- CONSTRUCTION, EQUIPMENT, LABOUR, COVERAGE and DILUTION are unmodelled only
  when the caller supplied no cap. The solver can bind them today; the fix is
  to supply the number. Letting a human wave that through would convert a
  missing input into an accepted risk, so these **block**.
- LEASE and SEQUENCING are unmodelled because the formulation has no lease
  admissibility check and no time dimension
  (`docs/design/ODP_NETPLAN_CONSTRAINT_CLASSES_2026-09-01.md`). No input the
  caller can supply changes that. Blocking every plan on them would stop the
  product outright, so they are **acknowledgeable**: a named, authorised person
  may accept the exposure in writing, and the receipt records who and why.
- CAPITAL is listed as required and is never acknowledgeable. `max_budget` is
  mandatory, so it is always modelled -- the entry exists so that a future
  formulation which somehow drops it fails closed rather than silently.

An acknowledgeable class is not a class that may be ignored. It is a class
whose exposure has to be signed for.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from shared.governance.decision_policy import DecisionPolicy

NETPLAN_DISCLOSURE_POLICY_KIND = "netplan_action"
NETPLAN_DISCLOSURE_POLICY_ID = "netplan-constraint-disclosure-policy"
NETPLAN_DISCLOSURE_POLICY_LABEL = "netplan-constraint-disclosure-policy-v1"
NETPLAN_DISCLOSURE_POLICY_VERSION = "1.0.0"

# The eight ODP-FR-NET-002 classes, as the requirement states them: all
# required. The requirement does not distinguish; the policy below does, and
# says on what grounds.
NETPLAN_REQUIRED_CONSTRAINT_CLASSES: tuple[str, ...] = (
    "CAPITAL",
    "LEASE",
    "CONSTRUCTION",
    "EQUIPMENT",
    "LABOUR",
    "COVERAGE",
    "DILUTION",
    "SEQUENCING",
)

# Structurally inexpressible in the current formulation -- see the module
# docstring. Everything else in the required set blocks.
NETPLAN_ACKNOWLEDGEABLE_CONSTRAINT_CLASSES: tuple[str, ...] = (
    "LEASE",
    "SEQUENCING",
)

# Authority to accept an unmodelled-constraint exposure. Matched against the
# principal_role on the verified management approval receipt, never against a
# role the caller passes in: an actor asserting their own authority is the
# failure this whole path exists to prevent.
NETPLAN_ACKNOWLEDGEMENT_ROLES: tuple[str, ...] = (
    "network-planning-authority",
    "network_planning_authority",
)


class NetPlanDisclosurePolicyError(ValueError):
    """The resolved policy cannot be read as a disclosure policy.

    Raised rather than defaulted. A policy row whose parameters do not parse
    is indistinguishable, from the approval path's point of view, from no
    policy at all -- and ODP-SD-AMD-001 §3.3 says that is a refusal.
    """


@dataclass(frozen=True)
class DisclosureEvaluation:
    """What the policy says about one solve's unmodelled classes.

    `blocking` and `acknowledgeable` partition the required classes this solve
    left unmodelled. `not_required` is kept separate rather than dropped so
    that a caller can report the full disclosure without having to re-derive
    which part of it the policy cared about.
    """

    policy_version_id: str
    policy_label: str
    policy_version: str
    unmodelled: tuple[str, ...]
    blocking: tuple[str, ...]
    acknowledgeable: tuple[str, ...]
    not_required: tuple[str, ...]

    @property
    def requires_acknowledgement(self) -> bool:
        return bool(self.acknowledgeable)

    @property
    def is_blocked(self) -> bool:
        return bool(self.blocking)


def _string_tuple(value: Any, *, field_name: str, policy: DecisionPolicy) -> tuple[str, ...]:
    if value is None:
        raise NetPlanDisclosurePolicyError(
            f"netplan disclosure policy {policy.policy_version_id} declares no "
            f"{field_name}; refusing to infer one"
        )
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise NetPlanDisclosurePolicyError(
            f"netplan disclosure policy {policy.policy_version_id} field "
            f"{field_name} must be a sequence of class names, got {type(value).__name__}"
        )
    return tuple(str(item).strip().upper() for item in value if str(item).strip())


def required_classes(policy: DecisionPolicy) -> tuple[str, ...]:
    """The classes this policy version insists a solve must have bound."""
    return _string_tuple(
        policy.parameters.get("required_classes"),
        field_name="required_classes",
        policy=policy,
    )


def acknowledgeable_classes(policy: DecisionPolicy) -> tuple[str, ...]:
    """The required classes a named authority may sign for instead.

    A class listed here but absent from `required_classes` is a contradiction
    in the policy row, not a permission: it would let an actor acknowledge
    something the policy never asked about. Rejected rather than intersected
    away, so the bad row is fixed instead of quietly half-honoured.
    """
    declared = _string_tuple(
        policy.parameters.get("acknowledgeable_classes"),
        field_name="acknowledgeable_classes",
        policy=policy,
    )
    required = set(required_classes(policy))
    stray = tuple(name for name in declared if name not in required)
    if stray:
        raise NetPlanDisclosurePolicyError(
            f"netplan disclosure policy {policy.policy_version_id} marks "
            f"{','.join(sorted(stray))} acknowledgeable but does not require "
            "them; a class that is not required cannot be waived"
        )
    return declared


def authorized_roles(policy: DecisionPolicy) -> tuple[str, ...]:
    """Roles permitted to sign an acknowledgement under this policy version.

    An empty list is a valid policy meaning "nobody may acknowledge anything",
    which is why it is not treated as an unset field. A *missing* key is
    different and refuses, per `_string_tuple`.
    """
    value = policy.parameters.get("authorized_acknowledgement_roles")
    if value is None:
        raise NetPlanDisclosurePolicyError(
            f"netplan disclosure policy {policy.policy_version_id} declares no "
            "authorized_acknowledgement_roles; refusing to infer authority"
        )
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise NetPlanDisclosurePolicyError(
            f"netplan disclosure policy {policy.policy_version_id} field "
            "authorized_acknowledgement_roles must be a sequence of role names"
        )
    return tuple(str(item).strip() for item in value if str(item).strip())


def role_is_authorized(policy: DecisionPolicy, role: str) -> bool:
    """Whether `role` may sign an acknowledgement under this policy version.

    Comparison is exact after trimming. Case folding is deliberately not
    applied: roles arrive from an external approval authority, and treating
    two distinct strings from that system as the same principal class is a
    decision this code is not entitled to make.
    """
    candidate = str(role or "").strip()
    if not candidate:
        return False
    return candidate in authorized_roles(policy)


def evaluate_disclosure(
    policy: DecisionPolicy,
    *,
    unmodelled_classes: Sequence[str],
) -> DisclosureEvaluation:
    """Sort one solve's unmodelled classes into blocked, waivable and irrelevant.

    Takes class *names* rather than the solver's `ConstraintClass` enum so that
    `shared.governance` keeps no dependency on `solver`. The enum is a
    `StrEnum`, so callers pass its members directly.
    """
    required = set(required_classes(policy))
    waivable = set(acknowledgeable_classes(policy))
    observed = tuple(
        dict.fromkeys(str(name).strip().upper() for name in unmodelled_classes if str(name).strip())
    )
    blocking = tuple(name for name in observed if name in required and name not in waivable)
    acknowledgeable = tuple(name for name in observed if name in required and name in waivable)
    not_required = tuple(name for name in observed if name not in required)
    return DisclosureEvaluation(
        policy_version_id=policy.policy_version_id,
        policy_label=policy.policy_label,
        policy_version=policy.policy_version,
        unmodelled=observed,
        blocking=blocking,
        acknowledgeable=acknowledgeable,
        not_required=not_required,
    )


def default_netplan_disclosure_policy(tenant_id: str) -> DecisionPolicy:
    """The seeded v1 policy, matching `workflow.seed_netplan_disclosure_policy`.

    Both this and the SQL seed exist for the same reason the four-light seed is
    a function with two callers: the in-memory reference and the registry row
    must not drift into permitting different things.
    """
    normalized_tenant_id = str(tenant_id or "").strip()
    if not normalized_tenant_id:
        raise ValueError("tenant_id is required for the netplan disclosure policy")
    return DecisionPolicy(
        policy_version_id=f"{NETPLAN_DISCLOSURE_POLICY_LABEL}:{normalized_tenant_id}",
        policy_label=NETPLAN_DISCLOSURE_POLICY_LABEL,
        policy_id=NETPLAN_DISCLOSURE_POLICY_ID,
        policy_version=NETPLAN_DISCLOSURE_POLICY_VERSION,
        policy_kind=NETPLAN_DISCLOSURE_POLICY_KIND,
        tenant_id=normalized_tenant_id,
        effective_from=datetime(2026, 9, 1, tzinfo=UTC),
        parameters={
            "required_classes": list(NETPLAN_REQUIRED_CONSTRAINT_CLASSES),
            "acknowledgeable_classes": list(NETPLAN_ACKNOWLEDGEABLE_CONSTRAINT_CLASSES),
            "authorized_acknowledgement_roles": list(NETPLAN_ACKNOWLEDGEMENT_ROLES),
        },
        declared_inputs=("unmodelled_constraint_classes", "approval_principal_role"),
        change_reason=(
            "Bind network plan approval to the solver's constraint disclosure: "
            "block classes the model could have bound, require a signed "
            "acknowledgement for the two it structurally cannot"
        ),
        approved_by="architecture_owner",
        owner_role="network-planning-authority",
    )


__all__ = [
    "NETPLAN_ACKNOWLEDGEABLE_CONSTRAINT_CLASSES",
    "NETPLAN_ACKNOWLEDGEMENT_ROLES",
    "NETPLAN_DISCLOSURE_POLICY_ID",
    "NETPLAN_DISCLOSURE_POLICY_KIND",
    "NETPLAN_DISCLOSURE_POLICY_LABEL",
    "NETPLAN_DISCLOSURE_POLICY_VERSION",
    "NETPLAN_REQUIRED_CONSTRAINT_CLASSES",
    "DisclosureEvaluation",
    "NetPlanDisclosurePolicyError",
    "acknowledgeable_classes",
    "authorized_roles",
    "default_netplan_disclosure_policy",
    "evaluate_disclosure",
    "required_classes",
    "role_is_authorized",
]
