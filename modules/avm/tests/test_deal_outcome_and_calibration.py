"""Unit tests for AVM deal outcome domain, liquidity training record derivation, and valuation calibration."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from modules.avm.application.calibration import (
    assert_finance_view_authorized,
    calculate_valuation_deviation,
    compute_deal_outcome_calibration,
    evaluate_calibration_coverage,
    is_finance_view_authorized,
    record_deal_outcome_export_audit,
)
from modules.avm.application.valuation import AVMService
from modules.avm.domain.deal_outcome import (
    REDACTED_CONFIDENTIAL_VALUE,
    DealOutcome,
    NoDealReasonCode,
)
from modules.avm.domain.liquidity import LiquidityTrainingRecord
from modules.avm.domain.valuation import (
    LensValuation,
    NormalizedMargin,
    PriceBand,
    ValuationInput,
    ValuationReport,
)
from modules.avm.infrastructure.repositories import InMemoryAVMRepository
from modules.dealroom.domain.confidential_access import create_identity_proof
from shared.audit import InMemoryAuditLog
from shared.auth.identity import Principal, Role


def _make_dummy_valuation_report(
    case_id: str = "case-001",
    report_id: str = "report-001",
    store_id: str = "store-100",
    p10: float = 8_000_000.0,
    p50: float = 10_000_000.0,
    p90: float = 12_000_000.0,
    reserve: float = 7_760_000.0,
    asking: float = 12_600_000.0,
) -> ValuationReport:
    margin = NormalizedMargin(
        case_id=case_id,
        store_id=store_id,
        gm_ttm=1_000_000.0,
        gm_fwd=1_100_000.0,
        normalized_gm=1_055_000.0,
        adjustment_reasons=("weighted_ttm_and_forecast_gm",),
        confidence="high",
    )
    lens = LensValuation(
        lens="income",
        p10=p10,
        p50=p50,
        p90=p90,
        method="normalized_gm_multiple",
        evidence={},
    )
    return ValuationReport(
        report_id=report_id,
        case_id=case_id,
        store_id=store_id,
        normalized_margin=margin,
        lenses=(lens,),
        fair_price=PriceBand(p10=p10, p50=p50, p90=p90),
        reserve_price=reserve,
        asking_price=asking,
        confidence="high",
        model_version="dealroom-avm-baseline-v1",
        feature_version="valuation-view-v1",
        prediction_origin_time=datetime.now(UTC),
        valued_at=datetime.now(UTC),
    )


class TestLiquidityTrainingRecordExtension:
    def test_liquidity_training_record_defaults_and_serialization(self) -> None:
        rec = LiquidityTrainingRecord(
            duration_days=45.0,
            sold=True,
            features={"gm_ttm": 1000000.0, "sqm": 50.0},
        )
        assert rec.duration_days == 45.0
        assert rec.sold is True
        assert rec.settlement_price is None
        assert rec.no_deal_reason_code is None
        assert rec.deal_terms == {}
        assert rec.valuation_id is None

        d = rec.to_dict()
        assert d["duration_days"] == 45.0
        assert d["sold"] is True
        assert d["settlement_price"] is None
        assert d["no_deal_reason_code"] is None

    def test_liquidity_training_record_from_mapping_extended_fields(self) -> None:
        data = {
            "duration_days": 60.0,
            "sold": True,
            "features": {"f1": 1.0, "f2": 2.0},
            "settlement_price": 9_500_000.0,
            "no_deal_reason_code": None,
            "deal_terms": {"earnest_money_twd": 500_000, "contingency": "financing"},
            "valuation_id": "val-123",
        }
        rec = LiquidityTrainingRecord.from_mapping(data)
        assert rec.duration_days == 60.0
        assert rec.sold is True
        assert rec.settlement_price == 9_500_000.0
        assert rec.no_deal_reason_code is None
        assert rec.deal_terms["earnest_money_twd"] == 500_000
        assert rec.valuation_id == "val-123"

    def test_liquidity_training_record_unsold_mapping(self) -> None:
        data = {
            "days_on_market": 90.0,
            "sold": False,
            "features": {"f1": 1.0},
            "no_deal_reason_code": "PRICE_GAP",
            "valuation_id": "val-456",
        }
        rec = LiquidityTrainingRecord.from_mapping(data)
        assert rec.duration_days == 90.0
        assert rec.sold is False
        assert rec.settlement_price is None
        assert rec.no_deal_reason_code == "PRICE_GAP"
        assert rec.valuation_id == "val-456"


class TestDealOutcomeDomain:
    def test_create_valid_sold_deal_outcome(self) -> None:
        outcome = DealOutcome(
            outcome_id="out-001",
            valuation_id="val-001",
            store_id="store-100",
            sold=True,
            settlement_price=10_500_000.0,
            settlement_date=date(2026, 8, 15),
            duration_days=42.0,
            deal_terms={"commission_rate": 0.02},
            source_authority="official_dealroom",
        )
        assert outcome.outcome_id == "out-001"
        assert outcome.valuation_id == "val-001"
        assert outcome.store_id == "store-100"
        assert outcome.sold is True
        assert outcome.settlement_price == 10_500_000.0
        assert outcome.duration_days == 42.0
        assert outcome.no_deal_reason_code is None

    def test_create_valid_unsold_deal_outcome(self) -> None:
        outcome = DealOutcome(
            outcome_id="out-002",
            valuation_id="val-002",
            store_id="store-101",
            sold=False,
            duration_days=120.0,
            no_deal_reason_code=NoDealReasonCode.PRICE_GAP,
            deal_terms={"buyer_offer_twd": 8_000_000, "seller_ask_twd": 12_000_000},
        )
        assert outcome.sold is False
        assert outcome.settlement_price is None
        assert outcome.no_deal_reason_code == NoDealReasonCode.PRICE_GAP

    def test_valuation_id_is_mandatory(self) -> None:
        with pytest.raises(ValueError, match="valuation_id is required"):
            DealOutcome(
                outcome_id="out-003",
                valuation_id="",
                store_id="store-100",
                sold=True,
                settlement_price=10_000_000.0,
            )

    def test_store_id_is_mandatory(self) -> None:
        with pytest.raises(ValueError, match="store_id is required"):
            DealOutcome(
                outcome_id="out-004",
                valuation_id="val-001",
                store_id="",
                sold=True,
                settlement_price=10_000_000.0,
            )

    def test_sold_requires_positive_settlement_price(self) -> None:
        with pytest.raises(ValueError, match="settlement_price must be a positive number"):
            DealOutcome(
                outcome_id="out-005",
                valuation_id="val-001",
                store_id="store-100",
                sold=True,
                settlement_price=None,
            )
        with pytest.raises(ValueError, match="settlement_price must be a positive number"):
            DealOutcome(
                outcome_id="out-006",
                valuation_id="val-001",
                store_id="store-100",
                sold=True,
                settlement_price=0.0,
            )

    def test_sold_cannot_have_no_deal_reason_code(self) -> None:
        with pytest.raises(ValueError, match="no_deal_reason_code must be None when sold is True"):
            DealOutcome(
                outcome_id="out-007",
                valuation_id="val-001",
                store_id="store-100",
                sold=True,
                settlement_price=10_000_000.0,
                no_deal_reason_code=NoDealReasonCode.PRICE_GAP,
            )

    def test_unsold_requires_valid_no_deal_reason_code(self) -> None:
        with pytest.raises(ValueError, match="no_deal_reason_code is required when sold is False"):
            DealOutcome(
                outcome_id="out-008",
                valuation_id="val-001",
                store_id="store-100",
                sold=False,
                no_deal_reason_code=None,
            )
        with pytest.raises(ValueError, match="Invalid no_deal_reason_code"):
            DealOutcome(
                outcome_id="out-009",
                valuation_id="val-001",
                store_id="store-100",
                sold=False,
                no_deal_reason_code="INVALID_REASON",
            )

    def test_derive_liquidity_training_record_from_deal_outcome(self) -> None:
        outcome = DealOutcome(
            outcome_id="out-010",
            valuation_id="val-010",
            store_id="store-100",
            sold=True,
            settlement_price=11_000_000.0,
            duration_days=35.0,
            deal_terms={"terms": "cash"},
        )
        features = {"gm_ttm": 1_200_000.0, "store_age_months": 24.0}
        rec = outcome.to_liquidity_training_record(features)
        assert rec.duration_days == 35.0
        assert rec.sold is True
        assert rec.settlement_price == 11_000_000.0
        assert rec.features == features
        assert rec.valuation_id == "val-010"
        assert rec.deal_terms == {"terms": "cash"}

    def test_deal_outcome_to_dict_and_redaction(self) -> None:
        outcome = DealOutcome(
            outcome_id="out-011",
            valuation_id="val-011",
            store_id="store-100",
            sold=True,
            settlement_price=10_000_000.0,
            settlement_date=date(2026, 8, 1),
            duration_days=30.0,
        )
        unmasked = outcome.to_dict(redact_settlement_price=False)
        assert unmasked["settlement_price"] == 10_000_000.0

        masked = outcome.to_dict(redact_settlement_price=True)
        assert masked["settlement_price"] == REDACTED_CONFIDENTIAL_VALUE


class TestValuationCalibration:
    def test_calculate_deviation_sold_within_p10_p90_band(self) -> None:
        report = _make_dummy_valuation_report(
            case_id="case-100",
            report_id="rep-100",
            p10=8_000_000.0,
            p50=10_000_000.0,
            p90=12_000_000.0,
            reserve=7_760_000.0,
            asking=12_600_000.0,
        )
        outcome = DealOutcome(
            outcome_id="out-100",
            valuation_id="rep-100",
            store_id="store-100",
            sold=True,
            settlement_price=10_500_000.0,
            duration_days=25.0,
        )
        item = calculate_valuation_deviation(outcome, report)
        assert item.sold is True
        assert item.is_covered_p10_p90 is True
        assert item.is_covered_p50_p90 is True
        assert item.is_covered_p10_p50 is False
        assert item.abs_error == 500_000.0
        assert item.percentage_error == 0.05
        assert item.calibration_ratio == 1.05
        assert item.reserve_gap == 2_740_000.0

    def test_calculate_deviation_unsold_outcome(self) -> None:
        report = _make_dummy_valuation_report(case_id="case-101", report_id="rep-101")
        outcome = DealOutcome(
            outcome_id="out-101",
            valuation_id="rep-101",
            store_id="store-100",
            sold=False,
            duration_days=90.0,
            no_deal_reason_code=NoDealReasonCode.FINANCING,
        )
        item = calculate_valuation_deviation(outcome, report)
        assert item.sold is False
        assert item.is_covered_p10_p90 is False
        assert item.settlement_price is None
        assert item.abs_error is None
        assert item.no_deal_reason_code == "FINANCING"

    def test_valuation_id_mismatch_raises_error(self) -> None:
        report = _make_dummy_valuation_report(case_id="case-102", report_id="rep-102")
        outcome = DealOutcome(
            outcome_id="out-102",
            valuation_id="different-id",
            store_id="store-100",
            sold=True,
            settlement_price=10_000_000.0,
        )
        with pytest.raises(ValueError, match="does not match report id"):
            calculate_valuation_deviation(outcome, report)

    def test_compute_deal_outcome_calibration_aggregate_metrics(self) -> None:
        pairs = []
        # 8 sold deals within band, 2 sold deals outside band, 2 unsold deals
        for i in range(8):
            rep = _make_dummy_valuation_report(
                case_id=f"case-{i}",
                report_id=f"rep-{i}",
                p10=8_000_000.0,
                p50=10_000_000.0,
                p90=12_000_000.0,
            )
            # Price between 9M and 11M (covered)
            price = 9_000_000.0 + (i * 250_000.0)
            out = DealOutcome(
                outcome_id=f"out-{i}",
                valuation_id=f"rep-{i}",
                store_id=f"store-{i}",
                sold=True,
                settlement_price=price,
                duration_days=30.0,
            )
            pairs.append((out, rep))

        # 2 sold outside band
        for i in (8, 9):
            rep = _make_dummy_valuation_report(case_id=f"case-{i}", report_id=f"rep-{i}")
            price = 15_000_000.0  # above p90 (12M)
            out = DealOutcome(
                outcome_id=f"out-{i}",
                valuation_id=f"rep-{i}",
                store_id=f"store-{i}",
                sold=True,
                settlement_price=price,
                duration_days=30.0,
            )
            pairs.append((out, rep))

        # 2 unsold deals
        rep10 = _make_dummy_valuation_report(case_id="case-10", report_id="rep-10")
        out10 = DealOutcome(
            outcome_id="out-10",
            valuation_id="rep-10",
            store_id="store-10",
            sold=False,
            no_deal_reason_code=NoDealReasonCode.PRICE_GAP,
        )
        pairs.append((out10, rep10))

        rep11 = _make_dummy_valuation_report(case_id="case-11", report_id="rep-11")
        out11 = DealOutcome(
            outcome_id="out-11",
            valuation_id="rep-11",
            store_id="store-11",
            sold=False,
            no_deal_reason_code=NoDealReasonCode.CONDITION,
        )
        pairs.append((out11, rep11))

        calib_report = compute_deal_outcome_calibration(pairs)
        assert calib_report.total_outcomes == 12
        assert calib_report.sold_count == 10
        assert calib_report.unsold_count == 2
        assert calib_report.aligned_count == 10

        # Coverage: 8 / 10 = 0.80 (80% ideal coverage target met!)
        assert calib_report.p10_p90_coverage_rate == 0.80
        assert calib_report.is_coverage_target_met is True
        assert calib_report.no_deal_reason_distribution["PRICE_GAP"] == 1
        assert calib_report.no_deal_reason_distribution["CONDITION"] == 1
        assert calib_report.no_deal_reason_distribution["FINANCING"] == 0

    def test_evaluate_calibration_coverage_helper(self) -> None:
        rep1 = _make_dummy_valuation_report(case_id="c-1", report_id="r-1")
        rep2 = _make_dummy_valuation_report(case_id="c-2", report_id="r-2")
        outcomes = [
            DealOutcome(outcome_id="o-1", valuation_id="r-1", store_id="s-1", sold=True, settlement_price=10_000_000.0),
            DealOutcome(outcome_id="o-2", valuation_id="r-2", store_id="s-2", sold=False, no_deal_reason_code=NoDealReasonCode.OTHER),
        ]
        calib = evaluate_calibration_coverage(outcomes, [rep1, rep2])
        assert calib.total_outcomes == 2
        assert calib.sold_count == 1
        assert calib.unsold_count == 1


class TestFinanceAccessAndAudit:
    def test_is_finance_view_authorized(self) -> None:
        fin_principal = Principal(
            subject_id="usr-fin-001",
            roles=frozenset({Role.FINANCE_LEGAL}),
            authenticated=True,
        )
        assert is_finance_view_authorized(fin_principal) is True

        admin_principal = Principal(
            subject_id="usr-adm-001",
            roles=frozenset({Role.PLATFORM_ADMIN}),
            authenticated=True,
        )
        assert is_finance_view_authorized(admin_principal) is True

        supervisor_principal = Principal(
            subject_id="usr-sup-001",
            roles=frozenset({Role.REGIONAL_SUPERVISOR}),
            authenticated=True,
        )
        # Regional supervisor does not have finance_legal role or clearance
        assert is_finance_view_authorized(supervisor_principal) is False

        assert is_finance_view_authorized(None) is False

    def test_assert_finance_view_authorized(self) -> None:
        fin_principal = Principal(
            subject_id="usr-fin-001",
            roles=frozenset({Role.FINANCE_LEGAL}),
            authenticated=True,
        )
        assert_finance_view_authorized(fin_principal)  # Should not raise

        unauth_principal = Principal(
            subject_id="usr-unauth",
            roles=frozenset({Role.FRANCHISEE}),
            authenticated=True,
        )
        with pytest.raises(PermissionError, match="not authorized for finance:view"):
            assert_finance_view_authorized(unauth_principal)

    def test_record_deal_outcome_export_audit(self) -> None:
        audit_log = InMemoryAuditLog()
        authority_key = "test-avm-outcome-authority-key-v1"
        identity_proof = create_identity_proof(
            "usr-fin-001",
            Role.FINANCE_LEGAL,
            "tenant-avm-001",
            authority_key=authority_key,
        )
        outcomes = [
            DealOutcome(outcome_id="o-1", valuation_id="r-1", store_id="s-1", sold=True, settlement_price=10_000_000.0),
            DealOutcome(outcome_id="o-2", valuation_id="r-2", store_id="s-2", sold=True, settlement_price=12_000_000.0),
        ]
        result = record_deal_outcome_export_audit(
            actor_id="usr-fin-001",
            role=Role.FINANCE_LEGAL,
            deal_outcomes=outcomes,
            audit_log=audit_log,
            tenant_id="tenant-avm-001",
            authority_key=authority_key,
            access_context={
                "authenticated": True,
                "verified_identity": True,
                "identity_proof_sha256": identity_proof,
                "tenant_id": "tenant-avm-001",
                "tenant_matched": True,
                "data_room_access": True,
                "clearance": "HIGH",
            },
        )
        assert result["decision"] == "PERMIT"
        assert result["record_count"] == 2
        assert result["zero_confidential_leak_verified"] is True
        assert len(audit_log._events) == 1
        assert audit_log._events[0].event_type == "avm.deal_outcomes_exported.v1"

    def test_record_deal_outcome_export_fails_closed_for_missing_key_and_role(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ODP_AVM_AUTHORITY_VERIFIER_KEY", raising=False)
        monkeypatch.delenv("ODP_AVM_AUTHORITY_PUBLIC_KEY", raising=False)
        outcome = DealOutcome(
            outcome_id="o-1",
            valuation_id="r-1",
            store_id="s-1",
            sold=True,
            settlement_price=10_000_000.0,
        )
        context = {
            "authenticated": True,
            "verified_identity": True,
            "identity_proof_sha256": "forged-proof",
            "tenant_id": "tenant-avm-001",
            "tenant_matched": True,
            "data_room_access": True,
            "clearance": "HIGH",
        }
        no_key = record_deal_outcome_export_audit(
            actor_id="usr-fin-001",
            role=Role.FINANCE_LEGAL,
            deal_outcomes=[outcome],
            access_context=context,
        )
        assert no_key["decision"] == "DENY"

        authority_key = "test-avm-outcome-authority-key-v1"
        unknown_proof = create_identity_proof(
            "usr-fin-001",
            "unknown_role",
            "tenant-avm-001",
            authority_key=authority_key,
        )
        unknown_role = record_deal_outcome_export_audit(
            actor_id="usr-fin-001",
            role="unknown_role",
            deal_outcomes=[outcome],
            authority_key=authority_key,
            access_context={**context, "identity_proof_sha256": unknown_proof},
        )
        assert unknown_role["decision"] == "DENY"
        assert unknown_role["role"] == "unknown_role"


class TestAVMServiceIntegration:
    def test_service_deal_outcome_lifecycle_and_calibration(self) -> None:
        repo = InMemoryAVMRepository()
        service = AVMService(repository=repo)

        # Create valuation case and value it
        val_input = ValuationInput(
            store_id="store-test-1",
            gm_ttm=1_000_000.0,
            forecast_gm_next_12m=1_100_000.0,
            asset_book_value=2_000_000.0,
            equipment_fair_value=500_000.0,
        )
        case = service.create_case(val_input, created_by="operator-1", correlation_id="corr-1")
        report = service.value(case.case_id, actor="operator-1", correlation_id="corr-2")

        # Record a sold deal outcome matching the case
        deal = DealOutcome(
            outcome_id="deal-1",
            valuation_id=report.report_id,
            store_id="store-test-1",
            sold=True,
            settlement_price=report.fair_price.p50,
            settlement_date=date(2026, 8, 20),
            duration_days=30.0,
        )
        saved = service.record_deal_outcome(deal)
        assert saved.outcome_id == "deal-1"

        # List outcomes
        all_deals = service.list_deal_outcomes()
        assert len(all_deals) == 1

        # Calibrate
        calib = service.calibrate_deal_outcomes()
        assert calib.total_outcomes == 1
        assert calib.sold_count == 1
        assert calib.p10_p90_coverage_rate == 1.0
        assert calib.mae == 0.0
        assert calib.median_calibration_ratio == 1.0
