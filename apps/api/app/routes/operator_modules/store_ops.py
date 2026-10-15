from __future__ import annotations

from dataclasses import replace
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from apps.api.oday_api.runtime_mode import live_data_required
from modules.opsboard.application.store_ops import (
    DurableStoreOpsRepository,
    StoreOpsConflict,
    StoreOpsLiveDataUnavailable,
    StoreOpsNotFound,
    StoreOpsPolicyError,
    StoreOpsService,
)
from modules.opsboard.application.store_ops_audit_delivery import StoreOpsAuditDelivery
from modules.opsboard.application.store_ops_scope import ScopedStoreOpsRepository, binding_hash
from modules.opsboard.domain.r4_dtos import AttachmentUploadRequest
from shared.audit import AuditEvent, InMemoryAuditLog
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore


class StoreOpsTransitionPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    issueId: str | None = None
    issueTitle: str | None = None
    storeId: str | None = None
    storeName: str | None = None
    actorRoleId: str | None = None
    actorName: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class StoreOpsCameraPurposePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    issueId: str | None = None
    issueTitle: str | None = None
    storeId: str | None = None
    storeName: str | None = None
    purpose: str
    cameraLocation: str | None = None
    timeWindow: str | None = None
    retentionHours: int | None = None
    privacyAcknowledged: bool = False
    auditNote: str | None = None
    actorRoleId: str | None = None
    actorName: str | None = None


def _live_data_required(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    return live_data_required()


def create_operator_store_ops_router(
    *,
    repository: Any = None,
    store_repository: Any = None,
    delivery_job_queue: Any = None,
    audit_log: InMemoryAuditLog | None = None,
    require_live_data: bool | None = None,
) -> APIRouter:
    from apps.api.oday_api.security.dependencies import (
        OPERATOR_CONSOLE_RESOURCE,
        OPERATOR_TENANT_ID,
        build_engine,
        require_operator_permission,
    )
    from shared.auth import Action

    active_audit_log = audit_log or InMemoryAuditLog()
    authz_engine = build_engine(audit_log=active_audit_log)
    live_required = _live_data_required(require_live_data)
    guard_tenant_id = None if live_required else OPERATOR_TENANT_ID
    read_guard = require_operator_permission(
        OPERATOR_CONSOLE_RESOURCE,
        Action.VIEW,
        tenant_id=guard_tenant_id,
        engine=authz_engine,
    )
    write_guard = require_operator_permission(
        "intervention",
        Action.CREATE,
        tenant_id=guard_tenant_id,
        engine=authz_engine,
    )
    service = StoreOpsService(
        repository=repository,
        audit_log=active_audit_log,
        require_live_data=live_required,
    )
    router = APIRouter(prefix="/operator/store-ops", tags=["operator-store-ops"])

    def call_service(
        request: Request, operation: str, method: Any, *args: Any, **kwargs: Any
    ) -> Any:
        try:
            if not live_required:
                return method(*args, **kwargs)
            principal = getattr(request.state, "operator_principal", None)
            scope = getattr(principal, "scope", None)
            tenant_id = str(getattr(scope, "tenant_id", "") or "").strip()
            subject_id = str(getattr(principal, "subject_id", "") or "").strip()
            if not tenant_id or not subject_id or not getattr(principal, "authenticated", False):
                raise HTTPException(
                    status_code=403, detail="verified Store Ops principal scope is required"
                )
            document_store = getattr(repository, "_store", None)
            engine = getattr(document_store, "engine", None)
            if (
                document_store is None
                or store_repository is None
                or not callable(getattr(engine, "transaction", None))
                or str(getattr(engine, "dialect", "")).lower() != "postgresql"
            ):
                raise StoreOpsLiveDataUnavailable(
                    "Store Ops scoped PostgreSQL repositories are not configured"
                )
            bound_tenant = getattr(document_store, "tenant_id", None)
            if bound_tenant is not None and bound_tenant != tenant_id:
                raise StoreOpsLiveDataUnavailable("Store Ops repository belongs to another tenant")
            scoped_store = (
                document_store
                if bound_tenant
                else TenantScopedDocumentStore(document_store, tenant_id)
            )
            binding = {
                "tenant_id": tenant_id,
                "subject_id": subject_id,
                "roles": sorted(role.value for role in principal.roles),
                "brand_ids": sorted(scope.brand_ids),
                "region_ids": sorted(scope.region_ids),
                "store_ids": sorted(scope.store_ids),
                "clearance": scope.clearance.value,
                "assigned_area_ids": sorted(scope.assigned_area_ids),
                "heat_zone_ids": sorted(scope.heat_zone_ids),
                "modules": sorted(scope.modules),
            }
            if "actor_name" in kwargs:
                kwargs["actor_name"] = subject_id
            if "actor_role_id" in kwargs:
                kwargs["actor_role_id"] = _store_ops_role_from_request(
                    request, str(getattr(request.state, "operator_role_id", ""))
                )
            if "tenant_id" in kwargs:
                kwargs["tenant_id"] = tenant_id
            if "user_roles" in kwargs:
                kwargs["user_roles"] = binding["roles"]
            if isinstance(kwargs.get("payload"), dict):
                kwargs["payload"] = {
                    key: value
                    for key, value in kwargs["payload"].items()
                    if key not in {"actorName", "actorRoleId"}
                }
            fingerprint = binding_hash(
                [
                    operation,
                    list(args),
                    {
                        key: value
                        for key, value in kwargs.items()
                        if key not in {"correlation_id", "idempotency_key"}
                    },
                ]
            )
            sink = getattr(active_audit_log, "_worm_sink", None)
            delivery = StoreOpsAuditDelivery(scoped_store, sink, delivery_job_queue) if sink is not None else None
            audit = _TenantStoreOpsAudit(active_audit_log, engine, tenant_id, subject_id, delivery)
            with engine.transaction():
                engine.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                    ("operator.store_ops:" + tenant_id,),
                )
                stores = store_repository.list_stores(
                    tenant_id=tenant_id,
                    brand_ids=tuple(scope.brand_ids),
                    region_codes=tuple(scope.region_ids),
                    store_ids=tuple(scope.store_ids),
                )
                scoped_repository = ScopedStoreOpsRepository(
                    DurableStoreOpsRepository(scoped_store),
                    store_ids=frozenset(str(store.store_id) for store in stores),
                    subject_id=subject_id,
                    scope_hash=binding_hash(binding),
                    request_hash=fingerprint,
                    operation=operation,
                )
                scoped_service = StoreOpsService(
                    repository=scoped_repository, audit_log=audit, require_live_data=True
                )
                issue_id = kwargs.get("issue_id") or (args[0] if args else None)
                if issue_id is not None:
                    # Authorize the current record before even looking up a
                    # receipt; scope reduction cannot reveal an old response.
                    scoped_service.get_issue(str(issue_id))
                result = getattr(scoped_service, method.__name__)(*args, **kwargs)
                if isinstance(result, dict) and result.get("idempotentReplay") is True:
                    result.update(scoped_service.snapshot())
                    if issue_id is not None:
                        result["issue"] = scoped_service.get_issue(str(issue_id))
                if not audit.recorded:
                    for event in scoped_repository.new_audits:
                        audit.record(
                            AuditEvent(
                                event_type="operator.store_ops." + event["action"],
                                actor=subject_id,
                                action=event["action"],
                                resource="operator/store-ops/issues/" + event["targetId"],
                                outcome="accepted",
                                correlation_id=request.state.correlation_id,
                                metadata={**event["metadata"], "workflowAuditId": event["id"]},
                            )
                        )
            if delivery is not None:
                try:
                    delivery_status = delivery.deliver_pending()
                    if isinstance(result, dict):
                        result["auditDelivery"] = delivery_status
                except Exception:
                    # The business transaction has committed. Delivery status
                    # loss cannot turn it into a fictitious write rejection.
                    if isinstance(result, dict):
                        result["auditDelivery"] = {"status": "pending", "pending": None, "retryable": True}
            return result
        except StoreOpsLiveDataUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "STORE_OPS_LIVE_DATA_UNAVAILABLE",
                    "operation": operation,
                    "message": str(exc),
                },
            ) from exc
        except StoreOpsConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except StoreOpsNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/summary", dependencies=[Depends(read_guard)])
    def get_summary(request: Request) -> dict[str, Any]:
        snapshot = call_service(request, "store_ops.summary", service.snapshot)
        return {
            "fourLightSummary": snapshot["fourLightSummary"],
            "stores": snapshot["stores"],
            "correlation_id": request.state.correlation_id,
        }

    @router.get("/issues", dependencies=[Depends(read_guard)])
    def list_issues(
        request: Request,
        query: str | None = None,
        statuses: Annotated[list[str] | None, Query()] = None,
        sources: Annotated[list[str] | None, Query()] = None,
        severities: Annotated[list[str] | None, Query()] = None,
        mineOnly: bool = False,
        roleId: str = "opsLead",
        light: Literal["demand", "operations", "staffing", "margin"] | None = None,
        lightStatus: Literal["green", "yellow", "red"] | None = None,
    ) -> dict[str, Any]:
        snapshot = call_service(
            request,
            "store_ops.issues",
            service.snapshot,
            query=query,
            statuses=statuses or (),
            sources=sources or (),
            severities=severities or (),
            mine_only=mineOnly,
            role_id=_store_ops_role_from_request(request, roleId),
            light=light,
            light_status=lightStatus,
        )
        snapshot["correlation_id"] = request.state.correlation_id
        return snapshot

    @router.get("/issues/{issue_id}", dependencies=[Depends(read_guard)])
    def get_issue(issue_id: str, request: Request) -> dict[str, Any]:
        try:
            return {
                "issue": call_service(
                    request,
                    "store_ops.issue",
                    service.get_issue,
                    issue_id,
                ),
                "correlation_id": request.state.correlation_id,
            }
        except StoreOpsNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.get("/issues/{issue_id}/evidence", dependencies=[Depends(read_guard)])
    def get_issue_evidence(issue_id: str, request: Request) -> dict[str, Any]:
        try:
            result = call_service(
                request,
                "store_ops.issue_evidence",
                service.issue_evidence,
                issue_id,
            )
        except StoreOpsNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        result["correlation_id"] = request.state.correlation_id
        return result

    @router.get("/issues/{issue_id}/attachments", dependencies=[Depends(read_guard)])
    def list_issue_attachments(
        issue_id: str,
        request: Request,
        masking_profile: str = Header(default="masked", alias="X-Masking-Profile"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_tenant_id: str | None = Header(default="tenant-a", alias="X-Tenant-Id"),
    ) -> dict[str, Any]:
        user_roles = [r.strip() for r in x_roles.split(",")] if x_roles else []
        try:
            attachments = call_service(
                request,
                "store_ops.list_attachments",
                service.list_attachments,
                issue_id=issue_id,
                masking_profile=masking_profile,
                user_roles=user_roles,
                tenant_id=x_tenant_id,
            )
        except StoreOpsNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return {
            "issueId": issue_id,
            "attachments": attachments,
            "count": len(attachments),
            "correlation_id": request.state.correlation_id,
        }

    @router.post(
        "/issues/{issue_id}/attachments",
        dependencies=[Depends(write_guard)],
    )
    def upload_issue_attachment(
        issue_id: str,
        body: AttachmentUploadRequest,
        request: Request,
        x_tenant_id: str | None = Header(default="tenant-a", alias="X-Tenant-Id"),
    ) -> dict[str, Any]:
        try:
            attachment = call_service(
                request,
                "store_ops.add_attachment",
                service.add_attachment,
                issue_id=issue_id,
                payload=body.model_dump(exclude_none=True),
                actor_role_id=body.actorRoleId or "opsLead",
                actor_name=body.actorName or _actor_name_from_role(body.actorRoleId),
                tenant_id=x_tenant_id or "tenant-a",
                correlation_id=request.state.correlation_id,
            )
        except StoreOpsNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except StoreOpsPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        attachment["correlation_id"] = request.state.correlation_id
        return attachment

    @router.get(
        "/issues/{issue_id}/attachments/{attachment_id}", dependencies=[Depends(read_guard)]
    )
    def get_issue_attachment(
        issue_id: str,
        attachment_id: str,
        request: Request,
        masking_profile: str = Header(default="masked", alias="X-Masking-Profile"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_tenant_id: str | None = Header(default="tenant-a", alias="X-Tenant-Id"),
    ) -> dict[str, Any]:
        user_roles = [r.strip() for r in x_roles.split(",")] if x_roles else []
        try:
            attachment = call_service(
                request,
                "store_ops.get_attachment",
                service.get_attachment,
                issue_id=issue_id,
                attachment_id=attachment_id,
                masking_profile=masking_profile,
                user_roles=user_roles,
                tenant_id=x_tenant_id,
            )
        except StoreOpsNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        attachment["correlation_id"] = request.state.correlation_id
        return attachment

    @router.get(
        "/issues/{issue_id}/attachments/{attachment_id}/download",
        dependencies=[Depends(read_guard)],
    )
    def download_issue_attachment(
        issue_id: str,
        attachment_id: str,
        request: Request,
        masking_profile: str = Header(default="masked", alias="X-Masking-Profile"),
        x_roles: str | None = Header(default=None, alias="X-Roles"),
        x_tenant_id: str | None = Header(default="tenant-a", alias="X-Tenant-Id"),
    ) -> dict[str, Any]:
        user_roles = [r.strip() for r in x_roles.split(",")] if x_roles else []
        try:
            attachment = call_service(
                request,
                "store_ops.download_attachment",
                service.download_attachment,
                issue_id=issue_id,
                attachment_id=attachment_id,
                masking_profile=masking_profile,
                user_roles=user_roles,
                tenant_id=x_tenant_id,
                actor_role_id=None,
                actor_name=None,
            )
        except StoreOpsNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        attachment["correlation_id"] = request.state.correlation_id
        return attachment

    @router.delete(
        "/issues/{issue_id}/attachments/{attachment_id}",
        dependencies=[Depends(write_guard)],
    )
    def delete_issue_attachment(
        issue_id: str,
        attachment_id: str,
        request: Request,
        actor_role_id: str = Header(default="opsLead", alias="X-Actor-Role-Id"),
        x_tenant_id: str | None = Header(default="tenant-a", alias="X-Tenant-Id"),
    ) -> dict[str, Any]:
        try:
            result = call_service(
                request,
                "store_ops.delete_attachment",
                service.delete_attachment,
                issue_id=issue_id,
                attachment_id=attachment_id,
                actor_role_id=actor_role_id,
                actor_name=_actor_name_from_role(actor_role_id),
                tenant_id=x_tenant_id or "tenant-a",
                correlation_id=request.state.correlation_id,
            )
        except StoreOpsNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        result["correlation_id"] = request.state.correlation_id
        return result

    @router.post(
        "/issues/{issue_id}/{action_type}",
        dependencies=[Depends(write_guard)],
    )
    def transition_issue(
        issue_id: str,
        action_type: str,
        body: StoreOpsTransitionPayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        try:
            if action_type == "camera-purpose":
                return call_service(
                    request,
                    "store_ops.camera_purpose",
                    service.record_camera_purpose,
                    issue_id=issue_id,
                    payload=body.model_dump(exclude_none=True),
                    idempotency_key=idempotency_key,
                    correlation_id=request.state.correlation_id,
                    actor_role_id=body.actorRoleId or "opsLead",
                    actor_name=body.actorName or _actor_name_from_role(body.actorRoleId),
                )
            return call_service(
                request,
                "store_ops.transition",
                service.transition_issue,
                issue_id=issue_id,
                action_type=action_type,
                payload=body.model_dump(exclude_none=True),
                idempotency_key=idempotency_key,
                correlation_id=request.state.correlation_id,
                actor_role_id=body.actorRoleId or "opsLead",
                actor_name=body.actorName or _actor_name_from_role(body.actorRoleId),
            )
        except StoreOpsNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except StoreOpsConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except StoreOpsPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post(
        "/issues/{issue_id}/camera-purpose",
        dependencies=[Depends(write_guard)],
    )
    def record_camera_purpose(
        issue_id: str,
        body: StoreOpsCameraPurposePayload,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        try:
            return call_service(
                request,
                "store_ops.camera_purpose",
                service.record_camera_purpose,
                issue_id=issue_id,
                payload=body.model_dump(exclude_none=True),
                idempotency_key=idempotency_key,
                correlation_id=request.state.correlation_id,
                actor_role_id=body.actorRoleId or "opsLead",
                actor_name=body.actorName or _actor_name_from_role(body.actorRoleId),
            )
        except StoreOpsNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except StoreOpsConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except StoreOpsPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router


class _TenantStoreOpsAudit:
    """Keep command audits on the same engine/transaction as the state write."""

    def __init__(
        self, audit_log: Any, engine: Any, tenant_id: str, subject_id: str, delivery: Any = None
    ) -> None:
        self._audit_log = audit_log
        self._engine = engine
        self._tenant_id = tenant_id
        self._subject_id = subject_id
        self._delivery = delivery
        self.recorded = 0

    def record(self, event: AuditEvent) -> AuditEvent:
        if getattr(self._audit_log, "_engine", None) is not self._engine:
            raise StoreOpsLiveDataUnavailable(
                "Store Ops command requires a transactional PostgreSQL audit sink"
            )
        event = replace(
            event,
            actor=self._subject_id,
            metadata={
                **event.metadata,
                "tenant_id": self._tenant_id,
                "actor_subject_id": self._subject_id,
            },
        )
        try:
            if self._delivery is not None:
                with self._audit_log.transactional_worm_sink(self._delivery):
                    recorded = self._audit_log.record(event)
            else:
                recorded = self._audit_log.record(event)
        except Exception as exc:
            raise StoreOpsLiveDataUnavailable(
                "Store Ops command audit could not be committed"
            ) from exc
        self.recorded += 1
        return recorded


def _actor_name_from_role(role_id: str | None) -> str:
    return {
        "opsLead": "營運主管",
        "supportLead": "客服主管",
        "facilitiesLead": "工務主任",
        "marketingManager": "行銷經理",
        "expansionManager": "展店經理",
        "auditPm": "PM／稽核",
    }.get(role_id or "opsLead", "Operator")


def _store_ops_role_from_request(request: Request, fallback: str) -> str:
    role_id = getattr(request.state, "operator_role_id", None)
    return {
        "ops-lead": "opsLead",
        "cs-lead": "supportLead",
        "field-lead": "facilitiesLead",
        "marketing-manager": "marketingManager",
        "expansion-manager": "expansionManager",
        "pm-audit": "auditPm",
    }.get(role_id or "", fallback)


__all__ = ["create_operator_store_ops_router"]
