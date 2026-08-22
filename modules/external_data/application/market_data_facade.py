"""Market Data Read Facade.

Contract: `odayplus.market-data-facade.v2`.
Part of Task: `ODP-LEGACY-FACADE-001`.

This application facade provides the single unified read interface for market data products
(SiteMarketContext, MarketCellProfile, CatchmentProfile, PropertyObservation) and foundation datasets
(StoreReference, StoreDayCoverage, PlatformFoundation).

Architectural Invariants:
1. Product authorization remains in odayplus: every query evaluates the caller's Principal and tenant scope.
2. Strictly read-only semantics: zero provider credentials, zero raw HTTP fetch, zero database table writes.
3. Consumes only generated versioned contract clients (ODP-XR-CLIENT-001, ODP-XR-PRODUCT-CLIENT-001).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from modules.external_data.infrastructure.data_platform_client import (
    DataPlatformClient,
    DataPlatformClientError,
    DataPlatformDocumentNotFoundError,
    DataPlatformIntegrityError,
    DataPlatformTransport,
    DataPlatformValidationError,
)
from packages.oday_data_contracts_client.models import (
    EMGIPlatformFoundationConfig,
    StoreDailyPerformance,
    StoreDayCoverage,
    StoreReference,
)
from packages.oday_data_product_contracts_client.models import (
    CatchmentProfileDocument,
    MarketCellProfileDocument,
    PropertyObservationDocument,
    SiteMarketContextDocument,
)
from packages.oday_data_product_contracts_client.models.catchment_profile import (
    CatchmentProfile,
)
from packages.oday_data_product_contracts_client.models.market_cell_profile import (
    MarketCellProfile,
)
from packages.oday_data_product_contracts_client.models.property_observation import (
    PropertyEntity,
    PropertyListingObservation,
)
from packages.oday_data_product_contracts_client.models.site_market_context import (
    PeriodGrain,
    SiteMarketContext,
)
from shared.audit.policy import build_security_event, requires_audit
from shared.auth import (
    AccessRequest,
    Action,
    DataClassification,
    Decision,
    Principal,
    ResourceDescriptor,
    Role,
)
from shared.auth.engine import AuthorizationEngine

FACADE_CONTRACT = "odayplus.market-data-facade.v2"
FACADE_VERSION = "2.0.0"
FACADE_MODE_ENV = "ODAY_MARKET_DATA_FACADE_MODE"
KILL_SWITCH_ENV = "ODAY_MARKET_DATA_KILL_SWITCH_ACTIVE"
ROLLBACK_PROBE_SITE_ID = "cutover-probe-site"

# Roles inherently permitted to read market data products and foundation references
ALLOWED_MARKET_DATA_ROLES = frozenset({
    Role.PLATFORM_ADMIN,
    Role.ARCHITECTURE_OWNER,
    Role.DATA_OWNER,
    Role.MODEL_OWNER,
    Role.RELEASE_OWNER,
    Role.EXPANSION_USER,
    Role.SITE_REVIEWER,
    Role.OPERATIONS_MANAGER,
    Role.REGIONAL_SUPERVISOR,
    Role.PRICING_MANAGER,
    Role.MARKETING_MANAGER,
    Role.FINANCE_LEGAL,
    Role.COMPLIANCE_OFFICER,
    Role.RECORDS_MANAGER,
    Role.RETENTION_MANAGER,
    Role.EXECUTIVE,
    Role.FRANCHISEE,
    Role.AUDITOR,
})


class MarketDataFacadeError(Exception):
    """Base error for MarketDataFacade operations."""

    def __init__(self, message: str, *, code: str = "market_data_facade_error", details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details) if details else {}


class MarketDataAuthorizationError(MarketDataFacadeError, PermissionError):
    """Raised when access to market data is denied by authorization policy."""

    def __init__(self, message: str, *, code: str = "market_data_unauthorized", details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code=code, details=details)


class MarketDataNotFoundError(MarketDataFacadeError, LookupError):
    """Raised when the requested market data entity or document does not exist."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code="market_data_not_found", details=details)


class MarketDataValidationError(MarketDataFacadeError, ValueError):
    """Raised when data platform payload fails contract schema validation."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code="market_data_validation_error", details=details)


class MarketDataFacade:
    """Application facade providing unified, authorized read access to EMGI data platform products.

    Contract: `odayplus.market-data-facade.v2`.
    """

    def __init__(
        self,
        client: DataPlatformClient | None = None,
        auth_engine: AuthorizationEngine | None = None,
        *,
        transport: DataPlatformTransport | None = None,
        enforce_auth: bool = True,
    ) -> None:
        if client is not None:
            self._client = client
        elif transport is not None:
            self._client = DataPlatformClient(transport=transport)
        else:
            raise MarketDataFacadeError(
                "MarketDataFacade requires an explicit DataPlatformClient or DataPlatformTransport.",
                code="missing_client",
            )
        self._auth_engine = auth_engine if auth_engine is not None else AuthorizationEngine()
        self._enforce_auth = enforce_auth

    @property
    def client(self) -> DataPlatformClient:
        return self._client

    @property
    def auth_engine(self) -> AuthorizationEngine:
        return self._auth_engine

    @property
    def contract(self) -> str:
        return FACADE_CONTRACT

    @property
    def version(self) -> str:
        return FACADE_VERSION

    # -----------------------------------------------------------------------
    # Authorization & Governance
    # -----------------------------------------------------------------------

    def _authorize_read(
        self,
        resource_type: str,
        resource_id: str | None,
        *,
        tenant_id: str | None = None,
        principal: Principal | None = None,
        classification: DataClassification = DataClassification.CONFIDENTIAL,
    ) -> str | None:
        """Enforce odayplus product authorization and tenant isolation rules.

        Returns:
            The effective tenant_id to be forwarded to downstream client queries.
        """
        effective_tenant_id = tenant_id or (principal.tenant_id if principal is not None else None)

        if not self._enforce_auth and principal is None:
            return effective_tenant_id

        if principal is None:
            raise MarketDataAuthorizationError(
                "Authentication required: no principal provided for market data read",
                code="authentication_required",
                details={"resource_type": resource_type, "resource_id": resource_id},
            )

        access = AccessRequest(
            principal=principal,
            action=Action.VIEW,
            resource=ResourceDescriptor(
                type=resource_type,
                resource_id=resource_id,
                tenant_id=effective_tenant_id,
                data_classification=classification,
            ),
        )

        if not principal.authenticated:
            decision = Decision.deny("Principal is not authenticated", policy_id="authenticated")
            if hasattr(self._auth_engine, "audit_log"):
                self._auth_engine.audit_log.record(build_security_event(access, decision))
            raise MarketDataAuthorizationError(
                "Principal is not authenticated",
                code="unauthenticated_principal",
                details={"subject_id": principal.subject_id, "resource_type": resource_type},
            )

        # 1. Tenant Isolation Check:
        # Default effective_tenant_id to principal.tenant_id if not explicitly provided.
        # If an explicit tenant_id is provided and differs from principal.tenant_id,
        # require PLATFORM_ADMIN role.
        if effective_tenant_id and principal.tenant_id and principal.tenant_id != effective_tenant_id:
            if not principal.has_role(Role.PLATFORM_ADMIN):
                decision = Decision.deny(
                    f"Cross-tenant access denied: principal tenant {principal.tenant_id!r} cannot access resource tenant {effective_tenant_id!r}",
                    policy_id="tenant_isolation",
                )
                if hasattr(self._auth_engine, "audit_log"):
                    self._auth_engine.audit_log.record(build_security_event(access, decision))
                raise MarketDataAuthorizationError(
                    f"Cross-tenant access denied: principal tenant {principal.tenant_id!r} cannot access resource tenant {effective_tenant_id!r}",
                    code="cross_tenant_access_denied",
                    details={
                        "principal_tenant_id": principal.tenant_id,
                        "resource_tenant_id": effective_tenant_id,
                        "resource_id": resource_id,
                    },
                )

        # 2. RBAC / Role Check - Every caller must have at least one allowed role
        has_allowed_role = any(role in ALLOWED_MARKET_DATA_ROLES for role in principal.roles)
        if not has_allowed_role:
            role_names = [getattr(r, "value", str(r)) for r in principal.roles]
            decision = Decision.deny(
                f"Principal {principal.subject_id!r} with roles {role_names} is not authorized for market data",
                policy_id="rbac",
            )
            if hasattr(self._auth_engine, "audit_log"):
                self._auth_engine.audit_log.record(build_security_event(access, decision))
            raise MarketDataAuthorizationError(
                f"Principal {principal.subject_id!r} with roles {role_names} is not authorized for market data",
                code="role_unauthorized",
                details={"subject_id": principal.subject_id, "roles": role_names},
            )

        # 3. Data classification clearance check
        if not principal.scope.permits_classification(classification):
            decision = Decision.deny(
                f"Principal clearance {principal.scope.clearance.name} insufficient for {classification.name} data",
                policy_id="data_classification",
            )
            if hasattr(self._auth_engine, "audit_log"):
                self._auth_engine.audit_log.record(build_security_event(access, decision))
            raise MarketDataAuthorizationError(
                f"Principal clearance {principal.scope.clearance.name} insufficient for {classification.name} data",
                code="insufficient_clearance",
                details={"clearance": principal.scope.clearance.value, "required": classification.value},
            )

        # 4. Audit recording for authorized reads
        decision = Decision.allow("authorized")
        if requires_audit(Action.VIEW, classification) and hasattr(self._auth_engine, "audit_log"):
            self._auth_engine.audit_log.record(build_security_event(access, decision))

        return effective_tenant_id

    # -----------------------------------------------------------------------
    # Product Reads: Site Market Context (emgi.site-market-context.v1)
    # -----------------------------------------------------------------------

    def get_site_market_context(
        self,
        site_id: str,
        *,
        period_grain: PeriodGrain | str = PeriodGrain.MONTHLY,
        period_key: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> SiteMarketContext:
        """Authorized read of a single SiteMarketContext for a site."""
        effective_tenant_id = self._authorize_read("site_market_context", site_id, tenant_id=tenant_id, principal=principal)
        try:
            return self._client.get_site_market_context(
                site_id,
                period_grain=period_grain,
                period_key=period_key,
                tenant_id=effective_tenant_id,
            )
        except DataPlatformDocumentNotFoundError as err:
            raise MarketDataNotFoundError(f"Site market context not found for site_id={site_id}: {err}", details=err.details) from err
        except DataPlatformValidationError as err:
            raise MarketDataValidationError(f"Invalid site market context schema: {err}", details=err.details) from err
        except DataPlatformClientError as err:
            raise MarketDataFacadeError(f"Data platform client error: {err}", details=err.details) from err

    def get_site_market_context_document(
        self,
        document_id: str | None = None,
        *,
        site_id: str | None = None,
        period_grain: PeriodGrain | str | None = None,
        period_key: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> SiteMarketContextDocument:
        """Authorized read of a complete SiteMarketContextDocument."""
        effective_tenant_id = self._authorize_read("site_market_context_document", document_id or site_id, tenant_id=tenant_id, principal=principal)
        try:
            return self._client.get_site_market_context_document(
                document_id=document_id,
                site_id=site_id,
                period_grain=period_grain,
                period_key=period_key,
                tenant_id=effective_tenant_id,
            )
        except DataPlatformDocumentNotFoundError as err:
            raise MarketDataNotFoundError(f"Site market context document not found: {err}", details=err.details) from err
        except DataPlatformValidationError as err:
            raise MarketDataValidationError(f"Invalid site market context document schema: {err}", details=err.details) from err
        except DataPlatformClientError as err:
            raise MarketDataFacadeError(f"Data platform client error: {err}", details=err.details) from err

    # -----------------------------------------------------------------------
    # Product Reads: Market Cell Profile (emgi.market-cell-profile.v1)
    # -----------------------------------------------------------------------

    def get_market_cell_profile(
        self,
        cell_id: str,
        *,
        period_grain: PeriodGrain | str = PeriodGrain.MONTHLY,
        period_key: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> MarketCellProfile:
        """Authorized read of a single MarketCellProfile for an H3 cell."""
        effective_tenant_id = self._authorize_read("market_cell_profile", cell_id, tenant_id=tenant_id, principal=principal)
        try:
            return self._client.get_market_cell_profile(
                cell_id,
                period_grain=period_grain,
                period_key=period_key,
                tenant_id=effective_tenant_id,
            )
        except DataPlatformDocumentNotFoundError as err:
            raise MarketDataNotFoundError(f"Market cell profile not found for cell_id={cell_id}: {err}", details=err.details) from err
        except DataPlatformValidationError as err:
            raise MarketDataValidationError(f"Invalid market cell profile schema: {err}", details=err.details) from err
        except DataPlatformClientError as err:
            raise MarketDataFacadeError(f"Data platform client error: {err}", details=err.details) from err

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
        """Authorized read of a complete MarketCellProfileDocument."""
        effective_tenant_id = self._authorize_read("market_cell_profile_document", document_id or cell_id, tenant_id=tenant_id, principal=principal)
        try:
            return self._client.get_market_cell_profile_document(
                document_id=document_id,
                cell_id=cell_id,
                period_grain=period_grain,
                period_key=period_key,
                tenant_id=effective_tenant_id,
            )
        except DataPlatformDocumentNotFoundError as err:
            raise MarketDataNotFoundError(f"Market cell profile document not found: {err}", details=err.details) from err
        except DataPlatformValidationError as err:
            raise MarketDataValidationError(f"Invalid market cell profile document schema: {err}", details=err.details) from err
        except DataPlatformClientError as err:
            raise MarketDataFacadeError(f"Data platform client error: {err}", details=err.details) from err

    # -----------------------------------------------------------------------
    # Product Reads: Catchment Profile (emgi.catchment-profile.v1)
    # -----------------------------------------------------------------------

    def get_catchment_profile(
        self,
        catchment_id: str,
        *,
        period_grain: PeriodGrain | str = PeriodGrain.MONTHLY,
        period_key: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> CatchmentProfile:
        """Authorized read of a single CatchmentProfile."""
        effective_tenant_id = self._authorize_read("catchment_profile", catchment_id, tenant_id=tenant_id, principal=principal)
        try:
            return self._client.get_catchment_profile(
                catchment_id,
                period_grain=period_grain,
                period_key=period_key,
                tenant_id=effective_tenant_id,
            )
        except DataPlatformDocumentNotFoundError as err:
            raise MarketDataNotFoundError(f"Catchment profile not found for catchment_id={catchment_id}: {err}", details=err.details) from err
        except DataPlatformValidationError as err:
            raise MarketDataValidationError(f"Invalid catchment profile schema: {err}", details=err.details) from err
        except DataPlatformClientError as err:
            raise MarketDataFacadeError(f"Data platform client error: {err}", details=err.details) from err

    def get_catchment_profile_document(
        self,
        document_id: str | None = None,
        *,
        catchment_id: str | None = None,
        period_grain: PeriodGrain | str | None = None,
        period_key: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> CatchmentProfileDocument:
        """Authorized read of a complete CatchmentProfileDocument."""
        effective_tenant_id = self._authorize_read("catchment_profile_document", document_id or catchment_id, tenant_id=tenant_id, principal=principal)
        try:
            return self._client.get_catchment_profile_document(
                document_id=document_id,
                catchment_id=catchment_id,
                period_grain=period_grain,
                period_key=period_key,
                tenant_id=effective_tenant_id,
            )
        except DataPlatformDocumentNotFoundError as err:
            raise MarketDataNotFoundError(f"Catchment profile document not found: {err}", details=err.details) from err
        except DataPlatformValidationError as err:
            raise MarketDataValidationError(f"Invalid catchment profile document schema: {err}", details=err.details) from err
        except DataPlatformClientError as err:
            raise MarketDataFacadeError(f"Data platform client error: {err}", details=err.details) from err

    # -----------------------------------------------------------------------
    # Product Reads: Property & Listing Observation (emgi.property-observation.v1)
    # -----------------------------------------------------------------------

    def get_property_entity(
        self,
        property_id: str,
        *,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> PropertyEntity:
        """Authorized read of a canonical real estate property entity."""
        effective_tenant_id = self._authorize_read("property_entity", property_id, tenant_id=tenant_id, principal=principal)
        try:
            return self._client.get_property_entity(property_id, tenant_id=effective_tenant_id)
        except DataPlatformDocumentNotFoundError as err:
            raise MarketDataNotFoundError(f"Property entity not found for property_id={property_id}: {err}", details=err.details) from err
        except DataPlatformValidationError as err:
            raise MarketDataValidationError(f"Invalid property entity schema: {err}", details=err.details) from err
        except DataPlatformClientError as err:
            raise MarketDataFacadeError(f"Data platform client error: {err}", details=err.details) from err

    def get_listing_observation(
        self,
        listing_id: str,
        *,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> PropertyListingObservation:
        """Authorized read of a property listing observation."""
        effective_tenant_id = self._authorize_read("listing_observation", listing_id, tenant_id=tenant_id, principal=principal)
        try:
            return self._client.get_listing_observation(listing_id, tenant_id=effective_tenant_id)
        except DataPlatformDocumentNotFoundError as err:
            raise MarketDataNotFoundError(f"Listing observation not found for listing_id={listing_id}: {err}", details=err.details) from err
        except DataPlatformValidationError as err:
            raise MarketDataValidationError(f"Invalid listing observation schema: {err}", details=err.details) from err
        except DataPlatformClientError as err:
            raise MarketDataFacadeError(f"Data platform client error: {err}", details=err.details) from err

    def get_property_observation_document(
        self,
        document_id: str | None = None,
        *,
        property_id: str | None = None,
        listing_id: str | None = None,
        tenant_id: str | None = None,
        principal: Principal | None = None,
    ) -> PropertyObservationDocument:
        """Authorized read of a complete PropertyObservationDocument."""
        effective_tenant_id = self._authorize_read("property_observation_document", document_id or property_id or listing_id, tenant_id=tenant_id, principal=principal)
        try:
            return self._client.get_property_observation_document(
                document_id=document_id,
                property_id=property_id,
                listing_id=listing_id,
                tenant_id=effective_tenant_id,
            )
        except DataPlatformDocumentNotFoundError as err:
            raise MarketDataNotFoundError(f"Property observation document not found: {err}", details=err.details) from err
        except DataPlatformValidationError as err:
            raise MarketDataValidationError(f"Invalid property observation document schema: {err}", details=err.details) from err
        except DataPlatformClientError as err:
            raise MarketDataFacadeError(f"Data platform client error: {err}", details=err.details) from err

    # -----------------------------------------------------------------------
    # Foundation Reads: Platform Configuration & Store Performance
    # -----------------------------------------------------------------------

    def get_platform_foundation_config(
        self,
        *,
        principal: Principal | None = None,
    ) -> EMGIPlatformFoundationConfig:
        """Authorized read of EMGI Platform Foundation Configuration."""
        self._authorize_read("platform_foundation_config", "foundation", principal=principal)
        try:
            return self._client.get_platform_foundation_config()
        except DataPlatformDocumentNotFoundError as err:
            raise MarketDataNotFoundError(f"Platform foundation config not found: {err}", details=err.details) from err
        except DataPlatformValidationError as err:
            raise MarketDataValidationError(f"Invalid platform foundation config schema: {err}", details=err.details) from err
        except DataPlatformClientError as err:
            raise MarketDataFacadeError(f"Data platform client error: {err}", details=err.details) from err

    def get_store_reference(
        self,
        store_id: str,
        *,
        principal: Principal | None = None,
    ) -> StoreReference:
        """Authorized read of StoreReference."""
        self._authorize_read("store_reference", store_id, principal=principal)
        try:
            return self._client.get_store_reference(store_id)
        except DataPlatformDocumentNotFoundError as err:
            raise MarketDataNotFoundError(f"Store reference not found for store_id={store_id}: {err}", details=err.details) from err
        except DataPlatformValidationError as err:
            raise MarketDataValidationError(f"Invalid store reference schema: {err}", details=err.details) from err
        except DataPlatformClientError as err:
            raise MarketDataFacadeError(f"Data platform client error: {err}", details=err.details) from err

    def get_store_day_coverage(
        self,
        store_id: str,
        date_key: str,
        *,
        principal: Principal | None = None,
    ) -> StoreDayCoverage:
        """Authorized read of StoreDayCoverage."""
        self._authorize_read("store_coverage", f"{store_id}:{date_key}", principal=principal)
        try:
            return self._client.get_store_day_coverage(store_id, date_key)
        except DataPlatformDocumentNotFoundError as err:
            raise MarketDataNotFoundError(f"Store coverage not found for store_id={store_id}, date={date_key}: {err}", details=err.details) from err
        except DataPlatformValidationError as err:
            raise MarketDataValidationError(f"Invalid store coverage schema: {err}", details=err.details) from err
        except DataPlatformClientError as err:
            raise MarketDataFacadeError(f"Data platform client error: {err}", details=err.details) from err

    def get_store_daily_performance(
        self,
        store_id: str,
        date_key: str,
        *,
        principal: Principal | None = None,
    ) -> StoreDailyPerformance:
        """Authorized read of StoreDailyPerformance."""
        self._authorize_read("store_performance", f"{store_id}:{date_key}", principal=principal)
        try:
            return self._client.get_store_daily_performance(store_id, date_key)
        except DataPlatformDocumentNotFoundError as err:
            raise MarketDataNotFoundError(f"Store daily performance not found for store_id={store_id}, date={date_key}: {err}", details=err.details) from err
        except DataPlatformValidationError as err:
            raise MarketDataValidationError(f"Invalid store daily performance schema: {err}", details=err.details) from err
        except DataPlatformClientError as err:
            raise MarketDataFacadeError(f"Data platform client error: {err}", details=err.details) from err

    # -----------------------------------------------------------------------
    # System Diagnostics & Health
    # -----------------------------------------------------------------------

    def get_diagnostics(self) -> dict[str, Any]:
        """Return comprehensive diagnostics on the facade and underlying contract clients."""
        return {
            "facade_contract": self.contract,
            "facade_version": self.version,
            "enforce_auth": self._enforce_auth,
            "client_diagnostics": self._client.get_diagnostics(),
        }

    def check_health(self) -> dict[str, Any]:
        """Check release integrity and return facade health status."""
        try:
            integrity = self._client.verify_integrity()
            return {
                "status": "healthy",
                "contract": self.contract,
                "version": self.version,
                "integrity": integrity,
            }
        except DataPlatformIntegrityError as err:
            return {
                "status": "degraded",
                "contract": self.contract,
                "version": self.version,
                "error": str(err),
            }


class _RollbackProbeClient:
    """Read-only platform client used by the subprocess rollback contract.

    The production facade receives a generated ``DataPlatformClient``.  The
    probe intentionally supplies a tiny client double so it can run without
    credentials or network access while still exercising the same
    ``MarketDataFacade`` authorization and read dispatch path.
    """

    def get_site_market_context(self, site_id: str, **_: Any) -> dict[str, Any]:
        return {
            "contract": "emgi.site-market-context.v1",
            "site_id": site_id,
            "value": 42,
        }


def rollback_probe() -> dict[str, Any]:
    """Exercise platform-primary and legacy-fallback read-only routing.

    This test-only hook is consumed by the producer cutover verifier in a
    subprocess.  It does not instantiate provider code, access credentials,
    perform network I/O, or write snapshots.  The fallback payload is stable
    so the verifier can detect corruption across repeated rollback reads.
    """

    configured_mode = os.environ.get(FACADE_MODE_ENV, "PLATFORM_PRIMARY")
    kill_switch_active = os.environ.get(KILL_SWITCH_ENV, "false").lower() == "true"
    mode = "LEGACY_FALLBACK" if kill_switch_active else configured_mode

    if mode == "LEGACY_FALLBACK":
        source = "legacy"
        payload = {
            "contract": "odayplus.market-data-facade.v2",
            "site_id": ROLLBACK_PROBE_SITE_ID,
            "source": source,
            "value": 42,
        }
    elif mode == "PLATFORM_PRIMARY":
        source = "platform"
        facade = MarketDataFacade(client=_RollbackProbeClient(), enforce_auth=False)
        payload = facade.get_site_market_context(ROLLBACK_PROBE_SITE_ID)
        payload = {**payload, "source": source}
    else:
        raise ValueError(f"Unsupported facade mode for rollback probe: {mode}")

    return {
        "mode": mode,
        "source": source,
        "payload": payload,
        "writes": 0,
    }


__all__ = [
    "ALLOWED_MARKET_DATA_ROLES",
    "FACADE_CONTRACT",
    "FACADE_VERSION",
    "MarketDataAuthorizationError",
    "MarketDataFacade",
    "MarketDataFacadeError",
    "MarketDataNotFoundError",
    "MarketDataValidationError",
    "rollback_probe",
]
