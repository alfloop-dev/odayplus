from __future__ import annotations

import pytest

from apps.api.oday_api.main import create_app
from modules.external_data.connectors import (
    ExternalProviderConfigError,
    ExternalProviderMode,
    ProviderCategory,
    provider_downstream_use_flags,
    provider_export_allowed,
    provider_registry,
    provider_secret_inventory,
    validate_external_providers,
)
from modules.external_data.connectors.provider_registry import LIVE_MODE_ENV_VAR

REQUIRED_ENV_VARS = {
    "ODP_LISTING_PROVIDER_API_KEY",
    "ODP_POI_PROVIDER_API_KEY",
    "ODP_GEOCODE_PROVIDER_API_KEY",
    "ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN",
    "ODP_COMPETITOR_MANUAL_SOURCE_ATTESTATION",
}


def test_provider_registry_covers_live_external_source_classes() -> None:
    providers = provider_registry()

    assert {provider.category for provider in providers} == {
        ProviderCategory.LISTING,
        ProviderCategory.POI,
        ProviderCategory.GEOCODE,
        ProviderCategory.ADMIN_BOUNDARY,
        ProviderCategory.COMPETITOR_MANUAL,
    }
    assert {
        env for provider in providers for env in provider.required_env_vars
    } == REQUIRED_ENV_VARS
    assert {provider.source_contract_id for provider in providers} == {
        "listing_raw_snapshot",
        "poi_snapshot",
        "geocode_result_snapshot",
        "admin_boundary_snapshot",
        "competitor_store_snapshot",
    }
    assert all(
        provider.connector_class.startswith("modules.external_data.") for provider in providers
    )
    assert all(
        provider.provider_class.startswith("modules.external_data.providers.")
        for provider in providers
    )
    assert all(provider.license.attribution for provider in providers)
    assert all(provider.license.downstream_use_flags for provider in providers)


def test_secret_inventory_contains_names_and_auth_modes_without_values() -> None:
    inventory = provider_secret_inventory()
    rendered = repr(inventory)

    assert REQUIRED_ENV_VARS <= {
        env_var for provider in inventory.values() for env_var in provider["env_vars"]
    }
    assert "super-secret" not in rendered
    for provider in inventory.values():
        assert provider["auth_modes"]
        assert provider["provider_class"]
        assert provider["connector_class"]
        assert provider["license"]["attribution"]
        assert "export_allowed" in provider["license"]


def test_fixture_mode_validates_without_provider_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LIVE_MODE_ENV_VAR, raising=False)
    for env_var in REQUIRED_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)

    result = validate_external_providers(correlation_id="corr-fixture-1")
    app = create_app()

    assert result.ok
    assert result.mode is ExternalProviderMode.FIXTURE
    assert app.state.external_provider_validation.mode is ExternalProviderMode.FIXTURE


def test_live_mode_fails_closed_when_required_credentials_are_missing() -> None:
    env = {LIVE_MODE_ENV_VAR: "live"}

    result = validate_external_providers(env=env, correlation_id="corr-live-missing")

    assert not result.ok
    assert result.mode is ExternalProviderMode.LIVE
    assert result.correlation_id == "corr-live-missing"
    assert {error.env_var for error in result.errors} == REQUIRED_ENV_VARS
    assert {error.code for error in result.errors} == {"missing_credential"}


def test_live_mode_startup_error_includes_correlation_and_no_secret_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LIVE_MODE_ENV_VAR, "live")
    for env_var in REQUIRED_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)

    with pytest.raises(ExternalProviderConfigError) as exc_info:
        create_app()

    message = str(exc_info.value)
    assert exc_info.value.result.correlation_id
    assert "correlation_id=" in message
    assert "missing_or_invalid_env=" in message
    for env_var in REQUIRED_ENV_VARS:
        assert env_var in message


def test_live_mode_flags_expired_or_unauthorized_credentials_with_correlation() -> None:
    env = {
        LIVE_MODE_ENV_VAR: "live",
        "ODP_LISTING_PROVIDER_API_KEY": "listing-token",
        "ODP_LISTING_PROVIDER_AUTH_STATUS": "expired",
        "ODP_POI_PROVIDER_API_KEY": "poi-token",
        "ODP_GEOCODE_PROVIDER_API_KEY": "geocode-token",
        "ODP_GEOCODE_PROVIDER_AUTH_STATUS": "unauthorized",
        "ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN": "admin-token",
        "ODP_COMPETITOR_MANUAL_SOURCE_ATTESTATION": "manual-attested",
    }

    result = validate_external_providers(env=env, correlation_id="corr-auth-bad")

    assert not result.ok
    assert result.correlation_id == "corr-auth-bad"
    assert {(error.env_var, error.code) for error in result.errors} == {
        ("ODP_LISTING_PROVIDER_AUTH_STATUS", "credential_expired"),
        ("ODP_GEOCODE_PROVIDER_AUTH_STATUS", "credential_unauthorized"),
    }
    assert "listing-token" not in repr(result)
    assert "geocode-token" not in repr(result)


def test_production_live_mode_blocks_providers_without_allowed_use_license() -> None:
    env = {
        LIVE_MODE_ENV_VAR: "live",
        "ODP_DEPLOY_ENV": "production",
        "ODP_LISTING_PROVIDER_API_KEY": "listing-token",
        "ODP_POI_PROVIDER_API_KEY": "poi-token",
        "ODP_GEOCODE_PROVIDER_API_KEY": "geocode-token",
        "ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN": "admin-token",
        "ODP_COMPETITOR_MANUAL_SOURCE_ATTESTATION": "manual-attested",
    }

    result = validate_external_providers(env=env, correlation_id="corr-license-block")

    assert not result.ok
    assert {
        (error.provider_id, error.code)
        for error in result.errors
        if error.code == "license_blocked"
    } == {("competitor.manual_source", "license_blocked")}


def test_downstream_export_flags_are_enforced_by_provider_license_metadata() -> None:
    assert provider_export_allowed("admin_boundary.official_dataset") is True
    assert provider_export_allowed("listing.partner_feed") is False
    assert "audit_evidence" in provider_downstream_use_flags("admin_boundary.official_dataset")
    assert "manual_review" in provider_downstream_use_flags("competitor.manual_source")
