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
    "ZONE_ID_REGEX",
    "ZoneLineage",
    "generate_merged_zone_id",
    "parse_datetime",
    "validate_composition_record",
]
