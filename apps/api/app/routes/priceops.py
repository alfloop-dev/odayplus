from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from shared.api.errors import ApiError, ErrorCode
from shared.api.idempotency import (
    MISSING,
    REPLAY_FIELD,
    IdempotencyConflictError,
    IdempotencyStore,
    apply_replay_marker,
    request_fingerprint,
)
from shared.api.pagination import page_params, paginate
from shared.audit import AuditEvent, InMemoryAuditLog

try:
    from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
else:
    from models.priceops.binding import ElasticityInputError, resolve_elasticity
    from modules.priceops.application import (
        ApprovalBlockedError,
        MissingRollbackPlanError,
        PlanNotFoundError,
        PriceOpsService,
    )
    from modules.priceops.domain import PriceConstraints, PricingPlanItem
    from modules.priceops.infrastructure import InMemoryPriceOpsRepository
    from modules.priceops.workers.optimizer_worker import (
        PlanRequest,
        PriceOpsBatchResult,
        run_priceops_optimizer_batch,
    )


    class PriceOpsPlanItemPayload(BaseModel):
        item_id: str | None = None
        store_id: str = Field(min_length=1)
        machine_type: str = Field(min_length=1)
        unit_cost: float
        current_price: float
        baseline_demand: float
        elasticity_value: float | None = None
        confidence: float | None = None
        price_demand_observations: list[dict[str, float]] | None = None
        margin_floor_ratio: float = 0.15
        max_increase_pct: float = 0.15
        max_decrease_pct: float = 0.15
        price_ladder_step: float = 0.5
        min_price: float | None = None
        max_price: float | None = None
        horizon: str = "4week"
        prediction_origin_time: str | None = None


    class PriceOpsPlanPayload(BaseModel):
        tenant_id: str = Field(min_length=1)
        items: list[PriceOpsPlanItemPayload] = Field(min_length=1)
        plan_id: str | None = None
        created_at: str | None = None
        idempotency_key: str | None = None


    class PriceOpsOptimizerJobPayload(BaseModel):
        plans: list[PriceOpsPlanPayload] = Field(min_length=1)
        optimized_at: str | None = None
        idempotency_key: str | None = None


    class PriceOpsActorPayload(BaseModel):
        actor: str = Field(default="system", min_length=1)
        reason: str = ""
        occurred_at: str | None = None


    class PriceOpsApprovalPayload(BaseModel):
        actor_id: str = Field(min_length=1)
        reason: str = Field(min_length=1)
        decision: str = "approved"
        approved_at: str | None = None


    class PriceOpsActivationPayload(BaseModel):
        executor: str = Field(default="system", min_length=1)
        intervention_type: str = "price_adjustment"
        measurement_method: str = "before_after"
        executed_at: str | None = None
        label_maturity_time: str | None = None


    class PriceOpsObservationPayload(BaseModel):
        actor: str = Field(default="system", min_length=1)
        start_time: str | None = None
        stop_conditions: dict[str, Any] = Field(default_factory=dict)


    class PriceOpsEvaluationPayload(BaseModel):
        actual_gross_margin: float
        actor: str = Field(default="system", min_length=1)
        measurement_method: str = "before_after"
        evidence_level: str = "medium"
        negative_impact_threshold: float = 0.05
        outcome_window_start: str | None = None
        outcome_window_end: str | None = None
        generated_at: str | None = None


    class PriceOpsJobStore:
        def __init__(self) -> None:
            self._jobs: dict[str, PriceOpsBatchResult] = {}
            self._idempotency_index: dict[str, str] = {}

        def put(
            self, result: PriceOpsBatchResult, *, idempotency_key: str | None = None
        ) -> tuple[PriceOpsBatchResult, bool]:
            if idempotency_key and idempotency_key in self._idempotency_index:
                return self._jobs[self._idempotency_index[idempotency_key]], False
            self._jobs[result.job_id] = result
            if idempotency_key:
                self._idempotency_index[idempotency_key] = result.job_id
            return result, True

        def get_by_idempotency_key(self, idempotency_key: str | None) -> PriceOpsBatchResult | None:
            if not idempotency_key:
                return None
            job_id = self._idempotency_index.get(idempotency_key)
            if job_id is None:
                return None
            return self._jobs[job_id]

        def get(self, job_id: str) -> PriceOpsBatchResult | None:
            return self._jobs.get(job_id)


    def create_priceops_router(
        *,
        repository: InMemoryPriceOpsRepository | None = None,
        audit_log: InMemoryAuditLog | None = None,
        job_store: PriceOpsJobStore | None = None,
    ) -> APIRouter:
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from shared.auth import Action

        router = APIRouter(prefix="/priceops", tags=["priceops"])
        price_repository = repository or InMemoryPriceOpsRepository()
        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)
        jobs = job_store or PriceOpsJobStore()
        service = PriceOpsService(repository=price_repository)
        # One store for every priceops mutation, replacing the router-local
        # `idempotency_index` dict. Unlike that dict this one fingerprints the
        # request body, so reusing a key for a different payload is a 409
        # rather than a silent replay of the first response.
        idempotency = IdempotencyStore()

        @router.post("/plans", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("priceops", Action.CREATE, engine=authz_engine))])
        def create_plan(
            body: PriceOpsPlanPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            effective_key = body.idempotency_key or idempotency_key
            if effective_key:
                try:
                    replayed_plan_id = idempotency.lookup(
                        key=effective_key,
                        scope="priceops:create_plan",
                        fingerprint=request_fingerprint(body.model_dump(mode="json")),
                    )
                except IdempotencyConflictError as exc:
                    raise ApiError(
                        status.HTTP_409_CONFLICT,
                        str(exc),
                        code=ErrorCode.IDEMPOTENCY_CONFLICT,
                        next_action=(
                            "Use a new Idempotency-Key, or resend the original request body."
                        ),
                    ) from exc
                if replayed_plan_id is not MISSING:
                    plan = service.repository.get_plan(replayed_plan_id)
                    if plan is not None:
                        payload = plan.to_dict()
                        # `created` is retained: existing callers and tests
                        # branch on it. REPLAY_FIELD is the uniform signal that
                        # every other guarded mutation now also emits.
                        payload["created"] = False
                        payload[REPLAY_FIELD] = True
                        return payload
            try:
                resolved = [_resolve_item(item) for item in body.items]
            except ElasticityInputError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
                ) from exc
            bindings = [binding for _item, binding in resolved]
            plan = service.create_plan(
                tenant_id=body.tenant_id,
                items=[plan_item for plan_item, _binding in resolved],
                correlation_id=request.state.correlation_id,
                plan_id=body.plan_id,
                created_at=_parse_time(body.created_at),
            )
            if effective_key:
                idempotency.remember(
                    key=effective_key,
                    scope="priceops:create_plan",
                    fingerprint=request_fingerprint(body.model_dump(mode="json")),
                    value=plan.plan_id,
                )
            _record_audit(
                active_audit_log,
                request,
                "priceops.plan_created.v1",
                "create",
                f"priceops/plans/{plan.plan_id}",
                {"created": True, "elasticity_bindings": bindings},
            )
            payload = plan.to_dict()
            payload["created"] = True
            payload[REPLAY_FIELD] = False
            payload["elasticity_bindings"] = bindings
            return payload

        @router.post("/optimizer-jobs", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_permission("priceops", Action.EXECUTE, engine=authz_engine))])
        def create_optimizer_job(
            body: PriceOpsOptimizerJobPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            effective_key = body.idempotency_key or idempotency_key
            result = jobs.get_by_idempotency_key(effective_key)
            created = result is None
            if result is None:
                try:
                    plan_requests = [
                        PlanRequest(
                            tenant_id=plan.tenant_id,
                            correlation_id=request.state.correlation_id,
                            items=[_item_from_payload(item) for item in plan.items],
                            plan_id=plan.plan_id,
                        )
                        for plan in body.plans
                    ]
                except ElasticityInputError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
                    ) from exc
                result, created = jobs.put(
                    run_priceops_optimizer_batch(
                        requests=plan_requests,
                        optimized_at=_parse_time(body.optimized_at),
                        repository=price_repository,
                    ),
                    idempotency_key=effective_key,
                )
            audit_event = _record_audit(
                active_audit_log,
                request,
                "priceops.optimized.v1",
                "run_model",
                "priceops/optimizer-job",
                {
                    "idempotency_key": effective_key,
                    "plan_count": len(body.plans),
                    "created": created,
                },
            )
            payload = result.to_dict()
            payload["created"] = created
            payload["audit_event_id"] = audit_event.event_id
            payload["correlation_id"] = request.state.correlation_id
            return payload

        @router.get("/optimizer-jobs/{job_id}", dependencies=[Depends(require_permission("priceops", Action.VIEW, engine=authz_engine))])
        def get_optimizer_job(job_id: str) -> dict[str, Any] | None:
            result = jobs.get(job_id)
            if result is None:
                return None
            return result.to_dict()

        @router.get("/plans", dependencies=[Depends(require_permission("priceops", Action.VIEW, engine=authz_engine))])
        def list_plans(
            tenant_id: str | None = Query(default=None),
            plan_status: str | None = Query(default=None, alias="status"),
            limit: int | None = Query(default=None),
            offset: int = Query(default=0),
            sort: str | None = Query(default=None),
            order: str = Query(default="asc"),
        ) -> dict[str, Any]:
            """List plans with consistent pagination, filtering and sorting.

            The response keeps `items` and `count` with their existing meaning
            (`count` is this page's size) and adds `total`/`limit`/`offset`/
            `has_more`, so a caller that ignores the new fields and passes no
            query parameters sees what it saw before.
            """
            rows = [plan.to_dict() for plan in service.repository.list_plans()]
            if tenant_id is not None:
                rows = [row for row in rows if row.get("tenant_id") == tenant_id]
            if plan_status is not None:
                rows = [row for row in rows if row.get("status") == plan_status]
            return paginate(
                rows, page_params(limit=limit, offset=offset, sort=sort, order=order)
            )

        @router.get("/plans/{plan_id}", dependencies=[Depends(require_permission("priceops", Action.VIEW, engine=authz_engine))])
        def get_plan(plan_id: str) -> dict[str, Any]:
            plan = service.repository.get_plan(plan_id)
            if plan is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="plan not found")
            payload = plan.to_dict()
            payload["simulation"] = _to_dict_or_none(service.repository.get_simulation(plan_id))
            payload["optimization"] = _to_dict_or_none(service.repository.get_optimization(plan_id))
            payload["approvals"] = [
                approval.to_dict() for approval in service.repository.list_approvals(plan_id)
            ]
            payload["rollback_plan"] = _to_dict_or_none(
                service.repository.get_rollback_plan(plan_id)
            )
            payload["execution"] = _to_dict_or_none(service.repository.get_execution(plan_id))
            payload["observation_window"] = _to_dict_or_none(service.repository.get_window(plan_id))
            payload["handoffs"] = [
                handoff.to_dict() for handoff in service.repository.list_handoffs(plan_id)
            ]
            payload["label_entries"] = [
                entry.to_dict() for entry in service.repository.list_label_entries(plan_id)
            ]
            payload["evaluation"] = _to_dict_or_none(service.repository.get_evaluation(plan_id))
            return payload

        @router.get("/plans/{plan_id}/comparison", dependencies=[Depends(require_permission("priceops", Action.VIEW, engine=authz_engine))])
        def get_plan_comparison(plan_id: str) -> dict[str, Any]:
            return _run_read(lambda: service.get_plan_comparison(plan_id))

        @router.post("/plans/{plan_id}/simulate", dependencies=[Depends(require_permission("priceops", Action.EXECUTE, engine=authz_engine))])
        def simulate(plan_id: str, body: PriceOpsActorPayload, request: Request) -> dict[str, Any]:
            return _run(
                lambda: service.simulate(
                    plan_id,
                    actor=body.actor,
                    reason=body.reason or "demand and margin simulation",
                    generated_at=_parse_time(body.occurred_at),
                ),
                active_audit_log,
                request,
                "priceops.simulated.v1",
                "simulate",
                plan_id,
            )

        @router.post("/plans/{plan_id}/optimize", dependencies=[Depends(require_permission("priceops", Action.EXECUTE, engine=authz_engine))])
        def optimize(plan_id: str, body: PriceOpsActorPayload, request: Request) -> dict[str, Any]:
            return _run(
                lambda: service.optimize(
                    plan_id,
                    actor=body.actor,
                    reason=body.reason or "constrained price optimization",
                    optimized_at=_parse_time(body.occurred_at),
                ),
                active_audit_log,
                request,
                "priceops.optimized.v1",
                "run_model",
                plan_id,
            )

        @router.post("/plans/{plan_id}/submit", dependencies=[Depends(require_permission("priceops", Action.CREATE, engine=authz_engine))])
        def submit(
            plan_id: str,
            body: PriceOpsActorPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            return _run(
                lambda: service.submit_for_approval(
                    plan_id,
                    actor=body.actor,
                    reason=body.reason or "submitted for pilot approval",
                    occurred_at=_parse_time(body.occurred_at),
                ),
                active_audit_log,
                request,
                "priceops.submitted.v1",
                "submit",
                plan_id,
                idempotency=idempotency,
                idempotency_key=idempotency_key,
                body=body,
            )

        @router.post("/plans/{plan_id}/approve", dependencies=[Depends(require_permission("priceops", Action.APPROVE, engine=authz_engine))])
        def approve(
            plan_id: str,
            body: PriceOpsApprovalPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            return _run(
                lambda: service.approve(
                    plan_id,
                    actor_id=body.actor_id,
                    reason=body.reason,
                    decision=body.decision,
                    approved_at=_parse_time(body.approved_at),
                ),
                active_audit_log,
                request,
                "priceops.approved.v1",
                body.decision,
                plan_id,
                idempotency=idempotency,
                idempotency_key=idempotency_key,
                body=body,
            )

        @router.post("/plans/{plan_id}/activate", dependencies=[Depends(require_permission("priceops", Action.EXECUTE, engine=authz_engine))])
        def activate(
            plan_id: str,
            body: PriceOpsActivationPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            return _run(
                lambda: service.activate(
                    plan_id,
                    executor=body.executor,
                    intervention_type=body.intervention_type,
                    measurement_method=body.measurement_method,
                    correlation_id=request.state.correlation_id,
                    executed_at=_parse_time(body.executed_at),
                    label_maturity_time=_parse_time(body.label_maturity_time),
                ),
                active_audit_log,
                request,
                "priceops.activated.v1",
                "execute",
                plan_id,
                idempotency=idempotency,
                idempotency_key=idempotency_key,
                body=body,
            )

        @router.post("/plans/{plan_id}/observation", dependencies=[Depends(require_permission("priceops", Action.EXECUTE, engine=authz_engine))])
        def start_observation(
            plan_id: str, body: PriceOpsObservationPayload, request: Request
        ) -> dict[str, Any]:
            return _run(
                lambda: service.start_observation(
                    plan_id,
                    actor=body.actor,
                    start_time=_parse_time(body.start_time),
                    stop_conditions=body.stop_conditions,
                ),
                active_audit_log,
                request,
                "priceops.observation_started.v1",
                "observe",
                plan_id,
            )

        @router.post("/plans/{plan_id}/evaluate", dependencies=[Depends(require_permission("priceops", Action.EXECUTE, engine=authz_engine))])
        def evaluate(
            plan_id: str, body: PriceOpsEvaluationPayload, request: Request
        ) -> dict[str, Any]:
            outcome_window = None
            if body.outcome_window_start and body.outcome_window_end:
                outcome_window = (
                    _parse_required_time(body.outcome_window_start),
                    _parse_required_time(body.outcome_window_end),
                )
            return _run(
                lambda: service.evaluate(
                    plan_id,
                    actual_gross_margin=body.actual_gross_margin,
                    actor=body.actor,
                    measurement_method=body.measurement_method,
                    evidence_level=body.evidence_level,
                    negative_impact_threshold=body.negative_impact_threshold,
                    outcome_window=outcome_window,
                    generated_at=_parse_time(body.generated_at),
                ),
                active_audit_log,
                request,
                "priceops.evaluated.v1",
                "evaluate",
                plan_id,
            )

        @router.post("/plans/{plan_id}/rollback", dependencies=[Depends(require_permission("priceops", Action.EXECUTE, engine=authz_engine))])
        def rollback(
            plan_id: str,
            body: PriceOpsActorPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            return _run(
                lambda: service.rollback(
                    plan_id,
                    actor=body.actor,
                    reason=body.reason or "explicit rollback",
                    occurred_at=_parse_time(body.occurred_at),
                ),
                active_audit_log,
                request,
                "priceops.rollback.v1",
                "rollback",
                plan_id,
                idempotency=idempotency,
                idempotency_key=idempotency_key,
                body=body,
            )

        return router

    def _resolve_item(
        item: PriceOpsPlanItemPayload,
    ) -> tuple[PricingPlanItem, dict[str, Any]]:
        estimate, binding = resolve_elasticity(
            current_price=item.current_price,
            observations=item.price_demand_observations,
            supplied_value=item.elasticity_value,
            supplied_confidence=item.confidence,
            horizon=item.horizon,
            prediction_origin_time=_parse_time(item.prediction_origin_time),
        )
        plan_item = PricingPlanItem.create(
            item_id=item.item_id,
            store_id=item.store_id,
            machine_type=item.machine_type,
            constraints=PriceConstraints(
                unit_cost=item.unit_cost,
                current_price=item.current_price,
                margin_floor_ratio=item.margin_floor_ratio,
                max_increase_pct=item.max_increase_pct,
                max_decrease_pct=item.max_decrease_pct,
                price_ladder_step=item.price_ladder_step,
                min_price=item.min_price,
                max_price=item.max_price,
            ),
            baseline_demand=item.baseline_demand,
            elasticity=estimate,
        )
        return plan_item, {
            "store_id": item.store_id,
            "machine_type": item.machine_type,
            **binding,
        }

    def _item_from_payload(item: PriceOpsPlanItemPayload) -> PricingPlanItem:
        plan_item, _binding = _resolve_item(item)
        return plan_item

    def _parse_required_time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed

    def _parse_time(value: str | None) -> datetime | None:
        if not value:
            return None
        return _parse_required_time(value)

    def _to_dict_or_none(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        return value.to_dict()

    def _record_audit(
        audit_log: InMemoryAuditLog,
        request: Request,
        event_type: str,
        action: str,
        resource: str,
        metadata: dict[str, Any],
    ) -> AuditEvent:
        return audit_log.record(
            AuditEvent(
                event_type=event_type,
                actor="system",
                action=action,
                resource=resource,
                outcome="accepted",
                correlation_id=request.state.correlation_id,
                metadata=metadata,
            )
        )

    def _run(
        action: Any,
        audit_log: InMemoryAuditLog,
        request: Request,
        event_type: str,
        audit_action: str,
        plan_id: str,
        *,
        idempotency: IdempotencyStore | None = None,
        idempotency_key: str | None = None,
        body: Any = None,
    ) -> dict[str, Any]:
        """Execute a plan transition, audit it, and return the plan payload.

        Every transition routes through here, so threading the idempotency
        policy in at this one point covers submit/approve/activate/observation/
        evaluate/rollback together. These were the transitions with no policy at
        all -- the ones where a double-submit approves a plan twice rather than
        merely creating a duplicate row.

        The guard wraps the audit record as well as the state change: replaying
        a transition must not append a second audit event claiming the
        transition happened again.
        """

        def _execute() -> dict[str, Any]:
            try:
                result = action()
            except PlanNotFoundError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
            except (ApprovalBlockedError, MissingRollbackPlanError, ValueError, RuntimeError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
                ) from exc
            audit_event = _record_audit(
                audit_log,
                request,
                event_type,
                audit_action,
                f"priceops/plans/{plan_id}",
                {"plan_id": plan_id},
            )
            payload = result.to_dict()
            payload["audit_event_id"] = audit_event.event_id
            payload["correlation_id"] = request.state.correlation_id
            return payload

        if idempotency is None:
            return _execute()

        try:
            outcome = idempotency.run(
                key=idempotency_key,
                # Scope by event type and plan: the same client key on a
                # different plan is a different mutation, not a replay.
                #
                # event_type, not audit_action: audit_action is `body.decision`
                # for approve, so scoping on it would put APPROVE and REJECT in
                # different scopes and let one key both approve *and* reject the
                # same plan. event_type ("priceops.approved.v1") is stable for
                # the operation, so the differing body is seen as the conflict
                # it is.
                scope=f"priceops:{event_type}:{plan_id}",
                payload=body.model_dump(mode="json") if hasattr(body, "model_dump") else body,
                operation=_execute,
            )
        except IdempotencyConflictError as exc:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                str(exc),
                code=ErrorCode.IDEMPOTENCY_CONFLICT,
                next_action="Use a new Idempotency-Key, or resend the original request body.",
            ) from exc
        return apply_replay_marker(outcome.value, replayed=outcome.replayed)

    def _run_read(action: Any) -> dict[str, Any]:
        try:
            result = action()
        except PlanNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return result.to_dict()

    __all__ = [
        "PriceOpsJobStore",
        "PriceOpsOptimizerJobPayload",
        "PriceOpsPlanPayload",
        "create_priceops_router",
    ]
