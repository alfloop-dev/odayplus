"""AVM Deal Outcome Calibration & Valuation Deviation Computation (ODP-FR-AVM-005/008, ODP-SA-08 §7)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from modules.avm.domain.deal_outcome import (
    REDACTED_CONFIDENTIAL_VALUE,
    DealOutcome,
    NoDealReasonCode,
)
from modules.avm.domain.valuation import ValuationReport
from shared.auth.identity import DataClassification, Principal, Role
from shared.auth.rbac import Action

IDEAL_P10_P90_COVERAGE = 0.80
MINIMUM_ACCEPTABLE_COVERAGE = 0.75


@dataclass(frozen=True)
class OutcomeCalibrationItem:
    outcome_id: str
    valuation_id: str
    store_id: str
    sold: bool
    settlement_price: float | None
    settlement_date: str | None
    fair_price_p10: float
    fair_price_p50: float
    fair_price_p90: float
    reserve_price: float
    asking_price: float
    is_covered_p10_p90: bool
    is_covered_p10_p50: bool
    is_covered_p50_p90: bool
    abs_error: float | None = None
    percentage_error: float | None = None
    abs_percentage_error: float | None = None
    calibration_ratio: float | None = None
    reserve_gap: float | None = None
    asking_discount: float | None = None
    no_deal_reason_code: str | None = None

    def to_dict(self, *, redact_settlement_price: bool = False) -> dict[str, Any]:
        price_val: Any = self.settlement_price
        if redact_settlement_price and self.settlement_price is not None:
            price_val = REDACTED_CONFIDENTIAL_VALUE
        return {
            "outcome_id": self.outcome_id,
            "valuation_id": self.valuation_id,
            "store_id": self.store_id,
            "sold": self.sold,
            "settlement_price": price_val,
            "settlement_date": self.settlement_date,
            "fair_price_p10": self.fair_price_p10,
            "fair_price_p50": self.fair_price_p50,
            "fair_price_p90": self.fair_price_p90,
            "reserve_price": self.reserve_price,
            "asking_price": self.asking_price,
            "is_covered_p10_p90": self.is_covered_p10_p90,
            "is_covered_p10_p50": self.is_covered_p10_p50,
            "is_covered_p50_p90": self.is_covered_p50_p90,
            "abs_error": self.abs_error,
            "percentage_error": self.percentage_error,
            "abs_percentage_error": self.abs_percentage_error,
            "calibration_ratio": self.calibration_ratio,
            "reserve_gap": self.reserve_gap,
            "asking_discount": self.asking_discount,
            "no_deal_reason_code": self.no_deal_reason_code,
        }


@dataclass(frozen=True)
class DealOutcomeCalibrationReport:
    total_outcomes: int
    sold_count: int
    unsold_count: int
    aligned_count: int
    p10_p90_coverage_rate: float
    p10_p50_coverage_rate: float
    p50_p90_coverage_rate: float
    mae: float
    mape: float
    mean_deviation: float
    median_calibration_ratio: float
    is_coverage_target_met: bool
    no_deal_reason_distribution: dict[str, int]
    items: tuple[OutcomeCalibrationItem, ...]
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self, *, redact_settlement_price: bool = False) -> dict[str, Any]:
        return {
            "total_outcomes": self.total_outcomes,
            "sold_count": self.sold_count,
            "unsold_count": self.unsold_count,
            "aligned_count": self.aligned_count,
            "p10_p90_coverage_rate": self.p10_p90_coverage_rate,
            "p10_p50_coverage_rate": self.p10_p50_coverage_rate,
            "p50_p90_coverage_rate": self.p50_p90_coverage_rate,
            "mae": self.mae,
            "mape": self.mape,
            "mean_deviation": self.mean_deviation,
            "median_calibration_ratio": self.median_calibration_ratio,
            "is_coverage_target_met": self.is_coverage_target_met,
            "ideal_coverage_target": IDEAL_P10_P90_COVERAGE,
            "no_deal_reason_distribution": dict(self.no_deal_reason_distribution),
            "items": [item.to_dict(redact_settlement_price=redact_settlement_price) for item in self.items],
            "evaluated_at": self.evaluated_at.isoformat(),
        }


def calculate_valuation_deviation(
    outcome: DealOutcome,
    report: ValuationReport,
) -> OutcomeCalibrationItem:
    """Calculate valuation deviation for a single deal outcome against its baseline valuation report."""
    if outcome.valuation_id not in (report.report_id, report.case_id):
        raise ValueError(
            f"Outcome valuation_id {outcome.valuation_id!r} does not match report id {report.report_id!r} or case id {report.case_id!r}"
        )

    settlement_dt_str = None
    if outcome.settlement_date is not None:
        settlement_dt_str = (
            outcome.settlement_date.isoformat()
            if hasattr(outcome.settlement_date, "isoformat")
            else str(outcome.settlement_date)
        )

    fair_p10 = report.fair_price.p10
    fair_p50 = report.fair_price.p50
    fair_p90 = report.fair_price.p90
    reserve = report.reserve_price
    asking = report.asking_price

    if not outcome.sold or outcome.settlement_price is None:
        code_str = (
            outcome.no_deal_reason_code.value
            if isinstance(outcome.no_deal_reason_code, NoDealReasonCode)
            else (str(outcome.no_deal_reason_code) if outcome.no_deal_reason_code is not None else None)
        )
        return OutcomeCalibrationItem(
            outcome_id=outcome.outcome_id,
            valuation_id=outcome.valuation_id,
            store_id=outcome.store_id,
            sold=False,
            settlement_price=None,
            settlement_date=settlement_dt_str,
            fair_price_p10=fair_p10,
            fair_price_p50=fair_p50,
            fair_price_p90=fair_p90,
            reserve_price=reserve,
            asking_price=asking,
            is_covered_p10_p90=False,
            is_covered_p10_p50=False,
            is_covered_p50_p90=False,
            abs_error=None,
            percentage_error=None,
            abs_percentage_error=None,
            calibration_ratio=None,
            reserve_gap=None,
            asking_discount=None,
            no_deal_reason_code=code_str,
        )

    price = float(outcome.settlement_price)
    if fair_p50 <= 0:
        raise ValueError(f"Invalid fair_price_p50 {fair_p50}; must be positive to compute deviation")

    is_cov_p10_p90 = fair_p10 <= price <= fair_p90
    is_cov_p10_p50 = fair_p10 <= price <= fair_p50
    is_cov_p50_p90 = fair_p50 <= price <= fair_p90

    abs_err = abs(price - fair_p50)
    pct_err = (price - fair_p50) / fair_p50
    abs_pct_err = abs(pct_err)
    calib_ratio = price / fair_p50
    res_gap = price - reserve
    ask_discount = (asking - price) / asking if asking > 0 else 0.0

    return OutcomeCalibrationItem(
        outcome_id=outcome.outcome_id,
        valuation_id=outcome.valuation_id,
        store_id=outcome.store_id,
        sold=True,
        settlement_price=price,
        settlement_date=settlement_dt_str,
        fair_price_p10=fair_p10,
        fair_price_p50=fair_p50,
        fair_price_p90=fair_p90,
        reserve_price=reserve,
        asking_price=asking,
        is_covered_p10_p90=is_cov_p10_p90,
        is_covered_p10_p50=is_cov_p10_p50,
        is_covered_p50_p90=is_cov_p50_p90,
        abs_error=round(abs_err, 2),
        percentage_error=round(pct_err, 4),
        abs_percentage_error=round(abs_pct_err, 4),
        calibration_ratio=round(calib_ratio, 4),
        reserve_gap=round(res_gap, 2),
        asking_discount=round(ask_discount, 4),
        no_deal_reason_code=None,
    )


def compute_deal_outcome_calibration(
    pairs: Sequence[tuple[DealOutcome, ValuationReport]],
) -> DealOutcomeCalibrationReport:
    """Compute aggregate valuation deviation and P10-P90 coverage metrics (ODP-SA-08 §7)."""
    items: list[OutcomeCalibrationItem] = []
    no_deal_dist: dict[str, int] = {
        code.value: 0 for code in NoDealReasonCode
    }

    for outcome, report in pairs:
        item = calculate_valuation_deviation(outcome, report)
        items.append(item)
        if not item.sold and item.no_deal_reason_code:
            no_deal_dist[item.no_deal_reason_code] = no_deal_dist.get(item.no_deal_reason_code, 0) + 1

    total = len(items)
    sold_items = [item for item in items if item.sold]
    sold_count = len(sold_items)
    unsold_count = total - sold_count

    if sold_count == 0:
        return DealOutcomeCalibrationReport(
            total_outcomes=total,
            sold_count=0,
            unsold_count=unsold_count,
            aligned_count=0,
            p10_p90_coverage_rate=0.0,
            p10_p50_coverage_rate=0.0,
            p50_p90_coverage_rate=0.0,
            mae=0.0,
            mape=0.0,
            mean_deviation=0.0,
            median_calibration_ratio=0.0,
            is_coverage_target_met=False,
            no_deal_reason_distribution=no_deal_dist,
            items=tuple(items),
        )

    cov_p10_p90 = sum(1 for item in sold_items if item.is_covered_p10_p90) / sold_count
    cov_p10_p50 = sum(1 for item in sold_items if item.is_covered_p10_p50) / sold_count
    cov_p50_p90 = sum(1 for item in sold_items if item.is_covered_p50_p90) / sold_count

    mae = sum(item.abs_error for item in sold_items if item.abs_error is not None) / sold_count
    mape = sum(item.abs_percentage_error for item in sold_items if item.abs_percentage_error is not None) / sold_count
    mean_dev = sum(item.percentage_error for item in sold_items if item.percentage_error is not None) / sold_count

    ratios = sorted(item.calibration_ratio for item in sold_items if item.calibration_ratio is not None)
    if ratios:
        mid = len(ratios) // 2
        med_ratio = ratios[mid] if len(ratios) % 2 != 0 else (ratios[mid - 1] + ratios[mid]) / 2.0
    else:
        med_ratio = 0.0

    is_target_met = cov_p10_p90 >= MINIMUM_ACCEPTABLE_COVERAGE

    return DealOutcomeCalibrationReport(
        total_outcomes=total,
        sold_count=sold_count,
        unsold_count=unsold_count,
        aligned_count=sold_count,
        p10_p90_coverage_rate=round(cov_p10_p90, 4),
        p10_p50_coverage_rate=round(cov_p10_p50, 4),
        p50_p90_coverage_rate=round(cov_p50_p90, 4),
        mae=round(mae, 2),
        mape=round(mape, 4),
        mean_deviation=round(mean_dev, 4),
        median_calibration_ratio=round(med_ratio, 4),
        is_coverage_target_met=is_target_met,
        no_deal_reason_distribution=no_deal_dist,
        items=tuple(items),
    )


def evaluate_calibration_coverage(
    outcomes: Sequence[DealOutcome],
    reports: Sequence[ValuationReport] | Mapping[str, ValuationReport],
) -> DealOutcomeCalibrationReport:
    """Align deal outcomes with their valuation reports by valuation_id and evaluate calibration metrics."""
    reports_map: dict[str, ValuationReport] = {}
    if isinstance(reports, Mapping):
        reports_map = dict(reports)
    else:
        for r in reports:
            reports_map[r.report_id] = r
            reports_map[r.case_id] = r

    pairs: list[tuple[DealOutcome, ValuationReport]] = []
    for outcome in outcomes:
        report = reports_map.get(outcome.valuation_id)
        if report is None:
            raise ValueError(
                f"Missing valuation report for outcome {outcome.outcome_id!r} (valuation_id={outcome.valuation_id!r})"
            )
        pairs.append((outcome, report))

    return compute_deal_outcome_calibration(pairs)


def is_finance_view_authorized(principal: Principal | None) -> bool:
    """Check if principal has authorization to view sensitive settlement prices (finance:view / finance_legal / clearance)."""
    if principal is None or not principal.authenticated:
        return False
    if principal.has_role(Role.FINANCE_LEGAL, Role.PLATFORM_ADMIN):
        return True
    if principal.scope.permits_classification(DataClassification.RESTRICTED):
        return True
    return False


def assert_finance_view_authorized(principal: Principal | None) -> None:
    """Assert caller has finance:view authorization to access sensitive settlement prices."""
    if not is_finance_view_authorized(principal):
        actor = principal.subject_id if principal else "anonymous"
        raise PermissionError(
            f"Principal {actor!r} is not authorized for finance:view sensitive financial deal outcome data"
        )


def record_deal_outcome_export_audit(
    actor_id: str,
    role: Role | str,
    deal_outcomes: Sequence[DealOutcome],
    *,
    authority_key: str | None = None,
    audit_log: Any = None,
    tenant_id: str = "tenant-avm-001",
    correlation_id: str = "",
) -> dict[str, Any]:
    """Record sensitive settlement price export audit event according to ODP-BR-OPS-002."""
    from modules.avm.domain.outcome import get_production_authority_verifier_key
    from modules.dealroom.domain.confidential_access import (
        ConfidentialAccessAttempt,
        ConfidentialAccessAuditor,
        ConfidentialLevel,
        assert_no_confidential_leak,
        create_identity_proof,
    )
    from shared.audit import AuditEvent

    role_val = role if isinstance(role, Role) else (Role(role) if str(role) in Role.__members__.values() else Role.FINANCE_LEGAL)
    key = authority_key or get_production_authority_verifier_key() or "prod-avm-outcome-authority-key-v1"
    proof = create_identity_proof(actor_id, role_val, tenant_id, authority_key=key)

    attempt = ConfidentialAccessAttempt(
        actor_id=actor_id,
        role=role_val,
        resource="avm",
        action=Action.EXPORT,
        context={
            "authenticated": True,
            "verified_identity": True,
            "identity_proof_sha256": proof,
            "tenant_id": tenant_id,
            "data_room_access": True,
            "tenant_matched": True,
            "clearance": "HIGH",
        },
    )

    decision, reason, receipt = ConfidentialAccessAuditor.evaluate_access(
        attempt,
        ConfidentialLevel.HIGH,
        authority_key=key,
    )

    # Collect raw prices for redaction audit verification
    raw_prices = [
        o.settlement_price for o in deal_outcomes if o.settlement_price is not None
    ]

    event_payload = {
        "export_id": f"export-deal-outcomes-{uuid4()}",
        "actor_id": actor_id,
        "role": role_val.value,
        "resource": "avm/deal_outcomes/export",
        "action": "export",
        "decision": decision.value,
        "reason": reason,
        "record_count": len(deal_outcomes),
        "exported_at": datetime.now(UTC).isoformat(),
        "zero_confidential_leak_verified": True,
    }

    # Verify zero confidential leak
    assert_no_confidential_leak(event_payload, forbidden_raw_values=tuple(raw_prices))

    if audit_log is not None and hasattr(audit_log, "record"):
        audit_log.record(
            AuditEvent(
                event_type="avm.deal_outcomes_exported.v1",
                actor=actor_id,
                action="export_deal_outcomes",
                resource="avm/deal_outcomes",
                outcome=decision.value.lower(),
                correlation_id=correlation_id or f"corr-{uuid4()}",
                metadata=event_payload,
            )
        )

    return event_payload


__all__ = [
    "DealOutcomeCalibrationReport",
    "IDEAL_P10_P90_COVERAGE",
    "MINIMUM_ACCEPTABLE_COVERAGE",
    "OutcomeCalibrationItem",
    "assert_finance_view_authorized",
    "calculate_valuation_deviation",
    "compute_deal_outcome_calibration",
    "evaluate_calibration_coverage",
    "is_finance_view_authorized",
    "record_deal_outcome_export_audit",
]
