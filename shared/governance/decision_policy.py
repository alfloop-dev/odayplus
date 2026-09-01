"""Versioned decision policies (ODP-SA-07 §8, ODP-SD-AMD-001 §2).

A Decision Policy is the thresholds and weights a decision was made under,
stored as data with a version rather than compiled into the code that reads
them. ODP-SA-07 §8 requires every formal Decision to record the `policy_id`
and `policy_version` that produced it.

Two properties carry the weight here.

**Resolution is point-in-time.** `resolve_policy` takes the moment the
decision is being made and returns the version in force *then* -- not the
newest one. Re-running a three-month-old alert resolves to the policy that was
live at the time, which is what makes ODP-AC-BR-004 answerable: a historical
decision can say which policy called it.

**Resolution fails closed.** When no version covers the requested instant,
`PolicyResolutionError` is raised. Callers must not fall back to built-in
thresholds -- a decision produced under an unresolvable policy cannot record
what governed it, and a default that silently substitutes for a missing policy
is the same failure the promotion path had when a missing geocode confidence
became 1.0 (ODP-LISTING-PROMOTION-FAILOPEN-001).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

__all__ = [
    "DecisionPolicy",
    "DecisionPolicyRepository",
    "InMemoryDecisionPolicyRepository",
    "PolicyResolutionError",
    "PolicySupersedeError",
    "resolve_policy",
]


class PolicyResolutionError(RuntimeError):
    """No policy version covers the requested instant.

    Callers fail closed on this. Producing the decision anyway, under built-in
    values, leaves a record that cannot name what governed it.
    """


class PolicySupersedeError(RuntimeError):
    """A supersede would break the single-version-in-force invariant."""


@dataclass(frozen=True)
class DecisionPolicy:
    """One version of one policy.

    `declared_inputs` is not decoration. A policy states which inputs it
    actually consults, so that a policy reading one signal while its
    specification lists ten is visible in the data rather than only in the
    code. ODP-SA-07 §5 lists ten inputs for the four-light policy; the shipped
    implementation reads one.
    """

    policy_id: str
    policy_version: str
    policy_kind: str
    tenant_id: str
    effective_from: datetime
    parameters: Mapping[str, Any]
    declared_inputs: tuple[str, ...]
    effective_to: datetime | None = None
    change_reason: str = ""
    rollback_policy_version: str | None = None
    approved_by: str = ""
    owner_role: str = ""

    def covers(self, instant: datetime) -> bool:
        """Whether this version was in force at `instant`.

        The window is half-open: `effective_from` inclusive, `effective_to`
        exclusive. Close-and-insert sets the outgoing version's `effective_to`
        to the incoming version's `effective_from`, so a half-open window means
        the changeover instant resolves to exactly one version rather than
        two or none.
        """
        if instant < self.effective_from:
            return False
        return self.effective_to is None or instant < self.effective_to

    def reads(self, input_name: str) -> bool:
        """Whether this version declares that it consults `input_name`.

        An input absent from `declared_inputs` is not consulted. Callers must
        not infer that an undeclared input is read but unlisted.
        """
        return input_name in self.declared_inputs

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_kind": self.policy_kind,
            "tenant_id": self.tenant_id,
            "effective_from": self.effective_from.isoformat(),
            "effective_to": (
                self.effective_to.isoformat() if self.effective_to is not None else None
            ),
            "parameters": dict(self.parameters),
            "declared_inputs": list(self.declared_inputs),
            "change_reason": self.change_reason,
            "rollback_policy_version": self.rollback_policy_version,
            "approved_by": self.approved_by,
            "owner_role": self.owner_role,
        }


class DecisionPolicyRepository(Protocol):
    """Where policy versions come from."""

    def find_effective(
        self, *, policy_kind: str, tenant_id: str, at: datetime
    ) -> DecisionPolicy | None:
        """The version in force for this kind and tenant at `at`, or None."""


def resolve_policy(
    repository: DecisionPolicyRepository,
    *,
    policy_kind: str,
    tenant_id: str,
    at: datetime,
) -> DecisionPolicy:
    """Resolve the governing policy, or refuse.

    There is deliberately no `default` parameter. A caller that cannot resolve
    a policy must not produce the decision.
    """
    policy = repository.find_effective(
        policy_kind=policy_kind, tenant_id=tenant_id, at=at
    )
    if policy is None:
        raise PolicyResolutionError(
            f"no {policy_kind} policy in force for tenant {tenant_id} "
            f"at {at.isoformat()}; refusing to decide without one"
        )
    return policy


@dataclass
class InMemoryDecisionPolicyRepository:
    """Reference implementation and test double.

    Holds the same invariant the unique partial index holds in SQL: at most one
    version in force per (policy_id, tenant_id).
    """

    _versions: list[DecisionPolicy] = field(default_factory=list)

    def __init__(self, versions: Iterable[DecisionPolicy] = ()) -> None:
        self._versions = []
        for version in versions:
            self.add(version)

    @property
    def versions(self) -> Sequence[DecisionPolicy]:
        return tuple(self._versions)

    def add(self, policy: DecisionPolicy) -> None:
        if policy.effective_to is None and self._in_force(
            policy.policy_id, policy.tenant_id
        ):
            raise PolicySupersedeError(
                f"{policy.policy_id} already has a version in force for tenant "
                f"{policy.tenant_id}; supersede it instead of adding a second"
            )
        self._versions.append(policy)

    def supersede(self, incoming: DecisionPolicy) -> DecisionPolicy:
        """Close the version in force and insert `incoming`.

        The outgoing version keeps every other field exactly as it stood --
        ODP-AC-BR-003 requires the old version retained, not amended. Only its
        `effective_to` is set, and only to the incoming version's start.
        """
        current = self._in_force(incoming.policy_id, incoming.tenant_id)
        if current is None:
            raise PolicySupersedeError(
                f"{incoming.policy_id} has no version in force for tenant "
                f"{incoming.tenant_id} to supersede"
            )
        if incoming.effective_from <= current.effective_from:
            raise PolicySupersedeError(
                "the incoming version must start after the one it supersedes"
            )
        closed = DecisionPolicy(
            **{**current.__dict__, "effective_to": incoming.effective_from}
        )
        self._versions = [v for v in self._versions if v is not current]
        self._versions.append(closed)
        self._versions.append(incoming)
        return closed

    def find_effective(
        self, *, policy_kind: str, tenant_id: str, at: datetime
    ) -> DecisionPolicy | None:
        matches = [
            version
            for version in self._versions
            if version.policy_kind == policy_kind
            and version.tenant_id == tenant_id
            and version.covers(at)
        ]
        if not matches:
            return None
        # The in-force invariant makes more than one match impossible for a
        # single policy_id; across policy_ids of the same kind, the most
        # recently started one governs.
        return max(matches, key=lambda version: version.effective_from)

    def _in_force(self, policy_id: str, tenant_id: str) -> DecisionPolicy | None:
        for version in self._versions:
            if (
                version.policy_id == policy_id
                and version.tenant_id == tenant_id
                and version.effective_to is None
            ):
                return version
        return None
