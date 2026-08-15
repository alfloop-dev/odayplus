"""Internal (IoT / upstream) source contracts exposed to the Integration Layer.

Thin facade over :mod:`modules.integration.domain.contracts` that scopes the
contract registry to ``kind == "internal"`` so callers (pipelines, tests) do not
have to know where the contract JSON lives.
"""

from __future__ import annotations

from modules.integration.domain.contracts import (
    SourceContract,
    ValidationResult,
    iter_contracts,
    load_contract,
    load_envelope,
    validate_record,
)

__all__ = [
    "internal_contracts",
    "internal_contract",
    "internal_contract_ids",
    "batch_envelope",
    "event_envelope",
    "validate_record",
    "ValidationResult",
    "SourceContract",
]


def internal_contracts() -> list[SourceContract]:
    """All internal source contracts declared in the registry."""
    return iter_contracts(kind="internal")


def internal_contract(contract_id: str) -> SourceContract:
    contract = load_contract(contract_id)
    if contract.kind != "internal":
        raise ValueError(f"{contract_id!r} is not an internal contract")
    return contract


def internal_contract_ids() -> list[str]:
    return [c.contract_id for c in internal_contracts()]


def batch_envelope() -> SourceContract:
    return load_envelope("batch")


def event_envelope() -> SourceContract:
    return load_envelope("event")
