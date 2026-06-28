from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

AVM_MODEL_VERSION = "dealroom-avm-baseline-v1"
AVM_FEATURE_VERSION = "valuation-view-v1"
AVM_POLICY_VERSION = "avm-finance-approval-policy-v1"


class ValuationCaseStatus(StrEnum):
    DRAFT = "DRAFT"
    DATA_READY = "DATA_READY"
    NORMALIZING = "NORMALIZING"
    VALUING = "VALUING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    DATAROOM_READY = "DATAROOM_READY"


@dataclass(frozen=True)
class ValuationInput:
    store_id: str
    gm_ttm: float
    forecast_gm_next_12m: float
    asset_book_value: float
    equipment_fair_value: float
    lease_liability: float = 0.0
    working_capital: float = 0.0
    comparable_multiples: tuple[float, ...] = ()
    liquidity_discount: float = 0.1
    quality_score: float = 1.0
    source_snapshot_ids: tuple[str, ...] = ()
    prediction_origin_time: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ValuationInput:
        return cls(
            store_id=str(data["store_id"]),
            gm_ttm=float(data.get("gm_ttm", data.get("gross_margin_ttm", 0.0))),
            forecast_gm_next_12m=float(
                data.get("forecast_gm_next_12m", data.get("gm_fwd", data.get("gm_ttm", 0.0)))
            ),
            asset_book_value=float(data.get("asset_book_value", 0.0)),
            equipment_fair_value=float(
                data.get("equipment_fair_value", data.get("equipment_value", 0.0))
            ),
            lease_liability=float(data.get("lease_liability", 0.0)),
            working_capital=float(data.get("working_capital", 0.0)),
            comparable_multiples=tuple(float(v) for v in data.get("comparable_multiples", ())),
            liquidity_discount=_bounded(data.get("liquidity_discount", 0.1), minimum=0.0, maximum=0.5),
            quality_score=_bounded(data.get("quality_score", data.get("data_quality_score", 1.0))),
            source_snapshot_ids=tuple(str(v) for v in data.get("source_snapshot_ids", ())),
            prediction_origin_time=_parse_datetime(
                data.get("prediction_origin_time") or datetime.now(UTC)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "store_id": self.store_id,
            "gm_ttm": self.gm_ttm,
            "forecast_gm_next_12m": self.forecast_gm_next_12m,
            "asset_book_value": self.asset_book_value,
            "equipment_fair_value": self.equipment_fair_value,
            "lease_liability": self.lease_liability,
            "working_capital": self.working_capital,
            "comparable_multiples": list(self.comparable_multiples),
            "liquidity_discount": self.liquidity_discount,
            "quality_score": self.quality_score,
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "prediction_origin_time": self.prediction_origin_time.isoformat(),
            "feature_version": AVM_FEATURE_VERSION,
        }


@dataclass(frozen=True)
class ValuationCase:
    case_id: str
    store_id: str
    status: ValuationCaseStatus
    valuation_input: ValuationInput
    created_by: str
    created_at: datetime
    status_history: tuple[dict[str, Any], ...] = ()

    @classmethod
    def create(
        cls,
        valuation_input: ValuationInput,
        *,
        created_by: str,
        correlation_id: str,
        case_id: str | None = None,
    ) -> ValuationCase:
        now = datetime.now(UTC)
        item = cls(
            case_id=case_id or f"avm-case-{uuid4()}",
            store_id=valuation_input.store_id,
            status=ValuationCaseStatus.DATA_READY,
            valuation_input=valuation_input,
            created_by=created_by,
            created_at=now,
        )
        return item.transition(
            ValuationCaseStatus.DATA_READY,
            actor=created_by,
            reason="valuation request created with required inputs",
            correlation_id=correlation_id,
            at=now,
        )

    def transition(
        self,
        status: ValuationCaseStatus,
        *,
        actor: str,
        reason: str,
        correlation_id: str,
        at: datetime | None = None,
    ) -> ValuationCase:
        timestamp = at or datetime.now(UTC)
        history = self.status_history + (
            {
                "from_status": self.status.value,
                "to_status": status.value,
                "actor": actor,
                "reason": reason,
                "timestamp": timestamp.isoformat(),
                "correlation_id": correlation_id,
            },
        )
        return ValuationCase(**{**self.__dict__, "status": status, "status_history": history})

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "store_id": self.store_id,
            "status": self.status.value,
            "valuation_input": self.valuation_input.to_dict(),
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "status_history": list(self.status_history),
        }


@dataclass(frozen=True)
class NormalizedMargin:
    case_id: str
    store_id: str
    gm_ttm: float
    gm_fwd: float
    normalized_gm: float
    adjustment_reasons: tuple[str, ...]
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "store_id": self.store_id,
            "gm_ttm": self.gm_ttm,
            "gm_fwd": self.gm_fwd,
            "normalized_gm": self.normalized_gm,
            "adjustment_reasons": list(self.adjustment_reasons),
            "confidence": self.confidence,
            "feature_version": AVM_FEATURE_VERSION,
        }


@dataclass(frozen=True)
class LensValuation:
    lens: str
    p10: float
    p50: float
    p90: float
    method: str
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "lens": self.lens,
            "p10": self.p10,
            "p50": self.p50,
            "p90": self.p90,
            "method": self.method,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class PriceBand:
    p10: float
    p50: float
    p90: float

    def to_dict(self) -> dict[str, float]:
        return {"p10": self.p10, "p50": self.p50, "p90": self.p90}


@dataclass(frozen=True)
class ApprovalDecision:
    decision_id: str
    actor_id: str
    approved_at: datetime
    decision_reason: str
    reserve_price: float
    policy_version: str = AVM_POLICY_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "policy_version": self.policy_version,
            "actor_id": self.actor_id,
            "approved_at": self.approved_at.isoformat(),
            "decision_reason": self.decision_reason,
            "reserve_price": self.reserve_price,
        }


@dataclass(frozen=True)
class ValuationReport:
    report_id: str
    case_id: str
    store_id: str
    normalized_margin: NormalizedMargin
    lenses: tuple[LensValuation, ...]
    fair_price: PriceBand
    reserve_price: float
    asking_price: float
    confidence: str
    model_version: str
    feature_version: str
    prediction_origin_time: datetime
    valued_at: datetime
    finance_approval: ApprovalDecision | None = None
    valuation_version: int = 1

    def with_version(self, *, valuation_version: int, report_id: str) -> ValuationReport:
        return ValuationReport(
            **{**self.__dict__, "valuation_version": valuation_version, "report_id": report_id}
        )

    def with_approval(self, approval: ApprovalDecision) -> ValuationReport:
        return ValuationReport(**{**self.__dict__, "finance_approval": approval})

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "valuation_version": self.valuation_version,
            "case_id": self.case_id,
            "store_id": self.store_id,
            "normalized_margin": self.normalized_margin.to_dict(),
            "lenses": [lens.to_dict() for lens in self.lenses],
            "lens_values": {lens.lens: lens.to_dict() for lens in self.lenses},
            "fair_price": self.fair_price.to_dict(),
            "reserve_price": self.reserve_price,
            "asking_price": self.asking_price,
            "confidence": self.confidence,
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "prediction_origin_time": self.prediction_origin_time.isoformat(),
            "valued_at": self.valued_at.isoformat(),
            "finance_approval": (
                self.finance_approval.to_dict() if self.finance_approval else None
            ),
        }


@dataclass(frozen=True)
class DataRoomDocument:
    document_id: str
    name: str
    status: str
    source_snapshot_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "name": self.name,
            "status": self.status,
            "source_snapshot_id": self.source_snapshot_id,
        }


@dataclass(frozen=True)
class DataRoom:
    dataroom_id: str
    case_id: str
    checklist: tuple[DataRoomDocument, ...]
    valuation_card: dict[str, Any]
    export_audit: tuple[dict[str, Any], ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def with_export(self, *, actor: str, reason: str, correlation_id: str) -> DataRoom:
        event = {
            "export_id": f"avm-export-{uuid4()}",
            "actor": actor,
            "reason": reason,
            "correlation_id": correlation_id,
            "exported_at": datetime.now(UTC).isoformat(),
        }
        return DataRoom(**{**self.__dict__, "export_audit": self.export_audit + (event,)})

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataroom_id": self.dataroom_id,
            "case_id": self.case_id,
            "checklist": [document.to_dict() for document in self.checklist],
            "valuation_card": self.valuation_card,
            "export_audit": list(self.export_audit),
            "created_at": self.created_at.isoformat(),
        }


def build_valuation_view(data: Mapping[str, Any]) -> ValuationInput:
    return ValuationInput.from_mapping(data)


def normalize_margin(case: ValuationCase) -> NormalizedMargin:
    item = case.valuation_input
    normalized = round((item.gm_ttm * 0.45) + (item.forecast_gm_next_12m * 0.55), 2)
    reasons = ["weighted_ttm_and_forecast_gm"]
    if item.quality_score < 0.8:
        normalized = round(normalized * 0.92, 2)
        reasons.append("quality_discount")
    return NormalizedMargin(
        case_id=case.case_id,
        store_id=case.store_id,
        gm_ttm=item.gm_ttm,
        gm_fwd=item.forecast_gm_next_12m,
        normalized_gm=normalized,
        adjustment_reasons=tuple(reasons),
        confidence=_confidence(item.quality_score),
    )


def value_store(case: ValuationCase, normalized_margin: NormalizedMargin) -> ValuationReport:
    item = case.valuation_input
    income_p50 = normalized_margin.normalized_gm * 2.8
    asset_p50 = max(item.asset_book_value + item.equipment_fair_value + item.working_capital - item.lease_liability, 0.0)
    multiple = _median(item.comparable_multiples) if item.comparable_multiples else 2.4
    market_p50 = normalized_margin.normalized_gm * multiple * (1 - item.liquidity_discount)

    lenses = (
        _lens("income", income_p50, "normalized_gm_multiple", {"multiple": 2.8}),
        _lens("asset", asset_p50, "net_asset_value", {"lease_liability": item.lease_liability}),
        _lens(
            "market",
            market_p50,
            "comparable_multiple_with_liquidity_discount",
            {"multiple": multiple, "liquidity_discount": item.liquidity_discount},
        ),
    )
    p10 = round(sum(lens.p10 for lens in lenses) / len(lenses), 2)
    p50 = round(sum(lens.p50 for lens in lenses) / len(lenses), 2)
    p90 = round(sum(lens.p90 for lens in lenses) / len(lenses), 2)
    fair = PriceBand(p10=p10, p50=p50, p90=p90)
    return ValuationReport(
        report_id=f"avm-report-{uuid4()}",
        case_id=case.case_id,
        store_id=case.store_id,
        normalized_margin=normalized_margin,
        lenses=lenses,
        fair_price=fair,
        reserve_price=round(p10 * 0.97, 2),
        asking_price=round(p90 * 1.05, 2),
        confidence=normalized_margin.confidence,
        model_version=AVM_MODEL_VERSION,
        feature_version=AVM_FEATURE_VERSION,
        prediction_origin_time=item.prediction_origin_time,
        valued_at=datetime.now(UTC),
    )


def generate_data_room(report: ValuationReport) -> DataRoom:
    checklist = (
        DataRoomDocument("financials", "Normalized GM and forecast evidence", "ready"),
        DataRoomDocument("assets", "Asset ledger and equipment valuation", "ready"),
        DataRoomDocument("lease", "Lease and liability summary", "ready"),
        DataRoomDocument("comparables", "Comparable transaction evidence", "ready"),
        DataRoomDocument("valuation_card", "Fair, reserve, and asking valuation card", "ready"),
    )
    return DataRoom(
        dataroom_id=f"avm-dataroom-{uuid4()}",
        case_id=report.case_id,
        checklist=checklist,
        valuation_card={
            "case_id": report.case_id,
            "store_id": report.store_id,
            "fair_price": report.fair_price.to_dict(),
            "reserve_price": report.reserve_price,
            "asking_price": report.asking_price,
            "model_version": report.model_version,
            "valuation_version": report.valuation_version,
        },
    )


def _lens(lens: str, p50: float, method: str, evidence: dict[str, Any]) -> LensValuation:
    p50 = max(round(p50, 2), 0.0)
    return LensValuation(
        lens=lens,
        p10=round(p50 * 0.82, 2),
        p50=p50,
        p90=round(p50 * 1.18, 2),
        method=method,
        evidence=evidence,
    )


def _confidence(quality_score: float) -> str:
    if quality_score >= 0.9:
        return "high"
    if quality_score >= 0.75:
        return "medium"
    return "low"


def _median(values: tuple[float, ...]) -> float:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _bounded(value: Any, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return min(max(float(value), minimum), maximum)


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
