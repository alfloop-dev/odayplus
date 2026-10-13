"""Operator presentation over the canonical Intake/workflow transaction owner.

Legacy operator rows remain readable. New submissions have one canonical Intake
document; operator presentation metadata lives inside that document, never in a
second intake collection. Workflow facts are read from the referenced resources.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import UUID

from modules.listing.application.intake_workflow_bootstrap import (
    IntakeWorkflowUnavailable,
    initialize_intake_workflow,
    record_workflow_change,
)
from shared.infrastructure.persistence.assisted_listing_intake import (
    _STATE_COLLECTIONS,
    AssistedIntakePersistenceConflict,
)

_WORKFLOW_NULLS = {
    "assignmentId": None, "assignmentVersion": None, "assignmentStatus": None,
    "ownerSubjectId": None, "slaInstanceId": None, "slaVersion": None,
    "slaState": None, "slaReceipt": None, "dueAt": None,
}


def project_operator_intake(
    value: dict[str, Any], assignments: dict[str, Any], slas: dict[str, Any],
) -> dict[str, Any]:
    """Project only known contract fields; never expose the canonical raw aliases."""
    scope = value.get("scope") or {}
    intake_id = value["intake_id"]
    tenant_id = scope.get("tenant_id")
    result = deepcopy(value.get("operator_presentation") or {})
    result.update({
        "id": intake_id, "tenantId": tenant_id,
        "heatZoneId": scope.get("heat_zone_id"), "regionId": scope.get("region_id"),
        "brandId": scope.get("brand_id"), "assignedAreaId": scope.get("assigned_area_id"),
        "storeId": scope.get("store_id"),
        "submittedBy": value.get("submitted_by"), "submittedAt": value.get("submitted_at"),
        "updatedAt": value.get("updated_at"), "lastObservedAt": value.get("last_observed_at"),
        "intakeMethod": value.get("intake_method"), "stage": value.get("state"),
        "version": value.get("version"), "sourceId": value.get("source_id"),
        "originalUrl": value.get("original_url"), "canonicalUrl": value.get("canonical_url"),
        "correlationId": value.get("correlation_id"), "policy": value.get("policy_state"),
        **_WORKFLOW_NULLS,
    })
    # Labels may be unavailable. A subject UUID is an identity, not a directory
    # lookup; no username/email or inferred display-name identity is returned.
    result["submitter"] = value.get("submitted_by")
    result["owner"] = None
    for name, default in (
        ("policyLabel", None), ("policyReason", None), ("rawSnapshot", None),
        ("snapshotId", None), ("capturedAt", None), ("parserVersion", None),
        ("parsedFields", {}), ("matchResult", None), ("auditEvents", []),
    ):
        result.setdefault(name, default)
    if "fields" in value:
        result["fields"] = deepcopy(value["fields"])
    assignment = assignments.get(value.get("assignment_id"))
    if (isinstance(assignment, dict) and assignment.get("intake_id") == intake_id
            and assignment.get("tenant_id") == tenant_id
            and assignment.get("assignment_id") == value.get("assignment_id")):
        result.update({
            "assignmentId": assignment["assignment_id"],
            "assignmentVersion": assignment.get("version"),
            "assignmentStatus": assignment.get("status"),
            "ownerSubjectId": assignment.get("owner_subject_id"),
            "owner": assignment.get("owner_subject_id"),
            "dueAt": assignment.get("due_at"),
        })
        sla = slas.get(value.get("sla_instance_id"))
        if (isinstance(sla, dict) and sla.get("intake_id") == intake_id
                and sla.get("tenant_id") == tenant_id
                and sla.get("assignment_id") == assignment["assignment_id"]
                and sla.get("sla_instance_id") == value.get("sla_instance_id")):
            result.update({
                "slaInstanceId": sla["sla_instance_id"], "slaVersion": sla.get("version"),
                "slaState": sla.get("state"), "slaReceipt": sla.get("receipt"),
                "dueAt": sla.get("due_at"),
            })
        else:
            sla = None
        # Pause/claim commands may update only the linked resource. Use its
        # recorded modification time, never capture time or display SLA order.
        timestamps = []
        for resource in (value, assignment, sla):
            stamp = resource.get("updated_at") if resource else None
            if isinstance(stamp, str):
                try:
                    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                    if parsed.tzinfo is not None:
                        timestamps.append((parsed, stamp))
                except ValueError:
                    pass
        result["updatedAt"] = max(timestamps)[1] if timestamps else None
    return result


class OperatorIntakeWorkflowRepository:
    """Tenant-bound adapter sharing the V1 store, engine and workflow policy."""

    def __init__(self, store: Any, legacy: Any, tenant_id: str, policy: Any) -> None:
        if str(UUID(tenant_id)) != tenant_id:
            raise ValueError("Canonical tenant required")
        if store._engine is not legacy._store.engine:
            raise ValueError("Operator and workflow must share one transaction engine")
        self._canonical = store
        self._legacy = legacy
        self._tenant_id = tenant_id
        self._policy = policy
        self._in_transaction = False
        store.refresh(tenant_id)

    def __getattr__(self, name: str) -> Any:
        # Presentation metadata and existing actor-bound replay records retain
        # their existing tenant partition and format.
        return getattr(self._legacy, name)

    @contextmanager
    def transaction(self, *, intake_id: str | None = None):
        self._canonical.refresh(self._tenant_id)
        with self._canonical.transaction():
            self._canonical._engine.query_one(
                "SELECT pg_advisory_xact_lock(hashtextextended(?, 0)) AS locked",
                (f"operator-intake-submission:{self._tenant_id}",),
            )
            if intake_id is not None:
                # V1 Assignment/SLA commands update the same parent Intake.
                # Lock it before the refreshed authorization read; this also
                # works across replicas that do not take our tenant lock.
                self._canonical._read_locked(_STATE_COLLECTIONS["intakes"], f"{self._tenant_id}:{intake_id}")
            self._canonical.refresh(self._tenant_id)
            self._in_transaction = True
            try:
                yield
                self._canonical.flush()
            finally:
                self._in_transaction = False

    def prepare_async_submission(self, job_queue: Any) -> None:
        if getattr(job_queue, "_engine", None) is not self._canonical._engine:
            raise IntakeWorkflowUnavailable("Intake retrieval requires the shared durable job queue")

    @property
    def outbox_repository(self) -> Any:
        from shared.infrastructure.persistence.outbox import DurableOutboxRepository

        return DurableOutboxRepository(self._canonical._engine)

    def _project(self, value: dict[str, Any]) -> dict[str, Any]:
        return project_operator_intake(value, self._canonical.assignments, self._canonical.slas)

    def list_intakes(self) -> list[dict[str, Any]]:
        if not self._in_transaction:
            self._canonical.refresh(self._tenant_id)
        canonical = {
            key: self._project(value) for key, value in self._canonical.intakes.items()
            if value.get("scope", {}).get("tenant_id") == self._tenant_id
        }
        legacy = []
        for item in self._legacy.list_intakes():
            if item.get("id") in canonical or item.get("tenantId") != self._tenant_id:
                continue
            row = deepcopy(item)
            # Pre-bridge display names/capture times cannot establish these facts.
            for key in ("submittedBy", "submittedAt", "updatedAt", "lastObservedAt"):
                row.setdefault(key, None)
            row.update(_WORKFLOW_NULLS)
            legacy.append(row)
        return [*canonical.values(), *legacy]

    def get_intake(self, intake_id: str) -> dict[str, Any] | None:
        return next((row for row in self.list_intakes() if row["id"] == intake_id), None)

    def save_intake(self, intake: dict[str, Any]) -> None:
        if not self._in_transaction:
            with self.transaction(intake_id=intake["id"]):
                self._save_intake(intake)
        else:
            self._save_intake(intake)

    def _save_intake(self, intake: dict[str, Any]) -> None:
        if intake.get("tenantId") != self._tenant_id:
            raise PermissionError("Intake tenant mismatch")
        intake_id = intake["id"]
        current = self._canonical.intakes.get(intake_id)
        if current is None and self._legacy.get_intake(intake_id) is not None:
            # Existing legacy intake edits do not fabricate historical workflow.
            self._legacy.save_intake(intake)
            return
        if current is None:
            if not self._in_transaction or not intake.get("submittedBy") or not intake.get("submittedAt"):
                raise IntakeWorkflowUnavailable("Verified submission context is required")
            UUID(intake_id)
            UUID(intake["submittedBy"])
            current = {
                "intake_id": intake_id, "scope": {
                    "tenant_id": self._tenant_id,
                    **{snake: intake[camel] for camel, snake in (
                        ("heatZoneId", "heat_zone_id"), ("regionId", "region_id"),
                        ("brandId", "brand_id"), ("assignedAreaId", "assigned_area_id"),
                    ) if intake.get(camel) is not None},
                },
                "submitted_by": intake["submittedBy"], "submitted_at": intake["submittedAt"],
                "fields": [], "audit": [], "processing_history": [],
            }
            is_new = True
        else:
            if current.get("scope", {}).get("tenant_id") != self._tenant_id:
                raise PermissionError("Intake tenant mismatch")
            if intake["version"] <= current["version"]:
                raise AssistedIntakePersistenceConflict("Stale operator Intake version")
            current = deepcopy(current)
            is_new = False
        current.update({
            "state": intake["stage"], "intake_method": intake.get("intakeMethod"),
            "version": intake["version"], "updated_at": intake.get("updatedAt"),
            "last_observed_at": intake.get("lastObservedAt"),
            "original_url": intake.get("originalUrl"), "canonical_url": intake.get("canonicalUrl"),
            "source_id": intake.get("sourceId"), "policy_state": intake.get("policy"),
            "correlation_id": intake.get("correlationId"),
            "operator_presentation": deepcopy(intake),
        })
        self._canonical.intakes[intake_id] = current
        if is_new:
            initialize_intake_workflow(self._canonical, current, self._policy)
        intake.update(self._project(current))

    def create_intake_if_absent(self, intake: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        with self.transaction(intake_id=intake["id"]):
            existing = self.get_intake(intake["id"])
            if existing is not None:
                return existing, False
            self._save_intake(intake)
            return deepcopy(intake), True

    def get_promotion(self, promotion_id: str) -> dict[str, Any] | None:
        value = self._canonical.promotions.get(promotion_id)
        if value is None:
            value = self._legacy.get_promotion(promotion_id)
        return deepcopy(value) if value and value.get("tenant_id") == self._tenant_id else None

    def list_promotions(self) -> list[dict[str, Any]]:
        values = {value["promotion_decision_id"]: value for value in self._legacy.list_promotions()}
        values.update(self._canonical.promotions)
        return [deepcopy(value) for value in values.values() if value.get("tenant_id") == self._tenant_id]

    def save_promotion(self, promotion: dict[str, Any]) -> None:
        if promotion.get("tenant_id") != self._tenant_id:
            raise PermissionError("Promotion tenant mismatch")
        if not self._in_transaction:
            with self.transaction(intake_id=promotion.get("intake_id")):
                self.save_promotion(promotion)
            return
        parent = self._canonical.intakes.get(promotion.get("intake_id"))
        if parent is None:
            self._legacy.save_promotion(promotion)
            return
        previous = self._canonical.promotions.get(promotion["promotion_decision_id"])
        evidence_parent = {**parent, "audit": parent.setdefault("audit", []),
                           "correlation_id": promotion["correlation_id"]}
        record_workflow_change(
            self._canonical, evidence_parent, resource_type="promotion_decision",
            before=deepcopy(previous), after=promotion,
            action="intake.promotion_requested" if previous is None else "promotion." + promotion["status"].lower(),
            actor_id=promotion.get("reviewer_subject_id") or promotion.get("reviewer") or promotion["proposer_subject_id"],
        )
        self._canonical.promotions[promotion["promotion_decision_id"]] = deepcopy(promotion)
