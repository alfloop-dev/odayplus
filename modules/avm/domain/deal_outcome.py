from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from modules.avm.domain.liquidity import LiquidityTrainingRecord


class NoDealReasonCode(StrEnum):
    PRICE_GAP = "PRICE_GAP"
    CONDITION = "CONDITION"
    FINANCING = "FINANCING"
    WITHDRAWN_BY_OWNER = "WITHDRAWN_BY_OWNER"
    OTHER = "OTHER"


VALID_NO_DEAL_REASONS = frozenset(code.value for code in NoDealReasonCode)
REDACTED_CONFIDENTIAL_VALUE = "[REDACTED_CONFIDENTIAL_VALUE]"


@dataclass(frozen=True)
class DealOutcome:
    outcome_id: str
    valuation_id: str
    store_id: str
    sold: bool
    settlement_price: float | None = None
    settlement_date: date | datetime | None = None
    duration_days: float = 0.0
    no_deal_reason_code: NoDealReasonCode | str | None = None
    deal_terms: Mapping[str, Any] = field(default_factory=dict)
    source_authority: str = "official_dealroom"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.valuation_id or not str(self.valuation_id).strip():
            raise ValueError("valuation_id is required for deal outcome to map fair/reserve/asking baseline")
        if not self.store_id or not str(self.store_id).strip():
            raise ValueError("store_id is required for deal outcome")
        if self.duration_days < 0:
            raise ValueError("duration_days cannot be negative")

        if self.sold:
            if self.settlement_price is None or float(self.settlement_price) <= 0:
                raise ValueError("settlement_price must be a positive number when sold is True")
            if self.no_deal_reason_code is not None:
                raise ValueError("no_deal_reason_code must be None when sold is True")
        else:
            if self.no_deal_reason_code is None:
                raise ValueError("no_deal_reason_code is required when sold is False")
            code_str = (
                self.no_deal_reason_code.value
                if isinstance(self.no_deal_reason_code, NoDealReasonCode)
                else str(self.no_deal_reason_code)
            )
            if code_str not in VALID_NO_DEAL_REASONS:
                raise ValueError(f"Invalid no_deal_reason_code {code_str!r}; expected one of {sorted(VALID_NO_DEAL_REASONS)}")
            if self.settlement_price is not None and float(self.settlement_price) > 0:
                raise ValueError("settlement_price must be None or 0 when sold is False")

    def to_liquidity_training_record(
        self,
        features: Mapping[str, float],
    ) -> LiquidityTrainingRecord:
        """Derive LiquidityTrainingRecord directly from this DealOutcome (no secondary source)."""
        if not isinstance(features, Mapping) or not features:
            raise ValueError("liquidity training record requires non-empty numeric features")
        code_str = (
            self.no_deal_reason_code.value
            if isinstance(self.no_deal_reason_code, NoDealReasonCode)
            else (str(self.no_deal_reason_code) if self.no_deal_reason_code is not None else None)
        )
        return LiquidityTrainingRecord(
            duration_days=float(self.duration_days),
            sold=bool(self.sold),
            features={str(k): float(v) for k, v in features.items()},
            settlement_price=float(self.settlement_price) if self.settlement_price is not None else None,
            no_deal_reason_code=code_str,
            deal_terms=dict(self.deal_terms),
            valuation_id=self.valuation_id,
        )

    def to_dict(self, *, redact_settlement_price: bool = False) -> dict[str, Any]:
        code_str = (
            self.no_deal_reason_code.value
            if isinstance(self.no_deal_reason_code, NoDealReasonCode)
            else (str(self.no_deal_reason_code) if self.no_deal_reason_code is not None else None)
        )
        settlement_dt_str = None
        if self.settlement_date is not None:
            settlement_dt_str = (
                self.settlement_date.isoformat()
                if hasattr(self.settlement_date, "isoformat")
                else str(self.settlement_date)
            )
        price_val: Any = self.settlement_price
        if redact_settlement_price and self.settlement_price is not None:
            price_val = REDACTED_CONFIDENTIAL_VALUE

        return {
            "outcome_id": self.outcome_id,
            "valuation_id": self.valuation_id,
            "store_id": self.store_id,
            "sold": self.sold,
            "settlement_price": price_val,
            "settlement_date": settlement_dt_str,
            "duration_days": self.duration_days,
            "no_deal_reason_code": code_str,
            "deal_terms": dict(self.deal_terms),
            "source_authority": self.source_authority,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> DealOutcome:
        valuation_id = data.get("valuation_id")
        if not valuation_id:
            raise ValueError("valuation_id is required")
        store_id = data.get("store_id")
        if not store_id:
            raise ValueError("store_id is required")

        outcome_id = str(data.get("outcome_id") or f"outcome-{uuid4()}")
        sold = bool(data.get("sold", False))

        settlement_price_raw = data.get("settlement_price")
        settlement_price = float(settlement_price_raw) if settlement_price_raw is not None else None

        settlement_date_raw = data.get("settlement_date")
        settlement_date = None
        if settlement_date_raw is not None:
            if isinstance(settlement_date_raw, (datetime, date)):
                settlement_date = settlement_date_raw
            else:
                try:
                    settlement_date = datetime.fromisoformat(str(settlement_date_raw)).date()
                except Exception:
                    settlement_date = str(settlement_date_raw)

        duration_days = float(data.get("duration_days", 0.0))
        raw_reason = data.get("no_deal_reason_code")
        no_deal_reason = str(raw_reason) if raw_reason is not None else None

        deal_terms_raw = data.get("deal_terms", {})
        deal_terms = dict(deal_terms_raw) if isinstance(deal_terms_raw, Mapping) else {}
        source_authority = str(data.get("source_authority", "official_dealroom"))

        created_at_raw = data.get("created_at")
        created_at = (
            datetime.fromisoformat(str(created_at_raw).replace("Z", "+00:00"))
            if created_at_raw
            else datetime.now(UTC)
        )
        updated_at_raw = data.get("updated_at")
        updated_at = (
            datetime.fromisoformat(str(updated_at_raw).replace("Z", "+00:00"))
            if updated_at_raw
            else datetime.now(UTC)
        )

        return cls(
            outcome_id=outcome_id,
            valuation_id=str(valuation_id),
            store_id=str(store_id),
            sold=sold,
            settlement_price=settlement_price,
            settlement_date=settlement_date,
            duration_days=duration_days,
            no_deal_reason_code=no_deal_reason,
            deal_terms=deal_terms,
            source_authority=source_authority,
            created_at=created_at,
            updated_at=updated_at,
        )


__all__ = [
    "DealOutcome",
    "NoDealReasonCode",
    "REDACTED_CONFIDENTIAL_VALUE",
    "VALID_NO_DEAL_REASONS",
]
