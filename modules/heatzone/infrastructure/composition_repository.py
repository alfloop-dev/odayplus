"""HeatZone composition repository interfaces and in-memory implementation (ODP-FR-HZ-006).

Maintains append-only history of heatzone compositions, active member uniqueness,
lineage, operator overrides, and soft rollback.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from modules.heatzone.domain.composition import (
    CompositionKind,
    CompositionValidationError,
    HeatZoneCompositionRecord,
    ZoneLineage,
    validate_composition_record,
)


class HeatZoneCompositionRepository(Protocol):
    """Protocol for heatzone composition persistence."""

    def save_composition(self, record: HeatZoneCompositionRecord) -> HeatZoneCompositionRecord:
        ...

    def save_composition_batch(
        self, records: Sequence[HeatZoneCompositionRecord]
    ) -> list[HeatZoneCompositionRecord]:
        ...

    def get_composition(self, zone_id: str, tenant_id: str) -> list[HeatZoneCompositionRecord]:
        ...

    def get_active_for_cell(
        self, cell_id: str, tenant_id: str
    ) -> HeatZoneCompositionRecord | None:
        ...

    def list_compositions(
        self, tenant_id: str, active_only: bool = True
    ) -> list[HeatZoneCompositionRecord]:
        ...

    def revert_composition(
        self, zone_id: str, tenant_id: str, reverted_at: datetime | None = None
    ) -> list[HeatZoneCompositionRecord]:
        ...

    def override_composition(
        self,
        zone_id: str,
        tenant_id: str,
        decided_by: str,
        override_reason: str,
        decision_policy_version_id: str,
        new_kind: CompositionKind | None = None,
        new_cells: Sequence[str] | None = None,
        parent_zone_id: str | None = None,
    ) -> list[HeatZoneCompositionRecord]:
        ...

    def get_lineage(self, zone_id: str, tenant_id: str) -> ZoneLineage | None:
        ...


class InMemoryHeatZoneCompositionRepository:
    """In-memory reference implementation of HeatZoneCompositionRepository."""

    def __init__(self) -> None:
        self._records: list[HeatZoneCompositionRecord] = []

    def save_composition(self, record: HeatZoneCompositionRecord) -> HeatZoneCompositionRecord:
        validate_composition_record(record)
        # Check active uniqueness on member cell
        if record.is_active:
            for existing in self._records:
                if (
                    existing.tenant_id == record.tenant_id
                    and existing.member_cell_id == record.member_cell_id
                    and existing.is_active
                    and existing.composition_id != record.composition_id
                ):
                    raise CompositionValidationError(
                        f"cell '{record.member_cell_id}' is already an active member of zone '{existing.zone_id}'"
                    )
        self._records.append(record)
        return record

    def save_composition_batch(
        self, records: Sequence[HeatZoneCompositionRecord]
    ) -> list[HeatZoneCompositionRecord]:
        saved: list[HeatZoneCompositionRecord] = []
        for record in records:
            saved.append(self.save_composition(record))
        return saved

    def get_composition(self, zone_id: str, tenant_id: str) -> list[HeatZoneCompositionRecord]:
        return [
            r for r in self._records
            if r.tenant_id == tenant_id and r.zone_id == zone_id
        ]

    def get_active_for_cell(
        self, cell_id: str, tenant_id: str
    ) -> HeatZoneCompositionRecord | None:
        for r in self._records:
            if r.tenant_id == tenant_id and r.member_cell_id == cell_id and r.is_active:
                return r
        return None

    def list_compositions(
        self, tenant_id: str, active_only: bool = True
    ) -> list[HeatZoneCompositionRecord]:
        return [
            r for r in self._records
            if r.tenant_id == tenant_id and (not active_only or r.is_active)
        ]

    def revert_composition(
        self, zone_id: str, tenant_id: str, reverted_at: datetime | None = None
    ) -> list[HeatZoneCompositionRecord]:
        now = reverted_at or datetime.now(UTC)
        reverted: list[HeatZoneCompositionRecord] = []
        new_records: list[HeatZoneCompositionRecord] = []

        for r in self._records:
            if r.tenant_id == tenant_id and r.zone_id == zone_id and r.is_active:
                if r.reverted_at is not None:
                    raise CompositionValidationError(
                        f"composition {r.composition_id} is already reverted"
                    )
                updated = HeatZoneCompositionRecord(
                    composition_id=r.composition_id,
                    zone_id=r.zone_id,
                    tenant_id=r.tenant_id,
                    member_cell_id=r.member_cell_id,
                    composition_kind=r.composition_kind,
                    parent_zone_id=r.parent_zone_id,
                    decided_by=r.decided_by,
                    decided_at=r.decided_at,
                    decision_policy_version_id=r.decision_policy_version_id,
                    override_reason=r.override_reason,
                    reverted_at=now,
                    created_at=r.created_at,
                )
                reverted.append(updated)
                new_records.append(updated)
            else:
                new_records.append(r)

        if not reverted:
            raise CompositionValidationError(f"no active composition found for zone '{zone_id}'")

        self._records = new_records
        return reverted

    def override_composition(
        self,
        zone_id: str,
        tenant_id: str,
        decided_by: str,
        override_reason: str,
        decision_policy_version_id: str,
        new_kind: CompositionKind | None = None,
        new_cells: Sequence[str] | None = None,
        parent_zone_id: str | None = None,
    ) -> list[HeatZoneCompositionRecord]:
        now = datetime.now(UTC)
        active = [
            r for r in self._records
            if r.tenant_id == tenant_id and r.zone_id == zone_id and r.is_active
        ]
        if not active:
            raise CompositionValidationError(f"no active composition found for zone '{zone_id}' to override")

        # Step 1: soft rollback existing active rows
        self.revert_composition(zone_id, tenant_id, reverted_at=now)

        # Step 2: append new override records
        effective_kind = new_kind or active[0].composition_kind
        effective_cells = new_cells or [r.member_cell_id for r in active]
        effective_parent = parent_zone_id if parent_zone_id is not None else active[0].parent_zone_id

        created: list[HeatZoneCompositionRecord] = []
        for cell_id in effective_cells:
            record = HeatZoneCompositionRecord(
                zone_id=zone_id,
                tenant_id=tenant_id,
                member_cell_id=cell_id,
                composition_kind=effective_kind,
                parent_zone_id=effective_parent,
                decided_by=decided_by,
                decided_at=now,
                decision_policy_version_id=decision_policy_version_id,
                override_reason=override_reason,
                reverted_at=None,
            )
            created.append(self.save_composition(record))

        return created

    def get_lineage(self, zone_id: str, tenant_id: str) -> ZoneLineage | None:
        matching = [
            r for r in self._records
            if r.tenant_id == tenant_id and r.zone_id == zone_id
        ]
        if not matching:
            return None

        # Sort by decided_at desc
        sorted_records = sorted(matching, key=lambda r: r.decided_at, reverse=True)
        active_records = [r for r in sorted_records if r.is_active]
        latest_record = active_records[0] if active_records else sorted_records[0]

        member_cells = tuple(sorted({r.member_cell_id for r in (active_records or sorted_records)}))

        return ZoneLineage(
            zone_id=zone_id,
            tenant_id=tenant_id,
            composition_kind=latest_record.composition_kind,
            member_cell_ids=member_cells,
            parent_zone_id=latest_record.parent_zone_id,
            decided_by=latest_record.decided_by,
            decided_at=latest_record.decided_at,
            decision_policy_version_id=latest_record.decision_policy_version_id,
            override_reason=latest_record.override_reason,
            reverted_at=latest_record.reverted_at,
            is_active=len(active_records) > 0,
            records=tuple(sorted_records),
        )


__all__ = [
    "HeatZoneCompositionRepository",
    "InMemoryHeatZoneCompositionRepository",
]
