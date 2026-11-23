"""Current-credential, canonical-parent owner for preliminary area planning.

Only planning intent is stored here. The original native NetPlan request owner
separately authorizes and admits scenario creation: materialization runs inside
that owner's CREATE operation and calls its original creation path. This owner
does not accept financial documents, supply financial values, solve, approve
or execute.
"""
from __future__ import annotations

import logging
from contextlib import ExitStack, contextmanager
from functools import wraps
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from modules.netplan.domain.area_planning_draft import (
    AreaPlanningDraft,
    AreaPlanningDraftInvalid,
    AreaPlanningDraftStatus,
    AreaPlanningFields,
)
from modules.netplan.infrastructure.area_planning_drafts import DurableAreaPlanningDraftRepository
from modules.opsboard.application.network_heat_zones import project_canonical_heat_zones
from modules.opsboard.application.network_read_scope import candidate_scope_evidence
from shared.audit import AuditEvent
from shared.audit.durable_delivery import DurableAuditDelivery
from shared.auth import Action, DataClassification
from shared.auth.rbac import rbac_allows
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.command_receipts import TenantScopedCommandReceiptStore
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.repositories import DurableNetPlanRepository

AREA_PLANNING_DRAFT_RESOURCE = "netplan_area_draft"
AREA_PLANNING_DRAFT_RECEIPT_SERVICE = "netplan-area-draft"
_LOG = logging.getLogger(__name__)


def _record_decided_denial(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        self._denial = None
        try:
            return method(self, *args, **kwargs)
        except HTTPException:
            # A command transaction has normally rolled back when this wrapper
            # runs. Inside an enclosing native owner transaction the denial is
            # kept for that caller, which records it after its own rollback.
            if getattr(getattr(self.engine, "_local", None), "connection", None) is None:
                self.record_denial()
            raise
    return wrapped


class AreaPlanningDraftOwner:
    def __init__(self, *, principal: Any, native_repository: Any, audit_log: Any,
                 queue: Any, heat_zone_reader: Any, candidate_snapshot_reader: Any,
                 correlation_id: str):
        documents = getattr(native_repository, "_store", None)
        engine = getattr(documents, "engine", None)
        if (not isinstance(native_repository, DurableNetPlanRepository)
                or not isinstance(documents, TenantScopedDocumentStore)
                or documents.tenant_id != principal.tenant_id
                or getattr(engine, "dialect", None) != "postgresql"
                or not isinstance(audit_log, DurableAuditLog)
                or not isinstance(queue, DurableJobQueue)
                or getattr(audit_log, "_engine", None) is not engine
                or getattr(queue, "_engine", None) is not engine
                or not callable(heat_zone_reader)):
            raise HTTPException(503, "AREA_PLANNING_DURABLE_OWNER_UNAVAILABLE")
        self.principal, self.native = principal, native_repository
        self.documents, self.engine = documents, engine
        self.repository = DurableAreaPlanningDraftRepository(documents)
        self.audit, self.queue = audit_log, queue
        self.zones, self.candidates = heat_zone_reader, candidate_snapshot_reader
        self.correlation_id, self._denial = correlation_id, None

    def record_denial(self):
        # A decided refusal remains a refusal if its audit sink is
        # unavailable, matching the existing authorization boundary.
        denial, self._denial = self._denial, None
        if denial is not None:
            try:
                self.audit.record(denial)
            except Exception:
                _LOG.exception("Area planning access denied; denial audit delivery unavailable")

    def _refuse(self, status: int, code: str, *, identity: str | None = None):
        self._denial = AuditEvent(
            event_type="netplan.area_draft.authorization_denied",
            actor=self.principal.subject_id or "anonymous", action="access",
            resource="netplan-area-draft/" + (identity or "collection"), outcome="denied",
            correlation_id=self.correlation_id,
            metadata={"tenant_id": self.principal.tenant_id, "reason": code},
        )
        raise HTTPException(status, code)

    def _bind(self, action: Action):
        principal = self.principal
        if not principal.authenticated or not principal.tenant_id:
            self._refuse(401, "AUTHENTICATION_REQUIRED")
        if principal.attributes.get("token_type") not in {"local", "oidc", "service"}:
            raise HTTPException(503, "AREA_PLANNING_CURRENT_CREDENTIAL_SOURCE_UNAVAILABLE")
        try:
            UUID(principal.subject_id)
            UUID(principal.tenant_id)
        except (ValueError, TypeError, AttributeError):
            self._refuse(403, "AREA_PLANNING_AUTHENTICATED_UUID_SCOPE_REQUIRED")
        if (not rbac_allows(principal, AREA_PLANNING_DRAFT_RESOURCE, action)
                or not principal.scope.permits_module("operator_console")
                or not principal.scope.permits_classification(DataClassification.CONFIDENTIAL)):
            self._refuse(403, "AREA_PLANNING_ROLE_OR_SCOPE_DENIED")

    def _admit(self, heat_zone_id: str, fields: AreaPlanningFields):
        try:
            source = self.zones()
        except Exception as exc:
            raise HTTPException(503, "AREA_PLANNING_HEAT_ZONE_SOURCE_UNAVAILABLE") from exc
        projected = project_canonical_heat_zones(
            {}, source, principal=self.principal, selected_id=heat_zone_id,
        )
        if projected["heatZoneAvailability"]["state"] != "available":
            raise HTTPException(503, "AREA_PLANNING_HEAT_ZONE_SOURCE_UNAVAILABLE")
        if not any(row["id"] == heat_zone_id for row in projected["heatZones"]):
            self._refuse(404, "AREA_PLANNING_PARENT_NOT_FOUND")
        if fields.candidate_ids:
            if not callable(self.candidates):
                raise HTTPException(503, "AREA_PLANNING_CANDIDATE_SOURCE_UNAVAILABLE")
            try:
                snapshot = self.candidates(self.principal.tenant_id)
                permitted = candidate_scope_evidence(self.principal, snapshot)
            except Exception as exc:
                raise HTTPException(503, "AREA_PLANNING_CANDIDATE_SOURCE_UNAVAILABLE") from exc
            for identifier in fields.candidate_ids:
                row = permitted.get(identifier)
                if row is None:
                    self._refuse(404, "AREA_PLANNING_PARENT_NOT_FOUND")
                zones = [source[key] for source in (row, row.get("scope") or {})
                         for key in ("heatZoneId", "heat_zone_id") if source.get(key) is not None]
                if not zones or any(value != heat_zone_id for value in zones):
                    self._refuse(404, "AREA_PLANNING_PARENT_NOT_FOUND")

    @staticmethod
    def _require_command_key(key: Any) -> None:
        """One owner rule for the receipt key of every area draft command."""
        if not isinstance(key, str) or not 16 <= len(key) <= 128 or any(
            not (c.isascii() and (c.isalnum() or c in "._:-")) for c in key
        ):
            raise AreaPlanningDraftInvalid("AREA_PLANNING_COMMAND_KEY_REQUIRED")

    def _get(self, identity: str) -> AreaPlanningDraft:
        draft = self.repository.get(identity)
        if draft is None:
            self._refuse(404, "AREA_PLANNING_DRAFT_NOT_FOUND", identity=identity)
        self._admit(draft.heat_zone_id, draft.fields)
        return draft

    @_record_decided_denial
    def get(self, identity: str):
        self._bind(Action.VIEW)
        return self._get(identity).to_dict()

    @_record_decided_denial
    def list(self, heat_zone_id: str):
        self._bind(Action.VIEW)
        self._admit(heat_zone_id, AreaPlanningFields("scope-check", None, None, ()))
        items = []
        for draft in self.repository.list_all():
            if draft.heat_zone_id != heat_zone_id:
                continue
            try:
                self._admit(draft.heat_zone_id, draft.fields)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                self._denial = None
                continue
            items.append(draft.to_dict())
        return {"heatZoneId": heat_zone_id, "items": items}

    @contextmanager
    def _transaction(self, identity: str | None, heat_zone_id: str | None,
                     fields: AreaPlanningFields | None, native: Any = None):
        # Share the native NetPlan tenant lock and SQL engine. The proposal row
        # is then locked before reading its current version or replaying a key.
        with self.native.scenario_transaction(None), ExitStack() as stack:
            if identity:
                self.engine.query_one(
                    "SELECT doc_id FROM durable_documents WHERE collection = ? AND doc_id = ? FOR UPDATE",
                    (self.documents._collection(self.repository.COLLECTION), identity),
                )
            if self.audit._worm_sink is not None:
                stack.enter_context(self.audit.transactional_worm_sink(DurableAuditDelivery(
                    self.engine, self.principal.tenant_id, self.queue, self.audit._worm_sink,
                )))
            if identity:
                current = self._get(identity)
                if native is not None and current.status == AreaPlanningDraftStatus.MATERIALIZED:
                    # A replay or repeated request never creates again. It
                    # still needs the actual native object in current scope.
                    native.scenario(current.native_scenario_id)
            if fields is not None:
                self._admit(heat_zone_id, fields)
            yield

    @_record_decided_denial
    def command(self, *, operation: str, payload: dict[str, Any], key: str,
                identity: str | None = None):
        self._bind(Action.CREATE if operation == "create" else Action.UPDATE)
        if operation not in {"create", "update"} or (operation == "create") != (identity is None):
            raise AreaPlanningDraftInvalid("AREA_PLANNING_OPERATION_INVALID")
        self._require_command_key(key)
        expected = {"name", "quarter", "budget", "candidateIds", "heatZoneId"}
        if operation == "update":
            expected.add("expectedVersion")
        if not isinstance(payload, dict) or set(payload) != expected:
            raise AreaPlanningDraftInvalid("AREA_PLANNING_COMMAND_FIELDS_INVALID")
        fields = AreaPlanningFields.from_mapping({k: payload[k] for k in (
            "name", "quarter", "budget", "candidateIds",
        )})
        zone = payload["heatZoneId"]
        if not isinstance(zone, str) or not zone or zone != zone.strip() or len(zone) > 200:
            raise AreaPlanningDraftInvalid("AREA_PLANNING_HEAT_ZONE_REQUIRED")
        if operation == "update" and (type(payload["expectedVersion"]) is not int or payload["expectedVersion"] < 1):
            raise AreaPlanningDraftInvalid("AREA_PLANNING_EXPECTED_VERSION_REQUIRED")
        if identity:
            self._get(identity)
        self._admit(zone, fields)

        def apply(_receipt_id):
            if operation == "create":
                current = self.repository.create(AreaPlanningDraft.create(
                    tenant_id=self.principal.tenant_id, created_by=self.principal.subject_id,
                    heat_zone_id=zone, fields=fields,
                ))
            else:
                previous = self._get(identity)
                if previous.heat_zone_id != zone:
                    raise AreaPlanningDraftInvalid("AREA_PLANNING_PARENT_CHANGE_FORBIDDEN")
                current = self.repository.save(previous, previous.update(
                    fields, expected_version=payload["expectedVersion"],
                ))
            event = self.audit.record(AuditEvent(
                event_type="netplan.area_draft." + operation,
                actor=self.principal.subject_id, action=operation,
                resource="netplan-area-draft/" + current.draft_id, outcome="success",
                correlation_id=self.correlation_id,
                metadata={"tenant_id": current.tenant_id, "draft_id": current.draft_id,
                          "heat_zone_id": current.heat_zone_id, "version": current.version},
            ))
            return {"draft": current.to_dict(), "auditEventIds": [event.event_id]}

        receipts = TenantScopedCommandReceiptStore(
            self.queue, AREA_PLANNING_DRAFT_RECEIPT_SERVICE,
            transaction_factory=lambda: self._transaction(identity, zone, fields),
        )
        outcome = receipts.run(
            tenant_id=self.principal.tenant_id, idempotency_key=key,
            scope=operation + ":" + (identity or "create") + ":" + self.principal.subject_id,
            payload={"operation": operation, "identity": identity, "actor": self.principal.subject_id,
                     "input": payload},
            correlation_id=self.correlation_id, operation=apply,
        )
        # This exact committed command response stays bound to the receipt.
        # The boundary above rechecks present access before every replay.
        return {**outcome.value, "receiptId": outcome.receipt_id, "replayed": outcome.replayed}

    @_record_decided_denial
    def materialize(self, *, identity: str, payload: dict[str, Any], key: str,
                    native: Any, create_native: Any):
        """Create the original native DRAFT and link this proposal atomically.

        ``native`` is the original native request owner already inside its
        CREATE operation; this owner was built from its ``raw`` repository.
        ``create_native`` is the original native creation path. Only the
        first receipt operation pins/admits the creation body, so the native
        existing-ID refusal stays intact while a replay never creates again.
        Candidate rows carry identifiers only: no financial value, source,
        model, currency or period is supplied here.
        """
        self._bind(Action.UPDATE)
        if (getattr(native, "raw", None) is not self.native
                or getattr(native, "principal", None) is not self.principal
                or not callable(create_native)):
            raise HTTPException(503, "AREA_PLANNING_NATIVE_OWNER_UNAVAILABLE")
        self._require_command_key(key)
        if not isinstance(payload, dict) or set(payload) != {"expectedVersion"}:
            raise AreaPlanningDraftInvalid("AREA_PLANNING_COMMAND_FIELDS_INVALID")
        expected = payload["expectedVersion"]
        if type(expected) is not int or expected < 1:
            raise AreaPlanningDraftInvalid("AREA_PLANNING_EXPECTED_VERSION_REQUIRED")
        self._get(identity)

        def apply(_receipt_id):
            previous = self._get(identity)
            scenario_id = self.repository.scenario_id_for_draft(previous.draft_id)
            # Version, state and planning-input completeness precede creation.
            linked = previous.materialize(
                scenario_id=scenario_id, actor=self.principal.subject_id, expected_version=expected,
            )
            created = create_native({
                "tenant_id": previous.tenant_id, "scenario_name": previous.fields.name,
                "planning_horizon": previous.fields.quarter,
                "constraints": {"max_budget": previous.fields.budget},
                "existing_stores": [],
                "candidate_sites": [{"candidate_site_id": candidate}
                                    for candidate in previous.fields.candidate_ids],
                "scenario_id": scenario_id,
            })
            if not isinstance(created, dict) or created.get("scenario_id") != scenario_id:
                raise HTTPException(503, "AREA_PLANNING_NATIVE_OWNER_UNAVAILABLE")
            current = self.repository.save(previous, linked)
            event = self.audit.record(AuditEvent(
                event_type="netplan.area_draft.materialize",
                actor=self.principal.subject_id, action="materialize",
                resource="netplan-area-draft/" + current.draft_id, outcome="success",
                correlation_id=self.correlation_id,
                metadata={"tenant_id": current.tenant_id, "draft_id": current.draft_id,
                          "heat_zone_id": current.heat_zone_id, "version": current.version,
                          "native_scenario_id": scenario_id},
            ))
            return {"draft": current.to_dict(),
                    "auditEventIds": [created["audit_event_id"], event.event_id]}

        receipts = TenantScopedCommandReceiptStore(
            self.queue, AREA_PLANNING_DRAFT_RECEIPT_SERVICE,
            transaction_factory=lambda: self._transaction(identity, None, None, native),
        )
        outcome = receipts.run(
            tenant_id=self.principal.tenant_id, idempotency_key=key,
            scope="materialize:" + identity + ":" + self.principal.subject_id,
            payload={"operation": "materialize", "identity": identity,
                     "actor": self.principal.subject_id, "input": payload},
            correlation_id=self.correlation_id, operation=apply,
        )
        return {**outcome.value, "receiptId": outcome.receipt_id, "replayed": outcome.replayed}
