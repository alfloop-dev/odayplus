from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

AVM_MODEL_VERSION = "dealroom-avm-baseline-v1"
AVM_FEATURE_VERSION = "valuation-view-v2"
AVM_POLICY_VERSION = "avm-finance-approval-policy-v1"
AVM_DEPRECIATION_VERSION = "avm-depreciation-straight-line-v1"
AVM_DEPRECIATION_LEGACY_VERSION = "avm-depreciation-absent-v0"
AVM_DEPRECIATION_NOT_APPLICABLE_VERSION = "avm-depreciation-not-applicable-v1"
AVM_DEPRECIATION_LEGACY_DISPOSITION_TEXT = "本估值採 2026-09-03 前之計算版本，資產折舊未納入"
QUALITY_SCORE_REQUIRED_MESSAGE = (
    "quality_score is required before AVM valuation; input quality is unmeasured"
)
LEGACY_UNKNOWN_QUALITY_STATUS = "legacy_unknown"
LEGACY_QUALITY_DISPOSITION = "legacy_unknown_downgraded"
MEASURED_QUALITY_STATUS = "measured"
UNMEASURED_QUALITY_STATUS = "unmeasured"


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
    quality_score: float | None = None
    quality_score_status: str | None = None
    source_snapshot_ids: tuple[str, ...] = ()
    prediction_origin_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    equipment_depreciation_basis: str | None = None
    equipment_original_cost: float | None = None
    asset_book_value_includes_equipment: bool | None = None
    useful_life_months: int | None = None
    residual_value_ratio: float | None = None
    depreciation_method: str | None = None
    depreciation_effective_date: str | None = None
    asset_in_service_date: str | None = None

    @property
    def is_pre_status_payload(self) -> bool:
        """True when this input predates ``quality_score_status``.

        ``__init__`` always writes every field into the instance dict, so a
        missing key can only come from unpickling a record stored before the
        field existed.  That is the sole case where a stored ``quality_score``
        may in fact be the former implicit perfect-score default rather than a
        measurement, and it must not be inferred from a caller merely leaving
        the status out when constructing a fresh input.
        """

        return "quality_score_status" not in self.__dict__

    @property
    def effective_quality_score_status(self) -> str:
        status = getattr(self, "quality_score_status", None)
        if status is not None:
            return status
        if self.quality_score is None:
            return UNMEASURED_QUALITY_STATUS
        if self.is_pre_status_payload:
            return LEGACY_UNKNOWN_QUALITY_STATUS
        return MEASURED_QUALITY_STATUS

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ValuationInput:
        quality_score = _optional_bounded(
            data.get("quality_score", data.get("data_quality_score"))
        )
        explicit_status = data.get("quality_score_status")
        if explicit_status:
            quality_score_status = str(explicit_status)
        elif quality_score is None:
            quality_score_status = UNMEASURED_QUALITY_STATUS
        else:
            quality_score_status = MEASURED_QUALITY_STATUS

        useful_life = data.get("useful_life_months")
        if useful_life is not None:
            try:
                ul_val = int(useful_life)
            except (TypeError, ValueError) as err:
                raise ValueError("useful_life_months must be an integer >= 1") from err
            if ul_val < 1:
                raise ValueError(f"useful_life_months must be >= 1, got {ul_val}")
            useful_life = ul_val

        residual_ratio = data.get("residual_value_ratio")
        if residual_ratio is not None:
            try:
                r_val = float(residual_ratio)
            except (TypeError, ValueError) as err:
                raise ValueError("residual_value_ratio must be a numeric float between 0.0 and 1.0") from err
            if math.isnan(r_val) or math.isinf(r_val) or not (0.0 <= r_val <= 1.0):
                raise ValueError(f"residual_value_ratio must be between 0.0 and 1.0, got {r_val}")
            residual_ratio = r_val

        equipment_cost = data.get("equipment_original_cost")
        if equipment_cost is not None:
            try:
                ec_val = float(equipment_cost)
            except (TypeError, ValueError) as err:
                raise ValueError("equipment_original_cost must be a numeric float >= 0.0") from err
            if math.isnan(ec_val) or math.isinf(ec_val) or ec_val < 0.0:
                raise ValueError(f"equipment_original_cost must be a non-negative finite float, got {ec_val}")
            equipment_cost = ec_val

        includes_equip = data.get("asset_book_value_includes_equipment")
        dep_basis = data.get("equipment_depreciation_basis")
        dep_method = data.get("depreciation_method")
        dep_effective = _date_str(data.get("depreciation_effective_date"))
        in_service = _date_str(data.get("asset_in_service_date"))

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
            liquidity_discount=_bounded(
                data.get("liquidity_discount", 0.1), minimum=0.0, maximum=0.5
            ),
            quality_score=quality_score,
            quality_score_status=quality_score_status,
            source_snapshot_ids=tuple(str(v) for v in data.get("source_snapshot_ids", ())),
            prediction_origin_time=_parse_datetime(
                data.get("prediction_origin_time") or datetime.now(UTC)
            ),
            equipment_depreciation_basis=str(dep_basis) if dep_basis is not None else None,
            equipment_original_cost=float(equipment_cost) if equipment_cost is not None else None,
            asset_book_value_includes_equipment=(
                bool(includes_equip) if includes_equip is not None else None
            ),
            useful_life_months=int(useful_life) if useful_life is not None else None,
            residual_value_ratio=float(residual_ratio) if residual_ratio is not None else None,
            depreciation_method=str(dep_method) if dep_method is not None else None,
            depreciation_effective_date=dep_effective,
            asset_in_service_date=in_service,
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
            "quality_score_status": self.effective_quality_score_status,
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "prediction_origin_time": self.prediction_origin_time.isoformat(),
            "equipment_depreciation_basis": self.equipment_depreciation_basis,
            "equipment_original_cost": self.equipment_original_cost,
            "asset_book_value_includes_equipment": self.asset_book_value_includes_equipment,
            "useful_life_months": self.useful_life_months,
            "residual_value_ratio": self.residual_value_ratio,
            "depreciation_method": self.depreciation_method,
            "depreciation_effective_date": self.depreciation_effective_date,
            "asset_in_service_date": self.asset_in_service_date,
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

    def with_legacy_quality_disposition(self) -> NormalizedMargin:
        """Apply the conservative disposition to an opaque persisted margin.

        A margin written before ``quality_score_status`` existed can carry a
        high-confidence value even when its case's former ``1.0`` default was
        actually an omitted measurement.  Make that margin safe for every
        valuation entry point.  The reason guard keeps the operation
        idempotent when a repository and a domain consumer both enforce it.
        """

        reasons = tuple(self.adjustment_reasons)
        normalized_gm = self.normalized_gm
        if "legacy_quality_unknown_discount" not in reasons:
            normalized_gm = round(normalized_gm * 0.92, 2)
            reasons += ("legacy_quality_unknown_discount",)
        return NormalizedMargin(
            **{
                **self.__dict__,
                "normalized_gm": normalized_gm,
                "adjustment_reasons": reasons,
                "confidence": "low",
            }
        )


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
    correlation_id: str
    policy_version: str = AVM_POLICY_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "policy_version": self.policy_version,
            "actor_id": self.actor_id,
            "approved_at": self.approved_at.isoformat(),
            "decision_reason": self.decision_reason,
            "reserve_price": self.reserve_price,
            "correlation_id": self.correlation_id,
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
    depreciation_version: str
    depreciation_applied: bool
    execution_metadata: dict[str, Any] = field(default_factory=dict)
    finance_approval: ApprovalDecision | None = None
    valuation_version: int = 1
    quality_score_status: str | None = None
    quality_disposition: str | None = None

    def __getattr__(self, name: str) -> Any:
        if name == "depreciation_version":
            return AVM_DEPRECIATION_LEGACY_VERSION
        if name == "depreciation_applied":
            return False
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    def with_version(self, *, valuation_version: int, report_id: str) -> ValuationReport:
        d = dict(self.__dict__)
        d.setdefault("depreciation_version", getattr(self, "depreciation_version", AVM_DEPRECIATION_LEGACY_VERSION))
        d.setdefault("depreciation_applied", getattr(self, "depreciation_applied", False))
        return ValuationReport(
            **{**d, "valuation_version": valuation_version, "report_id": report_id}
        )

    def with_approval(self, approval: ApprovalDecision) -> ValuationReport:
        d = dict(self.__dict__)
        d.setdefault("depreciation_version", getattr(self, "depreciation_version", AVM_DEPRECIATION_LEGACY_VERSION))
        d.setdefault("depreciation_applied", getattr(self, "depreciation_applied", False))
        return ValuationReport(**{**d, "finance_approval": approval})

    @property
    def is_legacy_quality_unknown(self) -> bool:
        return (
            getattr(self, "quality_score_status", None) == LEGACY_UNKNOWN_QUALITY_STATUS
            or getattr(self, "quality_disposition", None) == LEGACY_QUALITY_DISPOSITION
        )

    def with_legacy_quality_disposition(self) -> ValuationReport:
        """Downgrade an opaque historical report without rewriting its prices.

        Reports created before ``quality_score_status`` existed may contain a
        high confidence value that cannot be distinguished from the former
        perfect-score default. Keep the historical numbers for audit, but make
        every consumer see a named, low-confidence disposition and do not carry
        an old approval as if it were still actionable.
        """

        reasons = tuple(self.normalized_margin.adjustment_reasons)
        if "legacy_quality_unknown_discount" not in reasons:
            reasons += ("legacy_quality_unknown_discount",)
        normalized_margin = NormalizedMargin(
            **{
                **self.normalized_margin.__dict__,
                "adjustment_reasons": reasons,
                "confidence": "low",
            }
        )
        metadata = dict(getattr(self, "execution_metadata", {}) or {})
        previous_approval = getattr(self, "finance_approval", None)
        if previous_approval is not None:
            metadata.setdefault("legacy_finance_approval", previous_approval.to_dict())
        metadata["quality_score_status"] = LEGACY_UNKNOWN_QUALITY_STATUS
        metadata["quality_disposition"] = LEGACY_QUALITY_DISPOSITION
        d = dict(self.__dict__)
        d.setdefault("depreciation_version", getattr(self, "depreciation_version", AVM_DEPRECIATION_LEGACY_VERSION))
        d.setdefault("depreciation_applied", getattr(self, "depreciation_applied", False))
        return ValuationReport(
            **{
                **d,
                "normalized_margin": normalized_margin,
                "confidence": "low",
                "execution_metadata": metadata,
                "finance_approval": None,
                "quality_score_status": LEGACY_UNKNOWN_QUALITY_STATUS,
                "quality_disposition": LEGACY_QUALITY_DISPOSITION,
            }
        )

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
            "quality_score_status": getattr(self, "quality_score_status", None),
            "quality_disposition": getattr(self, "quality_disposition", None),
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "depreciation_version": getattr(self, "depreciation_version", AVM_DEPRECIATION_LEGACY_VERSION),
            "depreciation_applied": getattr(self, "depreciation_applied", False),
            "prediction_origin_time": self.prediction_origin_time.isoformat(),
            "valued_at": self.valued_at.isoformat(),
            "execution_metadata": self.execution_metadata,
            "finance_approval": (
                self.finance_approval.to_dict() if self.finance_approval else None
            ),
        }


def rehydrate_legacy_report(report: ValuationReport) -> ValuationReport:
    """Rehydrate a legacy report instance deserialized from pre-cutover store."""
    dep_version = getattr(report, "depreciation_version", AVM_DEPRECIATION_LEGACY_VERSION)
    dep_applied = getattr(report, "depreciation_applied", False)
    if (
        "depreciation_version" in report.__dict__
        and "depreciation_applied" in report.__dict__
    ):
        return report
    return ValuationReport(
        report_id=report.report_id,
        case_id=report.case_id,
        store_id=report.store_id,
        normalized_margin=report.normalized_margin,
        lenses=report.lenses,
        fair_price=report.fair_price,
        reserve_price=report.reserve_price,
        asking_price=report.asking_price,
        confidence=report.confidence,
        model_version=report.model_version,
        feature_version=report.feature_version,
        prediction_origin_time=report.prediction_origin_time,
        valued_at=report.valued_at,
        depreciation_version=dep_version,
        depreciation_applied=dep_applied,
        execution_metadata=dict(getattr(report, "execution_metadata", {}) or {}),
        finance_approval=getattr(report, "finance_approval", None),
        valuation_version=getattr(report, "valuation_version", 1),
        quality_score_status=getattr(report, "quality_score_status", None),
        quality_disposition=getattr(report, "quality_disposition", None),
    )


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
    quality_score_status: str | None = None
    quality_disposition: str | None = None

    @property
    def is_legacy_quality_unknown(self) -> bool:
        return (
            getattr(self, "quality_score_status", None) == LEGACY_UNKNOWN_QUALITY_STATUS
            or getattr(self, "quality_disposition", None) == LEGACY_QUALITY_DISPOSITION
            or self.valuation_card.get("quality_disposition") == LEGACY_QUALITY_DISPOSITION
        )

    def with_legacy_quality_disposition(self) -> DataRoom:
        """Make an old data room safe to read while retaining an audit marker."""

        card = dict(self.valuation_card)
        previous_approval = card.get("finance_approval")
        if previous_approval is not None:
            card.setdefault("legacy_finance_approval", previous_approval)
        card["finance_approval"] = None
        card["confidence"] = "low"
        card["quality_score_status"] = LEGACY_UNKNOWN_QUALITY_STATUS
        card["quality_disposition"] = LEGACY_QUALITY_DISPOSITION
        return DataRoom(
            **{
                **self.__dict__,
                "valuation_card": card,
                "quality_score_status": LEGACY_UNKNOWN_QUALITY_STATUS,
                "quality_disposition": LEGACY_QUALITY_DISPOSITION,
            }
        )

    @property
    def completeness(self) -> float:
        if not self.checklist:
            return 0.0
        ready = sum(1 for item in self.checklist if item.status == "ready")
        return round(ready / len(self.checklist), 4)

    @property
    def is_complete(self) -> bool:
        return self.completeness == 1.0

    @property
    def missing_documents(self) -> tuple[str, ...]:
        return tuple(item.document_id for item in self.checklist if item.status != "ready")

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
            "completeness": self.completeness,
            "is_complete": self.is_complete,
            "missing_documents": list(self.missing_documents),
            "valuation_card": self.valuation_card,
            "quality_score_status": getattr(self, "quality_score_status", None),
            "quality_disposition": getattr(self, "quality_disposition", None),
            "export_audit": list(self.export_audit),
            "created_at": self.created_at.isoformat(),
        }


def build_valuation_view(data: Mapping[str, Any]) -> ValuationInput:
    return ValuationInput.from_mapping(data)


def normalize_margin(case: ValuationCase) -> NormalizedMargin:
    item = case.valuation_input
    quality_score = _require_quality_score(item.quality_score)
    status = item.effective_quality_score_status
    normalized = round((item.gm_ttm * 0.45) + (item.forecast_gm_next_12m * 0.55), 2)
    reasons = ["weighted_ttm_and_forecast_gm"]
    if status == LEGACY_UNKNOWN_QUALITY_STATUS:
        normalized = round(normalized * 0.92, 2)
        reasons.append("legacy_quality_unknown_discount")
        confidence = "low"
    elif quality_score < 0.8:
        normalized = round(normalized * 0.92, 2)
        reasons.append("quality_discount")
        confidence = _confidence(quality_score)
    else:
        confidence = _confidence(quality_score)
    return NormalizedMargin(
        case_id=case.case_id,
        store_id=case.store_id,
        gm_ttm=item.gm_ttm,
        gm_fwd=item.forecast_gm_next_12m,
        normalized_gm=normalized,
        adjustment_reasons=tuple(reasons),
        confidence=confidence,
    )


def ensure_legacy_quality_disposition(
    case: ValuationCase,
    normalized_margin: NormalizedMargin,
) -> NormalizedMargin:
    """Do not let an opaque legacy margin bypass AVM quality handling."""

    if case.valuation_input.effective_quality_score_status != LEGACY_UNKNOWN_QUALITY_STATUS:
        return normalized_margin
    return normalized_margin.with_legacy_quality_disposition()


def rehydrate_legacy_valuation_card(card: Mapping[str, Any]) -> dict[str, Any]:
    rehydrated = dict(card)
    if "depreciation_version" not in rehydrated or rehydrated.get("depreciation_version") is None:
        rehydrated["depreciation_version"] = AVM_DEPRECIATION_LEGACY_VERSION
    if "depreciation_applied" not in rehydrated or rehydrated.get("depreciation_applied") is None:
        rehydrated["depreciation_applied"] = False
    if rehydrated.get("depreciation_version") == AVM_DEPRECIATION_LEGACY_VERSION:
        if "depreciation_disposition" not in rehydrated:
            rehydrated["depreciation_disposition"] = AVM_DEPRECIATION_LEGACY_DISPOSITION_TEXT
    return rehydrated


@dataclass(frozen=True)
class DepreciationCalculationResult:
    depreciation_version: str
    depreciation_applied: bool
    equipment_value_after_depreciation: float
    asset_p50: float
    evidence: dict[str, Any] | None
    delta_from_undepreciated: float
    accumulated_depreciation: float = 0.0
    residual: float = 0.0
    elapsed_months: int = 0


def calculate_depreciation(
    item: ValuationInput,
    *,
    depreciation_version_pin: str | None = None,
) -> DepreciationCalculationResult:
    """Calculate straight-line depreciation or evaluate basis per contract ODP-AVM-DEPRECIATION-CONTRACT-001."""
    if depreciation_version_pin == AVM_DEPRECIATION_LEGACY_VERSION:
        # Explicit operational rollback pin to v0 (R-1)
        equipment_value = item.equipment_fair_value
        asset_p50 = max(
            item.asset_book_value
            + equipment_value
            + item.working_capital
            - item.lease_liability,
            0.0,
        )
        return DepreciationCalculationResult(
            depreciation_version=AVM_DEPRECIATION_LEGACY_VERSION,
            depreciation_applied=False,
            equipment_value_after_depreciation=equipment_value,
            asset_p50=asset_p50,
            evidence=None,
            delta_from_undepreciated=0.0,
        )

    if item.equipment_depreciation_basis is None:
        raise ValueError(
            "equipment_depreciation_basis is required (must be 'original_cost' or 'appraised_fair_value')"
        )

    if item.equipment_depreciation_basis == "appraised_fair_value":
        # Appraised fair value is already net of age; do not depreciate twice (C-1)
        equipment_value = item.equipment_fair_value
        asset_p50 = max(
            item.asset_book_value
            + equipment_value
            + item.working_capital
            - item.lease_liability,
            0.0,
        )
        version = AVM_DEPRECIATION_NOT_APPLICABLE_VERSION
        evidence = {
            "basis": "appraised_fair_value",
            "equipment_value_after_depreciation": equipment_value,
            "method": "none",
            "version": version,
        }
        return DepreciationCalculationResult(
            depreciation_version=version,
            depreciation_applied=False,
            equipment_value_after_depreciation=equipment_value,
            asset_p50=asset_p50,
            evidence=evidence,
            delta_from_undepreciated=0.0,
        )

    if item.equipment_depreciation_basis == "original_cost":
        # Straight-line depreciation based on original acquisition cost (C-1, C-3, C-5)
        if item.asset_book_value_includes_equipment is True:
            raise ValueError(
                "asset_book_value_includes_equipment cannot be True when equipment_depreciation_basis is 'original_cost'"
            )
        if item.equipment_original_cost is None:
            raise ValueError(
                "equipment_original_cost is required when equipment_depreciation_basis is 'original_cost'"
            )
        cost = float(item.equipment_original_cost)
        if cost < 0.0 or math.isnan(cost) or math.isinf(cost):
            raise ValueError("equipment_original_cost must be a non-negative finite number")

        if item.useful_life_months is None or item.useful_life_months < 1:
            raise ValueError("useful_life_months is required and must be >= 1")
        useful_life = int(item.useful_life_months)

        if item.asset_in_service_date is None:
            raise ValueError(
                "asset_in_service_date is required when equipment_depreciation_basis is 'original_cost'"
            )
        if item.depreciation_effective_date is None:
            raise ValueError(
                "depreciation_effective_date is required when equipment_depreciation_basis is 'original_cost'"
            )
        if item.residual_value_ratio is None:
            raise ValueError(
                "residual_value_ratio is required when equipment_depreciation_basis is 'original_cost'"
            )
        ratio = float(item.residual_value_ratio)
        if not (0.0 <= ratio <= 1.0) or math.isnan(ratio) or math.isinf(ratio):
            raise ValueError("residual_value_ratio is required and must be between 0.0 and 1.0")

        if item.depreciation_method != "straight_line":
            raise ValueError("depreciation_method must be 'straight_line'")

        in_service = _parse_date(item.asset_in_service_date)
        effective = _parse_date(item.depreciation_effective_date)
        elapsed_months, negative_clamped = _calculate_elapsed_months(in_service, effective)

        residual = round(cost * ratio, 2)
        depreciable = max(0.0, round(cost - residual, 2))
        monthly = depreciable / max(1, useful_life)
        accumulated = round(min(depreciable, monthly * elapsed_months), 2)
        equipment_value = round(cost - accumulated, 2)

        asset_p50 = max(
            item.asset_book_value
            + equipment_value
            + item.working_capital
            - item.lease_liability,
            0.0,
        )
        version = AVM_DEPRECIATION_VERSION
        evidence = {
            "basis": "original_cost",
            "in_service_date": item.asset_in_service_date,
            "effective_date": item.depreciation_effective_date,
            "elapsed_months": elapsed_months,
            "useful_life_months": useful_life,
            "residual_value_ratio": ratio,
            "residual": residual,
            "accumulated_depreciation": accumulated,
            "equipment_value_after_depreciation": equipment_value,
            "method": "straight_line",
            "version": version,
        }
        if negative_clamped:
            evidence["negative_elapsed_clamped"] = True

        undepreciated_asset_p50 = max(
            item.asset_book_value + cost + item.working_capital - item.lease_liability,
            0.0,
        )
        delta = round(asset_p50 - undepreciated_asset_p50, 2)

        return DepreciationCalculationResult(
            depreciation_version=version,
            depreciation_applied=True,
            equipment_value_after_depreciation=equipment_value,
            asset_p50=asset_p50,
            evidence=evidence,
            delta_from_undepreciated=delta,
            accumulated_depreciation=accumulated,
            residual=residual,
            elapsed_months=elapsed_months,
        )

    raise ValueError(
        f"Unsupported equipment_depreciation_basis: {item.equipment_depreciation_basis!r}; must be 'original_cost' or 'appraised_fair_value'"
    )


def value_store(
    case: ValuationCase,
    normalized_margin: NormalizedMargin,
    *,
    depreciation_version_pin: str | None = None,
) -> ValuationReport:
    item = case.valuation_input
    _require_quality_score(item.quality_score)
    normalized_margin = ensure_legacy_quality_disposition(case, normalized_margin)
    quality_status = item.effective_quality_score_status
    income_p50 = normalized_margin.normalized_gm * 2.8

    dep_calc = calculate_depreciation(item, depreciation_version_pin=depreciation_version_pin)
    asset_p50 = dep_calc.asset_p50

    multiple = _median(item.comparable_multiples) if item.comparable_multiples else 2.4
    market_p50 = normalized_margin.normalized_gm * multiple * (1 - item.liquidity_discount)

    source_snapshot_ids = list(item.source_snapshot_ids)
    asset_evidence = {
        "asset_book_value": item.asset_book_value,
        "equipment_fair_value": item.equipment_fair_value,
        "working_capital": item.working_capital,
        "lease_liability": item.lease_liability,
        "source_snapshot_ids": source_snapshot_ids,
    }
    if dep_calc.evidence is not None:
        asset_evidence["depreciation"] = dep_calc.evidence

    base_lenses = (
        _lens(
            "income",
            income_p50,
            "normalized_gm_multiple",
            {
                "multiple": 2.8,
                "gm_ttm": item.gm_ttm,
                "gm_fwd": item.forecast_gm_next_12m,
                "normalized_gm": normalized_margin.normalized_gm,
                "source_snapshot_ids": source_snapshot_ids,
            },
        ),
        _lens(
            "asset",
            asset_p50,
            "net_asset_value",
            asset_evidence,
        ),
        _lens(
            "market",
            market_p50,
            "comparable_multiple_with_liquidity_discount",
            {
                "multiple": multiple,
                "comparable_multiples": list(item.comparable_multiples),
                "liquidity_discount": item.liquidity_discount,
                "evidence_status": "ready"
                if item.comparable_multiples
                else "missing_default_multiple",
                "source_snapshot_ids": source_snapshot_ids,
            },
        ),
    )
    p10 = round(sum(lens.p10 for lens in base_lenses) / len(base_lenses), 2)
    p50 = round(sum(lens.p50 for lens in base_lenses) / len(base_lenses), 2)
    p90 = round(sum(lens.p90 for lens in base_lenses) / len(base_lenses), 2)
    fair = PriceBand(p10=p10, p50=p50, p90=p90)
    blended = LensValuation(
        lens="blended",
        p10=p10,
        p50=p50,
        p90=p90,
        method="three_lens_average_fair_price_band",
        evidence={
            "included_lenses": [lens.lens for lens in base_lenses],
            "reserve_formula": "fair_price.p10 * 0.97",
            "asking_formula": "fair_price.p90 * 1.05",
        },
    )
    return ValuationReport(
        report_id=f"avm-report-{uuid4()}",
        case_id=case.case_id,
        store_id=case.store_id,
        normalized_margin=normalized_margin,
        lenses=base_lenses + (blended,),
        fair_price=fair,
        reserve_price=round(p10 * 0.97, 2),
        asking_price=round(p90 * 1.05, 2),
        confidence=normalized_margin.confidence,
        quality_score_status=quality_status,
        quality_disposition=(
            LEGACY_QUALITY_DISPOSITION
            if quality_status == LEGACY_UNKNOWN_QUALITY_STATUS
            else None
        ),
        model_version=AVM_MODEL_VERSION,
        feature_version=AVM_FEATURE_VERSION,
        prediction_origin_time=item.prediction_origin_time,
        valued_at=datetime.now(UTC),
        depreciation_version=dep_calc.depreciation_version,
        depreciation_applied=dep_calc.depreciation_applied,
    )


def build_model_valuation_report(
    case: ValuationCase,
    normalized_margin: NormalizedMargin,
    *,
    p10: float,
    p50: float,
    p90: float,
    model_version: str,
    execution_metadata: Mapping[str, Any],
    depreciation_version: str | None = None,
    depreciation_applied: bool | None = None,
    asset_p50: float | None = None,
    depreciation_evidence: Mapping[str, Any] | None = None,
) -> ValuationReport:
    """Build policy outputs from an already executed approved model interval."""

    _require_quality_score(case.valuation_input.quality_score)
    normalized_margin = ensure_legacy_quality_disposition(case, normalized_margin)
    quality_status = case.valuation_input.effective_quality_score_status

    fair = PriceBand(
        p10=round(float(p10), 2),
        p50=round(float(p50), 2),
        p90=round(float(p90), 2),
    )
    meta = dict(execution_metadata)
    model_lens = LensValuation(
        lens="approved_model",
        p10=fair.p10,
        p50=fair.p50,
        p90=fair.p90,
        method="approved_oss_model_artifact",
        evidence=meta,
    )
    lenses: list[LensValuation] = [model_lens]
    if asset_p50 is not None:
        asset_evidence = {
            "asset_book_value": case.valuation_input.asset_book_value,
            "equipment_fair_value": case.valuation_input.equipment_fair_value,
            "working_capital": case.valuation_input.working_capital,
            "lease_liability": case.valuation_input.lease_liability,
            "source_snapshot_ids": list(case.valuation_input.source_snapshot_ids),
        }
        if depreciation_evidence is not None:
            asset_evidence["depreciation"] = dict(depreciation_evidence)
        lenses.append(
            LensValuation(
                lens="asset",
                p10=round(asset_p50 * 0.82, 2),
                p50=round(asset_p50, 2),
                p90=round(asset_p50 * 1.18, 2),
                method="net_asset_value",
                evidence=asset_evidence,
            )
        )

    dep_version = depreciation_version or (
        AVM_DEPRECIATION_NOT_APPLICABLE_VERSION
        if case.valuation_input.equipment_depreciation_basis == "appraised_fair_value"
        else (
            AVM_DEPRECIATION_LEGACY_VERSION
            if case.valuation_input.equipment_depreciation_basis is None
            else AVM_DEPRECIATION_VERSION
        )
    )
    dep_applied = (
        depreciation_applied
        if depreciation_applied is not None
        else (case.valuation_input.equipment_depreciation_basis == "original_cost")
    )
    return ValuationReport(
        report_id=f"avm-report-{uuid4()}",
        case_id=case.case_id,
        store_id=case.store_id,
        normalized_margin=normalized_margin,
        lenses=tuple(lenses),
        fair_price=fair,
        reserve_price=round(fair.p10 * 0.97, 2),
        asking_price=round(fair.p90 * 1.05, 2),
        confidence=normalized_margin.confidence,
        quality_score_status=quality_status,
        quality_disposition=(
            LEGACY_QUALITY_DISPOSITION
            if quality_status == LEGACY_UNKNOWN_QUALITY_STATUS
            else None
        ),
        model_version=model_version,
        feature_version=AVM_FEATURE_VERSION,
        prediction_origin_time=case.valuation_input.prediction_origin_time,
        valued_at=datetime.now(UTC),
        execution_metadata=meta,
        depreciation_version=dep_version,
        depreciation_applied=dep_applied,
    )


def generate_data_room(report: ValuationReport) -> DataRoom:
    source_snapshot_ids = _report_source_snapshot_ids(report)
    checklist = (
        DataRoomDocument(
            "financials",
            "Normalized GM and forecast evidence",
            "ready" if report.normalized_margin.normalized_gm > 0 else "missing",
            source_snapshot_ids[0] if source_snapshot_ids else None,
        ),
        DataRoomDocument(
            "assets",
            "Asset ledger and equipment valuation",
            "ready" if _lens_value(report, "asset") > 0 else "missing",
            source_snapshot_ids[0] if source_snapshot_ids else None,
        ),
        DataRoomDocument(
            "lease",
            "Lease and liability summary",
            "ready" if "lease_liability" in _lens_evidence(report, "asset") else "missing",
            source_snapshot_ids[0] if source_snapshot_ids else None,
        ),
        DataRoomDocument(
            "comparables",
            "Comparable transaction evidence",
            "ready"
            if _lens_evidence(report, "market").get("evidence_status") == "ready"
            else "missing",
            source_snapshot_ids[0] if source_snapshot_ids else None,
        ),
        DataRoomDocument(
            "valuation_card",
            "Fair, reserve, and asking valuation card",
            "ready",
            report.report_id,
        ),
    )
    val_card: dict[str, Any] = {
        "case_id": report.case_id,
        "store_id": report.store_id,
        "fair_price": report.fair_price.to_dict(),
        "reserve_price": report.reserve_price,
        "asking_price": report.asking_price,
        "confidence": report.confidence,
        "quality_score_status": getattr(report, "quality_score_status", None),
        "quality_disposition": getattr(report, "quality_disposition", None),
        "model_version": report.model_version,
        "valuation_version": report.valuation_version,
        "depreciation_version": report.depreciation_version,
        "depreciation_applied": report.depreciation_applied,
        "equipment_depreciation_basis": (
            _lens_evidence(report, "asset").get("depreciation", {}).get("basis")
        ),
        "finance_approval": (
            report.finance_approval.to_dict() if report.finance_approval else None
        ),
    }
    if report.depreciation_version == AVM_DEPRECIATION_LEGACY_VERSION:
        val_card["depreciation_disposition"] = "本估值採 2026-09-03 前之計算版本，資產折舊未納入"

    return DataRoom(
        dataroom_id=f"avm-dataroom-{uuid4()}",
        case_id=report.case_id,
        checklist=checklist,
        valuation_card=val_card,
        quality_score_status=getattr(report, "quality_score_status", None),
        quality_disposition=getattr(report, "quality_disposition", None),
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


def _lens_evidence(report: ValuationReport, lens_name: str) -> dict[str, Any]:
    for lens in report.lenses:
        if lens.lens == lens_name:
            return lens.evidence
    return {}


def _lens_value(report: ValuationReport, lens_name: str) -> float:
    for lens in report.lenses:
        if lens.lens == lens_name:
            return lens.p50
    return 0.0


def _report_source_snapshot_ids(report: ValuationReport) -> tuple[str, ...]:
    source_ids: list[str] = []
    for lens in report.lenses:
        values = lens.evidence.get("source_snapshot_ids", ())
        for value in values:
            item = str(value)
            if item not in source_ids:
                source_ids.append(item)
    return tuple(source_ids)


def _bounded(value: Any, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return min(max(float(value), minimum), maximum)


def _optional_bounded(
    value: Any, *, minimum: float = 0.0, maximum: float = 1.0
) -> float | None:
    return None if value is None else _bounded(value, minimum=minimum, maximum=maximum)


def _require_quality_score(value: float | None) -> float:
    if value is None:
        raise ValueError(QUALITY_SCORE_REQUIRED_MESSAGE)
    return value


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _date_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if "T" in s:
        s = s.split("T")[0]
    elif " " in s:
        s = s.split(" ")[0]
    return date.fromisoformat(s)


def _calculate_elapsed_months(in_service: date, effective: date) -> tuple[int, bool]:
    day_offset = 1 if effective.day < in_service.day else 0
    raw_months = (
        (effective.year - in_service.year) * 12
        + (effective.month - in_service.month)
        - day_offset
    )
    if raw_months < 0:
        return 0, True
    return raw_months, False
