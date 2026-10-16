"""Versioned area proposals in the native NetPlan tenant document partition.

The request owner supplies current authorization, canonical parent admission,
command receipts and audit delivery. This repository supplies no permission or
financial-data authority, and never creates a native scenario on its own.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import UUID

from modules.netplan.domain.area_planning_draft import (
    AreaPlanningDraft,
    AreaPlanningDraftConflict,
    AreaPlanningDraftInvalid,
    AreaPlanningDraftStatus,
    AreaPlanningFields,
)
from modules.netplan.domain.planning import NetPlanScenario, NetPlanScenarioStatus
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore


class AreaPlanningDraftPersistenceError(RuntimeError):
    """The native durable owner or a stored proposal is not usable."""


class DurableAreaPlanningDraftRepository:
    COLLECTION = "netplan.area_planning_drafts"
    NATIVE_SCENARIO_PREFIX = "netplan-scenario-area-draft-"

    @classmethod
    def scenario_id_for_draft(cls, draft_id: str) -> str:
        return cls.NATIVE_SCENARIO_PREFIX + str(UUID(draft_id))

    def __init__(self, documents: TenantScopedDocumentStore) -> None:
        if (not isinstance(documents, TenantScopedDocumentStore)
                or getattr(documents.engine, "dialect", None) != "postgresql"):
            raise AreaPlanningDraftPersistenceError("AREA_PLANNING_DURABLE_OWNER_UNAVAILABLE")
        try:
            UUID(documents.tenant_id)
        except (ValueError, TypeError, AttributeError):
            raise AreaPlanningDraftPersistenceError("AREA_PLANNING_DURABLE_TENANT_REQUIRED") from None
        self.documents = documents
        self.engine = documents.engine
        self.tenant_id = documents.tenant_id

    def _validated(self, value: Any, version: int | None = None) -> AreaPlanningDraft:
        try:
            if not isinstance(value, AreaPlanningDraft) or value.tenant_id != self.tenant_id:
                raise AreaPlanningDraftPersistenceError("AREA_PLANNING_STORED_DRAFT_INVALID")
            # Existing document persistence uses pickle. Loading a dataclass
            # does not call __post_init__, so revalidate both nested fields and
            # the enclosing record before returning any data to the owner.
            if not isinstance(value.fields, AreaPlanningFields):
                raise AreaPlanningDraftPersistenceError("AREA_PLANNING_STORED_DRAFT_INVALID")
            # Stored facts must already be canonical. Re-run their original
            # constructor without converting through the user-input parser:
            # to_dict()/from_mapping() could normalize a corrupted string of
            # candidate IDs into several different, apparently valid IDs.
            fields = replace(value.fields)
            result = replace(value, fields=fields)
            if version is not None and (type(version) is not int or version != result.version):
                raise AreaPlanningDraftPersistenceError("AREA_PLANNING_STORED_VERSION_INVALID")
            return result
        except (AreaPlanningDraftInvalid, AttributeError, TypeError, ValueError):
            raise AreaPlanningDraftPersistenceError("AREA_PLANNING_STORED_DRAFT_INVALID") from None

    def get(self, draft_id: str) -> AreaPlanningDraft | None:
        row = self.documents.get_versioned(self.COLLECTION, draft_id)
        if row is None:
            return None
        value = self._validated(*row)
        if value.draft_id != draft_id:
            raise AreaPlanningDraftPersistenceError("AREA_PLANNING_STORED_IDENTITY_INVALID")
        return value

    def list_all(self) -> list[AreaPlanningDraft]:
        result = []
        for row in self.documents.list_all(self.COLLECTION):
            valid = self._validated(row)
            current = self.get(valid.draft_id)
            if current is None:
                raise AreaPlanningDraftPersistenceError("AREA_PLANNING_STORED_IDENTITY_INVALID")
            result.append(current)
        if len({row.draft_id for row in result}) != len(result):
            raise AreaPlanningDraftPersistenceError("AREA_PLANNING_STORED_IDENTITY_INVALID")
        return sorted(result, key=lambda row: (row.updated_at, row.draft_id), reverse=True)

    def create(self, draft: AreaPlanningDraft) -> AreaPlanningDraft:
        valid = self._validated(draft, 1)
        if valid.status != AreaPlanningDraftStatus.DRAFT:
            raise AreaPlanningDraftInvalid("AREA_PLANNING_NEW_DRAFT_REQUIRED")
        with self.engine.transaction():
            version = self.documents.compare_and_put(
                self.COLLECTION, valid.draft_id, valid, expected_version=None,
            )
            if version != 1:
                raise AreaPlanningDraftConflict("AREA_PLANNING_DRAFT_ALREADY_EXISTS")
        return valid

    def save(self, previous: AreaPlanningDraft, current: AreaPlanningDraft) -> AreaPlanningDraft:
        before, after = self._validated(previous), self._validated(current)
        if (before.status != AreaPlanningDraftStatus.DRAFT or after.version != before.version + 1
                or after.updated_at < before.updated_at
                or (after.status == AreaPlanningDraftStatus.MATERIALIZED and after.fields != before.fields)
                or any(getattr(before, key) != getattr(after, key) for key in (
                    "draft_id", "tenant_id", "created_by", "heat_zone_id", "created_at",
                ))):
            raise AreaPlanningDraftConflict("AREA_PLANNING_DRAFT_IDENTITY_OR_STATE_CONFLICT")
        with self.engine.transaction():
            persisted = self.get(before.draft_id)
            if persisted != before:
                raise AreaPlanningDraftConflict("AREA_PLANNING_DRAFT_VERSION_CONFLICT")
            if after.status == AreaPlanningDraftStatus.MATERIALIZED:
                # The native writer must have created this actual draft in the
                # same SQL transaction before the proposal link can commit.
                # This structural check complements request-owner authority;
                # it does not authorize scenario creation or admit finances.
                from shared.infrastructure.persistence.repositories import DurableNetPlanRepository

                scenario = self.documents.get(
                    DurableNetPlanRepository._SCENARIOS, after.native_scenario_id,
                )
                if (not isinstance(scenario, NetPlanScenario)
                        or scenario.scenario_id != after.native_scenario_id
                        or scenario.scenario_id != self.scenario_id_for_draft(after.draft_id)
                        or scenario.tenant_id != self.tenant_id
                        or scenario.status != NetPlanScenarioStatus.DRAFT
                        or scenario.scenario_name != after.fields.name
                        or scenario.planning_horizon != after.fields.quarter
                        or scenario.constraints.max_budget != after.fields.budget
                        or set(scenario.options_by_entity) != set(after.fields.candidate_ids)
                        or scenario.created_at < after.created_at):
                    raise AreaPlanningDraftPersistenceError("AREA_PLANNING_NATIVE_DRAFT_NOT_BOUND")
            version = self.documents.compare_and_put(
                self.COLLECTION, after.draft_id, after, expected_version=before.version,
            )
            if version != after.version:
                raise AreaPlanningDraftConflict("AREA_PLANNING_DRAFT_VERSION_CONFLICT")
        return after
