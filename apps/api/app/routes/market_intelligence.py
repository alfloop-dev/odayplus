"""FastAPI router for Market Intelligence BFF and Product Authorization.

Contract: `odayplus.market-intelligence-api.v2`.
Task ID: `ODP-API-001`.
"""

from __future__ import annotations

from typing import Any

from modules.external_data.application.market_data_facade import MarketDataFacade
from modules.market_intelligence_api.application.auth import (
    MarketIntelligenceAuthorizationError,
    MarketIntelligenceError,
    MarketIntelligenceNotFoundError,
    MarketIntelligenceValidationError,
)
from modules.market_intelligence_api.application.service import (
    MarketIntelligenceService,
)
from modules.market_intelligence_api.domain.contracts import (
    CONTRACT_CATEGORY,
    CONTRACT_ID,
    CONTRACT_VERSION,
)
from modules.market_intelligence_api.domain.models import (
    AcquisitionPlanFilter,
    CandidateCompareRequest,
    CoverageFilter,
    DataGapFilter,
)
from modules.external_data.infrastructure.data_platform_client import (
    DataPlatformClient,
    InMemoryDataPlatformTransport,
)
from modules.market_intelligence_api.infrastructure.repositories import (
    MarketIntelligenceRepository,
)
from packages.oday_data_product_contracts_client.models.data_acquisition_plan import (
    AcquisitionGap,
    AcquisitionScope,
    DataAcquisitionPlan,
    ExperimentStatus,
    PlanStatus,
    SourceValueExperiment,
)
from packages.oday_data_product_contracts_client.models.site_market_context import (
    PeriodGrain,
)
from shared.audit import InMemoryAuditLog

try:
    from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # pragma: no cover - optional API dependency
    APIRouter = None  # type: ignore[assignment]
else:
    class ComparePayload(BaseModel):
        site_ids: list[str] = Field(default_factory=list)
        cell_ids: list[str] = Field(default_factory=list)
        period_grain: str = "MONTHLY"
        period_key: str | None = None
        include_raw_context: bool = False

    class BatchSiteContextPayload(BaseModel):
        site_ids: list[str] = Field(min_length=1)
        period_grain: str = "MONTHLY"
        period_key: str | None = None

    class AcquisitionGapPayload(BaseModel):
        gap_id: str
        domain: str
        measure: str
        priority_rank: int = 1
        current_uncertainty_pct: float = 50.0
        expected_uncertainty_reduction_pct: float = 30.0
        decision_sensitivity: float = 1.0
        estimated_cost_units: float = 100.0
        estimated_latency_hours: float = 24.0
        survey_effort_hours: float = 4.0
        quota_units: float = 10.0
        rationale: str = ""
        recommended_source_ids: list[str] | None = None

    class SourceValueExperimentPayload(BaseModel):
        experiment_id: str
        source_id: str
        scope: str = "site"
        status: str = "planned"
        sample_size: int = 10
        hypothesis: str = ""
        baseline_uncertainty_pct: float = 50.0
        expected_uplift_pct: float = 20.0
        max_cost_units: float = 100.0
        max_latency_hours: float = 24.0
        max_quota_units: float = 10.0
        survey_effort_hours: float = 4.0
        paid_source: bool = False
        prior_value_evidence: bool = False
        gap_ids: list[str] = Field(default_factory=list)
        success_criteria: list[str] = Field(default_factory=list)

    class CreateAcquisitionPlanPayload(BaseModel):
        plan_id: str
        site_context_id: str
        coverage_surface_id: str
        status: str = "proposed"
        plan_version: int = 1
        effective_as_of: str | None = None
        knowledge_as_of: str | None = None
        gaps: list[AcquisitionGapPayload] = Field(default_factory=list)
        experiments: list[SourceValueExperimentPayload] = Field(default_factory=list)
        policy: dict[str, Any] = Field(default_factory=dict)
        metadata: dict[str, Any] = Field(default_factory=dict)

    def create_market_intelligence_router(
        *,
        service: MarketIntelligenceService | None = None,
        facade: MarketDataFacade | None = None,
        repository: MarketIntelligenceRepository | None = None,
        audit_log: InMemoryAuditLog | None = None,
        enforce_auth: bool = True,
    ) -> APIRouter:
        """Create and configure the Market Intelligence BFF APIRouter."""
        from apps.api.app.routes._common import resolve_tenant_id
        from apps.api.oday_api.security.dependencies import (
            build_engine,
            principal_from_headers,
        )

        active_audit_log = audit_log or InMemoryAuditLog()
        authz_engine = build_engine(audit_log=active_audit_log)

        if service is not None:
            active_service = service
        elif repository is not None:
            active_service = MarketIntelligenceService(
                repository=repository,
                auth_engine=authz_engine,
                enforce_auth=enforce_auth,
            )
        elif facade is not None:
            active_service = MarketIntelligenceService(
                facade=facade,
                auth_engine=authz_engine,
                enforce_auth=enforce_auth,
            )
        else:
            default_transport = InMemoryDataPlatformTransport()
            default_client = DataPlatformClient(transport=default_transport)
            default_facade = MarketDataFacade(
                client=default_client,
                auth_engine=authz_engine,
                enforce_auth=enforce_auth,
            )
            active_service = MarketIntelligenceService(
                facade=default_facade,
                auth_engine=authz_engine,
                enforce_auth=enforce_auth,
            )

        router = APIRouter(prefix="/market-intelligence", tags=["market-intelligence"])

        def _get_principal(request: Request) -> Any:
            principal = getattr(request.state, "operator_principal", None)
            if principal is None:
                principal = principal_from_headers(request.headers)
            return principal

        def _handle_error(exc: Exception) -> None:
            if isinstance(exc, MarketIntelligenceAuthorizationError):
                if "authenticated" in str(exc).lower() or exc.code == "unauthenticated_principal":
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail={"code": exc.code, "message": str(exc), "details": exc.details},
                    )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": exc.code, "message": str(exc), "details": exc.details},
                )
            if isinstance(exc, MarketIntelligenceNotFoundError):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": exc.code, "message": str(exc), "details": exc.details},
                )
            if isinstance(exc, (MarketIntelligenceValidationError, ValueError)):
                code = getattr(exc, "code", "market_intelligence_validation_error")
                details = getattr(exc, "details", {})
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": code, "message": str(exc), "details": details},
                )
            if isinstance(exc, HTTPException):
                raise exc
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "market_intelligence_error", "message": str(exc)},
            ) from exc

        # -------------------------------------------------------------------
        # Diagnostics & Health
        # -------------------------------------------------------------------

        @router.get("/health")
        def health_check() -> dict[str, Any]:
            return active_service.check_health()

        @router.get("/diagnostics")
        def get_diagnostics(request: Request) -> dict[str, Any]:
            _ = resolve_tenant_id(request)
            principal = _get_principal(request)
            if not principal or not principal.authenticated:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required for diagnostics",
                )
            return active_service.get_diagnostics()

        # -------------------------------------------------------------------
        # Market Cells
        # -------------------------------------------------------------------

        @router.get("/cells/{cell_id}")
        def get_market_cell(
            cell_id: str,
            request: Request,
            period_grain: str = "MONTHLY",
            period_key: str | None = None,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            principal = _get_principal(request)
            try:
                cell = active_service.get_market_cell(
                    cell_id,
                    period_grain=period_grain,
                    period_key=period_key,
                    tenant_id=tid,
                    principal=principal,
                )
                return cell.to_dict()
            except Exception as exc:
                _handle_error(exc)
                raise

        @router.get("/cells")
        def list_market_cells(
            request: Request,
            cell_ids: str | None = None,
            h3_resolution: int | None = None,
            admin_code: str | None = None,
            period_grain: str = "MONTHLY",
            period_key: str | None = None,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            principal = _get_principal(request)
            parsed_ids = [cid.strip() for cid in cell_ids.split(",") if cid.strip()] if cell_ids else None
            try:
                cells = active_service.list_market_cells(
                    parsed_ids,
                    h3_resolution=h3_resolution,
                    admin_code=admin_code,
                    period_grain=period_grain,
                    period_key=period_key,
                    tenant_id=tid,
                    principal=principal,
                )
                return {"items": [c.to_dict() for c in cells], "count": len(cells)}
            except Exception as exc:
                _handle_error(exc)
                raise

        # -------------------------------------------------------------------
        # Site Market Context
        # -------------------------------------------------------------------

        @router.get("/sites/{site_id}/context")
        def get_site_context(
            site_id: str,
            request: Request,
            period_grain: str = "MONTHLY",
            period_key: str | None = None,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            principal = _get_principal(request)
            try:
                ctx = active_service.get_site_context(
                    site_id,
                    period_grain=period_grain,
                    period_key=period_key,
                    tenant_id=tid,
                    principal=principal,
                )
                return ctx.to_dict()
            except Exception as exc:
                _handle_error(exc)
                raise

        @router.post("/sites/context/batch")
        def batch_get_site_contexts(
            body: BatchSiteContextPayload,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            principal = _get_principal(request)
            try:
                contexts = active_service.batch_get_site_contexts(
                    body.site_ids,
                    period_grain=body.period_grain,
                    period_key=body.period_key,
                    tenant_id=tid,
                    principal=principal,
                )
                return {"items": [c.to_dict() for c in contexts], "count": len(contexts)}
            except Exception as exc:
                _handle_error(exc)
                raise

        # -------------------------------------------------------------------
        # Candidate Compare
        # -------------------------------------------------------------------

        @router.post("/compare")
        def compare_candidates_post(
            body: ComparePayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            principal = _get_principal(request)
            compare_req = CandidateCompareRequest(
                site_ids=body.site_ids,
                cell_ids=body.cell_ids,
                period_grain=body.period_grain,
                period_key=body.period_key,
                tenant_id=tid,
                include_raw_context=body.include_raw_context,
            )
            try:
                result = active_service.compare_candidates(compare_req, principal=principal)
                payload = result.to_dict(include_raw=body.include_raw_context)
                if idempotency_key:
                    payload["idempotency_key"] = idempotency_key
                return payload
            except Exception as exc:
                _handle_error(exc)
                raise

        @router.get("/compare")
        def compare_candidates_get(
            request: Request,
            site_ids: str | None = None,
            cell_ids: str | None = None,
            period_grain: str = "MONTHLY",
            period_key: str | None = None,
            include_raw_context: bool = False,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            principal = _get_principal(request)
            parsed_site_ids = [s.strip() for s in site_ids.split(",") if s.strip()] if site_ids else []
            parsed_cell_ids = [c.strip() for c in cell_ids.split(",") if c.strip()] if cell_ids else []
            compare_req = CandidateCompareRequest(
                site_ids=parsed_site_ids,
                cell_ids=parsed_cell_ids,
                period_grain=period_grain,
                period_key=period_key,
                tenant_id=tid,
                include_raw_context=include_raw_context,
            )
            try:
                result = active_service.compare_candidates(compare_req, principal=principal)
                return result.to_dict(include_raw=include_raw_context)
            except Exception as exc:
                _handle_error(exc)
                raise

        # -------------------------------------------------------------------
        # Evidence & Lineage
        # -------------------------------------------------------------------

        @router.get("/evidence/{site_id}")
        def get_site_evidence(
            site_id: str,
            request: Request,
            period_grain: str = "MONTHLY",
            period_key: str | None = None,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            principal = _get_principal(request)
            try:
                chain = active_service.get_site_evidence(
                    site_id,
                    period_grain=period_grain,
                    period_key=period_key,
                    tenant_id=tid,
                    principal=principal,
                )
                return chain.to_dict()
            except Exception as exc:
                _handle_error(exc)
                raise

        @router.get("/evidence/cells/{cell_id}")
        def get_cell_evidence(
            cell_id: str,
            request: Request,
            period_grain: str = "MONTHLY",
            period_key: str | None = None,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            principal = _get_principal(request)
            try:
                chain = active_service.get_cell_evidence(
                    cell_id,
                    period_grain=period_grain,
                    period_key=period_key,
                    tenant_id=tid,
                    principal=principal,
                )
                return chain.to_dict()
            except Exception as exc:
                _handle_error(exc)
                raise

        # -------------------------------------------------------------------
        # Coverage Surface & Data Gaps
        # -------------------------------------------------------------------

        @router.get("/coverage")
        def get_coverage(
            request: Request,
            surface_id: str | None = None,
            admin_code: str | None = None,
            h3_index: str | None = None,
            business_date: str | None = None,
            readiness: str | None = None,
            state: str | None = None,
            limit: int = 100,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            principal = _get_principal(request)
            cov_filter = CoverageFilter(
                admin_code=admin_code,
                h3_index=h3_index,
                business_date=business_date,
                readiness=readiness,
                state=state,
                tenant_id=tid,
                limit=limit,
            )
            try:
                surface = active_service.get_coverage_surface(
                    surface_id=surface_id,
                    filters=cov_filter,
                    tenant_id=tid,
                    principal=principal,
                )
                return surface.to_dict()
            except Exception as exc:
                _handle_error(exc)
                raise

        @router.get("/data-gaps")
        def list_data_gaps(
            request: Request,
            domain: str | None = None,
            min_uncertainty_pct: float | None = None,
            severity: str | None = None,
            limit: int = 100,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            principal = _get_principal(request)
            gap_filter = DataGapFilter(
                domain=domain,
                min_uncertainty_pct=min_uncertainty_pct,
                severity=severity,
                tenant_id=tid,
                limit=limit,
            )
            try:
                gaps = active_service.list_data_gaps(
                    filters=gap_filter,
                    tenant_id=tid,
                    principal=principal,
                )
                return {"items": [g.to_dict() for g in gaps], "count": len(gaps)}
            except Exception as exc:
                _handle_error(exc)
                raise

        @router.get("/data-gaps/{gap_id}")
        def get_data_gap(
            gap_id: str,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            principal = _get_principal(request)
            try:
                gap = active_service.get_data_gap(
                    gap_id,
                    tenant_id=tid,
                    principal=principal,
                )
                return gap.to_dict()
            except Exception as exc:
                _handle_error(exc)
                raise

        # -------------------------------------------------------------------
        # Data Acquisition Plans
        # -------------------------------------------------------------------

        @router.get("/acquisition-plans")
        def list_acquisition_plans(
            request: Request,
            status: str | None = None,
            site_context_id: str | None = None,
            coverage_surface_id: str | None = None,
            limit: int = 50,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            principal = _get_principal(request)
            plan_filter = AcquisitionPlanFilter(
                status=status,
                site_context_id=site_context_id,
                coverage_surface_id=coverage_surface_id,
                tenant_id=tid,
                limit=limit,
            )
            try:
                plans = active_service.list_acquisition_plans(
                    filters=plan_filter,
                    tenant_id=tid,
                    principal=principal,
                )
                return {"items": [p.to_dict() for p in plans], "count": len(plans)}
            except Exception as exc:
                _handle_error(exc)
                raise

        @router.get("/acquisition-plans/{plan_id}")
        def get_acquisition_plan(
            plan_id: str,
            request: Request,
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            principal = _get_principal(request)
            try:
                plan = active_service.get_acquisition_plan(
                    plan_id,
                    tenant_id=tid,
                    principal=principal,
                )
                return plan.to_dict()
            except Exception as exc:
                _handle_error(exc)
                raise

        @router.post(
            "/acquisition-plans",
            status_code=status.HTTP_201_CREATED,
        )
        def create_acquisition_plan(
            body: CreateAcquisitionPlanPayload,
            request: Request,
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        ) -> dict[str, Any]:
            tid = resolve_tenant_id(request)
            principal = _get_principal(request)

            try:
                gaps = [
                    AcquisitionGap(
                        gap_id=g.gap_id,
                        domain=g.domain,
                        measure=g.measure,
                        priority_rank=g.priority_rank,
                        current_uncertainty_pct=g.current_uncertainty_pct,
                        expected_uncertainty_reduction_pct=g.expected_uncertainty_reduction_pct,
                        decision_sensitivity=g.decision_sensitivity,
                        estimated_cost_units=g.estimated_cost_units,
                        estimated_latency_hours=g.estimated_latency_hours,
                        survey_effort_hours=g.survey_effort_hours,
                        quota_units=g.quota_units,
                        rationale=g.rationale,
                        recommended_source_ids=g.recommended_source_ids,
                    )
                    for g in body.gaps
                ]

                experiments = [
                    SourceValueExperiment(
                        experiment_id=e.experiment_id,
                        source_id=e.source_id,
                        scope=AcquisitionScope(e.scope) if isinstance(e.scope, str) else e.scope,
                        status=ExperimentStatus(e.status) if isinstance(e.status, str) else e.status,
                        sample_size=e.sample_size,
                        hypothesis=e.hypothesis,
                        baseline_uncertainty_pct=e.baseline_uncertainty_pct,
                        expected_uplift_pct=e.expected_uplift_pct,
                        max_cost_units=e.max_cost_units,
                        max_latency_hours=e.max_latency_hours,
                        max_quota_units=e.max_quota_units,
                        survey_effort_hours=e.survey_effort_hours,
                        paid_source=e.paid_source,
                        prior_value_evidence=e.prior_value_evidence,
                        gap_ids=list(e.gap_ids),
                        success_criteria=list(e.success_criteria),
                    )
                    for e in body.experiments
                ]

                plan = DataAcquisitionPlan(
                    plan_id=body.plan_id,
                    site_context_id=body.site_context_id,
                    coverage_surface_id=body.coverage_surface_id,
                    status=PlanStatus(body.status) if isinstance(body.status, str) else body.status,
                    gaps=gaps,
                    experiments=experiments,
                    effective_as_of=body.effective_as_of or "",
                    knowledge_as_of=body.knowledge_as_of or "",
                    plan_version=body.plan_version,
                    policy=body.policy,
                    metadata=body.metadata,
                )

                saved = active_service.propose_acquisition_plan(
                    plan,
                    tenant_id=tid,
                    principal=principal,
                )
                payload = saved.to_dict()
                if idempotency_key:
                    payload["idempotency_key"] = idempotency_key
                return payload
            except Exception as exc:
                _handle_error(exc)
                raise

        return router


__all__ = [
    "BatchSiteContextPayload",
    "ComparePayload",
    "CreateAcquisitionPlanPayload",
    "create_market_intelligence_router",
]
