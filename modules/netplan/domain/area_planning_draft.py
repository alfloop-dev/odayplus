"""Preliminary area proposals retain unset inputs before native NetPlan creation.

A draft records human planning intent. It is not a scenario, solve, approval,
execution or financial assessment, and confers none of those permissions.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


class AreaPlanningDraftInvalid(ValueError):
    """The supplied proposal fields do not satisfy the draft contract."""


class AreaPlanningDraftConflict(ValueError):
    """The proposal changed or has already become a native scenario."""


class AreaPlanningDraftStatus(StrEnum):
    DRAFT = "DRAFT"
    MATERIALIZED = "MATERIALIZED"


@dataclass(frozen=True)
class AreaPlanningFields:
    name: str
    quarter: str | None
    budget: float | None
    candidate_ids: tuple[str, ...]

    @staticmethod
    def _parse(value: Any) -> tuple[str, str | None, float | None, tuple[str, ...]]:
        if not isinstance(value, Mapping) or set(value) != {
            "name", "quarter", "budget", "candidateIds"
        }:
            raise AreaPlanningDraftInvalid("AREA_PLANNING_DRAFT_FIELDS_INVALID")
        name, quarter, budget, candidates = (
            value["name"], value["quarter"], value["budget"], value["candidateIds"]
        )
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise AreaPlanningDraftInvalid("AREA_PLANNING_DRAFT_NAME_REQUIRED")
        if quarter is not None and (
            not isinstance(quarter, str) or not re.fullmatch(r"[1-9][0-9]{3}Q[1-4]", quarter)
        ):
            raise AreaPlanningDraftInvalid("AREA_PLANNING_DRAFT_QUARTER_INVALID")
        if budget is not None:
            try:
                valid = type(budget) in (int, float) and math.isfinite(budget) and budget >= 0
            except OverflowError:
                valid = False
            if not valid:
                raise AreaPlanningDraftInvalid("AREA_PLANNING_DRAFT_BUDGET_INVALID")
            budget = float(budget)
        if not isinstance(candidates, list) or any(
            not isinstance(candidate, str)
            or not candidate.strip()
            or candidate != candidate.strip()
            or len(candidate) > 200
            for candidate in candidates
        ):
            raise AreaPlanningDraftInvalid("AREA_PLANNING_DRAFT_CANDIDATES_INVALID")
        if len(candidates) != len(set(candidates)):
            raise AreaPlanningDraftInvalid("AREA_PLANNING_DRAFT_DUPLICATE_CANDIDATE")
        return name.strip(), quarter, budget, tuple(candidates)

    @classmethod
    def from_mapping(cls, value: Any) -> AreaPlanningFields:
        return cls(*cls._parse(value))

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_ids, tuple):
            raise AreaPlanningDraftInvalid("AREA_PLANNING_DRAFT_CANDIDATES_INVALID")
        parsed = self._parse(self.to_dict())
        if parsed != (self.name, self.quarter, self.budget, self.candidate_ids):
            raise AreaPlanningDraftInvalid("AREA_PLANNING_DRAFT_FIELDS_NOT_CANONICAL")

    @property
    def inputs_complete(self) -> bool:
        """Completion is planning input completeness, never financial admission."""
        return self.quarter is not None and self.budget is not None and bool(self.candidate_ids)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "quarter": self.quarter, "budget": self.budget,
                "candidateIds": list(self.candidate_ids)}


@dataclass(frozen=True)
class AreaPlanningDraft:
    draft_id: str
    tenant_id: str
    created_by: str
    heat_zone_id: str
    fields: AreaPlanningFields
    version: int
    status: AreaPlanningDraftStatus
    created_at: datetime
    updated_at: datetime
    native_scenario_id: str | None = None
    materialized_by: str | None = None

    def __post_init__(self) -> None:
        try:
            for identity in (self.draft_id, self.tenant_id, self.created_by):
                UUID(identity)
        except (ValueError, TypeError, AttributeError):
            raise AreaPlanningDraftInvalid("AREA_PLANNING_AUTHENTICATED_SCOPE_REQUIRED") from None
        if (type(self.version) is not int or self.version < 1
                or not isinstance(self.fields, AreaPlanningFields)
                or not isinstance(self.status, AreaPlanningDraftStatus)
                or not isinstance(self.heat_zone_id, str) or not self.heat_zone_id
                or self.heat_zone_id != self.heat_zone_id.strip() or len(self.heat_zone_id) > 200
                or any(not isinstance(stamp, datetime) or stamp.utcoffset() is None
                       for stamp in (self.created_at, self.updated_at))
                or self.updated_at < self.created_at):
            raise AreaPlanningDraftInvalid("AREA_PLANNING_DRAFT_RECORD_INVALID")
        if self.status == AreaPlanningDraftStatus.DRAFT:
            if self.native_scenario_id is not None or self.materialized_by is not None:
                raise AreaPlanningDraftInvalid("AREA_PLANNING_DRAFT_RECORD_INVALID")
        else:
            if (not self.fields.inputs_complete or not isinstance(self.native_scenario_id, str)
                    or not self.native_scenario_id.strip()
                    or self.native_scenario_id != self.native_scenario_id.strip()
                    or len(self.native_scenario_id) > 200):
                raise AreaPlanningDraftInvalid("AREA_PLANNING_DRAFT_RECORD_INVALID")
            try:
                UUID(self.materialized_by)
            except (ValueError, TypeError, AttributeError):
                raise AreaPlanningDraftInvalid("AREA_PLANNING_AUTHENTICATED_ACTOR_REQUIRED") from None

    def to_dict(self) -> dict[str, Any]:
        return {"draftId": self.draft_id, "tenantId": self.tenant_id,
                "createdBy": self.created_by, "heatZoneId": self.heat_zone_id,
                **self.fields.to_dict(), "version": self.version, "status": self.status.value,
                "createdAt": self.created_at.isoformat(), "updatedAt": self.updated_at.isoformat(),
                "nativeScenarioId": self.native_scenario_id, "materializedBy": self.materialized_by}

    @classmethod
    def create(cls, *, tenant_id: str, created_by: str, heat_zone_id: str,
               fields: AreaPlanningFields) -> AreaPlanningDraft:
        # HTTP ownership must still bind these identities and admit the actual
        # current native zone. A well-formed identifier is not an access grant.
        try:
            UUID(tenant_id)
            UUID(created_by)
        except (ValueError, TypeError, AttributeError):
            raise AreaPlanningDraftInvalid("AREA_PLANNING_AUTHENTICATED_SCOPE_REQUIRED") from None
        if not isinstance(heat_zone_id, str) or not heat_zone_id.strip() or len(heat_zone_id) > 200:
            raise AreaPlanningDraftInvalid("AREA_PLANNING_HEAT_ZONE_REQUIRED")
        if not isinstance(fields, AreaPlanningFields):
            raise AreaPlanningDraftInvalid("AREA_PLANNING_DRAFT_FIELDS_INVALID")
        stamp = datetime.now(UTC)
        return cls(str(uuid4()), tenant_id, created_by, heat_zone_id, fields, 1,
                   AreaPlanningDraftStatus.DRAFT, stamp, stamp)

    def update(self, fields: AreaPlanningFields, *, expected_version: int) -> AreaPlanningDraft:
        self._require_current(expected_version)
        if not isinstance(fields, AreaPlanningFields):
            raise AreaPlanningDraftInvalid("AREA_PLANNING_DRAFT_FIELDS_INVALID")
        return replace(self, fields=fields, version=self.version + 1, updated_at=datetime.now(UTC))

    def materialize(self, *, scenario_id: str, actor: str,
                    expected_version: int) -> AreaPlanningDraft:
        """Record only a scenario actually committed by the native owner.

        The caller must hold original netplan CREATE, pass current canonical
        financial admission, and bind scenario creation and this CAS to one SQL
        transaction. This value object supplies none of those authorities.
        """
        self._require_current(expected_version)
        if not self.fields.inputs_complete:
            raise AreaPlanningDraftInvalid("AREA_PLANNING_DRAFT_INPUTS_INCOMPLETE")
        if not isinstance(scenario_id, str) or not scenario_id.strip() or len(scenario_id) > 200:
            raise AreaPlanningDraftInvalid("AREA_PLANNING_NATIVE_SCENARIO_REQUIRED")
        try:
            UUID(actor)
        except (ValueError, TypeError, AttributeError):
            raise AreaPlanningDraftInvalid("AREA_PLANNING_AUTHENTICATED_ACTOR_REQUIRED") from None
        return replace(self, native_scenario_id=scenario_id, materialized_by=actor,
                       status=AreaPlanningDraftStatus.MATERIALIZED, version=self.version + 1,
                       updated_at=datetime.now(UTC))

    def _require_current(self, expected_version: int) -> None:
        if type(expected_version) is not int or expected_version != self.version:
            raise AreaPlanningDraftConflict("AREA_PLANNING_DRAFT_VERSION_CONFLICT")
        if self.status != AreaPlanningDraftStatus.DRAFT:
            raise AreaPlanningDraftConflict("AREA_PLANNING_DRAFT_ALREADY_MATERIALIZED")
