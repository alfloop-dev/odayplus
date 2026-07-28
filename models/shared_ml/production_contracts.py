"""Canonical production training-to-runtime model contracts.

This registry names only models that the production training command can
actually produce. A capability without a trainer or mature data contract stays
explicitly unavailable instead of inventing an MLflow alias for it.

Governed-disabled capabilities are an auditable first-class state: they expose
real observed/eligible label counts, a source contract, an owner, and an
activation gate so that the runtime can report ``productionBindingsReady=True``
while still failing closed for the disabled service.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class GovernedDisabledBinding:
    """Evidence record for a capability that is explicitly disabled by governance.

    All fields are required and must reflect real observed/eligible counts from
    the authoritative PG16 label-authority contract.  Auto-seeded, synthetic,
    fixture, or fabricated values are forbidden.
    """

    reason_code: str
    """Canonical SCREAMING_SNAKE reason code (e.g. DATA_CONTRACT_NOT_MATURE)."""

    observed_count: int
    """Real observed eligible row count as of the last authoritative label audit."""

    eligible_count: int
    """Minimum eligible row threshold required to activate this capability."""

    source_contract: str
    """Identifier of the data / label contract that owns the eligibility gate."""

    owner: str
    """Team or service that owns the activation decision."""

    activation_gate: str
    """Human-readable description of what must be true to move to active."""

    auto_seeded: bool = False
    """Must remain False; synthetic seed substitution is forbidden."""

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "reasonCode": self.reason_code,
            "observedCount": self.observed_count,
            "eligibleCount": self.eligible_count,
            "sourceContract": self.source_contract,
            "owner": self.owner,
            "activationGate": self.activation_gate,
            "autoSeeded": self.auto_seeded,
        }


@dataclass(frozen=True)
class ProductionModelContract:
    service: str
    model_name: str | None
    training_spec_key: str | None
    required_for_platform_readiness: bool
    outcome_contract_required: bool
    unavailable_reason: str | None = None
    governed_disabled_binding: GovernedDisabledBinding | None = None

    @property
    def trainable(self) -> bool:
        return self.model_name is not None and self.training_spec_key is not None

    @property
    def is_governed_disabled(self) -> bool:
        """True when this capability has explicit governed-disabled evidence."""
        return self.governed_disabled_binding is not None


# Canonical production capability registry.
#
# ForecastOps: trains from 1,303 PIT-safe PG16 rows; binds a real MLflow
#   production alias at startup.
#
# AVM, HeatZone, SiteScore: PG16 outcome data has not yet reached the
#   eligibility threshold required for a governed production release.  They are
#   declared as governed-disabled with auditable evidence; the runtime must not
#   fabricate a production alias for them.
PRODUCTION_MODEL_CONTRACTS: Mapping[str, ProductionModelContract] = (
    MappingProxyType(
        {
            "forecastops": ProductionModelContract(
                service="forecastops",
                model_name="forecast_revenue_interval",
                training_spec_key="forecastops",
                required_for_platform_readiness=True,
                outcome_contract_required=True,
            ),
            "avm": ProductionModelContract(
                service="avm",
                model_name="dealroom_avm",
                training_spec_key="avm",
                required_for_platform_readiness=True,
                outcome_contract_required=True,
                unavailable_reason="DATA_CONTRACT_NOT_MATURE",
                governed_disabled_binding=GovernedDisabledBinding(
                    reason_code="DATA_CONTRACT_NOT_MATURE",
                    observed_count=0,
                    eligible_count=500,
                    source_contract="avm.outcome_authority.pg16_label_v1",
                    owner="avm-platform-team",
                    activation_gate=(
                        "Requires >= 500 PG16 PIT-safe AVM outcome rows "
                        "and independent label-authority approval"
                    ),
                    auto_seeded=False,
                ),
            ),
            "sitescore": ProductionModelContract(
                service="sitescore",
                model_name="sitescore_propensity",
                training_spec_key="sitescore",
                required_for_platform_readiness=True,
                outcome_contract_required=True,
                unavailable_reason="DATA_CONTRACT_NOT_MATURE",
                governed_disabled_binding=GovernedDisabledBinding(
                    reason_code="DATA_CONTRACT_NOT_MATURE",
                    observed_count=0,
                    eligible_count=300,
                    source_contract="sitescore.outcome_authority.pg16_label_v1",
                    owner="sitescore-platform-team",
                    activation_gate=(
                        "Requires >= 300 PG16 PIT-safe SiteScore propensity rows "
                        "and independent label-authority approval"
                    ),
                    auto_seeded=False,
                ),
            ),
            "heatzone": ProductionModelContract(
                service="heatzone",
                model_name="heatzone_priority",
                training_spec_key="heatzone",
                required_for_platform_readiness=True,
                outcome_contract_required=True,
                unavailable_reason="DATA_CONTRACT_NOT_MATURE",
                governed_disabled_binding=GovernedDisabledBinding(
                    reason_code="DATA_CONTRACT_NOT_MATURE",
                    observed_count=0,
                    eligible_count=400,
                    source_contract="heatzone.pit_label_authority.pg16_v1",
                    owner="heatzone-platform-team",
                    activation_gate=(
                        "Requires >= 400 PG16 PIT-safe HeatZone priority labels "
                        "from the authoritative label authority and independent approval"
                    ),
                    auto_seeded=False,
                ),
            ),
        }
    )
)


def production_model_names(
    services: Iterable[str] | None = None,
) -> dict[str, str]:
    """Return service-to-MLflow names for capabilities with real trainers.

    Only returns names for services that are NOT governed-disabled; calling
    code must not fabricate aliases for governed-disabled capabilities.
    """

    requested = tuple(services) if services is not None else tuple(
        PRODUCTION_MODEL_CONTRACTS
    )
    names: dict[str, str] = {}
    for service in requested:
        contract = PRODUCTION_MODEL_CONTRACTS[service]
        if contract.model_name is not None and not contract.is_governed_disabled:
            names[service] = contract.model_name
    return names


def governed_disabled_services() -> frozenset[str]:
    """Return the set of services that are in governed-disabled state."""
    return frozenset(
        service
        for service, contract in PRODUCTION_MODEL_CONTRACTS.items()
        if contract.is_governed_disabled
    )


def required_production_model_services() -> frozenset[str]:
    return frozenset(
        service
        for service, contract in PRODUCTION_MODEL_CONTRACTS.items()
        if contract.required_for_platform_readiness
    )


__all__ = [
    "PRODUCTION_MODEL_CONTRACTS",
    "GovernedDisabledBinding",
    "ProductionModelContract",
    "governed_disabled_services",
    "production_model_names",
    "required_production_model_services",
]
