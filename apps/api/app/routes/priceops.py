from __future__ import annotations

import logging
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from models.shared_ml import (
    ProductionExecutionConfigurationError,
    production_execution_required,
)
from shared.api.errors import ApiError, ErrorCode
from shared.api.idempotency import (
    IdempotencyConflictError,
    apply_replay_marker,
)
from shared.api.pagination import page_params, paginate
from shared.audit import AuditEvent, InMemoryAuditLog
from shared.audit.backlog import AuditBacklogBlocked, AuditBacklogUnavailable, DurableAuditBacklog
from shared.audit.durable_delivery import DurableAuditDelivery
from shared.infrastructure.persistence.command_receipts import (
    CommandReceiptIncompleteError,
    CommandReceiptPersistenceError,
    TenantScopedCommandReceiptStore,
)
from shared.infrastructure.persistence.job_receipts import JobQueue
from shared.jobs.queue import InMemoryJobQueue

try:
    from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
else:
    from apps.api.app.routes._common import durable_store_required
    from models.priceops.binding import ElasticityInputError, resolve_elasticity
    from modules.priceops.application import (
        ApprovalBlockedError,
        ExplorationService,
        MissingRollbackPlanError,
        PlanNotFoundError,
        PriceOpsService,
        authorize_exploration,
        normalize_approval_decision,
    )
    from modules.priceops.domain import (
        ExplorationBudgetExceededError,
        ExplorationGate,
        ExplorationGateExpiredError,
        ExplorationGateRevokedError,
        ExplorationNotAuthorizedError,
        InvalidScenarioError,
        PriceConstraints,
        PriceScope,
        PricingPlanItem,
        UnavailableSimulationResultError,
    )
    from modules.priceops.domain.pricing import PlanAlreadyExistsError
    from modules.priceops.infrastructure import InMemoryPriceOpsRepository
    from modules.priceops.infrastructure.oss_optimizer import (
        PriceOpsMissingSourceLineageError,
        PriceOpsProductionOptimizer,
    )
    from modules.priceops.workers.optimizer_worker import (
        PlanRequest,
        run_priceops_optimizer_batch,
    )
    from shared.governance.vocabularies import EvidenceLevel

    logger = logging.getLogger("oday-api.priceops")


    def canonical_priceops_store(item: Any, expected_tenant: str | None, *,
                                 store_repository: Any, brand_repository: Any,
                                 production_required: bool = False,
                                 read_cache: dict[tuple[str, str], Any] | None = None) -> Any:
        if store_repository is None and brand_repository is None and not production_required:
            return None
        try:
            def read_master(kind: str, key: str, load: Any) -> Any:
                if read_cache is None:
                    return load(key)
                identity = (kind, key)
                if identity not in read_cache:
                    read_cache[identity] = load(key)
                return read_cache[identity]

            store = read_master("store", item.store_id, store_repository.get_store)
            if store is None or not store.is_current or store.tenant_id != expected_tenant:
                return None
            brand = read_master("brand", store.brand_id, brand_repository.get_brand)
            if brand is None or brand.tenant_id != expected_tenant:
                return None
            return store
        except Exception as exc:
            raise HTTPException(status_code=503, detail={
                "code": "PRICEOPS_STORE_BINDING_UNAVAILABLE",
                "message": "Canonical pricing ownership is unavailable",
            }) from exc


    def priceops_items_in_scope(items: Any, principal: Any, expected_tenant: str | None, *,
                                store_repository: Any = None, brand_repository: Any = None,
                                production_required: bool = False,
                                scope_module: str = "priceops",
                                read_cache: dict[tuple[str, str], Any] | None = None) -> bool:
        """Share object axes while retaining the caller's existing module guard.

        Native routes use priceops. Operator projections use operator_console,
        the module their existing read guard verifies; this is not another
        business-role or native PriceOps operation grant.
        """
        from shared.auth import DataClassification

        scope = principal.scope
        if not (
            scope.permits_module(scope_module)
            and scope.permits_classification(DataClassification.CONFIDENTIAL)
            and scope.permits_assigned_area(None)
            and scope.permits_heat_zone(None)
        ):
            return False
        for item in items:
            store = canonical_priceops_store(item, expected_tenant, store_repository=store_repository,
                                             brand_repository=brand_repository,
                                             production_required=production_required, read_cache=read_cache)
            canonical_required = store_repository is not None or brand_repository is not None or production_required
            if canonical_required and (store is None or item.brand_id != store.brand_id):
                return False
            brand_id = store.brand_id if store is not None else item.brand_id
            region_id = (store.region_code or None) if store is not None else None
            if not (scope.permits_store(item.store_id)
                    and scope.permits_brand(brand_id)
                    and scope.permits_region(region_id)):
                return False
        return True


    def priceops_plan_in_scope(plan: Any, principal: Any, *, store_repository: Any = None,
                               brand_repository: Any = None, production_required: bool = False,
                               require_tenant: bool = False, scope_module: str = "priceops",
                                read_cache: dict[tuple[str, str], Any] | None = None) -> bool:
        scope = principal.scope
        if scope.tenant_id and plan.tenant_id != scope.tenant_id:
            return False
        if require_tenant and not scope.tenant_id:
            return False
        return priceops_items_in_scope(plan.items, principal, plan.tenant_id,
                                       store_repository=store_repository, brand_repository=brand_repository,
                                       production_required=production_required, scope_module=scope_module, read_cache=read_cache)


    class PriceOpsPlanItemPayload(BaseModel):
        item_id: str | None = None
        store_id: str = Field(min_length=1)
        machine_type: str = Field(min_length=1)
        brand_id: str | None = None
        store_group: str | None = None
        sku_group: str | None = None
        unit_cost: float
        current_price: float
        baseline_demand: float
        elasticity_value: float | None = None
        confidence: float | None = None
        applicable_min_price: float | None = None
        applicable_max_price: float | None = None
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
        exploration_gate_id: str | None = None
        exploration_algorithm: str = "THOMPSON_SAMPLING"
        exploration_seed: int | None = None
        exploration_history: list[tuple[float, float]] | None = None


    class PriceOpsOptimizerJobPayload(BaseModel):
        plans: list[PriceOpsPlanPayload] = Field(min_length=1)
        optimized_at: str | None = None
        idempotency_key: str | None = None


    class PriceOpsActorPayload(BaseModel):
        actor: str = Field(default="system", min_length=1)
        reason: str = ""
        occurred_at: str | None = None
        # Exploration is opt-in.  Keeping these fields on the optimize command
        # makes the production entry point carry the same gate as the bandit
        # decision; omitted fields always select the deterministic path.
        exploration_gate_id: str | None = None
        exploration_algorithm: str = "THOMPSON_SAMPLING"
        exploration_seed: int | None = None
        exploration_history: list[tuple[float, float]] | None = None


    class PriceOpsScenarioSimulationPayload(BaseModel):
        candidate_prices: dict[str, float] = Field(default_factory=dict)
        actor: str = Field(default="system", min_length=1)
        reason: str = ""
        occurred_at: str | None = None


    class PriceOpsDecisionWritebackPayload(BaseModel):
        actor: str = Field(min_length=1)
        decision: str = Field(min_length=1)
        reason: str = Field(min_length=1)
        selected_scenario_id: str | None = None
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
        evidence_level: EvidenceLevel | None = None
        negative_impact_threshold: float = 0.05
        outcome_window_start: str | None = None
        outcome_window_end: str | None = None
        generated_at: str | None = None


    class PriceOpsExplorationGateRegisterPayload(BaseModel):
        tenant_id: str = Field(min_length=1)
        budget_limit: float = Field(gt=0)
        effective_from: str = Field(min_length=1)
        effective_to: str = Field(min_length=1)
        approved_by: str = Field(min_length=1)
        approval_decision_id: str = Field(min_length=1)
        approval_id: str = Field(min_length=1)
        rollback_condition: str = Field(min_length=1)
        decision_policy_version_id: str = Field(min_length=1)
        scope_brand_id: str | None = None
        scope_store_group: str | None = None
        scope_sku_group: str | None = None
        gate_id: str | None = None


    class PriceOpsExplorationGateRevokePayload(BaseModel):
        reason: str = Field(min_length=1)
        revoked_at: str | None = None


    class PriceOpsExplorationCandidatesPayload(BaseModel):
        tenant_id: str = Field(min_length=1)
        items: list[PriceOpsPlanItemPayload] = Field(min_length=1)
        scope_brand_id: str | None = None
        scope_store_group: str | None = None
        scope_sku_group: str | None = None
        algorithm: str = "THOMPSON_SAMPLING"
        seed: int | None = None
        history: list[tuple[float, float]] | None = None
        at: str | None = None


    def create_priceops_router(
        *,
        repository: InMemoryPriceOpsRepository | None = None,
        audit_log: InMemoryAuditLog | None = None,
        job_queue: JobQueue | None = None,
        require_durable_commands: bool | None = None,
        production_optimizer: PriceOpsProductionOptimizer | None = None,
        runtime_mode: str | None = None,
        store_repository: Any = None,
        brand_repository: Any = None,
    ) -> APIRouter:
        from apps.api.app.routes._common import runtime_binding_guard
        from apps.api.oday_api.security.dependencies import build_engine, require_permission
        from shared.auth import Action, DataClassification

        production_required = production_execution_required(runtime_mode)
        durable_commands_required = (
            production_required
            if require_durable_commands is None
            else require_durable_commands
        )
        price_repository = (
            repository
            if production_required
            else repository or InMemoryPriceOpsRepository()
        )
        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)
        local_job_queue = None if durable_commands_required else InMemoryJobQueue()
        composition_error: ProductionExecutionConfigurationError | None = None
        try:
            service = PriceOpsService(
                repository=price_repository,
                production_optimizer=production_optimizer,
                runtime_mode=runtime_mode,
            )
        except ProductionExecutionConfigurationError as exc:
            composition_error = exc
            service = None

        if production_required and (
            not callable(getattr(store_repository, "get_store", None))
            or not callable(getattr(brand_repository, "get_brand", None))
        ):
            composition_error = ProductionExecutionConfigurationError(
                "PriceOps production requires canonical store and brand repositories"
            )
        require_runtime_binding = runtime_binding_guard(composition_error)

        router = APIRouter(
            prefix="/priceops",
            tags=["priceops"],
            dependencies=[Depends(require_runtime_binding)],
        )

        def command_store(request: Request) -> TenantScopedCommandReceiptStore:
            request.state.priceops_production_required = production_required
            active_queue = job_queue
            if active_queue is None:
                app = request.scope.get("app")
                active_queue = getattr(
                    getattr(app, "state", None), "job_queue", None
                )
            active_queue = active_queue or local_job_queue
            if active_queue is None:
                raise _durable_store_required(
                    "PriceOps production commands require durable persistence"
                )
            store = TenantScopedCommandReceiptStore(
                queue=active_queue,
                service="priceops",
            )
            if durable_commands_required and not store.is_durable:
                raise _durable_store_required(
                    "PriceOps production commands reject process-local stores"
                )
            if store.is_durable:
                engine = getattr(getattr(price_repository, "_store", None), "engine", None)
                if engine is None or any(bound is not engine for bound in (
                    getattr(active_queue, "_engine", None),
                    getattr(active_audit_log, "_engine", None),
                )):
                    raise _durable_store_required(
                        "PriceOps commands require one engine for business, audit and receipts"
                    )
                for master in (store_repository, brand_repository):
                    if master is not None and getattr(master, "_engine", None) is not engine:
                        raise _durable_store_required(
                            "PriceOps canonical ownership must share the command transaction"
                        )
                store = TenantScopedCommandReceiptStore(
                    queue=active_queue, service="priceops",
                    transaction_factory=lambda: command_transaction(engine, active_queue, request),
                )
            return store

        @contextmanager
        def command_transaction(engine: Any, queue: Any, request: Request):
            """Recheck locked ownership and atomically persist every command effect."""
            with engine.transaction():
                postgres = getattr(engine, "dialect", "") == "postgresql"
                if not postgres and not engine._conn.in_transaction:
                    engine.execute("BEGIN IMMEDIATE")
                plan_id = request.path_params.get("plan_id")
                proposals = tuple(getattr(request.state, "priceops_proposals", ()))
                plan_ids = {p.plan_id for p in proposals if p.plan_id}
                if plan_id:
                    plan_ids.add(plan_id)
                if postgres:
                    for identity in sorted(plan_ids):
                        engine.query_one("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                                         ("priceops-plan:" + identity,))
                plan = service.repository.get_plan(plan_id) if plan_id else None
                items = [item for proposal in proposals for item in proposal.items]
                if plan is not None:
                    items.extend(plan.items)
                if postgres and store_repository is not None:
                    brand_ids = set()
                    for identity in sorted({item.store_id for item in items}):
                        row = engine.query_one("SELECT brand_id FROM stores WHERE store_id = ? FOR SHARE",
                                               (identity,))
                        if row:
                            brand_ids.add(row["brand_id"])
                    for identity in sorted(brand_ids):
                        engine.query_one("SELECT brand_id FROM brands WHERE brand_id = ? FOR SHARE",
                                         (identity,))
                principal = request.state.operator_principal
                if plan_id and (plan is None or not plan_in_scope(plan, principal)):
                    raise HTTPException(status_code=404, detail="plan not found")
                for proposal in proposals:
                    require_proposal_scope(proposal, request)
                approval = getattr(request.state, "priceops_approval", None)
                if approval is not None:
                    require_independent_approval(*approval, request)
                sink = getattr(active_audit_log, "_worm_sink", None)
                delivery = DurableAuditDelivery(engine, tenant_id(request), queue, sink) if sink else None
                context = active_audit_log.transactional_worm_sink(delivery) if delivery else nullcontext()
                with context:
                    yield

        def tenant_id(request: Request, fallback: str | None = None) -> str:
            principal = getattr(request.state, "operator_principal", None)
            scope = getattr(principal, "scope", None)
            value = getattr(scope, "tenant_id", None)
            if value:
                if fallback and str(fallback) != str(value):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail={
                            "code": "TENANT_SCOPE_MISMATCH",
                            "message": "PriceOps payload tenant does not match caller scope",
                        },
                    )
                return str(value)
            if durable_commands_required or production_required:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "code": "TENANT_SCOPE_REQUIRED",
                        "message": "PriceOps production commands require tenant scope",
                    },
                )
            return "__local__"

        def canonical_store(item: Any, expected_tenant: str | None, *, read_cache: dict[tuple[str, str], Any] | None = None) -> Any:
            return canonical_priceops_store(item, expected_tenant, store_repository=store_repository,
                                             brand_repository=brand_repository,
                                             production_required=production_required, read_cache=read_cache)

        def items_in_scope(items: Any, principal: Any, expected_tenant: str | None, *, read_cache: dict[tuple[str, str], Any] | None = None) -> bool:
            return priceops_items_in_scope(items, principal, expected_tenant,
                                           store_repository=store_repository, brand_repository=brand_repository,
                                           production_required=production_required, read_cache=read_cache)

        def plan_in_scope(plan: Any, principal: Any, *, read_cache: dict[tuple[str, str], Any] | None = None) -> bool:
            return priceops_plan_in_scope(plan, principal, store_repository=store_repository,
                                          brand_repository=brand_repository, production_required=production_required,
                                          require_tenant=durable_commands_required or production_required, read_cache=read_cache)

        def require_proposal_scope(body: PriceOpsPlanPayload, request: Request) -> None:
            principal = request.state.operator_principal
            tenant_id(request, body.tenant_id)
            if store_repository is not None or brand_repository is not None or production_required:
                for item in body.items:
                    store = canonical_store(item, body.tenant_id)
                    if store is None or (item.brand_id and item.brand_id != store.brand_id):
                        raise HTTPException(status_code=403, detail="pricing proposal ownership mismatch")
                    # Persist the actual master-data brand, not a client claim.
                    item.brand_id = store.brand_id
            if not items_in_scope(body.items, principal, body.tenant_id):
                raise HTTPException(status_code=403, detail="pricing proposal outside caller scope")
            if body.plan_id is not None:
                existing = service.repository.get_plan(body.plan_id)
                if existing is not None and not plan_in_scope(existing, principal):
                    raise HTTPException(status_code=404, detail="plan not found")
            proposals = getattr(request.state, "priceops_proposals", None)
            if proposals is None:
                proposals = request.state.priceops_proposals = []
            if not any(proposal is body for proposal in proposals):
                proposals.append(body)


        def require_plan_permission(action: Any) -> Any:
            permission = require_permission("priceops", action, engine=authz_engine)

            principal_dependency = Depends(permission)

            def dependency(request: Request, principal: Any = principal_dependency) -> Any:
                plan_id = request.path_params.get("plan_id")
                if plan_id is not None:
                    plan = service.repository.get_plan(plan_id)
                    if plan is None or not plan_in_scope(plan, principal):
                        raise HTTPException(status_code=404, detail="plan not found")
                return principal

            return dependency

        def require_independent_approval(plan_id: str, decision: str, request: Request) -> None:
            request.state.priceops_approval = (plan_id, decision)
            blockers = service.approval_actor_blockers(
                plan_id, request.state.operator_principal.subject_id, decision=decision,
            )
            if blockers:
                raise HTTPException(status_code=403, detail={
                    "code": "PRICEOPS_APPROVAL_SEPARATION_REQUIRED",
                    "message": "; ".join(blockers),
                })

        def gate_in_scope(gate: Any, principal: Any) -> bool:
            scope = principal.scope
            return (
                (not scope.tenant_id or gate.tenant_id == scope.tenant_id)
                and scope.permits_module("priceops")
                and scope.permits_classification(DataClassification.CONFIDENTIAL)
                and scope.permits_brand(gate.scope_brand_id)
                and scope.permits_store(None) and scope.permits_region(None)
                and scope.permits_assigned_area(None) and scope.permits_heat_zone(None)
            )

        def require_workflow_gate_write() -> None:
            if production_required or getattr(price_repository, "canonical_exploration", None) is not None:
                raise HTTPException(status_code=405, detail={
                    "code": "WORKFLOW_APPROVAL_REQUIRED",
                    "message": "Exploration gate creation and revocation use the workflow approval path",
                }, headers={"Allow": "GET"})

        def require_canonical_exploration() -> Any:
            canonical = getattr(price_repository, "canonical_exploration", None)
            if production_required and canonical is None:
                raise HTTPException(status_code=503, detail={
                    "code": "PRICEOPS_EXPLORATION_BINDING_REQUIRED",
                    "message": "Canonical pricing/workflow authorization is unavailable",
                })
            return canonical

        @router.post("/plans", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_plan_permission(Action.CREATE))])
        def create_plan(
            body: PriceOpsPlanPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            effective_key = body.idempotency_key or idempotency_key
            active_tenant_id = tenant_id(request, body.tenant_id)
            require_proposal_scope(body, request)

            def _execute(_receipt_id: str) -> dict[str, Any]:
                try:
                    resolved = [_resolve_item(item) for item in body.items]
                except ElasticityInputError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
                    ) from exc
                bindings = [binding for _item, binding in resolved]
                try:
                    plan = service.create_plan(
                        tenant_id=body.tenant_id,
                        items=[plan_item for plan_item, _binding in resolved],
                        correlation_id=request.state.correlation_id,
                        plan_id=body.plan_id,
                        created_at=_parse_time(body.created_at),
                        created_by=request.state.operator_principal.subject_id,
                    )
                except PlanAlreadyExistsError as exc:
                    raise HTTPException(status_code=409, detail="plan id already exists") from exc
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
                payload["elasticity_bindings"] = bindings
                return payload

            return _guard_creation(
                command_store(request),
                tenant_id=active_tenant_id,
                correlation_id=request.state.correlation_id,
                key=effective_key,
                scope="priceops:create_plan",
                payload={"verified_actor": request.state.operator_principal.subject_id,
                         "body": body.model_dump(mode="json")},
                operation=_execute,
            )

        @router.post("/optimizer-jobs", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_plan_permission(Action.EXECUTE))])
        def create_optimizer_job(
            body: PriceOpsOptimizerJobPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            effective_key = body.idempotency_key or idempotency_key
            active_tenant_id = tenant_id(request, body.plans[0].tenant_id)
            for plan in body.plans:
                require_proposal_scope(plan, request)
            active_store = command_store(request)

            def _execute(receipt_id: str) -> dict[str, Any]:
                if any(plan.exploration_gate_id for plan in body.plans):
                    _require_durable_audit_capacity(
                        active_store, active_audit_log, active_tenant_id,
                        production_required=production_required,
                    )
                try:
                    plan_requests = [
                        PlanRequest(
                            tenant_id=plan.tenant_id,
                            correlation_id=request.state.correlation_id,
                            items=[_item_from_payload(item) for item in plan.items],
                            plan_id=plan.plan_id,
                            created_by=request.state.operator_principal.subject_id,
                            exploration_gate_id=plan.exploration_gate_id,
                            exploration_algorithm=plan.exploration_algorithm,
                            exploration_seed=plan.exploration_seed,
                            exploration_history=plan.exploration_history,
                        )
                        for plan in body.plans
                    ]
                except ElasticityInputError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
                    ) from exc
                try:
                    result = run_priceops_optimizer_batch(
                        requests=plan_requests,
                        job_id=receipt_id,
                        optimized_at=_parse_time(body.optimized_at),
                        repository=price_repository,
                        production_optimizer=production_optimizer,
                        runtime_mode=runtime_mode,
                    )
                except PriceOpsMissingSourceLineageError as exc:
                    raise _missing_source_lineage_error() from exc
                except PlanAlreadyExistsError as exc:
                    raise HTTPException(status_code=409, detail="batch plan id already exists or is repeated") from exc
                audit_event = _record_audit(
                    active_audit_log,
                    request,
                    "priceops.optimized.v1",
                    "run_model",
                    "priceops/optimizer-job",
                    {
                        "idempotency_key": effective_key,
                        "plan_count": len(body.plans),
                        "created": True,
                    },
                )
                payload = result.to_dict()
                payload["created"] = True
                payload["audit_event_id"] = audit_event.event_id
                payload["correlation_id"] = request.state.correlation_id
                return payload

            return _guard_creation(
                active_store,
                tenant_id=active_tenant_id,
                correlation_id=request.state.correlation_id,
                key=effective_key,
                scope="priceops:create_optimizer_job",
                payload={"verified_actor": request.state.operator_principal.subject_id,
                         "body": body.model_dump(mode="json")},
                operation=_execute,
            )

        @router.get("/optimizer-jobs/{job_id}", dependencies=[Depends(require_plan_permission(Action.VIEW))])
        def get_optimizer_job(job_id: str, request: Request) -> dict[str, Any]:
            result = command_store(request).get(
                tenant_id=tenant_id(request),
                receipt_id=job_id,
            )
            if result is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="optimizer job not found",
                )
            plans = result.get("plans", [])
            if not plans or any(
                (plan := service.repository.get_plan(row.get("plan_id"))) is None
                or not plan_in_scope(plan, request.state.operator_principal)
                for row in plans
            ):
                raise HTTPException(status_code=404, detail="optimizer job not found")
            return result

        @router.get("/plans", dependencies=[Depends(require_plan_permission(Action.VIEW))])
        def list_plans(
            request: Request,
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
            # Master rows are reused only within this response; every plan
            # retains scope checks and command boundaries remain uncached.
            read_cache: dict[tuple[str, str], Any] = {}
            rows = [
                plan.to_dict() for plan in service.repository.list_plans()
                if plan_in_scope(plan, request.state.operator_principal, read_cache=read_cache)
            ]
            if tenant_id is not None:
                rows = [row for row in rows if row.get("tenant_id") == tenant_id]
            if plan_status is not None:
                rows = [row for row in rows if row.get("status") == plan_status]
            return paginate(
                rows, page_params(limit=limit, offset=offset, sort=sort, order=order)
            )

        @router.get("/plans/{plan_id}", dependencies=[Depends(require_plan_permission(Action.VIEW))])
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
            payload["decision_writebacks"] = [
                row.to_dict() for row in service.repository.list_decision_writebacks(plan_id)
            ]
            return payload

        @router.get("/plans/{plan_id}/comparison", dependencies=[Depends(require_plan_permission(Action.VIEW))])
        def get_plan_comparison(plan_id: str, request: Request) -> dict[str, Any]:
            from modules.priceops.domain.pricing import PRICEOPS_POLICY_VERSION
            from shared.auth.rbac import rbac_allows

            payload = _run_read(lambda: service.get_plan_comparison(plan_id))
            principal = request.state.operator_principal
            payload["command_permissions"] = {
                "simulate": rbac_allows(principal, "priceops", Action.EXECUTE),
                "writeback": rbac_allows(principal, "priceops", Action.APPROVE),
            }
            actor_blockers = service.approval_actor_blockers(plan_id, principal.subject_id)
            payload["approval_actor_blockers"] = actor_blockers
            payload["created_by"] = getattr(service.repository.get_plan(plan_id), "created_by", None)
            payload["is_approvable"] = payload["is_approvable"] and not actor_blockers
            payload["actor_id"] = principal.subject_id
            payload["policy_version"] = PRICEOPS_POLICY_VERSION
            payload["solver_version"] = service.repository.get_optimization(plan_id).solver_version
            return payload

        @router.post("/plans/{plan_id}/simulate", dependencies=[Depends(require_plan_permission(Action.EXECUTE))])
        def simulate(
            plan_id: str,
            body: PriceOpsActorPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
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
                command_store=command_store(request),
                tenant_id=tenant_id(request),
                idempotency_key=idempotency_key,
                body=body,
            )

        @router.post("/plans/{plan_id}/simulate-scenario", dependencies=[Depends(require_plan_permission(Action.EXECUTE))])
        def simulate_scenario(
            plan_id: str,
            body: PriceOpsScenarioSimulationPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            return _run(
                lambda: service.simulate_scenario(
                    plan_id,
                    candidate_prices=body.candidate_prices,
                    actor=body.actor,
                    reason=body.reason or "candidate scenario simulation",
                    generated_at=_parse_time(body.occurred_at),
                ),
                active_audit_log,
                request,
                "priceops.scenario_simulated.v1",
                "simulate_scenario",
                plan_id,
                command_store=command_store(request),
                tenant_id=tenant_id(request),
                idempotency_key=idempotency_key,
                body=body,
            )

        @router.post("/plans/{plan_id}/decision-writeback", dependencies=[Depends(require_plan_permission(Action.APPROVE))])
        def decision_writeback(
            plan_id: str,
            body: PriceOpsDecisionWritebackPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            require_independent_approval(plan_id, body.decision, request)
            return _run(
                lambda: service.writeback_decision(
                    plan_id,
                    actor=body.actor,
                    decision=body.decision,
                    reason=body.reason,
                    selected_scenario_id=body.selected_scenario_id,
                    occurred_at=_parse_time(body.occurred_at),
                    idempotency_key=idempotency_key,
                ),
                active_audit_log,
                request,
                "priceops.decision_written_back.v1",
                body.decision,
                plan_id,
                command_store=command_store(request),
                tenant_id=tenant_id(request),
                idempotency_key=idempotency_key,
                body=body,
            )

        @router.post("/plans/{plan_id}/optimize", dependencies=[Depends(require_plan_permission(Action.EXECUTE))])
        def optimize(
            plan_id: str,
            body: PriceOpsActorPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            return _run(
                lambda: service.optimize(
                    plan_id,
                    actor=body.actor,
                    reason=body.reason or "constrained price optimization",
                    optimized_at=_parse_time(body.occurred_at),
                    exploration_gate_id=body.exploration_gate_id,
                    exploration_algorithm=body.exploration_algorithm,
                    exploration_seed=body.exploration_seed,
                    exploration_history=body.exploration_history,
                ),
                active_audit_log,
                request,
                "priceops.optimized.v1",
                "run_model",
                plan_id,
                command_store=command_store(request),
                tenant_id=tenant_id(request),
                idempotency_key=idempotency_key,
                body=body,
            )

        @router.post("/plans/{plan_id}/submit", dependencies=[Depends(require_plan_permission(Action.CREATE))])
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
                command_store=command_store(request),
                tenant_id=tenant_id(request),
                idempotency_key=idempotency_key,
                body=body,
            )

        @router.post("/plans/{plan_id}/approve", dependencies=[Depends(require_plan_permission(Action.APPROVE))])
        def approve(
            plan_id: str,
            body: PriceOpsApprovalPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            require_independent_approval(plan_id, body.decision, request)
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
                command_store=command_store(request),
                tenant_id=tenant_id(request),
                idempotency_key=idempotency_key,
                body=body,
            )

        @router.post("/plans/{plan_id}/activate", dependencies=[Depends(require_plan_permission(Action.EXECUTE))])
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
                command_store=command_store(request),
                tenant_id=tenant_id(request),
                idempotency_key=idempotency_key,
                body=body,
            )

        @router.post("/plans/{plan_id}/observation", dependencies=[Depends(require_plan_permission(Action.EXECUTE))])
        def start_observation(
            plan_id: str,
            body: PriceOpsObservationPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
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
                command_store=command_store(request),
                tenant_id=tenant_id(request),
                idempotency_key=idempotency_key,
                body=body,
            )

        @router.post("/plans/{plan_id}/evaluate", dependencies=[Depends(require_plan_permission(Action.EXECUTE))])
        def evaluate(
            plan_id: str,
            body: PriceOpsEvaluationPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
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
                command_store=command_store(request),
                tenant_id=tenant_id(request),
                idempotency_key=idempotency_key,
                body=body,
            )

        @router.post("/plans/{plan_id}/rollback", dependencies=[Depends(require_plan_permission(Action.EXECUTE))])
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
                command_store=command_store(request),
                tenant_id=tenant_id(request),
                idempotency_key=idempotency_key,
                body=body,
            )

        # -- Exploration Gates & Bandit (ODP-FR-PRICE-006) -------------------
        @router.get("/exploration-gates", dependencies=[Depends(require_plan_permission(Action.VIEW))])
        def get_exploration_gates(
            request: Request,
            tenant_id_param: str | None = Query(default=None, alias="tenant_id"),
            brand_id: str | None = Query(default=None),
            store_group: str | None = Query(default=None),
            sku_group: str | None = Query(default=None),
            at: str | None = Query(default=None),
        ) -> dict[str, Any]:
            tid = tenant_id(request, tenant_id_param)
            canonical = require_canonical_exploration()
            now = _parse_time(at) or datetime.now(UTC)
            if production_required or canonical is not None:
                now = datetime.now(UTC)
            scope = PriceScope(
                tenant_id=tid,
                brand_id=brand_id,
                store_group=store_group,
                sku_group=sku_group,
            )
            active_gate = service.repository.find_active_gate(scope, at=now)
            principal = request.state.operator_principal
            all_gates = [gate for gate in service.repository.list_gates(tenant_id=tid) if gate_in_scope(gate, principal)]
            if active_gate is not None and not gate_in_scope(active_gate, principal):
                active_gate = None
            return {
                "tenant_id": tid,
                "scope": scope.to_dict(),
                "active_gate": active_gate.to_dict() if active_gate is not None else None,
                "authorized": active_gate is not None,
                "gates": [g.to_dict() for g in all_gates],
            }

        @router.post("/exploration-gates", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_workflow_gate_write), Depends(require_plan_permission(Action.EXECUTE))], responses={405: {"description": "Gate mutation requires workflow approval"}})
        def register_exploration_gate(
            body: PriceOpsExplorationGateRegisterPayload,
            request: Request,
        ) -> dict[str, Any]:
            active_tid = tenant_id(request, body.tenant_id)
            gate = service.repository.save_gate(
                ExplorationGate(
                    gate_id=body.gate_id or f"gate-{uuid4()}",
                    tenant_id=active_tid,
                    budget_limit=body.budget_limit,
                    effective_from=_parse_required_time(body.effective_from),
                    effective_to=_parse_required_time(body.effective_to),
                    approved_by=body.approved_by,
                    approval_decision_id=body.approval_decision_id,
                    approval_id=body.approval_id,
                    rollback_condition=body.rollback_condition,
                    decision_policy_version_id=body.decision_policy_version_id,
                    scope_brand_id=body.scope_brand_id,
                    scope_store_group=body.scope_store_group,
                    scope_sku_group=body.scope_sku_group,
                    budget_consumed=0.0,
                    revoked_at=None,
                )
            )
            audit_event = _record_audit(
                active_audit_log,
                request,
                "priceops.gate_registered.v1",
                "create",
                f"priceops/gates/{gate.gate_id}",
                {"gate_id": gate.gate_id, "tenant_id": gate.tenant_id},
            )
            payload = gate.to_dict()
            payload["audit_event_id"] = audit_event.event_id
            payload["correlation_id"] = request.state.correlation_id
            return payload

        @router.post("/exploration-gates/{gate_id}/revoke", dependencies=[Depends(require_workflow_gate_write), Depends(require_plan_permission(Action.EXECUTE))], responses={405: {"description": "Gate mutation requires workflow approval"}})
        def revoke_exploration_gate(
            gate_id: str,
            body: PriceOpsExplorationGateRevokePayload,
            request: Request,
            tenant_id_param: str | None = Query(default=None, alias="tenant_id"),
        ) -> dict[str, Any]:
            tid = tenant_id(request, tenant_id_param)
            revoked_at = _parse_time(body.revoked_at) or datetime.now(UTC)
            try:
                gate = service.repository.revoke_gate(gate_id, tenant_id=tid, revoked_at=revoked_at)
            except LookupError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
            except ExplorationGateRevokedError as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

            audit_event = _record_audit(
                active_audit_log,
                request,
                "priceops.gate_revoked.v1",
                "revoke",
                f"priceops/gates/{gate.gate_id}",
                {"gate_id": gate.gate_id, "reason": body.reason},
            )
            payload = gate.to_dict()
            payload["audit_event_id"] = audit_event.event_id
            payload["correlation_id"] = request.state.correlation_id
            return payload

        @router.post("/exploration-candidates", dependencies=[Depends(require_plan_permission(Action.EXECUTE))])
        def generate_exploration_candidates(
            body: PriceOpsExplorationCandidatesPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            active_tid = tenant_id(request, body.tenant_id)
            canonical = require_canonical_exploration()
            proposal = PriceOpsPlanPayload(tenant_id=body.tenant_id, items=body.items)
            require_proposal_scope(proposal, request)
            body.items = proposal.items
            active_store = command_store(request)
            now = _parse_time(body.at) or datetime.now(UTC)
            if production_required or canonical is not None:
                now = datetime.now(UTC)
            scope = PriceScope(
                tenant_id=active_tid,
                brand_id=body.scope_brand_id,
                store_group=body.scope_store_group,
                sku_group=body.scope_sku_group,
            )
            def _execute(_receipt_id: str) -> dict[str, Any]:
                _require_durable_audit_capacity(active_store, active_audit_log, active_tid,
                                               production_required=production_required)
                try:
                    gate = service.repository.find_active_gate(scope, at=now)
                    if gate is None:
                        raise ExplorationNotAuthorizedError("No active canonical exploration authorization")
                    if not gate_in_scope(gate, request.state.operator_principal):
                        raise HTTPException(status_code=403, detail="exploration gate outside caller scope")
                    context = canonical.transaction(gate.gate_id, active_tid) if canonical is not None else nullcontext()
                    with context:
                        clock = datetime.now(UTC) if canonical is not None or production_required else now
                        grant = authorize_exploration(scope, at=clock, repository=service.repository)
                        if grant.gate_id != gate.gate_id:
                            raise ExplorationNotAuthorizedError("Exploration authorization changed during preview")
                        domain_items = [_item_from_payload(item) for item in body.items]
                        if canonical is not None:
                            canonical.validate_items(gate, domain_items)
                        candidates = ExplorationService(repository=service.repository).explorer.generate_candidates(
                            scope=scope, grant=grant, items=domain_items,
                            algorithm=body.algorithm, history=body.history, seed=body.seed,
                        )
                        quoted_cost = sum(float(canonical.budget_cost(candidate.estimated_exploration_cost)) if canonical is not None
                                          else candidate.estimated_exploration_cost for candidate in candidates)
                        if quoted_cost > grant.budget_remaining + 1e-6:
                            raise ExplorationBudgetExceededError("Candidate set exceeds the authorized remaining budget")
                        audit = _record_audit(
                            active_audit_log, request, "priceops.exploration_previewed.v1", "preview",
                            "priceops/gates/" + gate.gate_id,
                            {"gate_id": gate.gate_id, "approval_id": gate.approval_id,
                             "approval_decision_id": gate.approval_decision_id,
                             "decision_policy_version_id": gate.decision_policy_version_id, "mode": "shadow"},
                        )
                        return {"exploration_enabled": True, "grant": grant.to_dict(),
                                "algorithm": body.algorithm, "mode": "shadow",
                                "candidates": [candidate.to_dict() for candidate in candidates],
                                "audit_event_id": audit.event_id, "correlation_id": request.state.correlation_id}
                except (ExplorationNotAuthorizedError, ExplorationBudgetExceededError, ElasticityInputError, ValueError) as exc:
                    raise HTTPException(status_code=422, detail={
                        "code": "EXPLORATION_NOT_AUTHORIZED", "message": str(exc), "exploration_enabled": False,
                    }) from exc

            return _guard_creation(
                active_store, tenant_id=active_tid, correlation_id=request.state.correlation_id,
                key=idempotency_key, scope="priceops:exploration_preview",
                payload={"verified_actor": request.state.operator_principal.subject_id,
                         "body": body.model_dump(mode="json")}, operation=_execute,
            )

        @router.get("/plans/{plan_id}/activation-receipt", dependencies=[Depends(require_plan_permission(Action.VIEW))])
        def get_activation_receipt(
            plan_id: str,
            request: Request,
        ) -> dict[str, Any]:
            receipt = service.repository.get_activation_receipt(plan_id)
            if receipt is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No activation receipt found for plan {plan_id}",
                )
            return receipt.to_dict()

        return router

    def _resolve_item(
        item: PriceOpsPlanItemPayload,
    ) -> tuple[PricingPlanItem, dict[str, Any]]:
        estimate, binding = resolve_elasticity(
            current_price=item.current_price,
            observations=item.price_demand_observations,
            supplied_value=item.elasticity_value,
            supplied_confidence=item.confidence,
            supplied_min_price=item.applicable_min_price,
            supplied_max_price=item.applicable_max_price,
            horizon=item.horizon,
            prediction_origin_time=_parse_time(item.prediction_origin_time),
        )
        plan_item = PricingPlanItem.create(
            item_id=item.item_id,
            store_id=item.store_id,
            machine_type=item.machine_type,
            brand_id=item.brand_id,
            store_group=item.store_group,
            sku_group=item.sku_group,
            constraints=PriceConstraints(
                unit_cost=item.unit_cost,
                current_price=item.current_price,
                margin_floor_ratio=item.margin_floor_ratio,
                max_increase_pct=item.max_increase_pct,
                max_decrease_pct=item.max_decrease_pct,
                price_ladder_step=item.price_ladder_step,
                min_price=item.min_price,
                max_price=item.max_price,
                # resolve_elasticity has already intersected any payload bounds
                # with the fitted support range; taking the payload values here
                # would let a caller widen the range back out.
                applicable_min_price=estimate.applicable_min_price,
                applicable_max_price=estimate.applicable_max_price,
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
                actor=request.state.operator_principal.subject_id,
                action=action,
                resource=resource,
                outcome="accepted",
                correlation_id=request.state.correlation_id,
                metadata={**metadata, "tenant_id": request.state.operator_principal.tenant_id},
            )
        )

    def _conflict_error(exc: IdempotencyConflictError) -> ApiError:
        return ApiError(
            status.HTTP_409_CONFLICT,
            str(exc),
            code=ErrorCode.IDEMPOTENCY_CONFLICT,
            next_action="Use a new Idempotency-Key, or resend the original request body.",
        )

    def _guard_creation(
        command_store: TenantScopedCommandReceiptStore,
        *,
        tenant_id: str,
        correlation_id: str,
        key: str | None,
        scope: str,
        payload: Any,
        operation: Any,
    ) -> dict[str, Any]:
        """Run a creation under the idempotency policy and mark the replay.

        The receipt store reserves the key before executing and returns the
        recorded response on replay. Durable PriceOps binds the entire run to
        its same-engine transaction; process-local PoC keeps its reservation
        policy. A separate lookup followed by a write would leave concurrent
        requests free to create multiple plans for the same intent.
        """
        try:
            outcome = command_store.run(
                tenant_id=tenant_id,
                idempotency_key=key,
                scope=scope,
                payload=payload,
                correlation_id=correlation_id,
                operation=operation,
            )
        except IdempotencyConflictError as exc:
            raise _conflict_error(exc) from exc
        except CommandReceiptIncompleteError as exc:
            raise _incomplete_receipt_error(exc) from exc
        except CommandReceiptPersistenceError as exc:
            raise _persistence_error(exc) from exc
        result = apply_replay_marker(outcome.value, replayed=outcome.replayed)
        if outcome.replayed:
            # `created` predates this task and callers branch on it; a replay
            # created nothing. apply_replay_marker already returned a copy, so
            # the stored response is not mutated.
            result["created"] = False
        return result

    def _run(
        action: Any,
        audit_log: InMemoryAuditLog,
        request: Request,
        event_type: str,
        audit_action: str,
        plan_id: str,
        *,
        command_store: TenantScopedCommandReceiptStore | None = None,
        tenant_id: str | None = None,
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

        principal = getattr(request.state, "operator_principal", None)
        if principal is None or not principal.authenticated:
            raise HTTPException(status_code=403, detail="verified principal required")
        if hasattr(body, "actor"):
            body.actor = principal.subject_id
        if hasattr(body, "actor_id"):
            body.actor_id = principal.subject_id
        if hasattr(body, "executor"):
            body.executor = principal.subject_id

        def require_activation_flag() -> None:
            # This key controls price go-live, not every EXECUTE permission.
            # Read the same actual app owner as admin/API/queue composition.
            from apps.api.oday_api.security.dependencies import require_feature_flag

            if command_store is not None and command_store.is_durable:
                from shared.infrastructure.persistence.feature_flags import (
                    FeatureFlagStoreUnavailable,
                    require_durable_flag_owner,
                )

                app = request.scope.get("app")
                owner = getattr(getattr(app, "state", None), "feature_flag_registry", None)
                try:
                    queue = command_store.queue
                    require_durable_flag_owner(owner, queue._engine, queue=queue)
                except FeatureFlagStoreUnavailable as exc:
                    raise HTTPException(status_code=503, detail="FEATURE_FLAG_STORE_UNAVAILABLE") from exc
            require_feature_flag("high_risk.priceops.execute")(request)

        activation = event_type == "priceops.activated.v1"
        if activation and (command_store is None or not command_store.is_durable):
            # Process-local receipt stores have no transaction rollback. Refuse
            # before reserving any receipt; this is not a local-mode exemption.
            require_activation_flag()

        def _execute() -> dict[str, Any]:
            if activation:
                # Durable command_store.run has already checked/replayed a
                # committed key and revalidated locked tenant/object ownership.
                # New refusal rolls back reservation before any domain/audit/job
                # success. Replay remains a read of the prior committed result.
                require_activation_flag()
            approving = (
                event_type in {"priceops.approved.v1", "priceops.decision_written_back.v1"}
                and normalize_approval_decision(getattr(body, "decision", "")) == "approved"
            )
            exploring = event_type == "priceops.optimized.v1" and bool(getattr(body, "exploration_gate_id", None))
            if approving or exploring or event_type == "priceops.activated.v1":
                _require_durable_audit_capacity(
                    command_store, audit_log, tenant_id or "__local__",
                    production_required=getattr(request.state, "priceops_production_required", False),
                )
            try:
                result = action()
            except PlanNotFoundError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
            except InvalidScenarioError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
                ) from exc
            except UnavailableSimulationResultError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
                ) from exc
            except (
                ApprovalBlockedError,
                MissingRollbackPlanError,
                ExplorationNotAuthorizedError,
                ExplorationBudgetExceededError,
                ExplorationGateExpiredError,
                ExplorationGateRevokedError,
                ValueError,
            ) as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
                ) from exc
            except PriceOpsMissingSourceLineageError as exc:
                raise _missing_source_lineage_error() from exc
            except RuntimeError as exc:
                correlation_id = getattr(request.state, "correlation_id", None) or "unknown"
                logger.exception(
                    "Unexpected runtime error during PriceOps plan transition (plan_id=%s, correlation_id=%s): %s",
                    plan_id,
                    correlation_id,
                    exc,
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"PriceOps plan transition failed; correlation_id={correlation_id}",
                ) from exc
            payload = result.to_dict()
            audit_event = _record_audit(
                audit_log,
                request,
                event_type,
                audit_action,
                f"priceops/plans/{plan_id}",
                {"plan_id": plan_id, **{
                    key: payload.get("solver_metadata", {})[key]
                    for key in ("gate_id", "decision_policy_version_id", "exploration_decision_ids", "exploration_budget_consumed")
                    if key in payload.get("solver_metadata", {})
                }, **{
                    key: payload[key] for key in (
                        "decision_id", "scenario_id", "selected_scenario_id",
                        "reason", "policy_version", "solver_version",
                    ) if key in payload
                }},
            )
            payload["audit_event_id"] = audit_event.event_id
            payload["correlation_id"] = request.state.correlation_id
            return payload

        if command_store is None:
            return _execute()

        try:
            outcome = command_store.run(
                tenant_id=tenant_id or "__local__",
                idempotency_key=idempotency_key,
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
                correlation_id=request.state.correlation_id,
                operation=lambda _receipt_id: _execute(),
            )
        except IdempotencyConflictError as exc:
            raise _conflict_error(exc) from exc
        except CommandReceiptIncompleteError as exc:
            raise _incomplete_receipt_error(exc) from exc
        except CommandReceiptPersistenceError as exc:
            raise _persistence_error(exc) from exc
        return apply_replay_marker(outcome.value, replayed=outcome.replayed)

    def _missing_source_lineage_error() -> ApiError:
        return ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "價格輸入來源尚未備妥，無法執行正式最佳化。",
            code=ErrorCode.UNPROCESSABLE,
            next_action="請先確認價格資料的可追溯來源已備妥，再重試最佳化。",
        )

    _durable_store_required = durable_store_required

    def _require_durable_audit_capacity(
        command_store: TenantScopedCommandReceiptStore | None,
        audit_log: Any,
        tenant_id: str,
        *,
        production_required: bool = False,
    ) -> None:
        if command_store is None or not command_store.is_durable:
            return
        sink = getattr(audit_log, "_worm_sink", None)
        if sink is None and not production_required:
            return
        try:
            DurableAuditBacklog(
                command_store.queue._engine, getattr(sink, "sink_id", None),
            ).require_available(tenant_id)
            if sink is None:
                raise AuditBacklogUnavailable("Production pricing requires its external audit sink")
        except (AuditBacklogBlocked, AuditBacklogUnavailable) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "BACKPRESSURE_ACTIVE", "message": "Audit delivery must recover before this pricing command"},
                headers={"Retry-After": "30"},
            ) from exc

    def _incomplete_receipt_error(
        exc: CommandReceiptIncompleteError,
    ) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "COMMAND_RECEIPT_INCOMPLETE",
                "message": str(exc),
            },
        )

    def _persistence_error(exc: CommandReceiptPersistenceError) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DURABLE_COMMAND_STORE_UNAVAILABLE",
                "message": str(exc),
            },
        )

    def _run_read(action: Any) -> dict[str, Any]:
        try:
            result = action()
        except PlanNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return result.to_dict()

    __all__ = [
        "PriceOpsOptimizerJobPayload",
        "PriceOpsPlanPayload",
        "create_priceops_router",
    ]
