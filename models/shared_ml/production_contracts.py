"""Canonical production training-to-runtime model contracts.

This registry names only models that the production training command can
actually produce. A capability without a trainer or mature data contract stays
explicitly unavailable instead of inventing an MLflow alias for it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class ProductionModelContract:
    service: str
    model_name: str | None
    training_spec_key: str | None
    required_for_platform_readiness: bool
    outcome_contract_required: bool
    unavailable_reason: str | None = None

    @property
    def trainable(self) -> bool:
        return self.model_name is not None and self.training_spec_key is not None


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
            ),
            "sitescore": ProductionModelContract(
                service="sitescore",
                model_name="sitescore_propensity",
                training_spec_key="sitescore",
                required_for_platform_readiness=True,
                outcome_contract_required=True,
            ),
            "heatzone": ProductionModelContract(
                service="heatzone",
                model_name="heatzone_priority",
                training_spec_key="heatzone",
                required_for_platform_readiness=True,
                outcome_contract_required=True,
            ),
        }
    )
)


def production_model_names(
    services: Iterable[str] | None = None,
) -> dict[str, str]:
    """Return service-to-MLflow names for capabilities with real trainers."""

    requested = tuple(services) if services is not None else tuple(
        PRODUCTION_MODEL_CONTRACTS
    )
    names: dict[str, str] = {}
    for service in requested:
        contract = PRODUCTION_MODEL_CONTRACTS[service]
        if contract.model_name is not None:
            names[service] = contract.model_name
    return names


def required_production_model_services() -> frozenset[str]:
    return frozenset(
        service
        for service, contract in PRODUCTION_MODEL_CONTRACTS.items()
        if contract.required_for_platform_readiness
    )


__all__ = [
    "PRODUCTION_MODEL_CONTRACTS",
    "ProductionModelContract",
    "production_model_names",
    "required_production_model_services",
]
