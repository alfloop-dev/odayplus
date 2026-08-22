"""Infrastructure repositories and data platform adapters for Market Intelligence BFF.

Contract: `odayplus.market-intelligence-api.v2`.
Task ID: `ODP-API-001`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Protocol, runtime_checkable

from modules.external_data.application.market_data_facade import (
    MarketDataAuthorizationError,
    MarketDataFacade,
    MarketDataFacadeError,
    MarketDataNotFoundError,
    MarketDataValidationError,
)
from modules.external_data.infrastructure.data_platform_client import (
    COVERAGE_CELL_PARAMS,
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
    CoverageCell,
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


def _effective_tenant_id(
    tenant_id: str | None,
    principal: Principal | None,
) -> str | None:
    """Resolve the tenant scope used by every raw data-platform read."""
    value = tenant_id or (principal.tenant_id if principal is not None else None)
    clean_value = str(value).strip() if value is not None else ""
    return clean_value or None


def _payload_is_in_tenant_scope(
    payload: Mapping[str, Any] | None,
    tenant_id: str | None,
) -> bool:
    """Fail closed when a raw payload cannot prove its tenant ownership.

    The released coverage, data-gap, and acquisition-plan contracts do not
    all expose tenant_id as a typed model field. The transport may still
    provide the scope as a top-level envelope attribute. A tenant-scoped BFF
    read must not treat an unscoped payload as shared data.
    """
    if tenant_id is None:
        return payload is not None
    if payload is None:
        return False
    payload_tenant = payload.get("tenant_id")
    return payload_tenant is not None and str(payload_tenant).strip() == tenant_id


def _product_read_params(
    *,
    resource_key: str | None = None,
    resource_id: str | None = None,
    period_grain: PeriodGrain | str | None = None,
    period_key: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Build the raw query used to prove a facade product read is scoped."""
    params: dict[str, Any] = {}
    if resource_key is not None and resource_id is not None:
        params[resource_key] = resource_id
    if period_grain is not None:
        params["period_grain"] = str(
            period_grain.value if isinstance(period_grain, PeriodGrain) else period_grain
        )
    if period_key is not None:
        params["period_key"] = period_key
    if tenant_id is not None:
        params["tenant_id"] = tenant_id
    return params


def _require_scoped_product_document(
    transport: DataPlatformTransport,
    contract_id: str,
    *,
    document_id: str | None = None,
    params: Mapping[str, Any] | None = None,
    tenant_id: str | None,
    resource_id: str | None,
) -> None:
    """Require raw product evidence before returning a facade projection."""
    raw = transport.fetch_document(contract_id, document_id=document_id, params=params)
    if not _payload_is_in_tenant_scope(raw, tenant_id):
        raise MarketIntelligenceNotFoundError(
            f"Unscoped {contract_id} read rejected (resource_id={resource_id})",
            details={
                "contract_id": contract_id,
                "resource_id": resource_id,
                "tenant_id": tenant_id,
            },
        )


def _coverage_cell_predicates(filters: CoverageFilter | None) -> dict[str, str]:
    """Collect the coverage-cell predicates a caller actually supplied.

    Every one of these names is a `CoverageCell` field, not a surface
    envelope field, so they narrow which cells the surface reports.
    """
    if filters is None:
        return {}
    predicates: dict[str, str] = {}
    for name in COVERAGE_CELL_PARAMS:
        value = getattr(filters, name, None)
        if value:
            predicates[name] = str(value)
    return predicates


def _coverage_cell_matches(cell: CoverageCell, predicates: Mapping[str, str]) -> bool:
    """True when a cell satisfies every supplied predicate."""
    return all(str(getattr(cell, name, None)) == value for name, value in predicates.items())


def _project_coverage_surface(
    surface: CoverageSurface,
    *,
    predicates: Mapping[str, str],
    limit: int,
    surface_id: str | None,
) -> CoverageSurface:
    """Narrow a stored surface to the cells the query asked for.

    Filtering is applied here as well as in the transport so the semantics
    hold for any transport, including one that ignores query parameters and
    returns the whole document. A filter that matches no cell is an empty
    result, not a surface served with cells the caller excluded.

    Contract-level aggregates (`state_breakdown`, `freshness`,
    `search_completeness`) are left exactly as the producer published them:
    they describe the whole surface and the BFF cannot honestly recompute
    them from an embedded cell subset. The projection is reported in
    `metadata.cell_query` instead.
    """
    if not predicates and limit <= 0:
        return surface

    matched = [cell for cell in surface.cells if _coverage_cell_matches(cell, predicates)]
    if predicates and not matched:
        raise MarketIntelligenceNotFoundError(
            f"No coverage cell matches the query (surface_id={surface.surface_id}, "
            f"filters={dict(predicates)})",
            details={
                "surface_id": surface_id or surface.surface_id,
                "filters": dict(predicates),
            },
        )

    visible = matched[:limit] if limit > 0 else matched
    if not predicates and len(visible) == len(surface.cells):
        return surface

    metadata = dict(surface.metadata)
    metadata["cell_query"] = {
        "filters": dict(predicates),
        "limit": limit if limit > 0 else None,
        "cell_count_published": len(surface.cells),
        "cell_count_matched": len(matched),
        "cell_count_returned": len(visible),
        "truncated_by_limit": len(visible) < len(matched),
    }
    return replace(surface, cells=visible, metadata=metadata)


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
            context = self._facade.get_site_market_context(
                site_id,
                period_grain=period_grain,
                period_key=period_key,
                tenant_id=tenant_id,
                principal=principal,
            )
            effective_tenant = _effective_tenant_id(tenant_id, principal)
            _require_scoped_product_document(
                self._transport,
                "emgi.site-market-context.v1",
                params=_product_read_params(
                    resource_key="site_id",
                    resource_id=site_id,
                    period_grain=period_grain,
                    period_key=period_key,
                    tenant_id=effective_tenant,
                ),
                tenant_id=effective_tenant,
                resource_id=site_id,
            )
            return context
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
            document = self._facade.get_site_market_context_document(
                document_id=document_id,
                site_id=site_id,
                period_grain=period_grain,
                period_key=period_key,
                tenant_id=tenant_id,
                principal=principal,
            )
            effective_tenant = _effective_tenant_id(tenant_id, principal)
            _require_scoped_product_document(
                self._transport,
                "emgi.site-market-context.v1",
                document_id=document_id,
                params=_product_read_params(
                    resource_key="site_id",
                    resource_id=site_id,
                    period_grain=period_grain,
                    period_key=period_key,
                    tenant_id=effective_tenant,
                ),
                tenant_id=effective_tenant,
                resource_id=document_id or site_id,
            )
            return document
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
            profile = self._facade.get_market_cell_profile(
                cell_id,
                period_grain=period_grain,
                period_key=period_key,
                tenant_id=tenant_id,
                principal=principal,
            )
            effective_tenant = _effective_tenant_id(tenant_id, principal)
            _require_scoped_product_document(
                self._transport,
                "emgi.market-cell-profile.v1",
                params=_product_read_params(
                    resource_key="cell_id",
                    resource_id=cell_id,
                    period_grain=period_grain,
                    period_key=period_key,
                    tenant_id=effective_tenant,
                ),
                tenant_id=effective_tenant,
                resource_id=cell_id,
            )
            return profile
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
            document = self._facade.get_market_cell_profile_document(
                document_id=document_id,
                cell_id=cell_id,
                period_grain=period_grain,
                period_key=period_key,
                tenant_id=tenant_id,
                principal=principal,
            )
            effective_tenant = _effective_tenant_id(tenant_id, principal)
            _require_scoped_product_document(
                self._transport,
                "emgi.market-cell-profile.v1",
                document_id=document_id,
                params=_product_read_params(
                    resource_key="cell_id",
                    resource_id=cell_id,
                    period_grain=period_grain,
                    period_key=period_key,
                    tenant_id=effective_tenant,
                ),
                tenant_id=effective_tenant,
                resource_id=document_id or cell_id,
            )
            return document
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
        effective_tenant = _effective_tenant_id(tenant_id, principal)
        predicates = _coverage_cell_predicates(filters)
        limit = filters.limit if filters else 0

        params: dict[str, Any] = dict(predicates)
        if effective_tenant:
            params["tenant_id"] = effective_tenant

        raw = self._transport.fetch_document(
            "emgi.coverage-surface.v1",
            document_id=surface_id,
            params=params if params else None,
        )
        if not _payload_is_in_tenant_scope(raw, effective_tenant):
            raise MarketIntelligenceNotFoundError(
                f"CoverageSurface not found (surface_id={surface_id}, params={params})",
                details={"surface_id": surface_id, "params": params},
            )
        try:
            surface = CoverageSurface.from_dict(raw)
        except Exception as err:
            raise MarketIntelligenceValidationError(
                f"Failed to parse CoverageSurface: {err}",
                details={"contract_id": "emgi.coverage-surface.v1", "raw": raw},
            ) from err
        return _project_coverage_surface(
            surface,
            predicates=predicates,
            limit=limit,
            surface_id=surface_id,
        )

    def list_data_gaps(
        self,
        *,
        filters: DataGapFilter | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> list[DataGap]:
        params: dict[str, Any] = {}
        effective_tenant = _effective_tenant_id(tenant_id, principal)
        if effective_tenant:
            params["tenant_id"] = effective_tenant
        raw = self._transport.fetch_document("emgi.data-gap.v1", params=params if params else None)
        gaps: list[DataGap] = []
        if _payload_is_in_tenant_scope(raw, effective_tenant):
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
                if not _payload_is_in_tenant_scope(rec, effective_tenant):
                    continue
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
        effective_tenant = _effective_tenant_id(tenant_id, principal)
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
        if _payload_is_in_tenant_scope(raw, effective_tenant):
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
            if not _payload_is_in_tenant_scope(rec, effective_tenant):
                continue
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
        effective_tenant = _effective_tenant_id(tenant_id, principal)
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
        if not _payload_is_in_tenant_scope(raw, effective_tenant):
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
