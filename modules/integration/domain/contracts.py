"""Source-data contract engine for the ODay Plus Integration Layer.

This module is the dependency-free core that loads the declarative source
contracts published under ``packages/schemas/source_contracts`` and validates
raw source records against them. The Integration Layer turns upstream
IoT/internal data and the External Data Platform turns external data into the
Canonical Data Model; both layers share these contracts so that
source-to-canonical mapping has reproducible, testable inputs.

Design references:
  - ODP-DATA-02  IoT / internal data exchange contract (envelope, datasets)
  - ODP-DATA-03  External data connector specification (acquisition methods)
  - ODP-DATA-05  Source-to-canonical mapping (transform/DQ/quarantine rules)

Scope: this engine validates *field-level* contract conformance (presence,
type, enum, basic time/amount sanity) and produces a quarantine decision whose
reason codes line up with ODP-DATA-05 §8. Richer cross-entity DQ (referential
integrity, row-count reconciliation, identity resolution) lives downstream and
is intentionally out of scope here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

# packages/schemas/source_contracts lives at the repository root, four parents
# up from this file (domain -> integration -> modules -> repo root).
_REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS_ROOT = _REPO_ROOT / "packages" / "schemas" / "source_contracts"

# Field primitive types understood by the engine.
SCALAR_TYPES = {"string", "number", "integer", "boolean", "date", "timestamp", "json", "array"}

# Integration modes (ODP-DATA-02 §2) and connector acquisition methods
# (ODP-DATA-03 §4) that contracts are allowed to declare.
INTEGRATION_MODES = {
    "batch_snapshot",
    "incremental_batch",
    "event_stream",
    "backfill",
    "api_lookup",
}
ACQUISITION_METHODS = {"api", "file", "manual", "feed", "public_dataset", "generated", "internal"}
ENVELOPE_KINDS = {"batch", "event"}

# Map low-level contract issue codes to the canonical quarantine reasons defined
# in ODP-DATA-05 §8. Only "error" issues quarantine a record.
_QUARANTINE_REASONS = {
    "missing_required_field": "missing_required_field",
    "null_violation": "missing_required_field",
    "type_mismatch": "schema_mismatch",
    "enum_violation": "schema_mismatch",
    "invalid_time": "invalid_time",
    "invalid_amount": "invalid_amount",
}


class ContractError(ValueError):
    """Raised when a contract definition itself is malformed."""


@dataclass(frozen=True)
class FieldSpec:
    """A single declared field in a source contract."""

    name: str
    type: str
    required: bool = False
    nullable: bool = True
    enum: tuple[str, ...] | None = None
    minimum: float | None = None
    invalid_code: str | None = None  # override error code on range failure
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FieldSpec:
        name = data.get("name")
        ftype = data.get("type")
        if not name or not ftype:
            raise ContractError(f"Field spec needs name and type: {data!r}")
        if ftype not in SCALAR_TYPES:
            raise ContractError(f"Unknown field type {ftype!r} for field {name!r}")
        enum = data.get("enum")
        return cls(
            name=name,
            type=ftype,
            required=bool(data.get("required", False)),
            nullable=bool(data.get("nullable", True)),
            enum=tuple(enum) if enum is not None else None,
            minimum=data.get("minimum"),
            invalid_code=data.get("invalid_code"),
            description=data.get("description", ""),
        )


@dataclass(frozen=True)
class Invariant:
    """A small, named cross-field rule. Currently only ``time_order``."""

    rule: str
    earlier: str = ""
    later: str = ""
    code: str = "invalid_time"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Invariant:
        rule = data.get("rule")
        if rule != "time_order":
            raise ContractError(f"Unsupported invariant rule: {rule!r}")
        return cls(
            rule=rule,
            earlier=data.get("earlier", ""),
            later=data.get("later", ""),
            code=data.get("code", "invalid_time"),
        )


@dataclass(frozen=True)
class SourceContract:
    """A declarative contract for one upstream dataset / topic."""

    contract_id: str
    title: str
    kind: str  # internal | external
    source_system: str
    source_dataset: str
    canonical_target: str
    mapping_id: str
    integration_mode: str
    envelope: str  # batch | event
    fields: tuple[FieldSpec, ...]
    acquisition_method: str = ""
    doc_ref: str = ""
    schema_version: str = ""
    allow_unknown: bool = True
    invariants: tuple[Invariant, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceContract:
        try:
            fields = tuple(FieldSpec.from_dict(f) for f in data["fields"])
        except KeyError as exc:
            raise ContractError(f"Contract missing required key: {exc}") from exc
        if not fields:
            raise ContractError(f"Contract {data.get('contract_id')!r} declares no fields")
        kind = data.get("kind")
        if kind not in {"internal", "external", "envelope"}:
            raise ContractError(f"Contract kind must be internal/external/envelope, got {kind!r}")
        mode = data.get("integration_mode")
        if mode not in INTEGRATION_MODES:
            raise ContractError(f"Unknown integration_mode {mode!r}")
        envelope = data.get("envelope")
        if envelope not in ENVELOPE_KINDS:
            raise ContractError(f"Unknown envelope {envelope!r}")
        acquisition = data.get("acquisition_method", "")
        if acquisition and acquisition not in ACQUISITION_METHODS:
            raise ContractError(f"Unknown acquisition_method {acquisition!r}")
        invariants = tuple(Invariant.from_dict(i) for i in data.get("invariants", []))
        return cls(
            contract_id=data["contract_id"],
            title=data.get("title", data["contract_id"]),
            kind=kind,
            source_system=data.get("source_system", ""),
            source_dataset=data.get("source_dataset", data["contract_id"]),
            canonical_target=data.get("canonical_target", ""),
            mapping_id=data.get("mapping_id", ""),
            integration_mode=mode,
            envelope=envelope,
            fields=fields,
            acquisition_method=acquisition,
            doc_ref=data.get("doc_ref", ""),
            schema_version=str(data.get("schema_version", "")),
            allow_unknown=bool(data.get("allow_unknown", True)),
            invariants=invariants,
        )

    def field_map(self) -> dict[str, FieldSpec]:
        return {f.name: f for f in self.fields}

    def required_fields(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.required)


@dataclass(frozen=True)
class ContractIssue:
    """A single contract violation found while validating a record."""

    field: str
    code: str
    severity: str  # error | warning
    message: str


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating one record against a contract."""

    contract_id: str
    issues: tuple[ContractIssue, ...] = ()

    @property
    def errors(self) -> tuple[ContractIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> tuple[ContractIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "warning")

    @property
    def ok(self) -> bool:
        """True when the record is accepted (no error-severity issues)."""
        return not self.errors

    @property
    def quarantined(self) -> bool:
        return not self.ok

    @property
    def error_codes(self) -> tuple[str, ...]:
        return tuple(i.code for i in self.errors)

    def quarantine_reasons(self) -> tuple[str, ...]:
        """Canonical ODP-DATA-05 §8 quarantine reasons, de-duplicated."""
        reasons: list[str] = []
        for issue in self.errors:
            reason = _QUARANTINE_REASONS.get(issue.code, "schema_mismatch")
            if reason not in reasons:
                reasons.append(reason)
        return tuple(reasons)


# --- type checks -----------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse_temporal(value: Any, as_date: bool) -> bool:
    if not isinstance(value, str) or not value:
        return False
    text = value.strip()
    try:
        if as_date:
            date.fromisoformat(text)
        else:
            # Accept trailing 'Z' (UTC) which fromisoformat handles on 3.11+.
            datetime.fromisoformat(text)
        return True
    except ValueError:
        return False


def _type_ok(field_spec: FieldSpec, value: Any) -> bool:
    ftype = field_spec.type
    if ftype == "string":
        return isinstance(value, str)
    if ftype == "number":
        return _is_number(value)
    if ftype == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if ftype == "boolean":
        return isinstance(value, bool)
    if ftype == "date":
        return _parse_temporal(value, as_date=True)
    if ftype == "timestamp":
        return _parse_temporal(value, as_date=False)
    if ftype == "json":
        return isinstance(value, (dict, list))
    if ftype == "array":
        return isinstance(value, list)
    return False  # pragma: no cover - guarded by SCALAR_TYPES at load time


def _type_error_code(field_spec: FieldSpec) -> str:
    if field_spec.type in {"date", "timestamp"}:
        return "invalid_time"
    return "type_mismatch"


def validate_record(contract: SourceContract, record: dict[str, Any]) -> ValidationResult:
    """Validate a single raw source ``record`` against ``contract``.

    Returns a :class:`ValidationResult`; ``result.ok`` is True when the record
    is accepted and False when it must be quarantined.
    """

    if not isinstance(record, dict):
        return ValidationResult(
            contract.contract_id,
            (ContractIssue("<record>", "type_mismatch", "error", "record must be an object"),),
        )

    issues: list[ContractIssue] = []
    field_map = contract.field_map()

    for spec in contract.fields:
        present = spec.name in record
        value = record.get(spec.name)
        if not present or value is None:
            if spec.required:
                issues.append(
                    ContractIssue(
                        spec.name,
                        "missing_required_field" if not present else "null_violation",
                        "error",
                        f"required field {spec.name!r} is "
                        + ("missing" if not present else "null"),
                    )
                )
            continue

        if not _type_ok(spec, value):
            issues.append(
                ContractIssue(
                    spec.name,
                    _type_error_code(spec),
                    "error",
                    f"field {spec.name!r} expected {spec.type}, got {type(value).__name__}",
                )
            )
            continue

        if spec.enum is not None and value not in spec.enum:
            issues.append(
                ContractIssue(
                    spec.name,
                    "enum_violation",
                    "error",
                    f"field {spec.name!r} value {value!r} not in {list(spec.enum)}",
                )
            )

        if spec.minimum is not None and _is_number(value) and value < spec.minimum:
            issues.append(
                ContractIssue(
                    spec.name,
                    spec.invalid_code or "type_mismatch",
                    "error",
                    f"field {spec.name!r} value {value} below minimum {spec.minimum}",
                )
            )

    if contract.allow_unknown is False:
        for key in record:
            if key not in field_map:
                issues.append(
                    ContractIssue(key, "unexpected_field", "warning", f"unexpected field {key!r}")
                )

    issues.extend(_check_invariants(contract, record))
    return ValidationResult(contract.contract_id, tuple(issues))


def _check_invariants(contract: SourceContract, record: dict[str, Any]) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    for inv in contract.invariants:
        if inv.rule == "time_order":
            earlier = record.get(inv.earlier)
            later = record.get(inv.later)
            if isinstance(earlier, str) and isinstance(later, str):
                e_ok = _parse_temporal(earlier, as_date=False)
                l_ok = _parse_temporal(later, as_date=False)
                if e_ok and l_ok and _to_dt(later) < _to_dt(earlier):
                    issues.append(
                        ContractIssue(
                            inv.later,
                            inv.code,
                            "error",
                            f"{inv.later!r} ({later}) is earlier than {inv.earlier!r} ({earlier})",
                        )
                    )
    return issues


def _to_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.strip())


def validate_records(
    contract: SourceContract, records: list[dict[str, Any]]
) -> list[ValidationResult]:
    return [validate_record(contract, r) for r in records]


# --- registry loading ------------------------------------------------------

_CONTRACT_CACHE: dict[str, SourceContract] = {}


def contracts_root() -> Path:
    return CONTRACTS_ROOT


def load_index() -> dict[str, Any]:
    index_path = CONTRACTS_ROOT / "index.json"
    return json.loads(index_path.read_text(encoding="utf-8"))


def _load_contract_file(path: Path) -> SourceContract:
    data = json.loads(path.read_text(encoding="utf-8"))
    return SourceContract.from_dict(data)


def load_contract(contract_id: str) -> SourceContract:
    """Load a contract by id, searching internal/ then external/."""

    if contract_id in _CONTRACT_CACHE:
        return _CONTRACT_CACHE[contract_id]
    for kind in ("internal", "external"):
        candidate = CONTRACTS_ROOT / kind / f"{contract_id}.json"
        if candidate.exists():
            contract = _load_contract_file(candidate)
            _CONTRACT_CACHE[contract_id] = contract
            return contract
    raise ContractError(f"No source contract named {contract_id!r}")


def iter_contracts(kind: str | None = None) -> list[SourceContract]:
    """Load every contract listed in the index, optionally filtered by kind."""

    index = load_index()
    contracts: list[SourceContract] = []
    for entry in index.get("contracts", []):
        if kind is not None and entry.get("kind") != kind:
            continue
        contracts.append(load_contract(entry["contract_id"]))
    return contracts


def load_envelope(kind: str) -> SourceContract:
    """Load a shared exchange envelope contract (``batch`` or ``event``)."""

    if kind not in ENVELOPE_KINDS:
        raise ContractError(f"Unknown envelope kind {kind!r}")
    path = CONTRACTS_ROOT / "envelopes" / f"{kind}_envelope.json"
    return _load_contract_file(path)
