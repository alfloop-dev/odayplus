"""Pure NetPlan optimization model primitives."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

NETPLAN_POLICY_VERSION = "netplan-network-policy-v1"
BUSINESS_UAT_UNVERIFIED = "BUSINESS_UAT_UNVERIFIED"
BUSINESS_UAT_VERIFIED = "BUSINESS_UAT_VERIFIED"
GOVERNED_DISABLED = "GOVERNED_DISABLED"
GOVERNED_ENABLED = "GOVERNED_ENABLED"


class NetworkAction(StrEnum):
    OPEN = "OPEN"
    KEEP = "KEEP"
    IMPROVE = "IMPROVE"
    MOVE = "MOVE"
    EXIT = "EXIT"


@dataclass(frozen=True)
class ActionOption:
    entity_id: str
    action: NetworkAction
    expected_gross_margin: float
    budget_cost: float
    risk_score: float
    capacity_delta: int = 0
    source_snapshot_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ActionOption:
        return cls(
            entity_id=str(data["entity_id"]),
            action=NetworkAction(str(data["action"]).upper()),
            expected_gross_margin=float(data.get("expected_gross_margin", data.get("expected_gm", 0.0))),
            budget_cost=float(data.get("budget_cost", data.get("cost", 0.0))),
            risk_score=_bounded(data.get("risk_score", data.get("risk", 0.0))),
            capacity_delta=int(data.get("capacity_delta", 0)),
            source_snapshot_ids=tuple(str(v) for v in data.get("source_snapshot_ids", ())),
            notes=tuple(str(v) for v in data.get("notes", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "action": self.action.value,
            "expected_gross_margin": self.expected_gross_margin,
            "budget_cost": self.budget_cost,
            "risk_score": self.risk_score,
            "capacity_delta": self.capacity_delta,
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class NetPlanConstraints:
    max_budget: float
    min_expected_gross_margin: float | None = None
    min_capacity_delta: int | None = None
    max_average_risk: float | None = None
    min_action_counts: Mapping[NetworkAction, int] = field(default_factory=dict)
    max_action_counts: Mapping[NetworkAction, int] = field(default_factory=dict)
    policy_version: str = NETPLAN_POLICY_VERSION

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> NetPlanConstraints:
        return cls(
            max_budget=float(data["max_budget"]),
            min_expected_gross_margin=_optional_float(data.get("min_expected_gross_margin", data.get("min_expected_gm"))),
            min_capacity_delta=_optional_int(data.get("min_capacity_delta")),
            max_average_risk=_optional_float(data.get("max_average_risk", data.get("max_risk"))),
            min_action_counts=_action_count_mapping(data.get("min_action_counts", {})),
            max_action_counts=_action_count_mapping(data.get("max_action_counts", {})),
            policy_version=str(data.get("policy_version", NETPLAN_POLICY_VERSION)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_budget": self.max_budget,
            "min_expected_gross_margin": self.min_expected_gross_margin,
            "min_capacity_delta": self.min_capacity_delta,
            "max_average_risk": self.max_average_risk,
            "min_action_counts": {k.value: v for k, v in self.min_action_counts.items()},
            "max_action_counts": {k.value: v for k, v in self.max_action_counts.items()},
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True)
class ManagementBaselineInput:
    baseline_id: str
    baseline_name: str
    scenario_id: str
    actions_by_entity: Mapping[str, NetworkAction]
    approval_receipt_id: str
    source_snapshot_ids: tuple[str, ...] = ()
    scope: str = ""
    release_id: str = ""

    def compute_canonical_hash(
        self,
        constraints: NetPlanConstraints | None = None,
        risk_penalty: float = 100_000.0,
    ) -> str:
        payload = {
            "baseline_id": self.baseline_id,
            "baseline_name": self.baseline_name,
            "scenario_id": self.scenario_id,
            "actions_by_entity": {k: v.value for k, v in sorted(self.actions_by_entity.items())},
            "source_snapshot_ids": sorted(self.source_snapshot_ids),
            "scope": self.scope,
            "release_id": self.release_id,
            "risk_penalty": float(risk_penalty),
        }
        if constraints is not None:
            payload["constraints"] = constraints.to_dict()
        return canonical_sha256(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_id": self.baseline_id,
            "baseline_name": self.baseline_name,
            "scenario_id": self.scenario_id,
            "actions_by_entity": {k: v.value for k, v in self.actions_by_entity.items()},
            "approval_receipt_id": self.approval_receipt_id,
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "scope": self.scope,
            "release_id": self.release_id,
            "business_uat_status": BUSINESS_UAT_UNVERIFIED,
            "governance_status": GOVERNED_DISABLED,
        }


@dataclass(frozen=True)
class ManagementApprovalReceipt:
    """Immutable readback from the configured management approval authority."""

    receipt_id: str
    source_system: str
    principal_id: str
    principal_role: str
    decision: str
    approval_reference_id: str
    issued_at: str
    expires_at: str
    scenario_id: str
    baseline_id: str
    baseline_name: str
    scope: str
    release_id: str
    policy_version: str
    actions_by_entity: Mapping[str, NetworkAction]
    source_snapshot_ids: tuple[str, ...]
    baseline_content_hash: str
    solver_problem_hash: str
    receipt_hash: str

    def compute_receipt_hash(self) -> str:
        return canonical_sha256(
            {
                "receipt_id": self.receipt_id,
                "source_system": self.source_system,
                "principal_id": self.principal_id,
                "principal_role": self.principal_role,
                "decision": self.decision,
                "approval_reference_id": self.approval_reference_id,
                "issued_at": self.issued_at,
                "expires_at": self.expires_at,
                "scenario_id": self.scenario_id,
                "baseline_id": self.baseline_id,
                "baseline_name": self.baseline_name,
                "scope": self.scope,
                "release_id": self.release_id,
                "policy_version": self.policy_version,
                "actions_by_entity": {
                    entity_id: action.value
                    for entity_id, action in sorted(self.actions_by_entity.items())
                },
                "source_snapshot_ids": sorted(self.source_snapshot_ids),
                "baseline_content_hash": self.baseline_content_hash,
                "solver_problem_hash": self.solver_problem_hash,
            }
        )


@dataclass(frozen=True)
class ManagementApprovalExpectation:
    receipt_id: str
    scenario_id: str
    baseline_id: str
    baseline_name: str
    scope: str
    release_id: str
    policy_version: str
    actions_by_entity: Mapping[str, NetworkAction]
    source_snapshot_ids: tuple[str, ...]
    baseline_content_hash: str
    solver_problem_hash: str


@dataclass(frozen=True)
class _AuthorityVerificationAttestation:
    """Opaque proof that the configured verifier performed authority readback."""

    attestation_id: str
    receipt_hash: str
    expectation_hash: str
    authority_identity_hash: str
    verified_at: str
    seal: str


_AUTHORITY_ATTESTATION_KEY = secrets.token_bytes(32)


@dataclass(frozen=True)
class ManagementApprovalVerification:
    verified: bool
    receipt: ManagementApprovalReceipt | None
    violations: tuple[str, ...]
    _authority_attestation: _AuthorityVerificationAttestation | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    @classmethod
    def _authority_verified(
        cls,
        *,
        receipt: ManagementApprovalReceipt,
        expectation: ManagementApprovalExpectation,
        source_system: str,
        principal_id: str,
        principal_role: str,
        verified_at: datetime,
    ) -> ManagementApprovalVerification:
        verification = cls(verified=True, receipt=receipt, violations=())
        attestation = _issue_authority_attestation(
            receipt=receipt,
            expectation=expectation,
            source_system=source_system,
            principal_id=principal_id,
            principal_role=principal_role,
            verified_at=verified_at,
        )
        object.__setattr__(verification, "_authority_attestation", attestation)
        return verification

    @property
    def authority_attestation_id(self) -> str | None:
        attestation = self._authority_attestation
        return attestation.attestation_id if attestation is not None else None

    @property
    def authority_binding_hash(self) -> str | None:
        attestation = self._authority_attestation
        if attestation is None:
            return None
        return canonical_sha256(_attestation_public_payload(attestation))

    @property
    def authority_verified_at(self) -> str | None:
        attestation = self._authority_attestation
        return attestation.verified_at if attestation is not None else None

    def authority_attests_receipt(self, receipt: ManagementApprovalReceipt) -> bool:
        attestation = self._authority_attestation
        return (
            self.verified
            and self.receipt == receipt
            and not self.violations
            and attestation is not None
            and _authority_attestation_is_valid(attestation, receipt)
        )


class ManagementApprovalReceiptVerifier(Protocol):
    def verify(
        self,
        expectation: ManagementApprovalExpectation,
    ) -> ManagementApprovalVerification: ...


class FixedManagementApprovalReceiptVerifier:
    """Resolve only receipts read back from one configured authority and principal."""

    def __init__(
        self,
        *,
        receipts: Mapping[str, ManagementApprovalReceipt],
        source_system: str,
        principal_id: str,
        principal_role: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        fixed_identity = (source_system, principal_id, principal_role)
        if any(not value.strip() or value.strip().upper() == "ANY" for value in fixed_identity):
            raise ValueError(
                "approval source system, principal, and role must be fixed non-wildcard values"
            )
        self._receipts = dict(receipts)
        self._source_system = source_system
        self._principal_id = principal_id
        self._principal_role = principal_role
        self._clock = clock or (lambda: datetime.now(UTC))

    def verify(
        self,
        expectation: ManagementApprovalExpectation,
    ) -> ManagementApprovalVerification:
        violations: list[str] = []
        receipt_id = expectation.receipt_id.strip()
        if not receipt_id or receipt_id.upper() in {"ANY", "UNVERIFIED"}:
            return ManagementApprovalVerification(
                verified=False,
                receipt=None,
                violations=("approval_receipt_id_invalid",),
            )

        receipt = self._receipts.get(receipt_id)
        if receipt is None:
            return ManagementApprovalVerification(
                verified=False,
                receipt=None,
                violations=("authoritative_approval_unresolved",),
            )

        if receipt.receipt_id != receipt_id:
            violations.append("approval_receipt_id_mismatch")
        if receipt.source_system != self._source_system:
            violations.append("approval_source_system_mismatch")
        if receipt.principal_id != self._principal_id:
            violations.append("approval_principal_mismatch")
        if receipt.principal_role != self._principal_role:
            violations.append("approval_principal_role_mismatch")
        if receipt.decision != "APPROVED":
            violations.append("approval_decision_not_active")
        if not receipt.approval_reference_id.strip():
            violations.append("approval_reference_missing")

        issued_at = strict_utc_datetime(receipt.issued_at)
        expires_at = strict_utc_datetime(receipt.expires_at)
        authority_time = self._clock()
        evaluation_time = authority_time.astimezone(UTC) if authority_time.tzinfo else None
        if issued_at is None:
            violations.append("approval_issued_at_invalid")
        if expires_at is None:
            violations.append("approval_expires_at_invalid")
        if evaluation_time is None:
            violations.append("approval_evaluation_time_not_utc")
        elif issued_at is not None and issued_at > evaluation_time:
            violations.append("approval_issued_in_future")
        elif expires_at is not None and expires_at <= evaluation_time:
            violations.append("approval_expired")
        if issued_at is not None and expires_at is not None and expires_at <= issued_at:
            violations.append("approval_time_window_invalid")

        exact_matches = {
            "approval_scenario_mismatch": receipt.scenario_id == expectation.scenario_id,
            "approval_baseline_id_mismatch": receipt.baseline_id == expectation.baseline_id,
            "approval_baseline_name_mismatch": receipt.baseline_name == expectation.baseline_name,
            "approval_scope_mismatch": receipt.scope == expectation.scope,
            "approval_release_mismatch": receipt.release_id == expectation.release_id,
            "approval_policy_version_mismatch": (
                receipt.policy_version == expectation.policy_version
            ),
            "approval_actions_domain_mismatch": (
                dict(receipt.actions_by_entity) == dict(expectation.actions_by_entity)
            ),
            "approval_source_snapshots_mismatch": (
                tuple(sorted(receipt.source_snapshot_ids))
                == tuple(sorted(expectation.source_snapshot_ids))
            ),
            "approval_baseline_hash_mismatch": (
                receipt.baseline_content_hash == expectation.baseline_content_hash
            ),
            "approval_solver_problem_hash_mismatch": (
                receipt.solver_problem_hash == expectation.solver_problem_hash
            ),
            "approval_receipt_integrity_mismatch": (
                receipt.receipt_hash == receipt.compute_receipt_hash()
            ),
        }
        violations.extend(reason for reason, matches in exact_matches.items() if not matches)
        if violations:
            return ManagementApprovalVerification(
                verified=False,
                receipt=receipt,
                violations=tuple(violations),
            )
        return ManagementApprovalVerification._authority_verified(
            receipt=receipt,
            expectation=expectation,
            source_system=self._source_system,
            principal_id=self._principal_id,
            principal_role=self._principal_role,
            verified_at=evaluation_time,
        )


@dataclass(frozen=True)
class ManagementBaselineComparisonReceipt:
    baseline_id: str
    baseline_feasible: bool
    baseline_objective_value: float | None
    solver_objective_value: float
    objective_gain_over_baseline: float | None
    superior_or_equal: bool
    baseline_canonical_hash: str = ""
    solver_problem_hash: str = ""
    solver_result_hash: str = ""
    scenario_hash: str = ""
    source_snapshot_hash: str = ""
    actions_domain_hash: str = ""
    approval_receipt_hash: str = ""
    comparison_output_hash: str = ""
    business_uat_status: str = BUSINESS_UAT_UNVERIFIED
    governance_status: str = GOVERNED_DISABLED
    approval_verified: bool = False
    baseline_constraint_violations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_id": self.baseline_id,
            "baseline_feasible": self.baseline_feasible,
            "baseline_objective_value": self.baseline_objective_value,
            "solver_objective_value": self.solver_objective_value,
            "objective_gain_over_baseline": self.objective_gain_over_baseline,
            "superior_or_equal": self.superior_or_equal,
            "baseline_canonical_hash": self.baseline_canonical_hash,
            "solver_problem_hash": self.solver_problem_hash,
            "solver_result_hash": self.solver_result_hash,
            "scenario_hash": self.scenario_hash,
            "source_snapshot_hash": self.source_snapshot_hash,
            "actions_domain_hash": self.actions_domain_hash,
            "approval_receipt_hash": self.approval_receipt_hash,
            "comparison_output_hash": self.comparison_output_hash,
            "business_uat_status": self.business_uat_status,
            "governance_status": self.governance_status,
            "approval_verified": self.approval_verified,
            "baseline_constraint_violations": list(self.baseline_constraint_violations),
        }


@dataclass(frozen=True)
class InfeasibilityDiagnosis:
    violated_constraint: str
    affected_stores: tuple[str, ...]
    required_relaxation: str
    business_impact: str
    suggested_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "violated_constraint": self.violated_constraint,
            "affected_stores": list(self.affected_stores),
            "required_relaxation": self.required_relaxation,
            "business_impact": self.business_impact,
            "suggested_action": self.suggested_action,
        }


def _bounded(value: Any, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    numeric = float(value)
    return max(minimum, min(maximum, numeric))


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _action_count_mapping(data: Mapping[str, Any]) -> dict[NetworkAction, int]:
    return {NetworkAction(str(action).upper()): int(count) for action, count in data.items()}


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _expectation_hash(expectation: ManagementApprovalExpectation) -> str:
    return canonical_sha256(
        {
            "receipt_id": expectation.receipt_id,
            "scenario_id": expectation.scenario_id,
            "baseline_id": expectation.baseline_id,
            "baseline_name": expectation.baseline_name,
            "scope": expectation.scope,
            "release_id": expectation.release_id,
            "policy_version": expectation.policy_version,
            "actions_by_entity": {
                entity_id: action.value
                for entity_id, action in sorted(expectation.actions_by_entity.items())
            },
            "source_snapshot_ids": sorted(expectation.source_snapshot_ids),
            "baseline_content_hash": expectation.baseline_content_hash,
            "solver_problem_hash": expectation.solver_problem_hash,
        }
    )


def _attestation_public_payload(
    attestation: _AuthorityVerificationAttestation,
) -> dict[str, str]:
    return {
        "attestation_id": attestation.attestation_id,
        "receipt_hash": attestation.receipt_hash,
        "expectation_hash": attestation.expectation_hash,
        "authority_identity_hash": attestation.authority_identity_hash,
        "verified_at": attestation.verified_at,
    }


def _issue_authority_attestation(
    *,
    receipt: ManagementApprovalReceipt,
    expectation: ManagementApprovalExpectation,
    source_system: str,
    principal_id: str,
    principal_role: str,
    verified_at: datetime,
) -> _AuthorityVerificationAttestation:
    unsigned = _AuthorityVerificationAttestation(
        attestation_id=f"netplan-authority-attestation-{secrets.token_hex(16)}",
        receipt_hash=receipt.receipt_hash,
        expectation_hash=_expectation_hash(expectation),
        authority_identity_hash=canonical_sha256(
            {
                "source_system": source_system,
                "principal_id": principal_id,
                "principal_role": principal_role,
            }
        ),
        verified_at=verified_at.astimezone(UTC).isoformat(),
        seal="",
    )
    payload = canonical_sha256(_attestation_public_payload(unsigned)).encode()
    seal = hmac.new(_AUTHORITY_ATTESTATION_KEY, payload, hashlib.sha256).hexdigest()
    return _AuthorityVerificationAttestation(
        **{**_attestation_public_payload(unsigned), "seal": seal}
    )


def _authority_attestation_is_valid(
    attestation: _AuthorityVerificationAttestation,
    receipt: ManagementApprovalReceipt,
) -> bool:
    if (
        not attestation.attestation_id
        or attestation.receipt_hash != receipt.receipt_hash
        or receipt.receipt_hash != receipt.compute_receipt_hash()
    ):
        return False
    payload = canonical_sha256(_attestation_public_payload(attestation)).encode()
    expected_seal = hmac.new(
        _AUTHORITY_ATTESTATION_KEY,
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(attestation.seal, expected_seal)


def strict_utc_datetime(value: str) -> datetime | None:
    if not value or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        return None
    return parsed if parsed.tzinfo is UTC or parsed.utcoffset() == UTC.utcoffset(parsed) else None


__all__ = [
    "BUSINESS_UAT_UNVERIFIED",
    "BUSINESS_UAT_VERIFIED",
    "GOVERNED_DISABLED",
    "GOVERNED_ENABLED",
    "NETPLAN_POLICY_VERSION",
    "ActionOption",
    "FixedManagementApprovalReceiptVerifier",
    "InfeasibilityDiagnosis",
    "ManagementApprovalExpectation",
    "ManagementApprovalReceipt",
    "ManagementApprovalReceiptVerifier",
    "ManagementApprovalVerification",
    "ManagementBaselineComparisonReceipt",
    "ManagementBaselineInput",
    "NetPlanConstraints",
    "NetworkAction",
    "canonical_sha256",
    "strict_utc_datetime",
]
