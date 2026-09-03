"""HeatZone composition, lineage, and merge/split domain model (ODP-FR-HZ-006).

Governs spatial zone compositions across H3 atomic units, parent/child split lineage,
append-only lifecycle, and operator overrides under DecisionPolicy.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

ZONE_ID_REGEX = re.compile(r"^MZ-[0-9a-f]{16}$")
COMPOSITION_MODEL_VERSION = "heatzone-composition-v1"


class CompositionKind(StrEnum):
    """Categorical kind of heat-zone composition."""

    MERGED = "MERGED"
    SPLIT_CHILD = "SPLIT_CHILD"
    ATOMIC = "ATOMIC"


class CompositionValidationError(ValueError):
    """Raised when a composition record violates domain or persistence constraints."""


def generate_merged_zone_id(member_cell_ids: Sequence[str]) -> str:
    """Generate a deterministic merged zone identifier from member cell IDs.

    Enforces the MZ-{hash16} format and guarantees isolation from atomic cell UUIDs.
    """
    cleaned = sorted(str(cell_id).strip() for cell_id in member_cell_ids if str(cell_id).strip())
    if not cleaned:
        raise CompositionValidationError("member_cell_ids must not be empty to generate a zone_id")
    digest = hashlib.sha256(":".join(cleaned).encode("utf-8")).hexdigest()[:16]
    return f"MZ-{digest}"


def parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = datetime.now(UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


@dataclass(frozen=True)
class HeatZoneCompositionRecord:
    """A single atomic cell membership record within a heat-zone composition.

    Mirrors expansion.heatzone_composition in PostgreSQL.
    """

    zone_id: str
    tenant_id: str
    member_cell_id: str
    composition_kind: CompositionKind
    composition_id: str = field(default_factory=lambda: str(uuid4()))
    parent_zone_id: str | None = None
    decided_by: str = "system"
    decided_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    decision_policy_version_id: str = ""
    model_version: str = COMPOSITION_MODEL_VERSION
    override_reason: str | None = None
    reverted_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        validate_composition_record(self)

    @property
    def is_active(self) -> bool:
        return self.reverted_at is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "composition_id": self.composition_id,
            "zone_id": self.zone_id,
            "tenant_id": self.tenant_id,
            "member_cell_id": self.member_cell_id,
            "composition_kind": self.composition_kind.value,
            "parent_zone_id": self.parent_zone_id,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.isoformat(),
            "decision_policy_version_id": self.decision_policy_version_id,
            "model_version": self.model_version,
            "override_reason": self.override_reason,
            "reverted_at": self.reverted_at.isoformat() if self.reverted_at else None,
            "created_at": self.created_at.isoformat(),
            "is_active": self.is_active,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> HeatZoneCompositionRecord:
        raw_kind = data.get("composition_kind", CompositionKind.MERGED)
        kind = raw_kind if isinstance(raw_kind, CompositionKind) else CompositionKind(str(raw_kind))
        return cls(
            composition_id=str(data.get("composition_id") or uuid4()),
            zone_id=str(data["zone_id"]),
            tenant_id=str(data["tenant_id"]),
            member_cell_id=str(data["member_cell_id"]),
            composition_kind=kind,
            parent_zone_id=str(data["parent_zone_id"]) if data.get("parent_zone_id") else None,
            decided_by=str(data.get("decided_by", "system")),
            decided_at=parse_datetime(data.get("decided_at")),
            decision_policy_version_id=str(data.get("decision_policy_version_id", "")),
            model_version=str(data.get("model_version", COMPOSITION_MODEL_VERSION)),
            override_reason=str(data["override_reason"]) if data.get("override_reason") else None,
            reverted_at=parse_datetime(data["reverted_at"]) if data.get("reverted_at") else None,
            created_at=parse_datetime(data.get("created_at")),
        )


def validate_composition_record(record: HeatZoneCompositionRecord) -> None:
    """Validate all domain and database-level constraints for a composition record."""
    if not ZONE_ID_REGEX.match(record.zone_id):
        raise CompositionValidationError(
            f"zone_id '{record.zone_id}' does not match required format '^MZ-[0-9a-f]{{16}}$'"
        )

    if record.composition_kind == CompositionKind.SPLIT_CHILD:
        if not record.parent_zone_id:
            raise CompositionValidationError("SPLIT_CHILD composition must specify parent_zone_id")
    else:
        if record.parent_zone_id is not None:
            raise CompositionValidationError(
                f"{record.composition_kind} composition must not have parent_zone_id"
            )

    if record.decided_by == "system":
        if record.override_reason is not None:
            raise CompositionValidationError("System decision must not carry override_reason")
    else:
        if not record.override_reason or not record.override_reason.strip():
            raise CompositionValidationError(
                f"Human decision by '{record.decided_by}' requires a non-empty override_reason"
            )

    if record.reverted_at is not None and record.reverted_at < record.decided_at:
        raise CompositionValidationError(
            f"reverted_at ({record.reverted_at.isoformat()}) cannot be earlier than decided_at ({record.decided_at.isoformat()})"
        )

    if record.decision_policy_version_id:
        expected_suffix = f":{record.tenant_id}"
        if not record.decision_policy_version_id.endswith(expected_suffix):
            raise CompositionValidationError(
                f"decision_policy_version_id '{record.decision_policy_version_id}' does not belong to tenant '{record.tenant_id}'"
            )


class ProposalStatus(StrEnum):
    """Lifecycle status of a merge/split proposal."""

    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"


@dataclass(frozen=True)
class MergeSplitProposalRecord:
    """Persisted heat-zone merge/split proposal for Operator preview and approval.

    A split is one proposal, not one proposal per child. `child_partitions`
    carries every side of the division, so approving it either lands the whole
    new topology or lands none of it. Emitting a proposal per child would let an
    operator approve one side, retire the parent, and leave the other side's
    cells in no active zone at all -- a state no later approval can repair,
    because the parent it would have to be split from is already gone.
    """

    proposal_id: str
    zone_id: str
    tenant_id: str
    composition_kind: CompositionKind
    member_cell_ids: tuple[str, ...]
    parent_zone_id: str | None
    ndcg_gain: float
    cannibalization_variance_reduction: float
    correlation_rho: float
    disconnect_index: float
    confidence: float
    model_version: str
    policy_version_id: str
    status: ProposalStatus = ProposalStatus.PROPOSED
    split_density_ratio: float | None = None
    child_partitions: tuple[tuple[str, ...], ...] = ()
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    approved_by: str | None = None
    approved_at: datetime | None = None
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        validate_proposal_record(self)

    def child_zone_ids(self) -> tuple[str, ...]:
        """Deterministic zone id for each child, in partition order."""
        return tuple(generate_merged_zone_id(part) for part in self.child_partitions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "zone_id": self.zone_id,
            "tenant_id": self.tenant_id,
            "composition_kind": self.composition_kind.value,
            "member_cell_ids": list(self.member_cell_ids),
            "member_count": len(self.member_cell_ids),
            "parent_zone_id": self.parent_zone_id,
            "ndcg_gain": self.ndcg_gain,
            "cannibalization_variance_reduction": self.cannibalization_variance_reduction,
            "correlation_rho": self.correlation_rho,
            "disconnect_index": self.disconnect_index,
            "split_density_ratio": self.split_density_ratio,
            "child_partitions": [list(part) for part in self.child_partitions],
            "child_zone_ids": list(self.child_zone_ids()),
            "confidence": self.confidence,
            "model_version": self.model_version,
            "policy_version_id": self.policy_version_id,
            "status": self.status.value,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "created_at": self.created_at.isoformat(),
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "rejection_reason": self.rejection_reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MergeSplitProposalRecord:
        raw_kind = data.get("composition_kind", CompositionKind.MERGED)
        kind = raw_kind if isinstance(raw_kind, CompositionKind) else CompositionKind(str(raw_kind))
        raw_status = data.get("status", ProposalStatus.PROPOSED)
        status = raw_status if isinstance(raw_status, ProposalStatus) else ProposalStatus(str(raw_status))
        return cls(
            proposal_id=str(data["proposal_id"]),
            zone_id=str(data["zone_id"]),
            tenant_id=str(data["tenant_id"]),
            composition_kind=kind,
            member_cell_ids=tuple(str(x) for x in data.get("member_cell_ids", ())),
            parent_zone_id=str(data["parent_zone_id"]) if data.get("parent_zone_id") else None,
            ndcg_gain=float(data.get("ndcg_gain", 0.0)),
            cannibalization_variance_reduction=float(data.get("cannibalization_variance_reduction", 0.0)),
            correlation_rho=float(data.get("correlation_rho", 0.0)),
            disconnect_index=float(data.get("disconnect_index", 0.0)),
            confidence=float(data.get("confidence", 0.0)),
            model_version=str(data.get("model_version", COMPOSITION_MODEL_VERSION)),
            policy_version_id=str(data.get("policy_version_id", "")),
            status=status,
            split_density_ratio=float(data["split_density_ratio"]) if data.get("split_density_ratio") is not None else None,
            child_partitions=tuple(
                tuple(str(cell) for cell in part) for part in data.get("child_partitions", ())
            ),
            reasons=tuple(str(x) for x in data.get("reasons", ())),
            warnings=tuple(str(x) for x in data.get("warnings", ())),
            created_at=parse_datetime(data.get("created_at")),
            approved_by=str(data["approved_by"]) if data.get("approved_by") else None,
            approved_at=parse_datetime(data["approved_at"]) if data.get("approved_at") else None,
            rejection_reason=str(data["rejection_reason"]) if data.get("rejection_reason") else None,
        )


def validate_proposal_record(record: MergeSplitProposalRecord) -> None:
    """Hold the invariants that make a split approvable in one step.

    A split proposal that does not describe the whole division is not a smaller
    split, it is an unapplyable one: approving it retires the parent zone and
    leaves whatever it failed to mention outside every active zone.
    """
    if record.composition_kind != CompositionKind.SPLIT_CHILD:
        if record.child_partitions:
            raise CompositionValidationError(
                f"{record.composition_kind} proposal must not carry child_partitions; "
                "only a split divides a zone"
            )
        return

    if not record.parent_zone_id:
        raise CompositionValidationError("SPLIT_CHILD proposal must specify parent_zone_id")

    if len(record.child_partitions) < 2:
        raise CompositionValidationError(
            f"SPLIT_CHILD proposal '{record.proposal_id}' carries "
            f"{len(record.child_partitions)} child partition(s); a split is only "
            "approvable as the whole division, so it needs at least 2"
        )

    seen: set[str] = set()
    for index, part in enumerate(record.child_partitions, start=1):
        if not part:
            raise CompositionValidationError(
                f"child partition {index} of proposal '{record.proposal_id}' is empty"
            )
        overlap = seen & set(part)
        if overlap:
            raise CompositionValidationError(
                f"cell(s) {sorted(overlap)} appear in more than one child partition of "
                f"proposal '{record.proposal_id}'; a cell belongs to exactly one child"
            )
        seen.update(part)

    if seen != set(record.member_cell_ids):
        missing = sorted(set(record.member_cell_ids) - seen)
        extra = sorted(seen - set(record.member_cell_ids))
        raise CompositionValidationError(
            f"child partitions of proposal '{record.proposal_id}' do not cover its members "
            f"exactly (unassigned: {missing}, not a member: {extra})"
        )


def approval_zone_assignments(
    record: MergeSplitProposalRecord,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """The zones an approval of `record` must create, and their members.

    A merge lands one zone; a split lands one zone per child partition. Both
    repository implementations route through this so the durable and in-memory
    approvals cannot drift into producing different topologies from the same
    proposal.
    """
    if record.composition_kind == CompositionKind.SPLIT_CHILD:
        return tuple(
            (generate_merged_zone_id(part), tuple(part)) for part in record.child_partitions
        )
    return ((record.zone_id, tuple(record.member_cell_ids)),)


@dataclass(frozen=True)
class ZoneLineage:
    """Aggregated lineage and active structure of a heat zone."""

    zone_id: str
    tenant_id: str
    composition_kind: CompositionKind
    member_cell_ids: tuple[str, ...]
    parent_zone_id: str | None
    decided_by: str
    decided_at: datetime
    decision_policy_version_id: str
    override_reason: str | None
    reverted_at: datetime | None
    is_active: bool
    model_version: str = COMPOSITION_MODEL_VERSION
    records: tuple[HeatZoneCompositionRecord, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "tenant_id": self.tenant_id,
            "composition_kind": self.composition_kind.value,
            "member_cell_ids": list(self.member_cell_ids),
            "member_count": len(self.member_cell_ids),
            "parent_zone_id": self.parent_zone_id,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.isoformat(),
            "decision_policy_version_id": self.decision_policy_version_id,
            "model_version": self.model_version,
            "override_reason": self.override_reason,
            "reverted_at": self.reverted_at.isoformat() if self.reverted_at else None,
            "is_active": self.is_active,
            "records": [r.to_dict() for r in self.records],
        }


__all__ = [
    "COMPOSITION_MODEL_VERSION",
    "CompositionKind",
    "CompositionValidationError",
    "HeatZoneCompositionRecord",
    "MergeSplitProposalRecord",
    "ProposalStatus",
    "ZONE_ID_REGEX",
    "ZoneLineage",
    "approval_zone_assignments",
    "generate_merged_zone_id",
    "parse_datetime",
    "validate_composition_record",
    "validate_proposal_record",
]
