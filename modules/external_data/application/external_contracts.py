"""External source contracts exposed to the External Data Platform.

External data (POI, competitor stores, listings, ...) must be landed and
canonicalized through these contracts before any model consumes it
(ODP-DATA-03 §2). This facade reuses the shared contract engine that lives in
the Integration Layer; the contract definitions themselves are published under
``packages/schemas/source_contracts/external``.
"""

from __future__ import annotations

from modules.integration.domain.contracts import (
    SourceContract,
    ValidationResult,
    iter_contracts,
    load_contract,
    validate_record,
)

__all__ = [
    "external_contracts",
    "external_contract",
    "external_contract_ids",
    "validate_record",
    "ValidationResult",
    "SourceContract",
]


def external_contracts() -> list[SourceContract]:
    """All external source contracts declared in the registry."""
    return iter_contracts(kind="external")


def external_contract(contract_id: str) -> SourceContract:
    contract = load_contract(contract_id)
    if contract.kind != "external":
        raise ValueError(f"{contract_id!r} is not an external contract")
    return contract


def external_contract_ids() -> list[str]:
    return [c.contract_id for c in external_contracts()]
