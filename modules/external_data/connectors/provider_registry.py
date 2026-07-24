"""External provider registry and startup validation.

This registry is intentionally metadata-only. Live adapter implementation,
scheduling, quota handling, and licensing gates are separate fleet tasks; this
module only declares provider classes, auth modes, secret env var names, and a
fail-closed startup check for live-provider mode.
"""

from __future__ import annotations

import os
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
    enabled_in_fixture: bool = True
    metadata: Mapping[str, str] = field(default_factory=dict)

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
                "auth_modes": [mode.value for mode in provider.auth_modes],
                "env_vars": list(provider.required_env_vars),
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
INVALID_AUTH_STATUSES = {"expired", "unauthorized", "revoked", "invalid"}
PLACEHOLDER_VALUES = {"", "changeme", "change-me", "todo", "placeholder", "dummy", "example"}


PROVIDER_REGISTRY: tuple[ExternalProviderDefinition, ...] = (
    ExternalProviderDefinition(
        provider_id="listing.partner_feed",
        category=ProviderCategory.LISTING,
        source_contract_id="listing_raw_snapshot",
        connector_class="modules.external_data.connectors.external.ListingConnector",
        provider_class="modules.external_data.providers.live.ListingPartnerFeedProvider",
        credentials=(
            ProviderCredential(
                env_var="ODP_LISTING_PROVIDER_API_KEY",
                auth_mode=ProviderAuthMode.API_KEY,
                status_env_var="ODP_LISTING_PROVIDER_AUTH_STATUS",
            ),
        ),
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
        provider_class="modules.external_data.providers.live.PoiCommercialApiProvider",
        credentials=(
            ProviderCredential(
                env_var="ODP_POI_PROVIDER_API_KEY",
                auth_mode=ProviderAuthMode.API_KEY,
                status_env_var="ODP_POI_PROVIDER_AUTH_STATUS",
            ),
        ),
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
        provider_class="modules.external_data.providers.live.PrimaryGeocodeProvider",
        credentials=(
            ProviderCredential(
                env_var="ODP_GEOCODE_PROVIDER_API_KEY",
                auth_mode=ProviderAuthMode.API_KEY,
                status_env_var="ODP_GEOCODE_PROVIDER_AUTH_STATUS",
            ),
        ),
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
        provider_class="modules.external_data.providers.live.AdminBoundaryDatasetProvider",
        credentials=(
            ProviderCredential(
                env_var="ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN",
                auth_mode=ProviderAuthMode.BEARER_TOKEN,
                status_env_var="ODP_ADMIN_BOUNDARY_PROVIDER_AUTH_STATUS",
            ),
        ),
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
    raise ValueError(
        f"{LIVE_MODE_ENV_VAR} must be fixture or live; got {raw!r}"
    )


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
    deploy_env = source_env.get("ODP_DEPLOY_ENV", source_env.get("APP_ENV", "development")).strip().lower()
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
                    message=(
                        "Production live mode requires an explicit provider allowlist."
                    ),
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
