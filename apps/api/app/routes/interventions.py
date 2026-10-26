"""InterventionOps lifecycle API (ODP-MOD-05 §8).

Exposes the shared intervention lifecycle: create a case, run eligibility,
build an action candidate, check conflicts, submit for approval, approve/reject,
execute (which opens the observation window), collect outcomes, and evaluate the
effect (which writes a matured label back to the Label Registry).
"""

from __future__ import annotations

import os
import re
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from typing import Any

from modules.adlift.domain.campaign import (
    CampaignConflict,
    CampaignForbidden,
    CampaignInvalid,
    CampaignUnavailable,
)
from modules.intervention.application.approval_contract import (
    InterventionOwnerIntegrityError,
    approval_read_context,
    require_positive_intervention_version,
    verified_creator_subject,
)
from modules.intervention.application.lifecycle_command_contract import (
    COMMAND_ACTIONS,
    LifecycleCommandContext,
    _VerifiedLifecycleTransition,
    lifecycle_command_context,
)
from modules.intervention.application.workflow import InterventionWorkflow
from modules.intervention.domain.lifecycle import (
    FEATURE_VERSION,
    InterventionError,
    InterventionKind,
    InterventionStatus,
)
from modules.intervention.infrastructure.repositories import InMemoryLabelRegistry
from shared.api.idempotency import IdempotencyConflictError, apply_replay_marker
from shared.infrastructure.persistence.command_receipts import (
    CommandReceiptIncompleteError,
    CommandReceiptPersistenceError,
    TenantScopedCommandReceiptStore,
)
from shared.infrastructure.persistence.job_receipts import JobQueue
from shared.jobs.queue import InMemoryJobQueue

try:
    from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
    from pydantic import BaseModel, Field, StrictInt, field_validator
except ModuleNotFoundError:  # pragma: no cover - optional API dependency
    APIRouter = None  # type: ignore[assignment]
else:
    from apps.api.app.routes._common import durable_store_required

    class OpenCasePayload(BaseModel):
        store_id: str = Field(min_length=1)
        kind: str
        trigger_ref: str = ""
        expected_outcome: str = Field(min_length=1)
        planned_start: str
        planned_end: str
        created_by: str = Field(min_length=1)
        action_spec: dict[str, Any] = Field(default_factory=dict)
        idempotency_key: str | None = None

    class LifecyclePayload(BaseModel):
        expected_version: Any = None
        expected_actor_subject_id: str | None = Field(default=None, alias="expectedActorSubjectId")
        expected_tenant_id: str | None = Field(default=None, alias="expectedTenantId")
        command_context: LifecycleCommandContext | None = None

    class AssignPayload(LifecyclePayload):
        assignee: str = Field(min_length=1)
        actor: str = Field(min_length=1)
        role: str | None = None
        expected_version: int | None = None

    class UnassignPayload(LifecyclePayload):
        actor: str = Field(min_length=1)
        expected_version: int | None = None

    class EligibilityPayload(LifecyclePayload):
        eligible: bool
        actor: str = Field(min_length=1)
        reasons: list[str] = Field(default_factory=list)

    class ActionPayload(LifecyclePayload):
        action_spec: dict[str, Any] = Field(default_factory=dict)
        actor: str = Field(min_length=1)

    class ConflictCheckPayload(LifecyclePayload):
        actor: str = Field(min_length=1)
        allow_overlap: bool = False
        reason: str = ""

    class SubmitPayload(LifecyclePayload):
        actor: str = Field(min_length=1)

    class ApprovalReadContext(BaseModel):
        model_config = {"extra": "forbid"}
        schema_version: StrictInt = Field(ge=1, le=1)
        intervention_id: str = Field(min_length=1)
        store_id: str = Field(min_length=1)
        tenant_id: str = Field(min_length=1)
        actor_subject_id: str = Field(min_length=1)
        version: StrictInt = Field(ge=1)
        policy_version: str = Field(min_length=1)
        snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    class DecisionPayload(BaseModel):
        action: str
        actor: str | None = None
        reason: str = ""
        expected_version: StrictInt | None = Field(default=None, ge=1)
        expected_actor_subject_id: str | None = Field(default=None, alias="expectedActorSubjectId")
        expected_tenant_id: str | None = Field(default=None, alias="expectedTenantId")
        review_context: ApprovalReadContext | None = None

    class ExecutePayload(LifecyclePayload):
        executor: str = Field(min_length=1)
        executed_at: str | None = None

    class OutcomePayload(LifecyclePayload):
        actor: str = Field(min_length=1)
        incremental_revenue: float = 0.0
        incremental_gross_margin: float = 0.0
        has_control_group: bool = False
        pretrend_status: str = "INCONCLUSIVE"
        treatment_store_count: int = 0
        control_store_count: int = 0
        evaluation_method: str = "BEFORE_AFTER"
        randomized: bool = False
        ad_spend: float = 0.0
        measurement_method: str | None = None

    class EvaluatePayload(LifecyclePayload):
        actor: str = Field(min_length=1)
        replicated: bool = False
        now: str | None = None

    class ClosePayload(LifecyclePayload):
        actor: str = Field(min_length=1)
        disposition: str
        reason: str = ""
        follow_up: bool = False
        follow_up_kind: str | None = None

    class AdjustPayload(LifecyclePayload):
        actor: str = Field(min_length=1)
        reason: str = Field(min_length=1)
        action_spec: dict[str, Any] | None = None
        planned_start: str | None = None
        planned_end: str | None = None
        expected_outcome: str | None = None
        rollback_plan: str | dict[str, Any] | None = None
        expected_version: int | None = None

        @field_validator("planned_start", "planned_end")
        @classmethod
        def validate_planned_time(cls, value: str | None) -> str | None:
            if value:
                try:
                    _parse_time(value)
                except (OverflowError, ValueError) as exc:
                    raise ValueError(f"invalid date format or range: {exc}") from exc
            return value

    class StopPayload(LifecyclePayload):
        actor: str = Field(min_length=1)
        reason: str = Field(min_length=1)

    class RollbackPayload(LifecyclePayload):
        actor: str = Field(min_length=1)
        reason: str = Field(min_length=1)

    def _parse_time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed

    def create_interventions_router(
        *,
        workflow: InterventionWorkflow | None = None,
        store_repository: Any = None,
        brand_repository: Any = None,
        require_object_scope: bool = False,
        label_registry: InMemoryLabelRegistry | None = None,
        job_queue: JobQueue | None = None,
        require_durable_commands: bool = False,
    ) -> APIRouter:
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from shared.auth import Action

        router = APIRouter(prefix="/interventions", tags=["interventions"])
        active_workflow = workflow or InterventionWorkflow()
        authz_engine = build_engine(audit_log=active_workflow.audit_log)
        registry = label_registry or InMemoryLabelRegistry()
        if registry not in active_workflow.label_hooks:
            active_workflow.register_label_hook(registry)
        strict_scope = require_object_scope or require_durable_commands or os.environ.get("ODP_PRODUCT_MODE") == "production"
        local_job_queue = None if strict_scope else InMemoryJobQueue()

        def approval_context(intervention: Any, *, tenant_id: str, actor_subject_id: str):
            try:
                return approval_read_context(intervention, tenant_id=tenant_id, actor_subject_id=actor_subject_id)
            except InterventionError as exc:
                raise HTTPException(409, detail="INTERVENTION_VERSION_INVALID") from exc

        def principal_for(request: Request):
            principal = getattr(request.state, "operator_principal", None)
            if principal is None or not principal.authenticated or not principal.subject_id:
                raise HTTPException(401, detail="AUTHENTICATED_PRINCIPAL_REQUIRED")
            return principal

        def command_actor(request: Request, legacy_actor: str | None) -> str:
            return principal_for(request).subject_id if strict_scope else str(legacy_actor or "")

        def scope_engine():
            from shared.infrastructure.persistence.audit_log import DurableAuditLog
            from shared.infrastructure.persistence.repositories import (
                DurableBrandRepository,
                DurableInterventionRepository,
                DurableStoreRepository,
            )
            repo = active_workflow.repository
            engine = getattr(getattr(repo, "_store", None), "engine", None)
            if (not isinstance(repo, DurableInterventionRepository)
                    or not isinstance(active_workflow.audit_log, DurableAuditLog)
                    or not isinstance(store_repository, DurableStoreRepository)
                    or not isinstance(brand_repository, DurableBrandRepository)
                    or engine is None
                    or getattr(active_workflow.audit_log, "_engine", None) is not engine
                    or getattr(store_repository, "_engine", None) is not engine
                    or getattr(brand_repository, "_engine", None) is not engine):
                raise HTTPException(503, detail="CANONICAL_STORE_SCOPE_BINDING_REQUIRED")
            return engine

        def authorize_store(request: Request, store_id: str, *, lock: bool = False):
            if not strict_scope:
                return None
            from apps.api.oday_api.security.dependencies import (
                _record_allow_or_fail_closed,
                _record_denial,
            )
            from shared.auth import (
                AccessRequest,
                DataClassification,
                Decision,
                Environment,
                ResourceDescriptor,
            )
            from shared.auth.abac import evaluate_abac
            from shared.auth.tenant import check_tenant_isolation
            principal = principal_for(request)
            engine = scope_engine()
            try:
                if lock and str(getattr(engine, "dialect", "")).lower() == "postgresql":
                    engine.query_one("SELECT store_id FROM stores WHERE store_id = ? FOR UPDATE", (store_id,))
                store = store_repository.get_store(store_id)
            except Exception as exc:
                raise HTTPException(503, detail="CANONICAL_STORE_SCOPE_UNAVAILABLE") from exc
            if store is None or str(store.store_id) != store_id or not store.is_current:
                raise HTTPException(404, detail="intervention store not found")
            tenant_decision = check_tenant_isolation(principal, store.tenant_id,
                resource_type="intervention", resource_id=store_id)
            scope = principal.scope
            denied = (not tenant_decision.allowed or not store.brand_id
                    or not scope.permits_store(store.store_id)
                    or not scope.permits_brand(store.brand_id)
                    or not scope.permits_region(store.region_code or None)
                    or not scope.permits_module("intervention")
                    or not scope.permits_assigned_area(None)
                    or not scope.permits_heat_zone(None)
                    or not scope.permits_classification(DataClassification.CONFIDENTIAL))
            action = Action.VIEW if request.method == "GET" else (
                Action.APPROVE if request.url.path.endswith("/approve") else Action.CREATE)
            action = getattr(request.state, "intervention_lifecycle_action", action)
            access = AccessRequest(principal=principal, action=action,
                resource=ResourceDescriptor(type="intervention", resource_id=store_id,
                    tenant_id=store.tenant_id, brand_id=store.brand_id,
                    region_id=store.region_code or None, store_id=store_id, module="intervention"),
                environment=Environment(attributes={"correlation_id": request.state.correlation_id}))
            denied = denied or not evaluate_abac(access).allowed
            if denied:
                _record_denial(authz_engine, access,
                    Decision.deny("INTERVENTION_OBJECT_SCOPE_DENIED", policy_id="intervention.object_scope"))
                raise HTTPException(403, detail="INTERVENTION_OBJECT_SCOPE_DENIED")
            try:
                if lock and str(getattr(engine, "dialect", "")).lower() == "postgresql":
                    engine.query_one("SELECT brand_id FROM brands WHERE brand_id = ? FOR UPDATE", (store.brand_id,))
                brand = brand_repository.get_brand(store.brand_id)
            except Exception as exc:
                raise HTTPException(503, detail="CANONICAL_STORE_SCOPE_UNAVAILABLE") from exc
            if brand is None or str(brand.brand_id) != str(store.brand_id) or brand.tenant_id != store.tenant_id:
                raise HTTPException(503, detail="CANONICAL_STORE_TENANCY_CONFLICT")
            _record_allow_or_fail_closed(authz_engine, access, Decision.allow("canonical intervention store scope accepted"))
            return store

        def command_target(request: Request, scope: str, payload: Any, *, lock: bool = False):
            if strict_scope:
                scope_engine()
            if scope == "interventions:create":
                return authorize_store(request, payload["store_id"], lock=lock)
            intervention_id = scope.removeprefix("interventions:").rsplit(":", 1)[0]
            if strict_scope:
                store_id = active_workflow.repository.store_id_for_scope(intervention_id, for_update=lock)
                if store_id is None:
                    raise HTTPException(404, detail="intervention not found")
                return authorize_store(request, store_id, lock=lock)
            try:
                intervention = active_workflow._require(intervention_id, for_update=lock)
            except InterventionError as exc:
                raise HTTPException(404, detail="intervention not found") from exc
            return authorize_store(request, intervention.store_id, lock=lock)

        @contextmanager
        def durable_transaction(request: Request, queue: Any, scope: str, payload: Any):
            from shared.audit.durable_delivery import DurableAuditDelivery
            from shared.infrastructure.persistence.audit_log import DurableAuditLog
            from shared.infrastructure.persistence.repositories import DurableInterventionRepository
            repo = active_workflow.repository
            engine = getattr(getattr(repo, "_store", None), "engine", None)
            audit = active_workflow.audit_log
            if (not isinstance(repo, DurableInterventionRepository)
                    or not isinstance(audit, DurableAuditLog)
                    or engine is None or getattr(queue, "_engine", None) is not engine
                    or getattr(audit, "_engine", None) is not engine
                    or getattr(store_repository, "_engine", None) is not engine
                    or getattr(brand_repository, "_engine", None) is not engine):
                raise HTTPException(503, detail="INTERVENTION_SHARED_TRANSACTION_REQUIRED")
            with repo.command_atomic(), ExitStack() as stack:
                principal = principal_for(request)
                verified_tenant = tenant_id(request)
                stack.enter_context(active_workflow.verified_command_context(
                    tenant_id=verified_tenant, actor_subject_id=principal.subject_id))
                if getattr(audit, "_worm_sink", None) is not None:
                    delivery = DurableAuditDelivery(engine, verified_tenant, queue, audit._worm_sink)
                    stack.enter_context(audit.transactional_worm_sink(delivery))
                budget_owner = active_workflow.campaign_budget_authority
                if budget_owner is not None and budget_owner.touches_native_command(scope, payload):
                    if budget_owner.boundary is None:
                        raise HTTPException(401, detail="CAMPAIGN_AUTH_BOUNDARY_REQUIRED")
                    command = scope.rsplit(":", 1)[-1]
                    from modules.intervention.application.lifecycle_command_contract import (
                        COMMAND_ACTIONS,
                    )
                    budget_action = Action.APPROVE if command == "decision" else Action[COMMAND_ACTIONS.get(command, "CREATE")]
                    trusted = require_permission("intervention", budget_action,
                        engine=authz_engine, boundary=budget_owner.boundary)(request)
                    if trusted != principal:
                        raise HTTPException(409, detail="CAMPAIGN_NATIVE_AUTH_CONTEXT_CHANGED")
                    stack.enter_context(budget_owner.native_command_context(principal=trusted,
                        scope=scope, payload=payload, correlation_id=request.state.correlation_id,
                        native_repository=repo, actual_queue=queue))
                # Resolve and authorize the locked canonical store before any
                # command reservation or replay; its tenant must match this
                # Principal, including when a WORM outbox is bound above.
                command_target(request, scope, payload, lock=True)
                yield

        def command_store(request: Request, scope: str, payload: Any) -> TenantScopedCommandReceiptStore:
            active_queue = job_queue
            if active_queue is None:
                app = request.scope.get("app")
                active_queue = getattr(
                    getattr(app, "state", None),
                    "job_queue",
                    None,
                )
            active_queue = active_queue or local_job_queue
            if active_queue is None:
                raise _durable_store_required(
                    "Intervention production commands require durable persistence"
                )
            store = TenantScopedCommandReceiptStore(
                queue=active_queue,
                service="interventions",
                transaction_factory=(lambda: durable_transaction(request, active_queue, scope, payload)) if strict_scope else None,
            )
            if strict_scope and not store.is_durable:
                raise _durable_store_required(
                    "Intervention production commands reject process-local stores"
                )
            return store

        def tenant_id(request: Request) -> str:
            principal = getattr(request.state, "operator_principal", None)
            value = getattr(principal, "tenant_id", None)
            if value:
                return str(value)
            if strict_scope:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "TENANT_SCOPE_REQUIRED",
                        "message": (
                            "Intervention production commands require tenant scope"
                        ),
                    },
                )
            return "__local__"

        def run_command(
            *,
            request: Request,
            scope: str,
            payload: Any,
            idempotency_key: str | None,
            operation: Any,
        ) -> dict[str, Any]:
            try:
                if strict_scope:
                    command_target(request, scope, payload)
                    principal = principal_for(request)
                    payload = {**payload, "actor": principal.subject_id}
                    if scope == "interventions:create":
                        payload["created_by"] = principal.subject_id
                def execute_fresh():
                    # The receipt store calls this only for a new command;
                    # authorized exact original replays retain their response.
                    if strict_scope and scope != "interventions:create":
                        intervention_id = scope.removeprefix("interventions:").rsplit(":", 1)[0]
                        owner_store = active_workflow.repository.store_id_for_scope(intervention_id, for_update=True)
                        current = active_workflow._require(intervention_id, for_update=True)
                        if current.intervention_id != intervention_id or current.store_id != owner_store:
                            raise HTTPException(409, detail="INTERVENTION_RESOURCE_CONTEXT_CONFLICT")
                        try:
                            require_positive_intervention_version(current.version)
                        except InterventionError as exc:
                            raise HTTPException(409, detail="INTERVENTION_VERSION_INVALID") from exc
                    return operation()

                outcome = command_store(request, scope, payload).run(
                    tenant_id=tenant_id(request),
                    idempotency_key=idempotency_key,
                    scope=scope,
                    payload=payload,
                    correlation_id=request.state.correlation_id,
                    operation=lambda _receipt_id: execute_fresh(),
                )
            except IdempotencyConflictError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "IDEMPOTENCY_KEY_REUSED",
                        "message": str(exc),
                    },
                ) from exc
            except CommandReceiptIncompleteError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "COMMAND_RECEIPT_INCOMPLETE",
                        "message": str(exc),
                    },
                ) from exc
            except CommandReceiptPersistenceError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "DURABLE_COMMAND_STORE_UNAVAILABLE",
                        "message": str(exc),
                    },
                ) from exc
            except HTTPException:
                raise
            except CampaignForbidden as exc:
                raise HTTPException(403, detail="CAMPAIGN_NATIVE_PERMISSION_DENIED") from exc
            except CampaignConflict as exc:
                raise HTTPException(409, detail="CAMPAIGN_NATIVE_CONTEXT_CONFLICT") from exc
            except CampaignInvalid as exc:
                raise HTTPException(422, detail="CAMPAIGN_NATIVE_INPUT_INVALID") from exc
            except CampaignUnavailable as exc:
                raise HTTPException(503, detail="CAMPAIGN_NATIVE_OWNER_UNAVAILABLE") from exc
            except Exception as exc:
                if strict_scope:
                    raise HTTPException(503, detail="INTERVENTION_COMMAND_TRANSACTION_FAILED") from exc
                raise
            return apply_replay_marker(
                outcome.value,
                replayed=outcome.replayed,
            )

        def lifecycle_authorize(request: Request, command: str) -> None:
            if command not in COMMAND_ACTIONS:
                raise HTTPException(422, detail="unknown lifecycle command")
            action = Action[COMMAND_ACTIONS[command]]
            require_permission("intervention", action, engine=authz_engine)(request)
            if command == "adjust":
                require_permission("intervention", Action.CREATE, engine=authz_engine)(request)
            request.state.intervention_lifecycle_action = action

        def lifecycle_context(current: Any, request: Request, command: str, store: Any):
            cohort = None
            if command in {"conflict-check", "submit"}:
                cohort = active_workflow.repository.lifecycle_cohort(store.store_id,
                    exclude_id=current.intervention_id)
            return lifecycle_command_context(current, command=command, tenant_id=store.tenant_id,
                actor_subject_id=principal_for(request).subject_id, cohort_sha256=cohort)

        def run_lifecycle_command(*, request: Request, scope: str, payload: Any,
                                  idempotency_key: str | None, operation: Any) -> dict[str, Any]:
            if not strict_scope:
                # Keep original ordinary payload fingerprints as well as behavior.
                legacy = {k: v for k, v in payload.items() if k not in {
                    "expected_actor_subject_id", "expected_tenant_id", "command_context"}}
                command = scope.rsplit(":", 1)[1]
                if command not in {"assign", "unassign", "adjust"}:
                    legacy.pop("expected_version", None)
                return run_command(request=request, scope=scope, payload=legacy,
                    idempotency_key=idempotency_key, operation=operation)
            command = scope.rsplit(":", 1)[1]
            intervention_id = scope.removeprefix("interventions:").rsplit(":", 1)[0]
            lifecycle_authorize(request, command)
            owner = command_target(request, scope, payload)
            principal = principal_for(request)
            # Check the original JSON primitive, before Pydantic int coercion in
            # existing assign/unassign/adjust DTOs. Never normalize caller types.
            raw = getattr(request, "_json", {})
            expected = raw.get("expected_version") if isinstance(raw, dict) else None
            if not isinstance(raw, dict) or "expected_version" not in raw or payload.get("command_context") is None:
                raise HTTPException(428, detail="command_context and expected_version are required")
            if type(expected) is not int or expected < 1:
                raise HTTPException(422, detail="expected_version must be a positive built-in integer")
            if_match = request.headers.get("If-Match")
            if if_match is None:
                raise HTTPException(428, detail="If-Match is required")
            if re.fullmatch(r'W/"[1-9][0-9]*"', if_match) is None:
                raise HTTPException(422, detail="invalid If-Match format")
            if int(if_match[3:-1]) != expected:
                raise HTTPException(409, detail="VERSION_CONTEXT_CONFLICT")
            if (request.headers.get("If-Actor-Match") != principal.subject_id
                    or payload.get("expected_actor_subject_id") != principal.subject_id
                    or payload.get("expected_tenant_id") != principal.tenant_id):
                raise HTTPException(409, detail="ACTOR_CONTEXT_CONFLICT")
            if not idempotency_key or not idempotency_key.strip():
                raise HTTPException(428, detail="Idempotency-Key is required")
            cid = request.headers.get("x-correlation-id")
            if not cid or not cid.strip() or cid != request.state.correlation_id:
                raise HTTPException(428, detail="explicit correlation ID is required")
            context = payload["command_context"]
            if (context["command"] != command or context["intervention_id"] != intervention_id
                    or context["store_id"] != owner.store_id or context["tenant_id"] != principal.tenant_id
                    or context["actor_subject_id"] != principal.subject_id or context["version"] != expected):
                raise HTTPException(409, detail="COMMAND_RESOURCE_CONTEXT_CONFLICT")
            immutable_payload = {**payload, "original_transport": {
                "body": raw, "if_match": if_match, "if_actor_match": request.headers.get("If-Actor-Match"),
                "correlation_id": cid, "idempotency_key": idempotency_key}}

            def execute_fresh():
                try:
                    store = command_target(request, scope, payload, lock=True)
                    if command in {"conflict-check", "submit"}:
                        active_workflow.repository.lifecycle_cohort_preflight(store.store_id)
                    current, saved_raw = active_workflow.repository.lifecycle_snapshot(intervention_id,
                        store_id=store.store_id)
                    # Integrity checks (including peer decoding) precede stale digest comparisons.
                    actual = lifecycle_context(current, request, command, store)
                    if actual != context:
                        raise HTTPException(409, detail="LIFECYCLE_READ_CONTEXT_CONFLICT")
                    if command == "submit":
                        active_workflow.repository.require_conflict_proof(current,
                            tenant_id=store.tenant_id, cohort_sha256=actual["cohort_sha256"])
                    transition = _VerifiedLifecycleTransition(original=current, raw=saved_raw,
                        engine=scope_engine(), tenant_id=store.tenant_id,
                        actor_subject_id=principal.subject_id, command=command, correlation_id=cid)
                    with active_workflow.verified_lifecycle_transition(transition):
                        result = operation()
                    if command == "conflict-check":
                        active_workflow.repository.save_conflict_proof(transition.saved,
                            tenant_id=store.tenant_id, cohort_sha256=actual["cohort_sha256"])
                    result["correlation_id"] = cid
                    events = [event for event in active_workflow.audit_log.list_events(
                        correlation_id=cid, tenant_id=store.tenant_id)
                        if event.event_type == "intervention.lifecycle.v1"
                        and event.resource == f"intervention/{intervention_id}"]
                    if len(events) != 1 or events[0].actor != principal.subject_id:
                        raise HTTPException(503, detail="LIFECYCLE_AUDIT_RECEIPT_REQUIRED")
                    result["lifecycle_audit_event_id"] = events[0].event_id
                    result["command"] = command
                    result["previous_version"] = current.version
                    result["version"] = transition.saved.version
                    return result
                except InterventionOwnerIntegrityError as exc:
                    raise HTTPException(503, detail="CANONICAL_INTERVENTION_OWNER_INDEX_CONFLICT") from exc
                except InterventionError as exc:
                    if "stale update" in str(exc):
                        raise HTTPException(409, detail=str(exc)) from exc
                    if "same-engine durable label" in str(exc):
                        raise HTTPException(503, detail="LIFECYCLE_LABEL_TRANSACTION_BINDING_REQUIRED") from exc
                    raise HTTPException(422, detail=str(exc)) from exc

            try:
                outcome = command_store(request, scope, immutable_payload).run(
                    tenant_id=tenant_id(request), idempotency_key=idempotency_key, scope=scope,
                    payload=immutable_payload, correlation_id=cid,
                    operation=lambda _receipt_id: execute_fresh())
            except IdempotencyConflictError as exc:
                raise HTTPException(409, detail="IDEMPOTENCY_KEY_REUSED") from exc
            except CommandReceiptIncompleteError as exc:
                raise HTTPException(409, detail="COMMAND_RECEIPT_INCOMPLETE") from exc
            except CommandReceiptPersistenceError as exc:
                raise HTTPException(503, detail="DURABLE_COMMAND_STORE_UNAVAILABLE") from exc
            except HTTPException:
                raise
            except CampaignForbidden as exc:
                raise HTTPException(403, detail="CAMPAIGN_NATIVE_PERMISSION_DENIED") from exc
            except CampaignConflict as exc:
                raise HTTPException(409, detail="CAMPAIGN_NATIVE_CONTEXT_CONFLICT") from exc
            except CampaignInvalid as exc:
                raise HTTPException(422, detail="CAMPAIGN_NATIVE_INPUT_INVALID") from exc
            except CampaignUnavailable as exc:
                raise HTTPException(503, detail="CAMPAIGN_NATIVE_OWNER_UNAVAILABLE") from exc
            except Exception as exc:
                raise HTTPException(503, detail="INTERVENTION_COMMAND_TRANSACTION_FAILED") from exc
            return apply_replay_marker(outcome.value, replayed=outcome.replayed)

        @router.get("/{intervention_id}/command-context")
        def get_lifecycle_command_context(intervention_id: str, request: Request, command: str) -> dict[str, Any]:
            lifecycle_authorize(request, command)
            if not strict_scope:
                raise HTTPException(409, detail="strict lifecycle context requires canonical durable scope")
            try:
                with active_workflow.repository.command_atomic():
                    store = command_target(request, f"interventions:{intervention_id}:{command}", {}, lock=True)
                    if command in {"conflict-check", "submit"}:
                        active_workflow.repository.lifecycle_cohort_preflight(store.store_id)
                    current, _raw = active_workflow.repository.lifecycle_snapshot(intervention_id, store_id=store.store_id)
                    return lifecycle_context(current, request, command, store)
            except InterventionOwnerIntegrityError as exc:
                raise HTTPException(503, detail="CANONICAL_INTERVENTION_OWNER_INDEX_CONFLICT") from exc
            except InterventionError as exc:
                raise HTTPException(409, detail=str(exc)) from exc

        def _get_or_404(intervention_id: str, request: Request):
            if strict_scope:
                scope_engine()
                store_id = active_workflow.repository.store_id_for_scope(intervention_id)
                if store_id is None:
                    raise HTTPException(404, detail="intervention not found")
                authorize_store(request, store_id)
            intervention = active_workflow.get(intervention_id)
            if intervention is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="intervention not found"
                )
            if strict_scope and (intervention.store_id != store_id or intervention.intervention_id != intervention_id):
                raise HTTPException(503, detail="CANONICAL_INTERVENTION_OWNER_CONFLICT")
            authorize_store(request, intervention.store_id)
            return intervention

        @router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("intervention", Action.CREATE, engine=authz_engine))])
        def open_case(
            body: OpenCasePayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            effective_key = body.idempotency_key or idempotency_key

            def execute() -> dict[str, Any]:
                try:
                    intervention = active_workflow.open_case(
                        store_id=body.store_id,
                        kind=body.kind,
                        trigger_ref=body.trigger_ref,
                        expected_outcome=body.expected_outcome,
                        planned_start=_parse_time(body.planned_start),
                        planned_end=_parse_time(body.planned_end),
                        created_by=command_actor(request, body.created_by),
                        action_spec=body.action_spec,
                        correlation_id=request.state.correlation_id,
                    )
                except (InterventionError, ValueError) as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=str(exc),
                    ) from exc
                payload = intervention.to_dict()
                payload["created"] = True
                payload["correlation_id"] = request.state.correlation_id
                return payload

            result = run_command(
                request=request,
                scope="interventions:create",
                payload=body.model_dump(mode="json"),
                idempotency_key=effective_key,
                operation=execute,
            )
            if result.get("idempotent_replay"):
                result["created"] = False
            return result

        @router.get("", dependencies=[Depends(require_permission("intervention", Action.VIEW, engine=authz_engine))])
        def list_interventions(
            request: Request,
            store_id: str | None = None,
            assigned_to: str | None = None,
            status: str | None = None,
            kind: str | None = None,
        ) -> dict[str, Any]:
            try:
                if strict_scope:
                    scope_engine()
                    target_status = InterventionStatus(status) if status else None
                    target_kind = InterventionKind(kind) if kind else None
                    principal = principal_for(request)
                    if store_id:
                        stores = [authorize_store(request, store_id)]
                    else:
                        if store_repository is None:
                            raise HTTPException(503, detail="CANONICAL_STORE_SCOPE_REQUIRED")
                        try:
                            stores = store_repository.list_stores(tenant_id=tenant_id(request),
                                brand_ids=tuple(principal.scope.brand_ids), region_codes=tuple(principal.scope.region_ids),
                                store_ids=tuple(principal.scope.store_ids))
                        except Exception as exc:
                            raise HTTPException(503, detail="CANONICAL_STORE_SCOPE_UNAVAILABLE") from exc
                    items = []
                    scoped_ids: list[tuple[str, str]] = []
                    for store in stores:
                        if not store.is_current:
                            continue
                        authorize_store(request, store.store_id)
                        scoped_ids.extend((iid, store.store_id)
                            for iid in active_workflow.repository.list_scope_ids_by_store(store.store_id))
                    # Every owner/index metadata row is checked before reading
                    # any business object; no unauthorized post-filter repair.
                    for iid, expected_store in scoped_ids:
                        item = _get_or_404(iid, request)
                        if item.store_id != expected_store:
                            raise InterventionOwnerIntegrityError("canonical intervention aggregate owner conflict")
                        require_positive_intervention_version(item.version)
                        if assigned_to and item.assigned_to != assigned_to:
                            continue
                        if target_status is not None and item.status != target_status:
                            continue
                        if target_kind is not None and item.kind != target_kind:
                            continue
                        items.append(item)
                else:
                    items = active_workflow.list_cases(store_id=store_id,
                        assigned_to=assigned_to, status=status, kind=kind)
            except InterventionOwnerIntegrityError as exc:
                raise HTTPException(503, detail="CANONICAL_INTERVENTION_OWNER_INDEX_CONFLICT") from exc
            except (InterventionError, ValueError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail=str(exc),
                ) from exc
            return {"items": [i.to_dict() for i in items], "count": len(items)}

        def lifecycle_audit_projection(intervention: Any, request: Request, store: Any,
                                       correlation_id: str) -> dict[str, Any] | None:
            # The selector never supplies authority. The caller has already
            # checked current VIEW, canonical ownership and tenant/store ABAC.
            try:
                events = active_workflow.audit_log.list_events(
                    correlation_id=correlation_id, tenant_id=store.tenant_id)
            except Exception as exc:
                raise HTTPException(503, detail="LIFECYCLE_AUDIT_READBACK_UNAVAILABLE") from exc
            events = [event for event in events
                if event.event_type == "intervention.lifecycle.v1"
                and event.resource == f"intervention/{intervention.intervention_id}"
                and event.correlation_id == correlation_id]
            if len(events) != 1:
                return None
            event = events[0]
            metadata = event.metadata
            if (not isinstance(metadata, dict)
                    or metadata.get("tenant_id") != store.tenant_id
                    or metadata.get("store_id") != intervention.store_id
                    or metadata.get("kind") != intervention.kind.value
                    or metadata.get("actor_evidence") != "authenticated_principal.v1"
                    or metadata.get("actor_subject_id") != event.actor
                    or not isinstance(event.actor, str) or not event.actor.strip()
                    or not isinstance(event.event_id, str) or not event.event_id.strip()
                    or not isinstance(event.outcome, str)
                    or not isinstance(event.occurred_at, datetime)
                    or not isinstance(metadata.get("reason"), str)
                    or not isinstance(metadata.get("policy_version"), str)
                    or not metadata["policy_version"].strip()):
                return None
            paths = {
                "assign": ("assign", [("assign",)]),
                "unassign": ("unassign", [("unassign",)]),
                "check_eligibility": ("eligibility", [("check_eligibility",)]),
                "propose_action": ("action", [("propose_action",)]),
                "check_conflict": ("conflict-check", [("check_conflict",)]),
                "submit_for_approval": ("submit", [("submit_for_approval",)]),
                "execute": ("execute", [("execute", "start_observation")]),
                "collect_outcome": ("outcomes", [("collect_outcome",)]),
                "evaluate_effect": ("evaluate", [("evaluate_effect",), ("evaluate_effect", "complete")]),
                "close": ("close", [("close",)]),
                "adjust": ("adjust", [("adjust",)]),
                "stop": ("stop", [("stop",)]),
                "rollback": ("rollback", [("rollback",)]),
            }
            allowed_sources = {
                "check_eligibility": {"CANDIDATE", "ELIGIBILITY_CHECKING"},
                "propose_action": {"ELIGIBLE"},
                "check_conflict": {"ACTION_PROPOSED", "CONFLICT_CHECKING"},
                "submit_for_approval": {"CONFLICT_CHECKING"},
                "execute": {"APPROVED"}, "collect_outcome": {"OBSERVING"},
                "evaluate_effect": {"OBSERVING", "EVALUATING"}, "close": {"COMPLETED"},
                "adjust": {"APPROVED", "EXECUTING", "OBSERVING"},
                "stop": {"APPROVED", "EXECUTING", "OBSERVING"},
                "rollback": {"EXECUTING", "OBSERVING", "EVALUATING"},
            }
            targets = {
                "check_eligibility": [("ELIGIBLE",), ("INELIGIBLE",)],
                "propose_action": [("ACTION_PROPOSED",)],
                "check_conflict": [("CONFLICT_CHECKING",)],
                "submit_for_approval": [("PENDING_APPROVAL",)],
                "execute": [("EXECUTING", "OBSERVING")], "collect_outcome": [("OBSERVING",)],
                "evaluate_effect": [("EVALUATING",), ("EVALUATING", "COMPLETED")],
                "close": [("CLOSED",)], "adjust": [("STOPPED",)], "stop": [("STOPPED",)],
                "rollback": [("ROLLED_BACK",)],
            }
            selected = [(index, row) for index, row in enumerate(intervention.history)
                if row.correlation_id == correlation_id]
            if event.action == "create":
                # Linked creation has no create history transition. Confirm
                # the native pair using another currently owned case read.
                if (intervention.status != InterventionStatus.CANDIDATE
                        or type(intervention.version) is not int or intervention.version != 1
                        or intervention.history or intervention.created_by != event.actor
                        or metadata.get("status") != InterventionStatus.CANDIDATE.value):
                    return None
                predecessor = intervention.predecessor_id
                follow_up = intervention.trigger_ref.removeprefix("follow-up:") if intervention.trigger_ref.startswith("follow-up:") else ""
                parent_id = predecessor or follow_up
                if not parent_id or parent_id == intervention.intervention_id:
                    return None
                try:
                    parent = _get_or_404(parent_id, request)
                except HTTPException as exc:
                    if exc.status_code == 404:
                        return None
                    raise
                if parent.store_id != intervention.store_id:
                    return None
                if predecessor:
                    paired = (parent.status == InterventionStatus.STOPPED
                        and parent.replacement_id == intervention.intervention_id
                        and metadata.get("predecessor_id") == predecessor)
                    parent_action = "adjust"
                else:
                    paired = (parent.status == InterventionStatus.CLOSED and parent.close is not None
                        and parent.close.follow_up_intervention_id == intervention.intervention_id)
                    parent_action = "close"
                parent_rows = [row for row in parent.history if row.correlation_id == correlation_id]
                if (not paired or len(parent_rows) != 1 or parent_rows[0].action != parent_action
                        or parent_rows[0].actor != event.actor):
                    return None
                command = "create"
            else:
                path = paths.get(event.action)
                if path is None or not selected:
                    return None
                command, expected = path
                rows = [row for _, row in selected]
                if (tuple(row.action for row in rows) not in expected
                        or [index for index, _ in selected] != list(range(selected[0][0], selected[-1][0] + 1))
                        or any(row.actor != event.actor for row in rows)
                        or any(a.to_status != b.from_status for a, b in zip(rows, rows[1:], strict=False))
                        or metadata.get("status") != rows[-1].to_status.value):
                    return None
                if event.action in {"assign", "unassign"}:
                    if rows[0].from_status != rows[0].to_status or rows[0].to_status.value in {
                            "INELIGIBLE", "REJECTED", "CLOSED", "STOPPED", "ROLLED_BACK"}:
                        return None
                elif (rows[0].from_status.value not in allowed_sources[event.action]
                        or tuple(row.to_status.value for row in rows) not in targets[event.action]):
                    return None
            # Policy/reason are the stored event's values. Native adjustment
            # policy and several history reasons legitimately differ from the
            # immutable case policy/history text; never manufacture metadata.
            return {"schema_version": 1, "intervention_id": intervention.intervention_id,
                "tenant_id": metadata["tenant_id"], "store_id": metadata["store_id"],
                "event_id": event.event_id, "event_type": event.event_type,
                "resource": event.resource, "action": event.action, "command": command,
                "actor": event.actor, "actor_subject_id": metadata["actor_subject_id"],
                "actor_evidence": metadata["actor_evidence"], "outcome": event.outcome,
                "correlation_id": event.correlation_id, "occurred_at": event.occurred_at.isoformat(),
                "reason": metadata["reason"], "policy_version": metadata["policy_version"],
                "kind": metadata["kind"], "status": metadata["status"],
                "transitions": [row.to_dict() for _, row in selected]}

        @router.get("/{intervention_id}", dependencies=[Depends(require_permission("intervention", Action.VIEW, engine=authz_engine))])
        def get_intervention(intervention_id: str, request: Request, response: Response,
                             lifecycle_correlation_id: str | None = None) -> dict[str, Any]:
            intervention = _get_or_404(intervention_id, request)
            result = intervention.to_dict()
            result["feature_version"] = FEATURE_VERSION
            result["read_at"] = datetime.now(UTC).isoformat()
            response.headers["ETag"] = f'W/"{intervention.version}"'
            if strict_scope:
                store = authorize_store(request, intervention.store_id)
                result["review_context"] = approval_context(intervention,
                    tenant_id=store.tenant_id, actor_subject_id=principal_for(request).subject_id)
                result["creator_verification"] = "authenticated_principal" if verified_creator_subject(
                    intervention, tenant_id=store.tenant_id, audit_log=active_workflow.audit_log) else "unverified_legacy_record"
                # A read capability is descriptive, never an authorization
                # credential. The command still rechecks current Principal,
                # canonical owner, strict snapshot and segregation in its txn.
                from shared.auth import AccessRequest, Environment, ResourceDescriptor
                from shared.auth.abac import evaluate_abac
                from shared.auth.rbac import rbac_allows
                principal = principal_for(request)
                approval_access = AccessRequest(principal=principal, action=Action.APPROVE,
                    resource=ResourceDescriptor(type="intervention", resource_id=store.store_id,
                        tenant_id=store.tenant_id, brand_id=store.brand_id,
                        region_id=store.region_code or None, store_id=store.store_id, module="intervention"),
                    environment=Environment(attributes={"correlation_id": request.state.correlation_id}))
                permitted = rbac_allows(principal, "intervention", Action.APPROVE) and evaluate_abac(approval_access).allowed
                session_refusal = None
                if permitted:
                    try:
                        # Exactly the command's RBAC/live-session/security-audit
                        # dependency, without AuthorizationEngine feature gates.
                        verified = require_permission("intervention", Action.APPROVE, engine=authz_engine)(request)
                        if verified != principal:
                            permitted = False
                            session_refusal = "APPROVAL_PRINCIPAL_CHANGED"
                    except HTTPException as exc:
                        if exc.status_code not in {401, 403}:
                            raise
                        permitted = False
                        session_refusal = "APPROVAL_SESSION_REQUIRED"
                pending = intervention.status == InterventionStatus.PENDING_APPROVAL
                creator = verified_creator_subject(intervention, tenant_id=store.tenant_id,
                    audit_log=active_workflow.audit_log)
                common = None if permitted and pending else (
                    session_refusal or "APPROVAL_PERMISSION_REQUIRED" if not permitted else "PENDING_APPROVAL_REQUIRED")
                approve_reason = common or ("CREATOR_VERIFICATION_REQUIRED" if creator is None else
                    "CREATOR_APPROVAL_FORBIDDEN" if creator == principal.subject_id else
                    "UNRESOLVED_CONFLICT" if intervention.conflict is not None and intervention.conflict.blocks_approval else None)
                result["approval_permissions"] = {
                    "approve": approve_reason is None, "reject": common is None,
                    "approve_refusal": approve_reason, "reject_refusal": common,
                }
                # Bind authoritative readback to the actual approval audit,
                # including its event ID. No browser-supplied audit is trusted.
                result["approval_audit"] = None
                if intervention.approval is not None:
                    approval = intervention.approval
                    action = "approve" if approval.approved else "reject"
                    transitions = [t for t in intervention.history if t.action == action
                        and t.actor == approval.actor_id and t.reason == approval.decision_reason]
                    if len(transitions) == 1:
                        events = [event for event in active_workflow.audit_log.list_events(tenant_id=store.tenant_id)
                            if event.event_type == "intervention.lifecycle.v1"
                            and event.resource == f"intervention/{intervention_id}" and event.action == action
                            and event.actor == approval.actor_id
                            and event.correlation_id == transitions[0].correlation_id
                            and event.metadata.get("reason") == approval.decision_reason
                            and event.metadata.get("policy_version") == approval.policy_version]
                        if len(events) == 1:
                            event = events[0]
                            result["approval_audit"] = {"event_id": event.event_id,
                                "actor": event.actor, "action": event.action,
                                "reason": event.metadata["reason"], "policy_version": approval.policy_version,
                                "correlation_id": event.correlation_id, "occurred_at": event.occurred_at.isoformat()}
            if lifecycle_correlation_id is not None:
                if not lifecycle_correlation_id.strip():
                    raise HTTPException(422, detail="lifecycle_correlation_id must be nonblank")
                result["lifecycle_audit"] = lifecycle_audit_projection(
                    intervention, request, store, lifecycle_correlation_id) if strict_scope else None
            return result

        @router.post("/{intervention_id}/assign", dependencies=[Depends(require_permission("intervention", Action.CREATE, engine=authz_engine))])
        def assign_case(
            intervention_id: str,
            body: AssignPayload,
            request: Request,
            idempotency_key: str | None = Header(
                default=None,
                alias="Idempotency-Key",
            ),
        ) -> dict[str, Any]:
            return run_lifecycle_command(
                request=request,
                scope=f"interventions:{intervention_id}:assign",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: active_workflow.assign_case(
                        intervention_id,
                        assignee=body.assignee,
                        actor=command_actor(request, body.actor),
                        role=body.role,
                        expected_version=body.expected_version,
                        correlation_id=request.state.correlation_id,
                    )
                ),
            )

        @router.post("/{intervention_id}/unassign", dependencies=[Depends(require_permission("intervention", Action.CREATE, engine=authz_engine))])
        def unassign_case(
            intervention_id: str,
            body: UnassignPayload,
            request: Request,
            idempotency_key: str | None = Header(
                default=None,
                alias="Idempotency-Key",
            ),
        ) -> dict[str, Any]:
            return run_lifecycle_command(
                request=request,
                scope=f"interventions:{intervention_id}:unassign",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: active_workflow.unassign_case(
                        intervention_id,
                        actor=command_actor(request, body.actor),
                        expected_version=body.expected_version,
                        correlation_id=request.state.correlation_id,
                    )
                ),
            )

        @router.post("/{intervention_id}/eligibility", dependencies=[Depends(require_permission("intervention", Action.CREATE, engine=authz_engine))])
        def check_eligibility(
            intervention_id: str,
            body: EligibilityPayload,
            request: Request,
            idempotency_key: str | None = Header(
                default=None,
                alias="Idempotency-Key",
            ),
        ) -> dict[str, Any]:
            return run_lifecycle_command(
                request=request,
                scope=f"interventions:{intervention_id}:eligibility",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: active_workflow.check_eligibility(
                        intervention_id,
                        eligible=body.eligible,
                        actor=command_actor(request, body.actor),
                        reasons=body.reasons,
                        correlation_id=request.state.correlation_id,
                    )
                ),
            )

        @router.post("/{intervention_id}/action", dependencies=[Depends(require_permission("intervention", Action.CREATE, engine=authz_engine))])
        def propose_action(
            intervention_id: str,
            body: ActionPayload,
            request: Request,
            idempotency_key: str | None = Header(
                default=None,
                alias="Idempotency-Key",
            ),
        ) -> dict[str, Any]:
            return run_lifecycle_command(
                request=request,
                scope=f"interventions:{intervention_id}:action",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: active_workflow.propose_action(
                        intervention_id,
                        action_spec=body.action_spec,
                        actor=command_actor(request, body.actor),
                        correlation_id=request.state.correlation_id,
                    )
                ),
            )

        @router.post("/{intervention_id}/conflict-check", dependencies=[Depends(require_permission("intervention", Action.CREATE, engine=authz_engine))])
        def check_conflict(
            intervention_id: str,
            body: ConflictCheckPayload,
            request: Request,
            idempotency_key: str | None = Header(
                default=None,
                alias="Idempotency-Key",
            ),
        ) -> dict[str, Any]:
            return run_lifecycle_command(
                request=request,
                scope=f"interventions:{intervention_id}:conflict-check",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: active_workflow.check_conflict(
                        intervention_id,
                        actor=command_actor(request, body.actor),
                        allow_overlap=body.allow_overlap,
                        reason=body.reason,
                        correlation_id=request.state.correlation_id,
                    )
                ),
            )

        @router.post("/{intervention_id}/submit", dependencies=[Depends(require_permission("intervention", Action.CREATE, engine=authz_engine))])
        def submit_for_approval(
            intervention_id: str,
            body: SubmitPayload,
            request: Request,
            idempotency_key: str | None = Header(
                default=None,
                alias="Idempotency-Key",
            ),
        ) -> dict[str, Any]:
            return run_lifecycle_command(
                request=request,
                scope=f"interventions:{intervention_id}:submit",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: active_workflow.submit_for_approval(
                        intervention_id,
                        actor=command_actor(request, body.actor),
                        correlation_id=request.state.correlation_id,
                    )
                ),
            )

        @router.post("/{intervention_id}/approve", dependencies=[Depends(require_permission("intervention", Action.APPROVE, engine=authz_engine))])
        def decide(
            intervention_id: str,
            body: DecisionPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
            if_match: str | None = Header(default=None, alias="If-Match"),
            if_actor_match: str | None = Header(default=None, alias="If-Actor-Match"),
        ) -> dict[str, Any]:
            action = body.action.upper()
            if action not in {"APPROVE", "REJECT"}:
                raise HTTPException(422, detail="action must be APPROVE or REJECT")
            if strict_scope:
                principal = principal_for(request)
                _get_or_404(intervention_id, request)
                if (if_actor_match != principal.subject_id
                        or body.expected_actor_subject_id != principal.subject_id
                        or body.expected_tenant_id != principal.tenant_id):
                    raise HTTPException(409, detail="ACTOR_CONTEXT_CONFLICT")
                if if_match is None:
                    raise HTTPException(428, detail="If-Match is required")
                if re.fullmatch(r'W/"[1-9][0-9]*"', if_match) is None:
                    raise HTTPException(422, detail="invalid If-Match format")
                if body.expected_version is None or body.review_context is None:
                    raise HTTPException(428, detail="approval read context and expected_version are required")
                if int(if_match[3:-1]) != body.expected_version:
                    raise HTTPException(409, detail="VERSION_CONTEXT_CONFLICT")
                if not idempotency_key or not idempotency_key.strip():
                    raise HTTPException(428, detail="Idempotency-Key is required")
            if not strict_scope and not body.actor:
                raise HTTPException(422, detail="actor is required")
            method = active_workflow.approve if action == "APPROVE" else active_workflow.reject

            def execute_decision() -> dict[str, Any]:
                if strict_scope:
                    current = active_workflow._require(intervention_id, for_update=True)
                    store = authorize_store(request, current.store_id, lock=True)
                    if current.intervention_id != intervention_id:
                        raise HTTPException(409, detail="APPROVAL_RESOURCE_CONTEXT_CONFLICT")
                    context = approval_context(current, tenant_id=store.tenant_id,
                        actor_subject_id=principal_for(request).subject_id)
                    if body.review_context.model_dump() != context:
                        raise HTTPException(409, detail="APPROVAL_READ_CONTEXT_CONFLICT")
                    if action == "APPROVE":
                        creator = verified_creator_subject(current, tenant_id=store.tenant_id,
                            audit_log=active_workflow.audit_log)
                        if creator is None:
                            raise HTTPException(409, detail="CREATOR_VERIFICATION_REQUIRED")
                        if creator == principal_for(request).subject_id:
                            raise HTTPException(403, detail="CREATOR_APPROVAL_FORBIDDEN")
                result = _run(lambda: method(intervention_id,
                    actor=command_actor(request, body.actor), reason=body.reason,
                    expected_version=body.expected_version if strict_scope else None,
                    correlation_id=request.state.correlation_id))
                result["correlation_id"] = request.state.correlation_id
                if strict_scope:
                    audits = [event for event in active_workflow.audit_log.list_events(
                        correlation_id=request.state.correlation_id, tenant_id=tenant_id(request))
                        if event.event_type == "intervention.lifecycle.v1"
                        and event.resource == f"intervention/{intervention_id}"
                        and event.action == action.lower()]
                    if len(audits) != 1:
                        raise HTTPException(503, detail="APPROVAL_AUDIT_RECEIPT_REQUIRED")
                    result["audit_event_id"] = audits[0].event_id
                return result

            return run_command(request=request,
                scope=f"interventions:{intervention_id}:decision",
                payload=body.model_dump(mode="json"), idempotency_key=idempotency_key,
                operation=execute_decision)

        @router.post("/{intervention_id}/execute", dependencies=[Depends(require_permission("intervention", Action.EXECUTE, engine=authz_engine))])
        def execute(
            intervention_id: str,
            body: ExecutePayload,
            request: Request,
            idempotency_key: str | None = Header(
                default=None,
                alias="Idempotency-Key",
            ),
        ) -> dict[str, Any]:
            executed_at = _parse_time(body.executed_at) if body.executed_at else None
            return run_lifecycle_command(
                request=request,
                scope=f"interventions:{intervention_id}:execute",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: active_workflow.execute(
                        intervention_id,
                        executor=command_actor(request, body.executor),
                        executed_at=executed_at,
                        correlation_id=request.state.correlation_id,
                    )
                ),
            )

        @router.post("/{intervention_id}/outcomes", dependencies=[Depends(require_permission("intervention", Action.EXECUTE, engine=authz_engine))])
        def collect_outcome(
            intervention_id: str,
            body: OutcomePayload,
            request: Request,
            idempotency_key: str | None = Header(
                default=None,
                alias="Idempotency-Key",
            ),
        ) -> dict[str, Any]:
            return run_lifecycle_command(
                request=request,
                scope=f"interventions:{intervention_id}:outcomes",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: active_workflow.collect_outcome(
                        intervention_id,
                        actor=command_actor(request, body.actor),
                        incremental_revenue=body.incremental_revenue,
                        incremental_gross_margin=body.incremental_gross_margin,
                        has_control_group=body.has_control_group,
                        pretrend_status=body.pretrend_status,
                        treatment_store_count=body.treatment_store_count,
                        control_store_count=body.control_store_count,
                        evaluation_method=body.evaluation_method,
                        randomized=body.randomized,
                        ad_spend=body.ad_spend,
                        measurement_method=body.measurement_method,
                        correlation_id=request.state.correlation_id,
                    )
                ),
            )

        @router.post("/{intervention_id}/evaluate", dependencies=[Depends(require_permission("intervention", Action.EXECUTE, engine=authz_engine))])
        def evaluate_effect(
            intervention_id: str,
            body: EvaluatePayload,
            request: Request,
            idempotency_key: str | None = Header(
                default=None,
                alias="Idempotency-Key",
            ),
        ) -> dict[str, Any]:
            now = _parse_time(body.now) if body.now else None

            def execute_evaluation() -> dict[str, Any]:
                try:
                    outcome = active_workflow.evaluate_effect(
                        intervention_id,
                        actor=command_actor(request, body.actor),
                        replicated=body.replicated,
                        now=now,
                        correlation_id=request.state.correlation_id,
                    )
                except InterventionError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=str(exc),
                    ) from exc
                payload = outcome.to_dict()
                payload["correlation_id"] = request.state.correlation_id
                return payload

            return run_lifecycle_command(
                request=request,
                scope=f"interventions:{intervention_id}:evaluate",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=execute_evaluation,
            )

        @router.post("/{intervention_id}/close", dependencies=[Depends(require_permission("intervention", Action.APPROVE, engine=authz_engine))])
        def close_case(
            intervention_id: str,
            body: ClosePayload,
            request: Request,
            idempotency_key: str | None = Header(
                default=None,
                alias="Idempotency-Key",
            ),
        ) -> dict[str, Any]:
            return run_lifecycle_command(
                request=request,
                scope=f"interventions:{intervention_id}:close",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: active_workflow.close_case(
                        intervention_id,
                        actor=command_actor(request, body.actor),
                        disposition=body.disposition,
                        reason=body.reason,
                        follow_up=body.follow_up,
                        follow_up_kind=body.follow_up_kind,
                        correlation_id=request.state.correlation_id,
                    )
                ),
            )

        @router.post(
            "/{intervention_id}/adjust",
            dependencies=[
                Depends(require_permission("intervention", Action.EXECUTE, engine=authz_engine)),
                Depends(require_permission("intervention", Action.CREATE, engine=authz_engine)),
            ],
        )
        def adjust_case(
            intervention_id: str,
            body: AdjustPayload,
            request: Request,
            idempotency_key: str | None = Header(
                default=None,
                alias="Idempotency-Key",
            ),
        ) -> dict[str, Any]:
            try:
                planned_start = _parse_time(body.planned_start) if body.planned_start else None
                planned_end = _parse_time(body.planned_end) if body.planned_end else None
            except (OverflowError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"invalid date format or range: {exc}",
                ) from exc
            return run_lifecycle_command(
                request=request,
                scope=f"interventions:{intervention_id}:adjust",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: active_workflow.adjust_case(
                        intervention_id,
                        actor=command_actor(request, body.actor),
                        reason=body.reason,
                        action_spec=body.action_spec,
                        planned_start=planned_start,
                        planned_end=planned_end,
                        expected_outcome=body.expected_outcome,
                        rollback_plan=body.rollback_plan,
                        expected_version=body.expected_version,
                        correlation_id=request.state.correlation_id,
                    )
                ),
            )

        @router.post("/{intervention_id}/stop", dependencies=[Depends(require_permission("intervention", Action.EXECUTE, engine=authz_engine))])
        def stop_case(
            intervention_id: str,
            body: StopPayload,
            request: Request,
            idempotency_key: str | None = Header(
                default=None,
                alias="Idempotency-Key",
            ),
        ) -> dict[str, Any]:
            return run_lifecycle_command(
                request=request,
                scope=f"interventions:{intervention_id}:stop",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: active_workflow.stop(
                        intervention_id,
                        actor=command_actor(request, body.actor),
                        reason=body.reason,
                        correlation_id=request.state.correlation_id,
                    )
                ),
            )

        @router.post("/{intervention_id}/rollback", dependencies=[Depends(require_permission("intervention", Action.EXECUTE, engine=authz_engine))])
        def rollback_case(
            intervention_id: str,
            body: RollbackPayload,
            request: Request,
            idempotency_key: str | None = Header(
                default=None,
                alias="Idempotency-Key",
            ),
        ) -> dict[str, Any]:
            return run_lifecycle_command(
                request=request,
                scope=f"interventions:{intervention_id}:rollback",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: active_workflow.rollback(
                        intervention_id,
                        actor=command_actor(request, body.actor),
                        reason=body.reason,
                        correlation_id=request.state.correlation_id,
                    )
                ),
            )

        @router.get("/{intervention_id}/label", dependencies=[Depends(require_permission("intervention", Action.VIEW, engine=authz_engine))])
        def get_label(intervention_id: str, request: Request) -> dict[str, Any]:
            intervention = _get_or_404(intervention_id, request)
            if strict_scope and getattr(getattr(registry, "_store", None), "engine", None) is not scope_engine():
                raise HTTPException(503, detail="CANONICAL_LABEL_SCOPE_BINDING_REQUIRED")
            label = registry.get(intervention_id)
            if label is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="label not written"
                )
            if strict_scope and (label.intervention_id != intervention_id or label.store_id != intervention.store_id):
                raise HTTPException(503, detail="CANONICAL_LABEL_SCOPE_CONFLICT")
            return label.to_dict()

        def _run(action: Any) -> dict[str, Any]:
            try:
                return action().to_dict()
            except InterventionOwnerIntegrityError as exc:
                raise HTTPException(503, detail="CANONICAL_INTERVENTION_OWNER_INDEX_CONFLICT") from exc
            except (InterventionError, ValueError, OverflowError) as exc:
                msg = str(exc)
                if "stale update" in msg:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "code": "STALE_UPDATE_CONFLICT",
                            "message": msg,
                        },
                    ) from exc
                # InterventionError subclasses ValueError; a bad enum value (e.g.
                # an unknown disposition/kind) also surfaces as a domain 422.
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=msg
                ) from exc

        return router

    _durable_store_required = durable_store_required

    __all__ = ["create_interventions_router"]
