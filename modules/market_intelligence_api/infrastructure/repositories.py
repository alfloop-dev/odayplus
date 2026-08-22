"""Infrastructure repositories and data platform adapters for Market Intelligence BFF.

Contract: `odayplus.market-intelligence-api.v2`.
Task ID: `ODP-API-001`.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from modules.external_data.application.market_data_facade import (
    MarketDataAuthorizationError,
    MarketDataFacade,
    MarketDataFacadeError,
    MarketDataNotFoundError,
    MarketDataValidationError,
)
from modules.external_data.infrastructure.data_platform_client import (
    DataPlatformTransport,
)
from modules.market_intelligence_api.application.auth import (
    MarketIntelligenceAuthorizationError,
    MarketIntelligenceError,
    MarketIntelligenceNotFoundError,
    MarketIntelligenceValidationError,
)
from modules.market_intelligence_api.domain.models import (
    AcquisitionPlanFilter,
    CoverageFilter,
    DataGapFilter,
)
from packages.oday_data_product_contracts_client.models.coverage_surface import (
    CoverageSurface,
    DataGap,
    DataGapDocument,
)
from packages.oday_data_product_contracts_client.models.data_acquisition_plan import (
    DataAcquisitionPlan,
)
from packages.oday_data_product_contracts_client.models.market_cell_profile import (
    MarketCellProfile,
    MarketCellProfileDocument,
)
from packages.oday_data_product_contracts_client.models.site_market_context import (
    PeriodGrain,
    SiteMarketContext,
    SiteMarketContextDocument,
)
from shared.auth import Principal


@runtime_checkable
class MarketIntelligenceRepository(Protocol):
    """Protocol for Market Intelligence data access."""

    def get_site_context(
        self,
        site_id: str,
        *,
        period_grain: PeriodGrain | str = PeriodGrain.MONTHLY,
        period_key: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> SiteMarketContext: ...

    def get_site_context_document(
        self,
        document_id: str | None = None,
        *,
        site_id: str | None = None,
        period_grain: PeriodGrain | str | None = None,
        period_key: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> SiteMarketContextDocument: ...

    def get_market_cell_profile(
        self,
        cell_id: str,
        *,
        period_grain: PeriodGrain | str = PeriodGrain.MONTHLY,
        period_key: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> MarketCellProfile: ...

    def get_market_cell_profile_document(
        self,
        document_id: str | None = None,
        *,
        cell_id: str | None = None,
        period_grain: PeriodGrain | str | None = None,
        period_key: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> MarketCellProfileDocument: ...

    def get_coverage_surface(
        self,
        surface_id: str | None = None,
        *,
        filters: CoverageFilter | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> CoverageSurface: ...

    def list_data_gaps(
        self,
        *,
        filters: DataGapFilter | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> list[DataGap]: ...

    def get_data_gap(
        self,
        gap_id: str,
        *,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> DataGap: ...

    def list_acquisition_plans(
        self,
        *,
        filters: AcquisitionPlanFilter | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> list[DataAcquisitionPlan]: ...

    def get_acquisition_plan(
        self,
        plan_id: str,
        *,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> DataAcquisitionPlan: ...

    def save_acquisition_plan(
        self,
        plan: DataAcquisitionPlan,
        *,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> DataAcquisitionPlan: ...


class DataPlatformMarketIntelligenceRepository:
    """Production data access adapter delegating to MarketDataFacade and DataPlatformTransport."""

    def __init__(
        self,
        facade: MarketDataFacade,
        *,
        transport: DataPlatformTransport | None = None,
    ) -> None:
        self._facade = facade
        self._transport = transport or facade.client.transport
        self._local_plans: dict[tuple[str | None, str], DataAcquisitionPlan] = {}

    @property
    def facade(self) -> MarketDataFacade:
        return self._facade

    @property
    def transport(self) -> DataPlatformTransport:
        return self._transport

    def get_site_context(
        self,
        site_id: str,
        *,
        period_grain: PeriodGrain | str = PeriodGrain.MONTHLY,
        period_key: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> SiteMarketContext:
        try:
            return self._facade.get_site_market_context(
                site_id,
                period_grain=period_grain,
                period_key=period_key,
                tenant_id=tenant_id,
                principal=principal,
            )
        except MarketDataAuthorizationError as err:
            raise MarketIntelligenceAuthorizationError(
                str(err), code=err.code, details=err.details
            ) from err
        except MarketDataNotFoundError as err:
            raise MarketIntelligenceNotFoundError(str(err), details=err.details) from err
        except MarketDataValidationError as err:
            raise MarketIntelligenceValidationError(str(err), details=err.details) from err
        except MarketDataFacadeError as err:
            raise MarketIntelligenceError(str(err), code=err.code, details=err.details) from err

    def get_site_context_document(
        self,
        document_id: str | None = None,
        *,
        site_id: str | None = None,
        period_grain: PeriodGrain | str | None = None,
        period_key: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> SiteMarketContextDocument:
        try:
            return self._facade.get_site_market_context_document(
                document_id=document_id,
                site_id=site_id,
                period_grain=period_grain,
                period_key=period_key,
                tenant_id=tenant_id,
                principal=principal,
            )
        except MarketDataAuthorizationError as err:
            raise MarketIntelligenceAuthorizationError(
                str(err), code=err.code, details=err.details
            ) from err
        except MarketDataNotFoundError as err:
            raise MarketIntelligenceNotFoundError(str(err), details=err.details) from err
        except MarketDataValidationError as err:
            raise MarketIntelligenceValidationError(str(err), details=err.details) from err
        except MarketDataFacadeError as err:
            raise MarketIntelligenceError(str(err), code=err.code, details=err.details) from err

    def get_market_cell_profile(
        self,
        cell_id: str,
        *,
        period_grain: PeriodGrain | str = PeriodGrain.MONTHLY,
        period_key: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> MarketCellProfile:
        try:
            return self._facade.get_market_cell_profile(
                cell_id,
                period_grain=period_grain,
                period_key=period_key,
                tenant_id=tenant_id,
                principal=principal,
            )
        except MarketDataAuthorizationError as err:
            raise MarketIntelligenceAuthorizationError(
                str(err), code=err.code, details=err.details
            ) from err
        except MarketDataNotFoundError as err:
            raise MarketIntelligenceNotFoundError(str(err), details=err.details) from err
        except MarketDataValidationError as err:
            raise MarketIntelligenceValidationError(str(err), details=err.details) from err
        except MarketDataFacadeError as err:
            raise MarketIntelligenceError(str(err), code=err.code, details=err.details) from err

    def get_market_cell_profile_document(
        self,
        document_id: str | None = None,
        *,
        cell_id: str | None = None,
        period_grain: PeriodGrain | str | None = None,
        period_key: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> MarketCellProfileDocument:
        try:
            return self._facade.get_market_cell_profile_document(
                document_id=document_id,
                cell_id=cell_id,
                period_grain=period_grain,
                period_key=period_key,
                tenant_id=tenant_id,
                principal=principal,
            )
        except MarketDataAuthorizationError as err:
            raise MarketIntelligenceAuthorizationError(
                str(err), code=err.code, details=err.details
            ) from err
        except MarketDataNotFoundError as err:
            raise MarketIntelligenceNotFoundError(str(err), details=err.details) from err
        except MarketDataValidationError as err:
            raise MarketIntelligenceValidationError(str(err), details=err.details) from err
        except MarketDataFacadeError as err:
            raise MarketIntelligenceError(str(err), code=err.code, details=err.details) from err

    def get_coverage_surface(
        self,
        surface_id: str | None = None,
        *,
        filters: CoverageFilter | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> CoverageSurface:
        params: dict[str, Any] = {}
        if filters:
            if filters.admin_code:
                params["admin_code"] = filters.admin_code
            if filters.h3_index:
                params["h3_index"] = filters.h3_index
            if filters.business_date:
                params["business_date"] = filters.business_date
            if filters.readiness:
                params["readiness"] = filters.readiness
            if filters.state:
                params["state"] = filters.state
        if tenant_id:
            params["tenant_id"] = tenant_id

        raw = self._transport.fetch_document(
            "emgi.coverage-surface.v1",
            document_id=surface_id,
            params=params if params else None,
        )
        if raw is None:
            raise MarketIntelligenceNotFoundError(
                f"CoverageSurface not found (surface_id={surface_id}, params={params})",
                details={"surface_id": surface_id, "params": params},
            )
        try:
            return CoverageSurface.from_dict(raw)
        except Exception as err:
            raise MarketIntelligenceValidationError(
                f"Failed to parse CoverageSurface: {err}",
                details={"contract_id": "emgi.coverage-surface.v1", "raw": raw},
            ) from err

    def list_data_gaps(
        self,
        *,
        filters: DataGapFilter | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> list[DataGap]:
        params: dict[str, Any] = {}
        if tenant_id:
            params["tenant_id"] = tenant_id
        raw = self._transport.fetch_document("emgi.data-gap.v1", params=params if params else None)
        gaps: list[DataGap] = []
        if raw is not None:
            try:
                doc = DataGapDocument.from_dict(raw)
                gaps = list(doc.gaps)
            except Exception as err:
                raise MarketIntelligenceValidationError(
                    f"Failed to parse DataGapDocument: {err}",
                    details={"contract_id": "emgi.data-gap.v1", "raw": raw},
                ) from err
        else:
            # Fallback: check query_records
            records = self._transport.query_records(
                "emgi.data-gap.v1", filter_params=params if params else None
            )
            for rec in records:
                try:
                    gaps.append(DataGap.from_dict(rec))
                except Exception:
                    pass

        # Apply filters
        if filters:
            if filters.domain:
                gaps = [g for g in gaps if g.domain == filters.domain]
            if filters.gap_kind:
                gaps = [g for g in gaps if g.gap_kind == filters.gap_kind]
            if filters.reason_code:
                gaps = [g for g in gaps if g.reason_code == filters.reason_code]
            if filters.limit > 0:
                gaps = gaps[: filters.limit]

        return gaps

    def get_data_gap(
        self,
        gap_id: str,
        *,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> DataGap:
        all_gaps = self.list_data_gaps(tenant_id=tenant_id, principal=principal)
        for g in all_gaps:
            if g.gap_id == gap_id:
                return g
        raise MarketIntelligenceNotFoundError(
            f"Data gap not found: gap_id={gap_id}",
            details={"gap_id": gap_id},
        )

    def list_acquisition_plans(
        self,
        *,
        filters: AcquisitionPlanFilter | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> list[DataAcquisitionPlan]:
        effective_tenant = tenant_id or (principal.tenant_id if principal is not None else None)
        if effective_tenant is not None:
            plans: list[DataAcquisitionPlan] = [
                p for (t_id, _), p in self._local_plans.items() if t_id == effective_tenant
            ]
        else:
            plans = list(self._local_plans.values())
        params: dict[str, Any] = {}
        if filters and filters.site_context_id:
            params["site_context_id"] = filters.site_context_id
        if effective_tenant:
            params["tenant_id"] = effective_tenant

        raw = self._transport.fetch_document(
            "emgi.data-acquisition-plan.v1", params=params if params else None
        )
        if raw is not None:
            try:
                plan = DataAcquisitionPlan.from_dict(raw)
                if plan.plan_id not in [p.plan_id for p in plans]:
                    plans.append(plan)
            except Exception:
                pass

        records = self._transport.query_records(
            "emgi.data-acquisition-plan.v1", filter_params=params if params else None
        )
        for rec in records:
            try:
                p = DataAcquisitionPlan.from_dict(rec)
                if p.plan_id not in [existing.plan_id for existing in plans]:
                    plans.append(p)
            except Exception:
                pass

        if filters:
            if filters.status:
                plans = [
                    p
                    for p in plans
                    if p.status.value == filters.status or p.status == filters.status
                ]
            if filters.site_context_id:
                plans = [p for p in plans if p.site_context_id == filters.site_context_id]
            if filters.coverage_surface_id:
                plans = [p for p in plans if p.coverage_surface_id == filters.coverage_surface_id]
            if filters.limit > 0:
                plans = plans[: filters.limit]

        return plans

    def get_acquisition_plan(
        self,
        plan_id: str,
        *,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> DataAcquisitionPlan:
        effective_tenant = tenant_id or (principal.tenant_id if principal is not None else None)
        if (effective_tenant, plan_id) in self._local_plans:
            return self._local_plans[(effective_tenant, plan_id)]
        if effective_tenant is None:
            for (_t_id, p_id), p in self._local_plans.items():
                if p_id == plan_id:
                    return p

        raw = self._transport.fetch_document(
            "emgi.data-acquisition-plan.v1",
            document_id=plan_id,
            params={"plan_id": plan_id, "tenant_id": effective_tenant}
            if effective_tenant
            else {"plan_id": plan_id},
        )
        if raw is None:
            raise MarketIntelligenceNotFoundError(
                f"DataAcquisitionPlan not found: plan_id={plan_id}",
                details={"plan_id": plan_id, "tenant_id": effective_tenant}
                if effective_tenant
                else {"plan_id": plan_id},
            )
        try:
            return DataAcquisitionPlan.from_dict(raw)
        except Exception as err:
            raise MarketIntelligenceValidationError(
                f"Failed to parse DataAcquisitionPlan: {err}",
                details={"contract_id": "emgi.data-acquisition-plan.v1", "raw": raw},
            ) from err

    def save_acquisition_plan(
        self,
        plan: DataAcquisitionPlan,
        *,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> DataAcquisitionPlan:
        effective_tenant = tenant_id or (principal.tenant_id if principal is not None else None)
        self._local_plans[(effective_tenant, plan.plan_id)] = plan
        return plan


__all__ = [
    "DataPlatformMarketIntelligenceRepository",
    "MarketIntelligenceRepository",
]
