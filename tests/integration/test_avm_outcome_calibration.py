from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from modules.avm.application.outcome_calibration import (
    generate_gate1_benchmark_receipt,
)
from modules.avm.domain.outcome import (
    AVMOutcomeTransaction,
    AVMOutcomeValidationError,
    AVMPredictionRecord,
    AVMVerdict,
    align_outcomes_and_predictions,
    compute_avm_outcome_calibration,
    create_avm_activation_receipt,
)
from modules.dealroom.application.outcome_audit import generate_dealroom_outcome_audit_receipt
from modules.dealroom.domain.confidential_access import (
    ConfidentialAccessAttempt,
    ConfidentialAccessAuditor,
    ConfidentialAccessDecision,
    ConfidentialLevel,
)
from shared.auth.rbac import Action, Role

VALID_DATASET_HASH = "a1b2c3d4e5f60718293a4b5c6d7e8f901234567890abcdef1234567890abcdef"
VALID_MODEL_HASH = "b2c3d4e5f60718293a4b5c6d7e8f901234567890abcdef1234567890abcdef01"
RAW_SHA = "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"


def _make_valid_audit_receipt() -> dict:
    valid_ctx = {"authenticated": True, "verified_identity": True, "data_room_access": True, "tenant_matched": True, "clearance": "HIGH"}
    attempts = [
        ("usr-fin-001", Role.FINANCE_LEGAL, "dealroom", Action.VIEW, valid_ctx),
        ("usr-adm-003", Role.PLATFORM_ADMIN, "dealroom", Action.EXPORT, valid_ctx),
    ]
    return generate_dealroom_outcome_audit_receipt(attempts)


def test_avm_outcome_insufficient_data_fails_closed_with_governed_disabled() -> None:
    report = compute_avm_outcome_calibration(
        [],
        observed_count=0,
        eligible_count=0,
        auto_seeded_count=0,
        dataset_snapshot_id="snapshot-001",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
    )

    assert report.is_governed_disabled is True
    assert report.reason_code == "DATA_CONTRACT_NOT_MATURE"
    assert report.verdict == AVMVerdict.FAIL_CLOSED
    assert report.activation_threshold == 120
    assert report.aligned_count == 0


def test_avm_outcome_alignment_computes_coverage_calibration_and_value_bands() -> None:
    now = datetime.now(UTC) - timedelta(days=1)
    pred_time = now - timedelta(hours=1)
    outcomes = []
    predictions = []
    for i in range(120):
        tx_id = f"tx-{i}"
        s_id = f"s-{i}"
        price = 8_000_000.0 + (i * 250_000.0)
        p50 = price * 1.01  # small noise so p50 != price (not copied)
        p10 = p50 * 0.8
        p90 = p50 * 1.2
        outcomes.append(
            AVMOutcomeTransaction(
                tx_id, s_id, price, now, is_mature=True, raw_record_sha256=RAW_SHA
            )
        )
        predictions.append(AVMPredictionRecord(f"pred-{i}", s_id, p10, p50, p90, predicted_at=pred_time))

    aligned = align_outcomes_and_predictions(outcomes, predictions)
    assert len(aligned) == 120

    activation_rcpt = create_avm_activation_receipt(VALID_DATASET_HASH, VALID_MODEL_HASH)
    audit_rcpt = _make_valid_audit_receipt()

    report = compute_avm_outcome_calibration(
        aligned,
        observed_count=120,
        eligible_count=120,
        auto_seeded_count=0,
        dataset_snapshot_id="snapshot-002",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
        activation_receipt=activation_rcpt,
        audit_receipt=audit_rcpt,
    )

    assert report.is_governed_disabled is False
    assert report.verdict == AVMVerdict.PASS
    assert report.p10_p90_coverage_rate == 1.0
    assert 0.95 <= report.median_calibration_ratio <= 1.05
    assert "band_low_lt10m" in report.value_band_metrics
    assert "band_mid_10m_to_30m" in report.value_band_metrics
    assert "band_high_gt30m" in report.value_band_metrics


# --- B1 Mutations ---

def test_mutation_caller_supplied_eligible_count_without_outcomes_fails_closed() -> None:
    # Caller passes eligible_count=120 with 0 aligned pairs -> must fail closed
    report = compute_avm_outcome_calibration(
        [],
        observed_count=0,
        eligible_count=120,
        auto_seeded_count=0,
        dataset_snapshot_id="snapshot-001",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
    )
    assert report.is_governed_disabled is True
    assert report.verdict == AVMVerdict.FAIL_CLOSED
    assert report.reason_code == "DATA_CONTRACT_NOT_MATURE"


def test_mutation_reconciled_count_mismatch_raises_error() -> None:
    now = datetime.now(UTC) - timedelta(days=1)
    pred_time = now - timedelta(hours=1)
    outcomes = [AVMOutcomeTransaction("tx-1", "s-1", 10_000_000.0, now, is_mature=True, raw_record_sha256=RAW_SHA)]
    predictions = [AVMPredictionRecord("pred-1", "s-1", 8_000_000.0, 10_200_000.0, 12_000_000.0, predicted_at=pred_time)]
    aligned = align_outcomes_and_predictions(outcomes, predictions)

    with pytest.raises(AVMOutcomeValidationError, match="Reconciled count mismatch"):
        compute_avm_outcome_calibration(
            aligned,
            observed_count=0,  # observed < aligned count (0 < 1)
            eligible_count=1,
            dataset_snapshot_hash=VALID_DATASET_HASH,
            model_artifact_hash=VALID_MODEL_HASH,
        )


def test_mutation_coverage_below_target_fails_closed() -> None:
    now = datetime.now(UTC) - timedelta(days=1)
    pred_time = now - timedelta(hours=1)
    outcomes = []
    predictions = []
    for i in range(120):
        tx_id = f"tx-{i}"
        s_id = f"s-{i}"
        price = 10_000_000.0
        # Narrow interval missing realized price 10m -> coverage = 0.0 < 0.80
        predictions.append(AVMPredictionRecord(f"pred-{i}", s_id, 1_000_000.0, 2_000_000.0, 3_000_000.0, predicted_at=pred_time))
        outcomes.append(AVMOutcomeTransaction(tx_id, s_id, price, now, is_mature=True, raw_record_sha256=RAW_SHA))

    aligned = align_outcomes_and_predictions(outcomes, predictions)
    report = compute_avm_outcome_calibration(
        aligned,
        observed_count=120,
        eligible_count=120,
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
    )
    assert report.is_governed_disabled is True
    assert report.verdict == AVMVerdict.FAIL_CLOSED
    assert report.reason_code == "CALIBRATION_TARGET_NOT_MET"


# --- B2 Mutations ---

def test_mutation_duplicate_store_prediction_fails_closed() -> None:
    now = datetime.now(UTC) - timedelta(days=1)
    pred_time = now - timedelta(hours=1)
    predictions = [
        AVMPredictionRecord("pred-1", "s-1", 10_000_000.0, 15_000_000.0, 20_000_000.0, predicted_at=pred_time),
        AVMPredictionRecord("pred-2", "s-1", 10_000_000.0, 15_000_000.0, 20_000_000.0, predicted_at=pred_time),  # Duplicate store_id s-1
    ]
    outcomes = [
        AVMOutcomeTransaction("tx-1", "s-1", 15_000_000.0, now, is_mature=True, raw_record_sha256=RAW_SHA),
        AVMOutcomeTransaction("tx-2", "s-2", 15_000_000.0, now, is_mature=True, raw_record_sha256=RAW_SHA),
    ]

    with pytest.raises(AVMOutcomeValidationError, match="Duplicate prediction record for store_id|Population key reconciliation mismatch"):
        align_outcomes_and_predictions(outcomes, predictions)


def test_mutation_mixed_model_versions_fails_closed() -> None:
    now = datetime.now(UTC) - timedelta(days=1)
    pred_time = now - timedelta(hours=1)
    predictions = [
        AVMPredictionRecord("pred-1", "s-1", 10_000_000.0, 15_000_000.0, 20_000_000.0, model_version="wrong-version", predicted_at=pred_time),
    ]
    outcomes = [AVMOutcomeTransaction("tx-1", "s-1", 15_000_000.0, now, is_mature=True, raw_record_sha256=RAW_SHA)]

    with pytest.raises(AVMOutcomeValidationError, match="Mixed or wrong model version"):
        align_outcomes_and_predictions(outcomes, predictions)


def test_mutation_missing_prediction_for_outcome_fails_closed() -> None:
    now = datetime.now(UTC) - timedelta(days=1)
    pred_time = now - timedelta(hours=1)
    predictions = [
        AVMPredictionRecord("pred-1", "s-1", 10_000_000.0, 14_000_000.0, 20_000_000.0, predicted_at=pred_time),
        AVMPredictionRecord("pred-2", "s-other", 10_000_000.0, 14_000_000.0, 20_000_000.0, predicted_at=pred_time),
    ]
    outcomes = [
        AVMOutcomeTransaction("tx-1", "s-1", 15_000_000.0, now, is_mature=True, raw_record_sha256=RAW_SHA),
        AVMOutcomeTransaction("tx-2", "s-missing", 20_000_000.0, now, is_mature=True, raw_record_sha256=RAW_SHA),
    ]

    with pytest.raises(AVMOutcomeValidationError, match="Population key reconciliation mismatch|Missing prediction record"):
        align_outcomes_and_predictions(outcomes, predictions)


# --- B3 Mutations ---

def test_mutation_fixture_authority_partition_fails_closed() -> None:
    now = datetime.now(UTC) - timedelta(days=1)
    pred_time = now - timedelta(hours=1)
    outcomes = [
        AVMOutcomeTransaction("tx-1", "s-1", 15_000_000.0, now, is_mature=True, authority_partition="fixture_partition", raw_record_sha256=RAW_SHA)
    ]
    predictions = [AVMPredictionRecord("pred-1", "s-1", 12_000_000.0, 15_200_000.0, 18_000_000.0, predicted_at=pred_time)]

    with pytest.raises(AVMOutcomeValidationError, match="Non-authoritative partition"):
        align_outcomes_and_predictions(outcomes, predictions)


def test_mutation_blank_or_invalid_raw_record_sha256_fails_closed() -> None:
    now = datetime.now(UTC) - timedelta(days=1)
    pred_time = now - timedelta(hours=1)
    outcomes = [
        AVMOutcomeTransaction("tx-1", "s-1", 15_000_000.0, now, is_mature=True, raw_record_sha256="")
    ]
    predictions = [AVMPredictionRecord("pred-1", "s-1", 12_000_000.0, 15_200_000.0, 18_000_000.0, predicted_at=pred_time)]

    with pytest.raises(AVMOutcomeValidationError, match="Invalid or missing raw_record_sha256"):
        align_outcomes_and_predictions(outcomes, predictions)


def test_mutation_future_transaction_date_marked_mature_fails_closed() -> None:
    future_date = datetime.now(UTC) + timedelta(days=10)
    pred_time = datetime.now(UTC) - timedelta(days=1)
    outcomes = [
        AVMOutcomeTransaction("tx-future", "s-1", 15_000_000.0, future_date, is_mature=True, raw_record_sha256=RAW_SHA)
    ]
    predictions = [AVMPredictionRecord("pred-1", "s-1", 12_000_000.0, 15_200_000.0, 18_000_000.0, predicted_at=pred_time)]

    with pytest.raises(AVMOutcomeValidationError, match="Future transaction date"):
        align_outcomes_and_predictions(outcomes, predictions)


# --- B4 Mutations ---

def test_mutation_single_substituted_outcome_row_fails_closed() -> None:
    now = datetime.now(UTC) - timedelta(days=1)
    pred_time = now - timedelta(hours=1)
    outcomes = [
        AVMOutcomeTransaction("tx-1", "s-1", 15_000_000.0, now, is_mature=True, raw_record_sha256=RAW_SHA),
        AVMOutcomeTransaction("tx-2", "s-2", 25_000_000.0, now, is_mature=True, raw_record_sha256=RAW_SHA),
    ]
    # Row 1 is normal, Row 2 has copied p50 == outcome.realized_price (single substituted row fraud)
    predictions = [
        AVMPredictionRecord("pred-1", "s-1", 10_000_000.0, 14_000_000.0, 20_000_000.0, predicted_at=pred_time),
        AVMPredictionRecord("pred-2", "s-2", 20_000_000.0, 25_000_000.0, 30_000_000.0, predicted_at=pred_time),
    ]

    with pytest.raises(AVMOutcomeValidationError, match="Prediction value p50=25000000.0 was directly copied"):
        align_outcomes_and_predictions(outcomes, predictions)


# --- B5 Mutations ---

def test_mutation_uppercase_or_non_hex_sha256_fails_closed() -> None:
    with pytest.raises(AVMOutcomeValidationError, match="Unbound or invalid dataset_snapshot_hash"):
        compute_avm_outcome_calibration(
            [],
            observed_count=0,
            eligible_count=0,
            dataset_snapshot_hash="A" * 64,  # Uppercase hex is invalid
            model_artifact_hash=VALID_MODEL_HASH,
        )


def test_mutation_receipt_lineage_mismatch_fails_closed() -> None:
    report = compute_avm_outcome_calibration(
        [],
        observed_count=0,
        eligible_count=0,
        dataset_snapshot_id="snapshot-001",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
    )
    with pytest.raises(AVMOutcomeValidationError, match="Receipt lineage mismatch with report"):
        generate_gate1_benchmark_receipt(
            report,
            dataset_snapshot_id="different-snapshot-id",
            dataset_snapshot_hash=VALID_DATASET_HASH,
            model_artifact_hash=VALID_MODEL_HASH,
        )


def test_mutation_dataclass_replaced_pass_with_eligible_zero_fails_closed() -> None:
    report = compute_avm_outcome_calibration(
        [],
        observed_count=0,
        eligible_count=0,
        dataset_snapshot_id="snapshot-001",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
    )
    # Dataclass replacement forging PASS on zero-eligible report
    forged_report = dataclasses.replace(report, verdict=AVMVerdict.PASS, is_governed_disabled=False)

    with pytest.raises(AVMOutcomeValidationError, match="Receipt boundary detected forged or invalid PASS"):
        generate_gate1_benchmark_receipt(
            forged_report,
            dataset_snapshot_id="snapshot-001",
            dataset_snapshot_hash=VALID_DATASET_HASH,
            model_artifact_hash=VALID_MODEL_HASH,
        )


# --- B6 Mutations ---

def test_mutation_finance_legal_delete_on_unrelated_secret_returns_deny() -> None:
    attempt = ConfidentialAccessAttempt(
        actor_id="usr-fin-001",
        role=Role.FINANCE_LEGAL,
        resource="unrelated_secret",
        action=Action.DELETE,
    )
    decision, reason, receipt = ConfidentialAccessAuditor.evaluate_access(
        attempt, ConfidentialLevel.HIGH
    )
    assert decision == ConfidentialAccessDecision.DENY


def test_mutation_finance_legal_unauthorized_action_returns_deny() -> None:
    attempt = ConfidentialAccessAttempt(
        actor_id="usr-fin-001",
        role=Role.FINANCE_LEGAL,
        resource="dealroom",
        action=Action.DELETE,
    )
    decision, reason, receipt = ConfidentialAccessAuditor.evaluate_access(
        attempt, ConfidentialLevel.HIGH
    )
    assert decision == ConfidentialAccessDecision.DENY


# --- B7 Mutations ---

def test_authoritative_evidence_represents_unpopulated_snapshot_honestly() -> None:
    from scripts.models.avm_benchmark import generate_avm_outcome_evidence_pack
    report, gate1_receipt, audit_receipt, report_md, handback_json = generate_avm_outcome_evidence_pack(
        observed_count=0,
        eligible_count=0,
    )
    assert report.is_governed_disabled is True
    assert report.dataset_snapshot_id == "empty-snapshot-unpopulated"
    assert len(report.dataset_snapshot_hash) == 64
    assert report.dataset_snapshot_hash.islower()
    assert gate1_receipt["governed_disabled"] is True


# --- B8 to B16 Tests & Mutations ---

def test_b8_and_b12_activation_authority_gate_fails_closed_without_attestation() -> None:
    now = datetime.now(UTC) - timedelta(days=10)
    pred_time = now - timedelta(hours=1)
    outcomes = []
    predictions = []
    for i in range(120):
        tx_id = f"tx-{i}"
        s_id = f"s-{i}"
        price = 8_000_000.0 + (i * 250_000.0)
        p50 = price * 1.01
        p10 = p50 * 0.8
        p90 = p50 * 1.2
        outcomes.append(
            AVMOutcomeTransaction(
                tx_id, s_id, price, now, is_mature=True, raw_record_sha256=RAW_SHA
            )
        )
        predictions.append(
            AVMPredictionRecord(f"pred-{i}", s_id, p10, p50, p90, predicted_at=pred_time)
        )

    aligned = align_outcomes_and_predictions(outcomes, predictions)
    audit_rcpt = _make_valid_audit_receipt()

    # Without activation receipt (default None): fails closed with AUTHENTIC_DATA_ACTIVATION_PENDING
    unactivated_report = compute_avm_outcome_calibration(
        aligned,
        observed_count=120,
        eligible_count=120,
        auto_seeded_count=0,
        dataset_snapshot_id="snapshot-002",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
        activation_receipt=None,
        audit_receipt=audit_rcpt,
    )
    assert unactivated_report.is_governed_disabled is True
    assert unactivated_report.verdict == AVMVerdict.FAIL_CLOSED
    assert unactivated_report.reason_code == "AUTHENTIC_DATA_ACTIVATION_PENDING"

    # With valid Human/Ops activation receipt: produces PASS
    valid_activation = create_avm_activation_receipt(VALID_DATASET_HASH, VALID_MODEL_HASH)
    activated_report = compute_avm_outcome_calibration(
        aligned,
        observed_count=120,
        eligible_count=120,
        auto_seeded_count=0,
        dataset_snapshot_id="snapshot-002",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
        activation_receipt=valid_activation,
        audit_receipt=audit_rcpt,
    )
    assert activated_report.is_governed_disabled is False
    assert activated_report.verdict == AVMVerdict.PASS
    assert activated_report.reason_code == "MATURE_LABEL_CONTRACT_READY"

    # B12: Forged activation receipt with invalid authority_id fails
    with pytest.raises(AVMOutcomeValidationError, match="Invalid activation authority_id"):
        create_avm_activation_receipt(VALID_DATASET_HASH, VALID_MODEL_HASH, authority_id="attacker")


def test_b13_missing_abac_authority_context_fails_closed() -> None:
    # Empty context defaults to fail-closed DENY
    empty_ctx_attempt = ConfidentialAccessAttempt(
        actor_id="usr-fin-001",
        role=Role.FINANCE_LEGAL,
        resource="dealroom",
        action=Action.VIEW,
        context={},
    )
    decision, reason, receipt = ConfidentialAccessAuditor.evaluate_access(
        empty_ctx_attempt, ConfidentialLevel.HIGH
    )
    assert decision == ConfidentialAccessDecision.DENY
    assert "not authenticated" in reason or "not authoritatively verified" in reason

    # Unverified identity attempt fails closed
    unverified_attempt = ConfidentialAccessAttempt(
        actor_id="usr-fin-001",
        role=Role.FINANCE_LEGAL,
        resource="dealroom",
        action=Action.VIEW,
        context={"authenticated": True, "data_room_access": True, "tenant_matched": True, "clearance": "HIGH"},
    )
    decision, reason, receipt = ConfidentialAccessAuditor.evaluate_access(
        unverified_attempt, ConfidentialLevel.HIGH
    )
    assert decision == ConfidentialAccessDecision.DENY
    assert "not authoritatively verified" in reason


def test_b14_calibration_pass_gated_by_access_audit() -> None:
    now = datetime.now(UTC) - timedelta(days=10)
    pred_time = now - timedelta(hours=1)
    outcomes = []
    predictions = []
    for i in range(120):
        outcomes.append(AVMOutcomeTransaction(f"tx-{i}", f"s-{i}", 8_000_000.0 + (i * 250_000.0), now, is_mature=True, raw_record_sha256=RAW_SHA))
        p50 = (8_000_000.0 + (i * 250_000.0)) * 1.01
        predictions.append(AVMPredictionRecord(f"pred-{i}", f"s-{i}", p50 * 0.8, p50, p50 * 1.2, predicted_at=pred_time))

    aligned = align_outcomes_and_predictions(outcomes, predictions)
    valid_activation = create_avm_activation_receipt(VALID_DATASET_HASH, VALID_MODEL_HASH)

    # Missing audit receipt: fails closed with ACCESS_AUDIT_NOT_VERIFIED
    report = compute_avm_outcome_calibration(
        aligned,
        observed_count=120,
        eligible_count=120,
        dataset_snapshot_id="snapshot-002",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
        activation_receipt=valid_activation,
        audit_receipt=None,
    )
    assert report.is_governed_disabled is True
    assert report.verdict == AVMVerdict.FAIL_CLOSED
    assert report.reason_code == "ACCESS_AUDIT_NOT_VERIFIED"


def test_b15_population_drift_exact_key_reconciliation_fails_closed() -> None:
    now = datetime.now(UTC) - timedelta(days=10)
    pred_time = now - timedelta(hours=1)

    # 121 predictions vs 120 outcomes
    outcomes = [AVMOutcomeTransaction(f"tx-{i}", f"s-{i}", 10_000_000.0, now, is_mature=True, raw_record_sha256=RAW_SHA) for i in range(120)]
    predictions = [AVMPredictionRecord(f"pred-{i}", f"s-{i}", 8_000_000.0, 10_100_000.0, 12_000_000.0, predicted_at=pred_time) for i in range(121)]

    with pytest.raises(AVMOutcomeValidationError, match="Population drift detected"):
        align_outcomes_and_predictions(outcomes, predictions)


def test_b16_stale_transaction_freshness_policy_fails_closed() -> None:
    stale_date = datetime.now(UTC) - timedelta(days=9500)  # 26-year-old row
    pred_time = stale_date - timedelta(hours=1)

    outcomes = [AVMOutcomeTransaction("tx-stale", "s-1", 10_000_000.0, stale_date, is_mature=True, raw_record_sha256=RAW_SHA)]
    predictions = [AVMPredictionRecord("pred-1", "s-1", 8_000_000.0, 10_100_000.0, 12_000_000.0, predicted_at=pred_time)]

    with pytest.raises(AVMOutcomeValidationError, match="Stale transaction row detected"):
        align_outcomes_and_predictions(outcomes, predictions)
