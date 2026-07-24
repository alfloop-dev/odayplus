from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from apps.data_platform.contracts import QuarantineReason, SourceKind
from apps.data_platform.mapping import SourceContractError
from apps.data_platform.serialization import parse_datetime

_CANONICAL_STATUSES = {"succeeded", "failed", "refunded", "voided", "partial"}
_CATEGORY_VALUES = {
    "transaction": _CANONICAL_STATUSES,
    "trade": _CANONICAL_STATUSES,
    "merchant_operation": {"active", "inactive"},
    "place_operation": {"planned", "open", "suspended", "closed", "transferred"},
    "device_connection": {
        "online",
        "offline",
        "error",
        "available",
        "occupied",
        "maintenance",
    },
}


@dataclass(frozen=True)
class StatusMappingContract:
    version: str
    approved_by: str
    approved_at: datetime
    mappings: dict[str, dict[str, str]]
    trade_paid_amount_rule: str | None = None

    @classmethod
    def load(cls, path: str | Path) -> StatusMappingContract:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        contract = cls(
            version=str(raw.get("version") or "").strip(),
            approved_by=str(raw.get("approved_by") or "").strip(),
            approved_at=parse_datetime(
                raw.get("approved_at"), field_name="status_mapping.approved_at"
            ),
            mappings={
                str(source): {
                    str(code): str(status)
                    for code, status in dict(values).items()
                }
                for source, values in dict(raw.get("mappings") or {}).items()
            },
            trade_paid_amount_rule=raw.get("trade_paid_amount_rule"),
        )
        contract.validate()
        return contract

    def validate(self) -> None:
        if not self.version or not self.approved_by:
            raise ValueError("Status mapping version and approved_by are required")
        for source, mapping in self.mappings.items():
            if source not in {*_CATEGORY_VALUES, "place_type"}:
                raise ValueError(f"Unsupported governed mapping category: {source}")
            if not mapping:
                raise ValueError(f"Governed mapping is empty for {source}")
            allowed = _CATEGORY_VALUES.get(source)
            invalid = set(mapping.values()) - allowed if allowed else set()
            if invalid and allowed:
                raise ValueError(f"Invalid canonical statuses for {source}: {sorted(invalid)}")
            if source == "place_type" and any(not value.strip() for value in mapping.values()):
                raise ValueError("place_type mappings cannot be empty")
        if self.trade_paid_amount_rule not in {
            None,
            "gross_when_succeeded_zero_otherwise",
        }:
            raise ValueError("Unsupported trade_paid_amount_rule")

    def resolve(self, source_kind: SourceKind, source_status: str) -> str:
        return self.resolve_category(source_kind.value, source_status)

    def resolve_category(self, category: str, source_value: str) -> str:
        try:
            return self.mappings[category][source_value]
        except KeyError as exc:
            raise SourceContractError(
                QuarantineReason.STATUS_MAPPING_UNAPPROVED,
                (
                    f"No approved {category} mapping for code {source_value!r}"
                ),
            ) from exc


def optional_status_contract(path: str | None) -> StatusMappingContract | None:
    return None if not path else StatusMappingContract.load(path)
