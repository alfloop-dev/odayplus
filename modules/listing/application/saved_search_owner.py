"""Personal criteria commands over the canonical native saved-view owner."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException

from modules.listing.application.intake_authorization import (
    authorize_intake_action,
    collection_filters_in_scope,
    intake_resource_in_scope,
    mask_intake,
)
from modules.listing.application.saved_search import (
    LISTING_SEARCH_RECEIPT_SERVICE,
    SavedListingCriteria,
    receipt_matching_facts,
)
from modules.listing.application.saved_search_admission import listing_search_source_admitted
from shared.audit import AuditEvent
from shared.audit.durable_delivery import DurableAuditDelivery
from shared.auth import Role
from shared.infrastructure.persistence.assisted_listing_intake import DurableAssistedIntakeStore
from shared.infrastructure.persistence.command_receipts import TenantScopedCommandReceiptStore
from shared.infrastructure.persistence.listing_search_matches import (
    ListingSearchMatchStore,
    evidence_digest,
)
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore


def authorize_listing_search_query(principal: Any, query: dict[str, Any]) -> None:
    """Authorize declared filters; results separately project every full row.

    A private user-authored query is not a brand/store/area data grant. Unlike
    an unprojected intake collection, its results below check every scope axis
    before counting or returning a record, including staff record ownership.
    """
    criteria = SavedListingCriteria.from_query(query)
    if criteria is None:
        raise HTTPException(404, "LISTING_SEARCH_NOT_FOUND")
    authorize_intake_action(principal, "view", tenant_id=principal.tenant_id)
    if not collection_filters_in_scope(principal, {"heatZoneId": criteria.heat_zone_id}):
        raise HTTPException(403, "SCOPE_DENIED")


class SavedListingSearchOwner:
    @classmethod
    def from_runtime(cls, state: Any):
        bundle = getattr(state, "persistence_bundle", None)
        return cls(
            getattr(state, "assisted_intake_store", None)
            or getattr(bundle, "assisted_intake_store", None),
            getattr(state, "job_queue", None) or getattr(bundle, "job_queue", None),
            getattr(state, "audit_log", None) or getattr(bundle, "audit_log", None),
        )

    def __init__(self, store: Any, queue: Any, audit_log: Any):
        if (
            not isinstance(store, DurableAssistedIntakeStore)
            or getattr(queue, "_engine", None) is not store._engine
            or getattr(audit_log, "_engine", None) is not store._engine
        ):
            raise HTTPException(503, "LISTING_SEARCH_DURABLE_OWNER_UNAVAILABLE")
        self.store, self.queue, self.audit_log = store, queue, audit_log
        self.engine = store._engine

    def _bind(self, principal: Any) -> None:
        if not principal.authenticated:
            raise HTTPException(401, "AUTHENTICATION_REQUIRED")
        if not principal.has_role(
            Role.EXPANSION_USER, Role.SITE_REVIEWER, Role.EXECUTIVE, Role.DATA_OWNER
        ):
            raise HTTPException(403, "ROLE_DENIED")
        try:
            UUID(principal.subject_id)
            UUID(principal.tenant_id)
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(403, "LISTING_SEARCH_AUTHENTICATED_UUID_SCOPE_REQUIRED") from None
        self.store.refresh(principal.tenant_id)

    @staticmethod
    def _criteria(row: dict[str, Any]) -> SavedListingCriteria:
        criteria = SavedListingCriteria.from_query(row.get("query"))
        if criteria is None:
            raise HTTPException(404, "LISTING_SEARCH_NOT_FOUND")
        return criteria

    def _authorize(self, principal: Any, row: dict[str, Any]) -> None:
        authorize_listing_search_query(principal, row.get("query"))

    def _owned(self, principal: Any, identity: str, *, include_deleted=False) -> dict[str, Any]:
        row = next((v for v in self.store.saved_views if v.get("saved_view_id") == identity), None)
        if (
            row is None
            or row.get("owner_subject_id") != principal.subject_id
            or row.get("tenant_id") != principal.tenant_id
            or row.get("visibility") != "PRIVATE"
            or row.get("deleted_at")
            and not include_deleted
        ):
            raise HTTPException(404, "LISTING_SEARCH_NOT_FOUND")
        self._authorize(principal, row)
        return row

    @staticmethod
    def _view(row: dict[str, Any]) -> dict[str, Any]:
        return {
            k: deepcopy(row.get(k))
            for k in (
                "saved_view_id",
                "name",
                "query",
                "version",
                "owner_subject_id",
                "tenant_id",
                "visibility",
                "created_at",
                "updated_at",
                "deleted_at",
                "resource",
                "shared_role",
            )
        }

    def list(self, principal: Any, *, heat_zone_id: str) -> dict[str, Any]:
        self._bind(principal)
        authorize_listing_search_query(
            principal, {"kind": "listing-search.v1", "heatZoneId": heat_zone_id}
        )
        rows = []
        for row in self.store.saved_views:
            if (
                row.get("owner_subject_id") != principal.subject_id
                or row.get("tenant_id") != principal.tenant_id
                or row.get("visibility") != "PRIVATE"
                or row.get("deleted_at")
            ):
                continue
            criteria = SavedListingCriteria.from_query(row.get("query"))
            if criteria and criteria.heat_zone_id == heat_zone_id:
                rows.append(self._view(row))
        return {"heatZoneId": heat_zone_id, "items": rows}

    def get(self, principal: Any, identity: str) -> dict[str, Any]:
        self._bind(principal)
        return self._view(self._owned(principal, identity))

    @contextmanager
    def _transaction(self, principal: Any, identity: str | None):
        with self.store.transaction(), ExitStack() as stack:
            self.engine.query_one(
                "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                ("listing-search-command:" + principal.tenant_id + ":" + principal.subject_id,),
            )
            if identity:
                self.store._read_locked(
                    "assisted_intake.saved_views", principal.tenant_id + ":" + identity
                )
            self.store.refresh(principal.tenant_id)
            if self.audit_log._worm_sink is not None:
                stack.enter_context(
                    self.audit_log.transactional_worm_sink(
                        DurableAuditDelivery(
                            self.engine, principal.tenant_id, self.queue, self.audit_log._worm_sink
                        )
                    )
                )
            yield

    def command(
        self,
        principal: Any,
        *,
        operation: str,
        payload: dict[str, Any],
        key: str,
        correlation_id: str,
        identity: str | None = None,
    ) -> dict[str, Any]:
        self._bind(principal)
        if operation not in {"create", "update", "delete"} or (operation == "create") != (
            identity is None
        ):
            raise HTTPException(422, "LISTING_SEARCH_OPERATION_INVALID")
        if operation != "delete":
            name = payload.get("name")
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
                raise HTTPException(422, "LISTING_SEARCH_NAME_REQUIRED")
            criteria = SavedListingCriteria.from_query(payload.get("query"))
            if criteria is None:
                raise HTTPException(422, "LISTING_SEARCH_CRITERIA_REQUIRED")
            self._authorize(principal, {"query": criteria.to_query()})
        # Authorization runs both before a replay and under the command lock.
        if identity:
            self._owned(principal, identity, include_deleted=operation == "delete")

        def execute(_receipt_id):
            stamp = datetime.now(UTC).isoformat()
            if operation == "create":
                row = {
                    "saved_view_id": str(uuid4()),
                    "tenant_id": principal.tenant_id,
                    "owner_subject_id": principal.subject_id,
                    "visibility": "PRIVATE",
                    "resource": "intake",
                    "shared_role": None,
                    "version": 1,
                    "created_at": stamp,
                }
                self.store.saved_views.append(row)
            else:
                row = self._owned(principal, identity)
                if (
                    type(payload.get("expectedVersion")) is not int
                    or payload["expectedVersion"] != row["version"]
                ):
                    raise HTTPException(409, "LISTING_SEARCH_VERSION_CONFLICT")
                row["version"] += 1
            if operation == "delete":
                row["deleted_at"] = stamp
            else:
                self._authorize(principal, {"query": criteria.to_query()})
                row.update(name=name.strip(), query=criteria.to_query())
            row["updated_at"] = stamp
            event = self.audit_log.record(
                AuditEvent(
                    event_type="listing.search." + operation,
                    actor=principal.subject_id,
                    action=operation,
                    resource="saved-view/" + row["saved_view_id"],
                    outcome="success",
                    correlation_id=correlation_id,
                    metadata={
                        "tenant_id": principal.tenant_id,
                        "search_id": row["saved_view_id"],
                        "version": row["version"],
                        "visibility": "PRIVATE",
                    },
                )
            )
            self.store.flush()
            return {"search": self._view(row), "auditEventIds": [event.event_id]}

        def authorized_execute(receipt_id):
            if identity:
                self._owned(principal, identity)
            return execute(receipt_id)

        # Also authorize current identity on successful replays; a deleted row
        # can replay only its own delete, without resurrecting the criteria.
        @contextmanager
        def boundary():
            with self._transaction(principal, identity):
                if identity:
                    self._owned(principal, identity, include_deleted=operation == "delete")
                if operation != "delete":
                    self._authorize(principal, {"query": criteria.to_query()})
                yield

        receipts = TenantScopedCommandReceiptStore(
            self.queue, LISTING_SEARCH_RECEIPT_SERVICE, transaction_factory=boundary
        )
        outcome = receipts.run(
            tenant_id=principal.tenant_id,
            idempotency_key=key,
            scope=operation
            + ":"
            + (identity or "create")
            + ":"
            + sha256(principal.subject_id.encode()).hexdigest(),
            payload={
                "operation": operation,
                "identity": identity,
                "input": payload,
                "actor": principal.subject_id,
            },
            correlation_id=correlation_id,
            operation=authorized_execute,
        )
        return {**outcome.value, "receiptId": outcome.receipt_id, "replayed": outcome.replayed}

    def results(self, principal: Any, identity: str) -> dict[str, Any]:
        self._bind(principal)
        search = self._owned(principal, identity)
        criteria = self._criteria(search)
        ledger = ListingSearchMatchStore(
            TenantScopedDocumentStore(self.store._store, principal.tenant_id)
        )
        visible = []
        for match in ledger.history(search_id=identity, search_version=search["version"]):
            current = self.store.intakes.get(match.get("intakeId"))
            if (
                match.get("ownerSubjectId") != principal.subject_id
                or match.get("outcome") != "MATCH"
                or not current
                or current.get("version") != match.get("intakeVersion")
                or evidence_digest(receipt_matching_facts(current))
                != match.get("receiptFactsSha256")
                or not intake_resource_in_scope(principal, current)
            ):
                continue
            resource = {
                **current,
                "tenant_id": current.get("scope", {}).get("tenant_id"),
                "owner": current.get("assigned_to") or current.get("owner"),
                "submitter": current.get("submitted_by") or current.get("submitter"),
            }
            try:
                authorize_intake_action(principal, "view", resource=resource)
            except HTTPException as exc:
                if exc.status_code == 403:
                    continue
                raise
            if (
                criteria.evaluate(
                    mask_intake(principal, current), tenant_id=principal.tenant_id
                ).outcome
                != "MATCH"
            ):
                continue
            if not listing_search_source_admitted(self.engine, current, principal.tenant_id):
                continue
            visible.append({k: match[k] for k in ("intakeId", "intakeVersion", "recordedAt")})
        return {"search": self._view(search), "items": visible, "total": len(visible)}
