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

# ---------------------------------------------------------------------------
# Reversible consumer cutover switch (ODP-XR-CUTOVER-PREP-002)
# ---------------------------------------------------------------------------
# One env-driven switch decides, for every consumer of legacy external
# ingestion, whether odayplus still fetches (`LEGACY_ONLY`), fetches while the
# platform read path is exercised alongside it (`DUAL_RUN`), or has handed the
# datasets over entirely (`PLATFORM_PRIMARY`).
#
# It lives here rather than in each consumer because the API trigger, the
# scheduler tick and the worker handler have to agree on the answer. A
# scheduler that still enqueues while the worker dead-letters produces nothing
# but dead letters, so three independent readings of the environment is the one
# failure mode a cutover switch may not have.
#
# The default is `LEGACY_ONLY`: this task prepares the cutover and must not
# perform it. Nothing is disabled until an operator sets the mode, and a later
# activation task authorizes that.

FACADE_MODE_ENV = "ODAY_MARKET_DATA_FACADE_MODE"
KILL_SWITCH_ENV = "ODAY_MARKET_DATA_KILL_SWITCH_ACTIVE"

CUTOVER_MODE_LEGACY_ONLY = "LEGACY_ONLY"
CUTOVER_MODE_DUAL_RUN = "DUAL_RUN"
CUTOVER_MODE_PLATFORM_PRIMARY = "PLATFORM_PRIMARY"

CUTOVER_MODES = (
    CUTOVER_MODE_LEGACY_ONLY,
    CUTOVER_MODE_DUAL_RUN,
    CUTOVER_MODE_PLATFORM_PRIMARY,
)
DEFAULT_CUTOVER_MODE = CUTOVER_MODE_LEGACY_ONLY

#: PR #970 named the rolled-back state `LEGACY_FALLBACK`. It describes the same
#: consumer behaviour as `LEGACY_ONLY`, so it resolves to it instead of
#: becoming a fourth mode nobody can tell apart from the third.
_CUTOVER_MODE_ALIASES = {"LEGACY_FALLBACK": CUTOVER_MODE_LEGACY_ONLY}

#: Modes in which odayplus still owns external fetch: the manual API trigger,
#: the scheduler tick and the worker handler all stay live.
_LEGACY_FETCH_MODES = frozenset({CUTOVER_MODE_LEGACY_ONLY, CUTOVER_MODE_DUAL_RUN})

#: Modes in which the data-platform snapshot read path is consulted. `DUAL_RUN`
#: reads it for comparison only; `PLATFORM_PRIMARY` serves it.
_PLATFORM_READ_MODES = frozenset({CUTOVER_MODE_DUAL_RUN, CUTOVER_MODE_PLATFORM_PRIMARY})

ROLLBACK_PROBE_SITE_ID = "cutover-probe-site"

#: Freshness SLA reported for a platform-sourced snapshot row. The published
#: release carries no per-source SLA of its own, so the consumer states the one
#: it holds the platform to rather than inventing a number per request.
DEFAULT_SNAPSHOT_FRESHNESS_SLA_SECONDS = 24 * 60 * 60

#: Release arms the pinned client verifies, each reported as one snapshot row.
PLATFORM_SNAPSHOT_ARMS = ("foundation", "product")

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


def _env_source(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def kill_switch_active(env: Mapping[str, str] | None = None) -> bool:
    """True when the operator has pulled the rollback lever."""
    raw = str(_env_source(env).get(KILL_SWITCH_ENV, "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def resolve_cutover_mode(env: Mapping[str, str] | None = None) -> str:
    """Return the effective cutover mode for this process, right now.

    Callers must resolve per request / per tick rather than caching at start-up:
    the point of the switch is that an operator can move a running deployment
    back to legacy without a redeploy.

    The kill switch is evaluated before the configured mode is validated. It is
    the emergency lever, so a rollback must not be blocked by a typo in the very
    variable the operator is rolling back from.

    Raises:
        MarketDataValidationError: the configured mode is not one this
            deployment knows. Refusing beats guessing: silently continuing to
            fetch while an operator believes they cut over is the failure this
            switch exists to prevent.
    """
    source = _env_source(env)
    if kill_switch_active(source):
        return CUTOVER_MODE_LEGACY_ONLY

    raw = str(source.get(FACADE_MODE_ENV, "") or "").strip()
    if not raw:
        return DEFAULT_CUTOVER_MODE
    normalized = raw.upper()
    normalized = _CUTOVER_MODE_ALIASES.get(normalized, normalized)
    if normalized not in CUTOVER_MODES:
        raise MarketDataValidationError(
            f"Unsupported {FACADE_MODE_ENV}={raw!r}: expected one of "
            + ", ".join(CUTOVER_MODES),
            details={
                "env_var": FACADE_MODE_ENV,
                "value": raw,
                "supported": list(CUTOVER_MODES),
            },
        )
    return normalized


def legacy_external_fetch_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True while odayplus still owns external fetch (manual, scheduled, worker)."""
    return resolve_cutover_mode(env) in _LEGACY_FETCH_MODES


def platform_read_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True when the data-platform snapshot read path should be consulted."""
    return resolve_cutover_mode(env) in _PLATFORM_READ_MODES


def cutover_state(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Operator-facing description of the switch, for responses and logs.

    Reports both the configured and the effective mode so a pulled kill switch
    is visible as a rollback rather than as someone having edited the config.
    """
    source = _env_source(env)
    killed = kill_switch_active(source)
    effective = resolve_cutover_mode(source)
    configured = str(source.get(FACADE_MODE_ENV, "") or "").strip().upper()
    configured = _CUTOVER_MODE_ALIASES.get(configured, configured) or DEFAULT_CUTOVER_MODE
    return {
        "mode": effective,
        "configured_mode": configured,
        "kill_switch_active": killed,
        "legacy_external_fetch_enabled": effective in _LEGACY_FETCH_MODES,
        "platform_read_enabled": effective in _PLATFORM_READ_MODES,
    }


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

    # -----------------------------------------------------------------------
    # Reversible Data-Platform Snapshot Read Path (ODP-XR-CUTOVER-PREP-002)
    # -----------------------------------------------------------------------

    def get_platform_snapshot(
        self,
        *,
        correlation_id: str = "",
        freshness_sla_seconds: int = DEFAULT_SNAPSHOT_FRESHNESS_SLA_SECONDS,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Describe the published platform snapshot this consumer reads.

        This is the read path the cutover moves ``/external-data/freshness``
        onto: instead of the provenance of a run odayplus performed itself, it
        reports the provenance of the release odayplus consumes. Each verified
        release arm becomes one freshness row in the same wire shape the legacy
        route already serves, so a dual run compares like with like and the
        cutover is a change of source rather than a change of contract.

        Like :meth:`check_health` and :meth:`get_diagnostics` this reads release
        identity only -- no tenant-scoped or subject data -- so it takes no
        principal. Callers that expose it over HTTP gate it with their own
        product authorization, which is what the API route does.

        No credentials, no fetch, no writes; ``writes`` is reported as ``0`` so
        the read-only claim is asserted in the payload a verifier reads back,
        not only in this docstring.
        """
        state = cutover_state(env)
        try:
            integrity = self._client.verify_integrity()
            status = "healthy"
            error: str | None = None
        except DataPlatformIntegrityError as err:
            integrity = {}
            status = "degraded"
            error = str(err)

        snapshot: dict[str, Any] = {
            "contract": self.contract,
            "version": self.version,
            "source": "data_platform",
            "status": status,
            "mode": state["mode"],
            "kill_switch_active": state["kill_switch_active"],
            "release": integrity,
            "freshness": _platform_freshness_rows(
                integrity,
                correlation_id=correlation_id,
                freshness_sla_seconds=freshness_sla_seconds,
            ),
            "writes": 0,
        }
        if error is not None:
            snapshot["error"] = error
        return snapshot

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


def _platform_freshness_rows(
    integrity: Mapping[str, Any],
    *,
    correlation_id: str,
    freshness_sla_seconds: int,
) -> list[dict[str, Any]]:
    """Map a verified release report onto legacy-shaped freshness rows.

    A row is emitted only for an arm that reported a release id. An arm the
    client could not verify has no snapshot to describe, and inventing a row
    for it would report the cutover as healthier than it is.

    ``provider_observed_at`` / ``ingested_at`` stay ``None`` on purpose: those
    are timestamps of a fetch odayplus performed, and after the cutover it
    performs none. The release id is the provenance the consumer actually holds.
    """
    rows: list[dict[str, Any]] = []
    for arm in PLATFORM_SNAPSHOT_ARMS:
        report = integrity.get(arm) or {}
        release_id = str(report.get("release_id") or "").strip()
        if not release_id:
            continue
        compatible = bool(report.get("compatible"))
        rows.append(
            {
                "provider_id": f"data_platform.{arm}",
                "source_snapshot_id": release_id,
                "data_status": "FRESH" if compatible else "STALE",
                "provider_observed_at": None,
                "ingested_at": None,
                "freshness_sla_seconds": freshness_sla_seconds,
                "correlation_id": correlation_id,
                "quality_flags": [] if compatible else ["release_incompatible"],
            }
        )
    return rows


class _RollbackProbeClient:
    """Read-only platform client double used by the subprocess rollback contract.

    The production facade receives a generated ``DataPlatformClient``. The probe
    intentionally supplies a tiny client double so it can run without
    credentials or network access while still exercising the same
    ``MarketDataFacade`` authorization and read dispatch path.
    """

    def get_site_market_context(self, site_id: str, **_: Any) -> dict[str, Any]:
        return {
            "contract": "emgi.site-market-context.v1",
            "site_id": site_id,
            "value": 42,
        }

    def verify_integrity(self) -> dict[str, Any]:
        return {
            "status": "healthy",
            "foundation": {
                "compatible": True,
                "release_id": "rollback-probe-foundation",
                "semantic_version": "0.0.0",
                "contracts_checked": 0,
            },
            "product": {
                "compatible": True,
                "release_id": "rollback-probe-product",
                "semantic_version": "0.0.0",
                "contracts_checked": 0,
            },
        }


def rollback_probe(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Exercise platform-primary, dual-run and legacy-fallback read-only routing.

    This test-only hook is consumed by the producer cutover verifier in a
    subprocess. It does not instantiate provider code, access credentials,
    perform network I/O, or write snapshots. The legacy payload is stable so the
    verifier can detect corruption across repeated rollback reads.

    ``LEGACY_ONLY`` (and its ``LEGACY_FALLBACK`` alias) is the default, so an
    unconfigured deployment probes as rolled back rather than as cut over.
    """
    mode = resolve_cutover_mode(env)
    legacy_payload = {
        "contract": FACADE_CONTRACT,
        "site_id": ROLLBACK_PROBE_SITE_ID,
        "source": "legacy",
        "value": 42,
    }

    if mode == CUTOVER_MODE_LEGACY_ONLY:
        return {
            "mode": mode,
            "source": "legacy",
            "payload": legacy_payload,
            "writes": 0,
        }

    facade = MarketDataFacade(client=_RollbackProbeClient(), enforce_auth=False)
    platform_payload = {
        **facade.get_site_market_context(ROLLBACK_PROBE_SITE_ID),
        "source": "platform",
    }
    if mode == CUTOVER_MODE_PLATFORM_PRIMARY:
        return {
            "mode": mode,
            "source": "platform",
            "payload": platform_payload,
            "writes": 0,
        }

    # DUAL_RUN: both arms are read and returned side by side. The verifier
    # compares them; neither one is authoritative yet, which is the whole point
    # of running the cutover in this mode before selecting PLATFORM_PRIMARY.
    return {
        "mode": mode,
        "source": "legacy",
        "payload": legacy_payload,
        "platform_payload": platform_payload,
        "snapshot": facade.get_platform_snapshot(env=env),
        "writes": 0,
    }


__all__ = [
    "ALLOWED_MARKET_DATA_ROLES",
    "CUTOVER_MODES",
    "CUTOVER_MODE_DUAL_RUN",
    "CUTOVER_MODE_LEGACY_ONLY",
    "CUTOVER_MODE_PLATFORM_PRIMARY",
    "DEFAULT_CUTOVER_MODE",
    "DEFAULT_SNAPSHOT_FRESHNESS_SLA_SECONDS",
    "FACADE_CONTRACT",
    "FACADE_MODE_ENV",
    "FACADE_VERSION",
    "KILL_SWITCH_ENV",
    "MarketDataAuthorizationError",
    "MarketDataFacade",
    "MarketDataFacadeError",
    "MarketDataNotFoundError",
    "MarketDataValidationError",
    "PLATFORM_SNAPSHOT_ARMS",
    "ROLLBACK_PROBE_SITE_ID",
    "cutover_state",
    "kill_switch_active",
    "legacy_external_fetch_enabled",
    "platform_read_enabled",
    "resolve_cutover_mode",
    "rollback_probe",
]
