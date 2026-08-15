"""Integration domain: source-contract engine and validation primitives."""

from modules.integration.domain.contracts import (
    ContractError,
    ContractIssue,
    FieldSpec,
    Invariant,
    SourceContract,
    ValidationResult,
    contracts_root,
    iter_contracts,
    load_contract,
    load_envelope,
    load_index,
    validate_record,
    validate_records,
)

__all__ = [
    "ContractError",
    "ContractIssue",
    "FieldSpec",
    "Invariant",
    "SourceContract",
    "ValidationResult",
    "contracts_root",
    "iter_contracts",
    "load_contract",
    "load_envelope",
    "load_index",
    "validate_record",
    "validate_records",
]
