"""External provider registry and startup validation.

This registry is intentionally metadata-only. Live adapter implementation,
scheduling, quota handling, and licensing gates are separate fleet tasks; this
module only declares provider classes, auth modes, secret env var names, and a
fail-closed startup check for live-provider mode.
"""

from __future__ import annotations

import os
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum

from shared.observability import new_correlation_id


class ExternalProviderMode(StrEnum):
    FIXTURE = "fixture"
    LIVE = "live"


class ProviderCategory(StrEnum):
    CONTROL_PLANE = "control_plane"
    LISTING = "listing"
    POI = "poi"
    GEOCODE = "geocode"
    ADMIN_BOUNDARY = "admin_boundary"
    COMPETITOR_MANUAL = "competitor_manual"
    STORE_OPENING_AUTHORITY = "store_opening_authority"


class ProviderAuthMode(StrEnum):
    API_KEY = "api_key"
    BEARER_TOKEN = "bearer_token"
    OAUTH2_CLIENT_CREDENTIALS = "oauth2_client_credentials"
    MANUAL_ATTESTATION = "manual_attestation"


@dataclass(frozen=True)
class ProviderCredential:
    """Credential metadata. ``env_var`` is the contract; values are never stored."""

    env_var: str
    auth_mode: ProviderAuthMode
    required_in_live: bool = True
    status_env_var: str | None = None


@dataclass(frozen=True)
class ProviderLicense:
    attribution: str
    expires_on: date | None = None
    allowed_in_production: bool = True
    downstream_use_flags: tuple[str, ...] = ("internal_decisioning",)
    export_allowed: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "attribution": self.attribution,
            "expires_on": self.expires_on.isoformat() if self.expires_on else None,
            "allowed_in_production": self.allowed_in_production,
            "downstream_use_flags": list(self.downstream_use_flags),
            "export_allowed": self.export_allowed,
        }


@dataclass(frozen=True)
class ExternalProviderDefinition:
    provider_id: str
    category: ProviderCategory
    source_contract_id: str
    connector_class: str
    provider_class: str
    credentials: tuple[ProviderCredential, ...]
    license: ProviderLicense
    endpoint_env_var: str | None = None
    enabled_in_fixture: bool = True
    metadata: Mapping[str, str] = field(default_factory=dict)
    #: Task id that retired this provider's concrete adapter from odayplus.
    #: The declaration itself stays — ``modules/external_data/connectors/**`` is
    #: frozen legacy producer surface, and deleting an entry would silently drop
    #: the record of a credential this deployment once held. What it means is
    #: that ``provider_class`` no longer resolves and the deployment preflight
    #: must refuse to select this provider for live production.
    decommissioned_by: str = ""

    @property
    def decommissioned(self) -> bool:
        return bool(self.decommissioned_by)

    @property
    def required_env_vars(self) -> tuple[str, ...]:
        return tuple(c.env_var for c in self.credentials if c.required_in_live)

    @property
    def auth_modes(self) -> tuple[ProviderAuthMode, ...]:
        return tuple(c.auth_mode for c in self.credentials)


@dataclass(frozen=True)
class ProviderValidationError:
    provider_id: str
    category: ProviderCategory
    env_var: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "provider_id": self.provider_id,
            "category": self.category.value,
            "env_var": self.env_var,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class ProviderValidationResult:
    mode: ExternalProviderMode
    correlation_id: str
    providers: tuple[ExternalProviderDefinition, ...]
    errors: tuple[ProviderValidationError, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def secret_inventory(self) -> dict[str, dict[str, object]]:
        return {
            provider.provider_id: {
                "category": provider.category.value,
                "source_contract_id": provider.source_contract_id,
                "connector_class": provider.connector_class,
                "provider_class": provider.provider_class,
                "decommissioned_by": provider.decommissioned_by,
                "auth_modes": [mode.value for mode in provider.auth_modes],
                "env_vars": list(provider.required_env_vars),
                "endpoint_env_var": provider.endpoint_env_var,
                "license": provider.license.to_dict(),
            }
            for provider in self.providers
        }


class ExternalProviderConfigError(RuntimeError):
    """Raised when live provider startup validation fails closed."""

    def __init__(self, result: ProviderValidationResult) -> None:
        self.result = result
        env_vars = ", ".join(error.env_var for error in result.errors)
        super().__init__(
            "External provider startup validation failed "
            f"(mode={result.mode.value}, correlation_id={result.correlation_id}, "
            f"missing_or_invalid_env={env_vars})"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.result.mode.value,
            "correlation_id": self.result.correlation_id,
            "errors": [error.to_dict() for error in self.result.errors],
        }


LIVE_MODE_ENV_VAR = "ODP_EXTERNAL_PROVIDER_MODE"
PRODUCTION_PROVIDER_IDS_ENV_VAR = "ODP_PRODUCTION_PROVIDER_IDS"
# Providers that MUST be live-configured before the External Data Platform may
# run in production live mode.
#
# The set used to hold the enrichment/reference sources with a live upstream:
# geocode, POI and admin boundary. ``listing.partner_feed`` was deliberately
# excluded, because it needs a signed licensed-data partner that does not exist
# yet and requiring it would have blocked live mode on a business dependency.
# See docs/design/EXTERNAL_PROVIDER_LIVE_REQUIRED_RECONCILIATION.md.
#
# XR-CUTOVER-001 emptied it. Geocode, POI and admin-boundary are exactly
# the adapters the cutover retired from odayplus: the ``tgos``, ``open_poi`` and
# ``ris_nlsc`` domains they fed are ingested by oday-data-platform and read
# through the market data facade. Requiring a live endpoint and credential for a
# provider this repository can no longer call would make every production deploy
# demand secrets that nothing here can use — the opposite of decommissioning
# them. The set stays as a named, non-empty-able contract rather than being
# deleted, so a future live-required provider has one place to be declared.
REQUIRED_PRODUCTION_PROVIDER_IDS: frozenset[str] = frozenset()
INVALID_AUTH_STATUSES = {"expired", "unauthorized", "revoked", "invalid"}
PLACEHOLDER_VALUES = {"", "changeme", "change-me", "todo", "placeholder", "dummy", "example"}


PROVIDER_REGISTRY: tuple[ExternalProviderDefinition, ...] = (
    ExternalProviderDefinition(
        provider_id="listing.partner_feed",
        category=ProviderCategory.LISTING,
        source_contract_id="listing_raw_snapshot",
        connector_class="modules.external_data.connectors.external.ListingConnector",
        provider_class="",
        decommissioned_by="XR-CUTOVER-001",
        credentials=(
            ProviderCredential(
                env_var="ODP_LISTING_PROVIDER_API_KEY",
                auth_mode=ProviderAuthMode.API_KEY,
                status_env_var="ODP_LISTING_PROVIDER_AUTH_STATUS",
            ),
        ),
        endpoint_env_var="ODP_LISTING_PROVIDER_FEED_URL",
        license=ProviderLicense(
            attribution="Listing partner feed; internal expansion decisioning only",
            downstream_use_flags=("internal_decisioning", "derived_features"),
            export_allowed=False,
        ),
    ),
    ExternalProviderDefinition(
        provider_id="poi.commercial_api",
        category=ProviderCategory.POI,
        source_contract_id="poi_snapshot",
        connector_class="modules.external_data.connectors.external.PoiConnector",
        provider_class="",
        decommissioned_by="XR-CUTOVER-001",
        credentials=(
            ProviderCredential(
                env_var="ODP_POI_PROVIDER_API_KEY",
                auth_mode=ProviderAuthMode.API_KEY,
                status_env_var="ODP_POI_PROVIDER_AUTH_STATUS",
            ),
        ),
        endpoint_env_var="ODP_POI_PROVIDER_URL",
        license=ProviderLicense(
            attribution="Commercial POI provider",
            downstream_use_flags=("internal_decisioning", "map_visualization"),
            export_allowed=False,
        ),
    ),
    ExternalProviderDefinition(
        provider_id="geocode.primary_api",
        category=ProviderCategory.GEOCODE,
        source_contract_id="geocode_result_snapshot",
        connector_class="modules.external_data.connectors.external.GeocodeConnector",
        provider_class="",
        decommissioned_by="XR-CUTOVER-001",
        credentials=(
            ProviderCredential(
                env_var="ODP_GEOCODE_PROVIDER_API_KEY",
                auth_mode=ProviderAuthMode.API_KEY,
                status_env_var="ODP_GEOCODE_PROVIDER_AUTH_STATUS",
            ),
        ),
        endpoint_env_var="ODP_GEOCODE_PROVIDER_URL",
        license=ProviderLicense(
            attribution="Primary geocode API",
            downstream_use_flags=("internal_decisioning", "geocode_enrichment"),
            export_allowed=False,
        ),
    ),
    ExternalProviderDefinition(
        provider_id="admin_boundary.official_dataset",
        category=ProviderCategory.ADMIN_BOUNDARY,
        source_contract_id="admin_boundary_snapshot",
        connector_class="modules.external_data.connectors.external.AdminBoundaryConnector",
        provider_class="",
        decommissioned_by="XR-CUTOVER-001",
        credentials=(
            ProviderCredential(
                env_var="ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN",
                auth_mode=ProviderAuthMode.BEARER_TOKEN,
                status_env_var="ODP_ADMIN_BOUNDARY_PROVIDER_AUTH_STATUS",
            ),
        ),
        endpoint_env_var="ODP_ADMIN_BOUNDARY_PROVIDER_URL",
        license=ProviderLicense(
            attribution="Official admin boundary dataset",
            downstream_use_flags=("internal_decisioning", "map_visualization", "audit_evidence"),
            export_allowed=True,
        ),
    ),
    ExternalProviderDefinition(
        provider_id="competitor.manual_source",
        category=ProviderCategory.COMPETITOR_MANUAL,
        source_contract_id="competitor_store_snapshot",
        connector_class="modules.external_data.connectors.external.CompetitorStoreConnector",
        provider_class="modules.external_data.providers.manual.CompetitorManualSourceProvider",
        credentials=(
            ProviderCredential(
                env_var="ODP_COMPETITOR_MANUAL_SOURCE_ATTESTATION",
                auth_mode=ProviderAuthMode.MANUAL_ATTESTATION,
                status_env_var="ODP_COMPETITOR_MANUAL_SOURCE_STATUS",
            ),
        ),
        license=ProviderLicense(
            attribution="Manual competitor observation; no production automated use",
            allowed_in_production=False,
            downstream_use_flags=("manual_review",),
            export_allowed=False,
        ),
        metadata={"source_type": "manual"},
    ),
    ExternalProviderDefinition(
        provider_id="store_opening_authority",
        category=ProviderCategory.STORE_OPENING_AUTHORITY,
        source_contract_id="store_opening_authority_snapshot",
        connector_class="modules.external_data.connectors.store_opening.StoreOpeningAuthorityConnector",
        provider_class="modules.external_data.connectors.store_opening.StoreOpeningAuthorityConnector",
        credentials=(
            ProviderCredential(
                env_var="ODP_STORE_OPENING_AUTHORITY_ATTESTATION",
                auth_mode=ProviderAuthMode.MANUAL_ATTESTATION,
                status_env_var="ODP_STORE_OPENING_AUTHORITY_STATUS",
                required_in_live=False,
            ),
        ),
        license=ProviderLicense(
            attribution="Official store opening date authority",
            allowed_in_production=True,
            downstream_use_flags=("internal_decisioning", "audit_evidence"),
            export_allowed=True,
        ),
        metadata={"source_type": "official_registry"},
    ),
)


def provider_registry() -> tuple[ExternalProviderDefinition, ...]:
    return PROVIDER_REGISTRY


def provider_secret_inventory() -> dict[str, dict[str, object]]:
    return ProviderValidationResult(
        mode=ExternalProviderMode.FIXTURE,
        correlation_id="inventory",
        providers=PROVIDER_REGISTRY,
    ).secret_inventory()


def external_provider_mode(env: Mapping[str, str] | None = None) -> ExternalProviderMode:
    raw = (env or os.environ).get(LIVE_MODE_ENV_VAR, ExternalProviderMode.FIXTURE.value)
    normalized = raw.strip().lower()
    if normalized in {"", "fixture", "fixtures", "stub", "source-stub", "source_stub"}:
        return ExternalProviderMode.FIXTURE
    if normalized == "live":
        return ExternalProviderMode.LIVE
    raise ValueError(f"{LIVE_MODE_ENV_VAR} must be fixture or live; got {raw!r}")


def validate_external_providers(
    *,
    env: Mapping[str, str] | None = None,
    mode: ExternalProviderMode | str | None = None,
    correlation_id: str | None = None,
) -> ProviderValidationResult:
    source_env = env or os.environ
    resolved_mode = (
        ExternalProviderMode(mode)
        if isinstance(mode, str)
        else mode
        if mode is not None
        else external_provider_mode(source_env)
    )
    corr = correlation_id or new_correlation_id()
    errors: list[ProviderValidationError] = []
    deploy_env = (
        source_env.get("ODP_DEPLOY_ENV", source_env.get("APP_ENV", "development")).strip().lower()
    )
    production_like = deploy_env in {"prod", "production"}
    now = _today_utc(source_env)
    providers = PROVIDER_REGISTRY

    if resolved_mode is ExternalProviderMode.LIVE:
        raw_provider_ids = source_env.get(PRODUCTION_PROVIDER_IDS_ENV_VAR, "")
        selected_provider_ids = {
            provider_id.strip()
            for provider_id in raw_provider_ids.split(",")
            if provider_id.strip()
        }
        known_provider_ids = {provider.provider_id for provider in PROVIDER_REGISTRY}
        unknown_provider_ids = selected_provider_ids - known_provider_ids
        if production_like and not selected_provider_ids:
            errors.append(
                ProviderValidationError(
                    provider_id="provider_registry",
                    category=ProviderCategory.CONTROL_PLANE,
                    env_var=PRODUCTION_PROVIDER_IDS_ENV_VAR,
                    code="provider_allowlist_required",
                    message=("Production live mode requires an explicit provider allowlist."),
                )
            )
            providers = ()
        elif selected_provider_ids:
            for provider_id in sorted(unknown_provider_ids):
                errors.append(
                    ProviderValidationError(
                        provider_id=provider_id,
                        category=ProviderCategory.CONTROL_PLANE,
                        env_var=PRODUCTION_PROVIDER_IDS_ENV_VAR,
                        code="unknown_provider",
                        message="The production provider allowlist contains an unknown provider ID.",
                    )
                )
            providers = tuple(
                provider
                for provider in PROVIDER_REGISTRY
                if provider.provider_id in selected_provider_ids
            )
            for provider in providers:
                if not provider.decommissioned:
                    continue
                # The allowlist is the operator's statement of what this
                # deployment runs live. Naming a retired adapter there cannot be
                # satisfied by any credential, so it fails closed here rather
                # than surfacing later as a missing-secret error that invites
                # someone to provision one.
                errors.append(
                    ProviderValidationError(
                        provider_id=provider.provider_id,
                        category=provider.category,
                        env_var=PRODUCTION_PROVIDER_IDS_ENV_VAR,
                        code="provider_decommissioned",
                        message=(
                            f"{provider.provider_id} was decommissioned by "
                            f"{provider.decommissioned_by}; odayplus holds no "
                            "adapter for it. Read the dataset from "
                            "oday-data-platform through the market data facade."
                        ),
                    )
                )
        if production_like and selected_provider_ids:
            missing_required_ids = REQUIRED_PRODUCTION_PROVIDER_IDS - selected_provider_ids
            for provider_id in sorted(missing_required_ids):
                errors.append(
                    ProviderValidationError(
                        provider_id=provider_id,
                        category=ProviderCategory.CONTROL_PLANE,
                        env_var=PRODUCTION_PROVIDER_IDS_ENV_VAR,
                        code="required_provider_not_selected",
                        message=(
                            "Production live mode requires this provider in the "
                            "explicit provider allowlist."
                        ),
                    )
                )

        for provider in providers:
            if provider.decommissioned:
                # Credential revocation, expressed in code: nothing in this
                # repository can call a retired adapter, so live mode must not
                # ask a deployment to hold its endpoint or secret. Selecting one
                # explicitly is already an error above; reaching it implicitly
                # (live mode with no allowlist) simply requires nothing.
                continue
            if production_like and not provider.license.allowed_in_production:
                errors.append(
                    ProviderValidationError(
                        provider_id=provider.provider_id,
                        category=provider.category,
                        env_var="ODP_DEPLOY_ENV",
                        code="license_blocked",
                        message="Provider license does not allow production automated use.",
                    )
                )
            if provider.license.expires_on is not None and provider.license.expires_on < now:
                errors.append(
                    ProviderValidationError(
                        provider_id=provider.provider_id,
                        category=provider.category,
                        env_var="provider_license",
                        code="license_expired",
                        message="Provider license is expired; renew before live use.",
                    )
                )
            if production_like and provider.endpoint_env_var is not None:
                endpoint = source_env.get(provider.endpoint_env_var, "").strip()
                parsed_endpoint = urllib.parse.urlsplit(endpoint)
                if not endpoint:
                    errors.append(
                        ProviderValidationError(
                            provider_id=provider.provider_id,
                            category=provider.category,
                            env_var=provider.endpoint_env_var,
                            code="missing_endpoint",
                            message=("Required production provider endpoint is missing."),
                        )
                    )
                elif (
                    parsed_endpoint.scheme not in {"http", "https"}
                    or not parsed_endpoint.netloc
                    or parsed_endpoint.username is not None
                    or parsed_endpoint.password is not None
                ):
                    errors.append(
                        ProviderValidationError(
                            provider_id=provider.provider_id,
                            category=provider.category,
                            env_var=provider.endpoint_env_var,
                            code="invalid_endpoint",
                            message=(
                                "Production provider endpoint must be an absolute "
                                "HTTP(S) URL without embedded credentials."
                            ),
                        )
                    )
                elif parsed_endpoint.scheme != "https" and parsed_endpoint.hostname not in {
                    "127.0.0.1",
                    "::1",
                    "localhost",
                }:
                    errors.append(
                        ProviderValidationError(
                            provider_id=provider.provider_id,
                            category=provider.category,
                            env_var=provider.endpoint_env_var,
                            code="insecure_endpoint",
                            message=(
                                "Production provider endpoint must use HTTPS; "
                                "plain HTTP is allowed only for loopback tests."
                            ),
                        )
                    )
            for credential in provider.credentials:
                if not credential.required_in_live:
                    continue
                value = source_env.get(credential.env_var, "")
                if _is_missing_or_placeholder(value):
                    errors.append(
                        ProviderValidationError(
                            provider_id=provider.provider_id,
                            category=provider.category,
                            env_var=credential.env_var,
                            code="missing_credential",
                            message=(
                                "Required live provider credential is missing or placeholder; "
                                "set the named env var before startup."
                            ),
                        )
                    )
                    continue
                if credential.status_env_var:
                    status = source_env.get(credential.status_env_var, "").strip().lower()
                    if status in INVALID_AUTH_STATUSES:
                        errors.append(
                            ProviderValidationError(
                                provider_id=provider.provider_id,
                                category=provider.category,
                                env_var=credential.status_env_var,
                                code=f"credential_{status}",
                                message=(
                                    "Live provider credential status is not usable; "
                                    "rotate or reauthorize before startup."
                                ),
                            )
                        )

    return ProviderValidationResult(
        mode=resolved_mode,
        correlation_id=corr,
        providers=providers,
        errors=tuple(errors),
    )


def validate_external_providers_or_raise(
    *,
    env: Mapping[str, str] | None = None,
    mode: ExternalProviderMode | str | None = None,
    correlation_id: str | None = None,
) -> ProviderValidationResult:
    result = validate_external_providers(env=env, mode=mode, correlation_id=correlation_id)
    if not result.ok:
        raise ExternalProviderConfigError(result)
    return result


def _is_missing_or_placeholder(value: str) -> bool:
    normalized = value.strip()
    return normalized.lower() in PLACEHOLDER_VALUES


def provider_export_allowed(provider_id: str) -> bool:
    return _provider_by_id(provider_id).license.export_allowed


def provider_downstream_use_flags(provider_id: str) -> tuple[str, ...]:
    return _provider_by_id(provider_id).license.downstream_use_flags


def _provider_by_id(provider_id: str) -> ExternalProviderDefinition:
    for provider in PROVIDER_REGISTRY:
        if provider.provider_id == provider_id:
            return provider
    raise ValueError(f"unknown external provider {provider_id}")


def _today_utc(env: Mapping[str, str]) -> date:
    override = env.get("ODP_PROVIDER_LICENSE_TODAY")
    if override:
        return datetime.fromisoformat(override.replace("Z", "+00:00")).date()
    return datetime.now(UTC).date()
