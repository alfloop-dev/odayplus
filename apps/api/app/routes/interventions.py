"""InterventionOps lifecycle API (ODP-MOD-05 §8).

Exposes the shared intervention lifecycle: create a case, run eligibility,
build an action candidate, check conflicts, submit for approval, approve/reject,
execute (which opens the observation window), collect outcomes, and evaluate the
effect (which writes a matured label back to the Label Registry).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from modules.intervention.application.workflow import InterventionWorkflow
from modules.intervention.domain.lifecycle import InterventionError
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
    from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover - optional API dependency
    APIRouter = None  # type: ignore[assignment]
else:

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

    class EligibilityPayload(BaseModel):
        eligible: bool
        actor: str = Field(min_length=1)
        reasons: list[str] = Field(default_factory=list)

    class ActionPayload(BaseModel):
        action_spec: dict[str, Any] = Field(default_factory=dict)
        actor: str = Field(min_length=1)

    class ConflictCheckPayload(BaseModel):
        actor: str = Field(min_length=1)
        allow_overlap: bool = False
        reason: str = ""

    class SubmitPayload(BaseModel):
        actor: str = Field(min_length=1)

    class DecisionPayload(BaseModel):
        action: str
        actor: str = Field(min_length=1)
        reason: str = ""

    class ExecutePayload(BaseModel):
        executor: str = Field(min_length=1)
        executed_at: str | None = None

    class OutcomePayload(BaseModel):
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

    class EvaluatePayload(BaseModel):
        actor: str = Field(min_length=1)
        replicated: bool = False
        now: str | None = None

    class ClosePayload(BaseModel):
        actor: str = Field(min_length=1)
        disposition: str
        reason: str = ""
        follow_up: bool = False
        follow_up_kind: str | None = None

    def _parse_time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed

    def create_interventions_router(
        *,
        workflow: InterventionWorkflow | None = None,
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
        local_job_queue = None if require_durable_commands else InMemoryJobQueue()

        def command_store(request: Request) -> TenantScopedCommandReceiptStore:
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
            )
            if require_durable_commands and not store.is_durable:
                raise _durable_store_required(
                    "Intervention production commands reject process-local stores"
                )
            return store

        def tenant_id(request: Request) -> str:
            principal = getattr(request.state, "operator_principal", None)
            value = getattr(principal, "tenant_id", None)
            if value:
                return str(value)
            if require_durable_commands:
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
                outcome = command_store(request).run(
                    tenant_id=tenant_id(request),
                    idempotency_key=idempotency_key,
                    scope=scope,
                    payload=payload,
                    correlation_id=request.state.correlation_id,
                    operation=lambda _receipt_id: operation(),
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
            return apply_replay_marker(
                outcome.value,
                replayed=outcome.replayed,
            )

        def _get_or_404(intervention_id: str):
            intervention = active_workflow.get(intervention_id)
            if intervention is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="intervention not found"
                )
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
                        created_by=body.created_by,
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
        def list_interventions(store_id: str | None = None) -> dict[str, Any]:
            items = (
                active_workflow.list_by_store(store_id)
                if store_id
                else active_workflow.list_all()
            )
            return {"items": [i.to_dict() for i in items], "count": len(items)}

        @router.get("/{intervention_id}", dependencies=[Depends(require_permission("intervention", Action.VIEW, engine=authz_engine))])
        def get_intervention(intervention_id: str) -> dict[str, Any]:
            return _get_or_404(intervention_id).to_dict()

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
            return run_command(
                request=request,
                scope=f"interventions:{intervention_id}:eligibility",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: active_workflow.check_eligibility(
                        intervention_id,
                        eligible=body.eligible,
                        actor=body.actor,
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
            return run_command(
                request=request,
                scope=f"interventions:{intervention_id}:action",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: active_workflow.propose_action(
                        intervention_id,
                        action_spec=body.action_spec,
                        actor=body.actor,
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
            return run_command(
                request=request,
                scope=f"interventions:{intervention_id}:conflict-check",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: active_workflow.check_conflict(
                        intervention_id,
                        actor=body.actor,
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
            return run_command(
                request=request,
                scope=f"interventions:{intervention_id}:submit",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: active_workflow.submit_for_approval(
                        intervention_id,
                        actor=body.actor,
                        correlation_id=request.state.correlation_id,
                    )
                ),
            )

        @router.post("/{intervention_id}/approve", dependencies=[Depends(require_permission("intervention", Action.APPROVE, engine=authz_engine))])
        def decide(
            intervention_id: str,
            body: DecisionPayload,
            request: Request,
            idempotency_key: str | None = Header(
                default=None,
                alias="Idempotency-Key",
            ),
        ) -> dict[str, Any]:
            action = body.action.upper()
            if action not in {"APPROVE", "REJECT"}:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="action must be APPROVE or REJECT",
                )
            method = active_workflow.approve if action == "APPROVE" else active_workflow.reject
            return run_command(
                request=request,
                scope=f"interventions:{intervention_id}:decision",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: method(
                        intervention_id,
                        actor=body.actor,
                        reason=body.reason,
                        correlation_id=request.state.correlation_id,
                    )
                ),
            )

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
            return run_command(
                request=request,
                scope=f"interventions:{intervention_id}:execute",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: active_workflow.execute(
                        intervention_id,
                        executor=body.executor,
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
            return run_command(
                request=request,
                scope=f"interventions:{intervention_id}:outcomes",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: active_workflow.collect_outcome(
                        intervention_id,
                        actor=body.actor,
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
                        actor=body.actor,
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

            return run_command(
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
            return run_command(
                request=request,
                scope=f"interventions:{intervention_id}:close",
                payload=body.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                operation=lambda: _run(
                    lambda: active_workflow.close_case(
                        intervention_id,
                        actor=body.actor,
                        disposition=body.disposition,
                        reason=body.reason,
                        follow_up=body.follow_up,
                        follow_up_kind=body.follow_up_kind,
                        correlation_id=request.state.correlation_id,
                    )
                ),
            )

        @router.get("/{intervention_id}/label", dependencies=[Depends(require_permission("intervention", Action.VIEW, engine=authz_engine))])
        def get_label(intervention_id: str) -> dict[str, Any]:
            _get_or_404(intervention_id)
            label = registry.get(intervention_id)
            if label is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="label not written"
                )
            return label.to_dict()

        def _run(action: Any) -> dict[str, Any]:
            try:
                return action().to_dict()
            except (InterventionError, ValueError) as exc:
                # InterventionError subclasses ValueError; a bad enum value (e.g.
                # an unknown disposition/kind) also surfaces as a domain 422.
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
                ) from exc

        return router

    def _durable_store_required(message: str) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DURABLE_COMMAND_STORE_REQUIRED",
                "message": message,
            },
        )

    __all__ = ["create_interventions_router"]
