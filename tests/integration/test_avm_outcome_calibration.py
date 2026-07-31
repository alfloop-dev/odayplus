from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from modules.avm.application.outcome_calibration import (
    generate_benchmark_report_md,
    generate_gate1_benchmark_receipt,
)
from modules.avm.domain.outcome import (
    AVMActivationAuthorityReceipt,
    AVMOutcomeTransaction,
    AVMOutcomeValidationError,
    AVMPredictionRecord,
    AVMQuerySourceReceipt,
    AVMVerdict,
    align_outcomes_and_predictions,
    compute_avm_outcome_calibration,
    create_avm_activation_receipt,
    create_avm_query_source_receipt,
)
from modules.dealroom.application.outcome_audit import (
    generate_dealroom_outcome_audit_receipt,
    verify_audit_receipt,
)
from modules.dealroom.domain.confidential_access import (
    ConfidentialAccessAttempt,
    ConfidentialAccessAuditor,
    ConfidentialAccessDecision,
    ConfidentialLevel,
    create_identity_proof,
)
from shared.auth.rbac import Action, Role

VALID_DATASET_HASH = "a1b2c3d4e5f60718293a4b5c6d7e8f901234567890abcdef1234567890abcdef"
VALID_MODEL_HASH = "b2c3d4e5f60718293a4b5c6d7e8f901234567890abcdef1234567890abcdef01"
RAW_SHA = "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"


def _make_valid_audit_receipt(snapshot_hash: str = VALID_DATASET_HASH) -> dict:
    ctx_fin = {
        "authenticated": True,
        "verified_identity": True,
        "identity_proof_sha256": create_identity_proof("usr-fin-001", Role.FINANCE_LEGAL),
        "tenant_id": "tenant-avm-001",
        "data_room_access": True,
        "tenant_matched": True,
        "clearance": "HIGH",
    }
    ctx_adm = {
        "authenticated": True,
        "verified_identity": True,
        "identity_proof_sha256": create_identity_proof("usr-adm-003", Role.PLATFORM_ADMIN),
        "tenant_id": "tenant-avm-001",
        "data_room_access": True,
        "tenant_matched": True,
        "clearance": "HIGH",
    }
    attempts = [
        ("usr-fin-001", Role.FINANCE_LEGAL, "dealroom", Action.VIEW, ctx_fin),
        ("usr-adm-003", Role.PLATFORM_ADMIN, "dealroom", Action.EXPORT, ctx_adm),
    ]
    return generate_dealroom_outcome_audit_receipt(attempts, dataset_snapshot_hash=snapshot_hash)


def _make_valid_query_receipt(
    snapshot_id: str = "snapshot-002",
    snapshot_hash: str = VALID_DATASET_HASH,
    observed: int = 120,
    eligible: int = 120,
    population_keys: list[str] | tuple[str, ...] | None = None,
) -> AVMQuerySourceReceipt:
    if population_keys is None and observed == 120:
        population_keys = [f"tx-low-{i}" for i in range(40)] + [f"tx-mid-{i}" for i in range(40)] + [f"tx-high-{i}" for i in range(40)]
    return create_avm_query_source_receipt(
        dataset_snapshot_id=snapshot_id,
        dataset_snapshot_hash=snapshot_hash,
        observed_labeled_count=observed,
        eligible_mature_count=eligible,
        population_keys=population_keys,
    )


def _make_balanced_aligned_pairs(count: int = 120) -> list:
    now = datetime.now(UTC) - timedelta(days=1)
    pred_time = now - timedelta(hours=1)
    outcomes = []
    predictions = []

    # 40 in low band (<10M), 40 in mid band (10M-30M), 40 in high band (>30M)
    for i in range(40):
        price_low = 5_000_000.0 + (i * 100_000.0)
        p50_low = price_low * 1.01
        outcomes.append(AVMOutcomeTransaction(f"tx-low-{i}", f"s-low-{i}", price_low, now, is_mature=True, raw_record_sha256=RAW_SHA))
        predictions.append(AVMPredictionRecord(f"pred-low-{i}", f"s-low-{i}", p50_low * 0.8, p50_low, p50_low * 1.2, predicted_at=pred_time))

        price_mid = 12_000_000.0 + (i * 400_000.0)
        p50_mid = price_mid * 1.01
        outcomes.append(AVMOutcomeTransaction(f"tx-mid-{i}", f"s-mid-{i}", price_mid, now, is_mature=True, raw_record_sha256=RAW_SHA))
        predictions.append(AVMPredictionRecord(f"pred-mid-{i}", f"s-mid-{i}", p50_mid * 0.8, p50_mid, p50_mid * 1.2, predicted_at=pred_time))

        price_high = 32_000_000.0 + (i * 500_000.0)
        p50_high = price_high * 1.01
        outcomes.append(AVMOutcomeTransaction(f"tx-high-{i}", f"s-high-{i}", price_high, now, is_mature=True, raw_record_sha256=RAW_SHA))
        predictions.append(AVMPredictionRecord(f"pred-high-{i}", f"s-high-{i}", p50_high * 0.8, p50_high, p50_high * 1.2, predicted_at=pred_time))

    return align_outcomes_and_predictions(outcomes, predictions)


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
    aligned = _make_balanced_aligned_pairs()
    assert len(aligned) == 120

    activation_rcpt = create_avm_activation_receipt(VALID_DATASET_HASH, VALID_MODEL_HASH)
    audit_rcpt = _make_valid_audit_receipt()
    query_rcpt = _make_valid_query_receipt()

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
        query_source_receipt=query_rcpt,
    )

    assert report.is_governed_disabled is False
    assert report.verdict == AVMVerdict.PASS
    assert report.p10_p90_coverage_rate == 1.0
    assert 0.95 <= report.median_calibration_ratio <= 1.05
    assert "band_low_lt10m" in report.value_band_metrics
    assert "band_mid_10m_to_30m" in report.value_band_metrics
    assert "band_high_gt30m" in report.value_band_metrics
    assert report.value_band_metrics["band_low_lt10m"].aligned_count == 40
    assert report.value_band_metrics["band_mid_10m_to_30m"].aligned_count == 40
    assert report.value_band_metrics["band_high_gt30m"].aligned_count == 40


# --- B1 Mutations ---

def test_mutation_caller_supplied_eligible_count_without_outcomes_fails_closed() -> None:
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
            observed_count=0,
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
        AVMPredictionRecord("pred-2", "s-1", 10_000_000.0, 15_000_000.0, 20_000_000.0, predicted_at=pred_time),
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
            dataset_snapshot_hash="A" * 64,
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
    forged_report = dataclasses.replace(report, verdict=AVMVerdict.PASS, is_governed_disabled=False)

    with pytest.raises(AVMOutcomeValidationError, match="requires non-null activation_receipt|Receipt boundary detected"):
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
    aligned = _make_balanced_aligned_pairs()
    audit_rcpt = _make_valid_audit_receipt()
    query_rcpt = _make_valid_query_receipt()

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
        query_source_receipt=query_rcpt,
    )
    assert unactivated_report.is_governed_disabled is True
    assert unactivated_report.verdict == AVMVerdict.FAIL_CLOSED
    assert unactivated_report.reason_code == "AUTHENTIC_DATA_ACTIVATION_PENDING"

    # With valid Human/Ops activation receipt and query receipt: produces PASS
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
        query_source_receipt=query_rcpt,
    )
    assert activated_report.is_governed_disabled is False
    assert activated_report.verdict == AVMVerdict.PASS
    assert activated_report.reason_code == "MATURE_LABEL_CONTRACT_READY"

    # B12: Forged activation receipt with invalid authority_id fails
    with pytest.raises(AVMOutcomeValidationError, match="Invalid activation authority_id"):
        create_avm_activation_receipt(VALID_DATASET_HASH, VALID_MODEL_HASH, authority_id="attacker")


def test_b13_missing_abac_authority_context_fails_closed() -> None:
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
    aligned = _make_balanced_aligned_pairs()
    valid_activation = create_avm_activation_receipt(VALID_DATASET_HASH, VALID_MODEL_HASH)
    query_rcpt = _make_valid_query_receipt()

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
        query_source_receipt=query_rcpt,
    )
    assert report.is_governed_disabled is True
    assert report.verdict == AVMVerdict.FAIL_CLOSED
    assert report.reason_code == "ACCESS_AUDIT_NOT_VERIFIED"


def test_b15_population_drift_exact_key_reconciliation_fails_closed() -> None:
    now = datetime.now(UTC) - timedelta(days=10)
    pred_time = now - timedelta(hours=1)

    outcomes = [AVMOutcomeTransaction(f"tx-{i}", f"s-{i}", 10_000_000.0, now, is_mature=True, raw_record_sha256=RAW_SHA) for i in range(120)]
    predictions = [AVMPredictionRecord(f"pred-{i}", f"s-{i}", 8_000_000.0, 10_100_000.0, 12_000_000.0, predicted_at=pred_time) for i in range(121)]

    with pytest.raises(AVMOutcomeValidationError, match="Population drift detected"):
        align_outcomes_and_predictions(outcomes, predictions)


def test_b16_stale_transaction_freshness_policy_fails_closed() -> None:
    stale_date = datetime.now(UTC) - timedelta(days=9500)
    pred_time = stale_date - timedelta(hours=1)

    outcomes = [AVMOutcomeTransaction("tx-stale", "s-1", 10_000_000.0, stale_date, is_mature=True, raw_record_sha256=RAW_SHA)]
    predictions = [AVMPredictionRecord("pred-1", "s-1", 8_000_000.0, 10_100_000.0, 12_000_000.0, predicted_at=pred_time)]

    with pytest.raises(AVMOutcomeValidationError, match="Stale transaction row detected"):
        align_outcomes_and_predictions(outcomes, predictions)


# --- B17 to B22 Negative Mutation Tests ---

def test_b17_mutation_activation_receipt_empty_signature_fails_verification() -> None:
    receipt = AVMActivationAuthorityReceipt(
        authority_id="Human/Ops",
        approval_status="APPROVED",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
        signature_digest="",
    )
    assert receipt.verify_attestation(VALID_DATASET_HASH, VALID_MODEL_HASH) is False


def test_b17_mutation_activation_receipt_unkeyed_or_invalid_key_raises_error() -> None:
    with pytest.raises(AVMOutcomeValidationError, match="Invalid or missing authority key"):
        create_avm_activation_receipt(
            VALID_DATASET_HASH,
            VALID_MODEL_HASH,
            authority_key="invalid-unauthorized-key",
        )


def test_b18_mutation_audit_receipt_forged_sha256_fails_verification() -> None:
    receipt = _make_valid_audit_receipt()
    receipt["sha256"] = "z" * 64
    assert verify_audit_receipt(receipt, expected_snapshot_hash=VALID_DATASET_HASH) is False

    receipt_tampered = _make_valid_audit_receipt()
    receipt_tampered["permitted_count"] = 999
    assert verify_audit_receipt(receipt_tampered, expected_snapshot_hash=VALID_DATASET_HASH) is False


def test_b18_mutation_caller_naked_boolean_without_identity_proof_fails() -> None:
    attempt = ConfidentialAccessAttempt(
        actor_id="usr-fin-001",
        role=Role.FINANCE_LEGAL,
        resource="dealroom",
        action=Action.VIEW,
        context={"authenticated": True, "verified_identity": True, "data_room_access": True, "tenant_matched": True, "clearance": "HIGH"},
    )
    decision, reason, receipt = ConfidentialAccessAuditor.evaluate_access(
        attempt, ConfidentialLevel.HIGH
    )
    assert decision == ConfidentialAccessDecision.DENY
    assert "not authoritatively verified" in reason


def test_b19_mutation_missing_or_tampered_query_source_receipt_fails_closed() -> None:
    aligned = _make_balanced_aligned_pairs()
    activation_rcpt = create_avm_activation_receipt(VALID_DATASET_HASH, VALID_MODEL_HASH)
    audit_rcpt = _make_valid_audit_receipt()

    report_no_query = compute_avm_outcome_calibration(
        aligned,
        observed_count=120,
        eligible_count=120,
        dataset_snapshot_id="snapshot-002",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
        activation_receipt=activation_rcpt,
        audit_receipt=audit_rcpt,
        query_source_receipt=None,
    )
    assert report_no_query.is_governed_disabled is True
    assert report_no_query.verdict == AVMVerdict.FAIL_CLOSED
    assert report_no_query.reason_code == "QUERY_SOURCE_RECEIPT_NOT_VERIFIED"

    tampered_query = AVMQuerySourceReceipt(
        relation="model_ready.valuation_view",
        query_timestamp=datetime.now(UTC),
        dataset_snapshot_id="snapshot-002",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        observed_labeled_count=120,
        eligible_mature_count=120,
        receipt_sha256="0" * 64,
    )
    report_bad_query = compute_avm_outcome_calibration(
        aligned,
        observed_count=120,
        eligible_count=120,
        dataset_snapshot_id="snapshot-002",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
        activation_receipt=activation_rcpt,
        audit_receipt=audit_rcpt,
        query_source_receipt=tampered_query,
    )
    assert report_bad_query.is_governed_disabled is True
    assert report_bad_query.verdict == AVMVerdict.FAIL_CLOSED
    assert report_bad_query.reason_code == "QUERY_SOURCE_RECEIPT_NOT_VERIFIED"


def test_b20_mutation_non_positive_realized_price_raises_validation_error() -> None:
    now = datetime.now(UTC) - timedelta(days=1)
    pred_time = now - timedelta(hours=1)
    outcomes = [
        AVMOutcomeTransaction("tx-neg", "s-1", -5_000_000.0, now, is_mature=True, raw_record_sha256=RAW_SHA)
    ]
    predictions = [
        AVMPredictionRecord("pred-1", "s-1", 4_000_000.0, 5_000_000.0, 6_000_000.0, predicted_at=pred_time)
    ]
    with pytest.raises(AVMOutcomeValidationError, match="Non-positive or non-finite realized_price"):
        align_outcomes_and_predictions(outcomes, predictions)


def test_b20_mutation_non_positive_prediction_quantiles_raises_validation_error() -> None:
    now = datetime.now(UTC) - timedelta(days=1)
    pred_time = now - timedelta(hours=1)
    outcomes = [
        AVMOutcomeTransaction("tx-1", "s-1", 15_000_000.0, now, is_mature=True, raw_record_sha256=RAW_SHA)
    ]
    predictions = [
        AVMPredictionRecord("pred-1", "s-1", -1_000_000.0, 15_000_000.0, 20_000_000.0, predicted_at=pred_time)
    ]
    with pytest.raises(AVMOutcomeValidationError, match="Non-positive or non-finite prediction quantiles"):
        align_outcomes_and_predictions(outcomes, predictions)


def test_b21_mutation_single_low_band_population_fails_closed() -> None:
    now = datetime.now(UTC) - timedelta(days=1)
    pred_time = now - timedelta(hours=1)
    outcomes = []
    predictions = []
    for i in range(120):
        price = 5_000_000.0 + (i * 10_000.0)
        p50 = price * 1.01
        outcomes.append(AVMOutcomeTransaction(f"tx-{i}", f"s-{i}", price, now, is_mature=True, raw_record_sha256=RAW_SHA))
        predictions.append(AVMPredictionRecord(f"pred-{i}", f"s-{i}", p50 * 0.8, p50, p50 * 1.2, predicted_at=pred_time))

    aligned = align_outcomes_and_predictions(outcomes, predictions)
    activation_rcpt = create_avm_activation_receipt(VALID_DATASET_HASH, VALID_MODEL_HASH)
    audit_rcpt = _make_valid_audit_receipt()
    query_rcpt = _make_valid_query_receipt()

    report = compute_avm_outcome_calibration(
        aligned,
        observed_count=120,
        eligible_count=120,
        dataset_snapshot_id="snapshot-002",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
        activation_receipt=activation_rcpt,
        audit_receipt=audit_rcpt,
        query_source_receipt=query_rcpt,
    )

    assert report.is_governed_disabled is True
    assert report.verdict == AVMVerdict.FAIL_CLOSED
    assert report.reason_code == "VALUE_BAND_CALIBRATION_NOT_MET"


def test_b22_mutation_benchmark_report_md_does_not_hardcode_true_on_unverified_audit() -> None:
    report = compute_avm_outcome_calibration(
        [],
        observed_count=0,
        eligible_count=0,
        dataset_snapshot_id="snapshot-001",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
    )
    empty_audit = {}
    report_md = generate_benchmark_report_md(report, empty_audit)
    assert "- **Zero Confidential Leak Verified**: `False / Unverified`" in report_md


# --- B23 to B26 Remediation Tests ---

def test_b23_activation_authority_verifier_key_enforced() -> None:
    with pytest.raises(AVMOutcomeValidationError, match="Invalid or missing authority key"):
        create_avm_activation_receipt(VALID_DATASET_HASH, VALID_MODEL_HASH, authority_key="unauthorized-key")

    rcpt = create_avm_activation_receipt(VALID_DATASET_HASH, VALID_MODEL_HASH)
    assert rcpt.verify_attestation(VALID_DATASET_HASH, VALID_MODEL_HASH, authority_key="unauthorized-key") is False


def test_b24_audit_with_zero_permitted_count_fails_verification() -> None:
    ctx_deny = {
        "authenticated": False,
        "verified_identity": False,
        "tenant_id": "tenant-avm-001",
        "data_room_access": False,
        "tenant_matched": False,
        "clearance": "PUBLIC",
    }
    attempts = [("usr-unauth-001", Role.FRANCHISEE, "dealroom", Action.VIEW, ctx_deny)]
    audit_rcpt = generate_dealroom_outcome_audit_receipt(attempts, dataset_snapshot_hash=VALID_DATASET_HASH)
    assert audit_rcpt["permitted_count"] == 0
    assert audit_rcpt["denied_count"] == 1
    assert verify_audit_receipt(audit_rcpt, expected_snapshot_hash=VALID_DATASET_HASH) is False

    aligned = _make_balanced_aligned_pairs()
    activation_rcpt = create_avm_activation_receipt(VALID_DATASET_HASH, VALID_MODEL_HASH)
    query_rcpt = _make_valid_query_receipt()

    report = compute_avm_outcome_calibration(
        aligned,
        observed_count=120,
        eligible_count=120,
        dataset_snapshot_id="snapshot-002",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
        activation_receipt=activation_rcpt,
        audit_receipt=audit_rcpt,
        query_source_receipt=query_rcpt,
    )
    assert report.is_governed_disabled is True
    assert report.verdict == AVMVerdict.FAIL_CLOSED
    assert report.reason_code == "ACCESS_AUDIT_NOT_VERIFIED"


def test_b25_query_receipt_population_mismatch_fails_reconciliation() -> None:
    aligned = _make_balanced_aligned_pairs(120)
    query_rcpt_121 = create_avm_query_source_receipt(
        dataset_snapshot_id="snapshot-002",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        observed_labeled_count=121,
        eligible_mature_count=121,
    )
    activation_rcpt = create_avm_activation_receipt(VALID_DATASET_HASH, VALID_MODEL_HASH)
    audit_rcpt = _make_valid_audit_receipt()

    with pytest.raises(AVMOutcomeValidationError, match="Reconciled count mismatch"):
        compute_avm_outcome_calibration(
            aligned,
            observed_count=121,
            eligible_count=121,
            dataset_snapshot_id="snapshot-002",
            dataset_snapshot_hash=VALID_DATASET_HASH,
            model_artifact_hash=VALID_MODEL_HASH,
            activation_receipt=activation_rcpt,
            audit_receipt=audit_rcpt,
            query_source_receipt=query_rcpt_121,
        )


def test_b26_gate1_receipt_boundary_revalidates_value_bands_and_rejects_empty_metrics() -> None:
    aligned = _make_balanced_aligned_pairs()
    activation_rcpt = create_avm_activation_receipt(VALID_DATASET_HASH, VALID_MODEL_HASH)
    audit_rcpt = _make_valid_audit_receipt()
    query_rcpt = _make_valid_query_receipt()

    pass_report = compute_avm_outcome_calibration(
        aligned,
        observed_count=120,
        eligible_count=120,
        dataset_snapshot_id="snapshot-002",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
        activation_receipt=activation_rcpt,
        audit_receipt=audit_rcpt,
        query_source_receipt=query_rcpt,
    )
    assert pass_report.verdict == AVMVerdict.PASS

    # Forged report with empty value band metrics must fail receipt generation
    forged_report = dataclasses.replace(pass_report, value_band_metrics={})
    with pytest.raises(AVMOutcomeValidationError, match="missing or incomplete value band metrics"):
        generate_gate1_benchmark_receipt(
            forged_report,
            dataset_snapshot_id="snapshot-002",
            dataset_snapshot_hash=VALID_DATASET_HASH,
            model_artifact_hash=VALID_MODEL_HASH,
            audit_receipt=audit_rcpt,
            activation_receipt=activation_rcpt,
            query_source_receipt=query_rcpt,
        )

    # Unverified audit receipt must fail receipt generation even on PASS report
    fake_audit = {"sha256": "a" * 64, "total_access_attempts": 1, "permitted_count": 0, "denied_count": 1}
    with pytest.raises(AVMOutcomeValidationError, match="invalid or confidential-leaking audit_receipt|forged or invalid PASS"):
        generate_gate1_benchmark_receipt(
            pass_report,
            dataset_snapshot_id="snapshot-002",
            dataset_snapshot_hash=VALID_DATASET_HASH,
            model_artifact_hash=VALID_MODEL_HASH,
            audit_receipt=fake_audit,
            activation_receipt=activation_rcpt,
            query_source_receipt=query_rcpt,
        )


def test_m2_query_receipt_population_key_mismatch_fails_closed() -> None:
    """M2: Query receipt over attacker population keys differing from aligned tx IDs must fail-close calibration."""
    aligned = _make_balanced_aligned_pairs(120)
    activation_rcpt = create_avm_activation_receipt(VALID_DATASET_HASH, VALID_MODEL_HASH)
    audit_rcpt = _make_valid_audit_receipt()

    # Query receipt signed over 120 attacker-controlled population keys
    attacker_keys = [f"attacker-tx-key-{i}" for i in range(120)]
    attacker_query_rcpt = create_avm_query_source_receipt(
        dataset_snapshot_id="snapshot-002",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        observed_labeled_count=120,
        eligible_mature_count=120,
        population_keys=attacker_keys,
    )

    report = compute_avm_outcome_calibration(
        aligned,
        observed_count=120,
        eligible_count=120,
        dataset_snapshot_id="snapshot-002",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
        activation_receipt=activation_rcpt,
        audit_receipt=audit_rcpt,
        query_source_receipt=attacker_query_rcpt,
    )

    assert report.is_governed_disabled is True
    assert report.verdict == AVMVerdict.FAIL_CLOSED
    assert report.reason_code == "QUERY_SOURCE_RECEIPT_NOT_VERIFIED"


def test_m3_audit_receipt_self_hashed_confidential_leak_fails_verification() -> None:
    """M3: Self-hashed audit containing raw confidential realized_price must fail verification and calibration."""
    import hashlib
    import json

    audit_rcpt = _make_valid_audit_receipt()

    # Attacker inserts raw confidential price into audit events and recomputes self-hash
    audit_rcpt["audit_events"][0]["realized_price"] = 15800000
    body = {k: v for k, v in audit_rcpt.items() if k != "sha256"}
    audit_rcpt["sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    assert verify_audit_receipt(audit_rcpt, expected_snapshot_hash=VALID_DATASET_HASH) is False

    aligned = _make_balanced_aligned_pairs(120)
    activation_rcpt = create_avm_activation_receipt(VALID_DATASET_HASH, VALID_MODEL_HASH)
    query_rcpt = _make_valid_query_receipt()

    report = compute_avm_outcome_calibration(
        aligned,
        observed_count=120,
        eligible_count=120,
        dataset_snapshot_id="snapshot-002",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
        activation_receipt=activation_rcpt,
        audit_receipt=audit_rcpt,
        query_source_receipt=query_rcpt,
    )

    assert report.is_governed_disabled is True
    assert report.verdict == AVMVerdict.FAIL_CLOSED
    assert report.reason_code == "ACCESS_AUDIT_NOT_VERIFIED"


def test_m4_gate1_receipt_generator_rejects_omitted_or_forged_receipts_for_pass_verdict() -> None:
    """M4: Gate 1 generator must require non-null activation, query, and audit receipts for PASS verdict."""
    import hashlib
    import json

    aligned = _make_balanced_aligned_pairs(120)
    activation_rcpt = create_avm_activation_receipt(VALID_DATASET_HASH, VALID_MODEL_HASH)
    audit_rcpt = _make_valid_audit_receipt()
    query_rcpt = _make_valid_query_receipt()

    pass_report = compute_avm_outcome_calibration(
        aligned,
        observed_count=120,
        eligible_count=120,
        dataset_snapshot_id="snapshot-002",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
        activation_receipt=activation_rcpt,
        audit_receipt=audit_rcpt,
        query_source_receipt=query_rcpt,
    )
    assert pass_report.verdict == AVMVerdict.PASS

    # 1. Missing activation receipt
    with pytest.raises(AVMOutcomeValidationError, match="requires non-null activation_receipt"):
        generate_gate1_benchmark_receipt(
            pass_report,
            dataset_snapshot_id="snapshot-002",
            dataset_snapshot_hash=VALID_DATASET_HASH,
            model_artifact_hash=VALID_MODEL_HASH,
            audit_receipt=audit_rcpt,
            query_source_receipt=query_rcpt,
            activation_receipt=None,
        )

    # 2. Missing query source receipt
    with pytest.raises(AVMOutcomeValidationError, match="requires non-null query_source_receipt"):
        generate_gate1_benchmark_receipt(
            pass_report,
            dataset_snapshot_id="snapshot-002",
            dataset_snapshot_hash=VALID_DATASET_HASH,
            model_artifact_hash=VALID_MODEL_HASH,
            audit_receipt=audit_rcpt,
            activation_receipt=activation_rcpt,
            query_source_receipt=None,
        )

    # 3. Forged audit receipt with raw confidential values
    forged_audit = _make_valid_audit_receipt()
    forged_audit["audit_events"][0]["realized_price"] = 15800000
    forged_body = {k: v for k, v in forged_audit.items() if k != "sha256"}
    forged_audit["sha256"] = hashlib.sha256(json.dumps(forged_body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    with pytest.raises(AVMOutcomeValidationError, match="invalid or confidential-leaking audit_receipt"):
        generate_gate1_benchmark_receipt(
            pass_report,
            dataset_snapshot_id="snapshot-002",
            dataset_snapshot_hash=VALID_DATASET_HASH,
            model_artifact_hash=VALID_MODEL_HASH,
            audit_receipt=forged_audit,
            activation_receipt=activation_rcpt,
            query_source_receipt=query_rcpt,
        )


def test_b27_forged_audit_event_with_invalid_identity_proof_fails_closed() -> None:
    """B27: Audit receipt containing forged identity_proof_sha256 on PERMIT event must fail verification and calibration."""
    import hashlib
    import json

    audit_rcpt = _make_valid_audit_receipt()
    # Attacker mutates identity_proof_sha256 to forged value
    audit_rcpt["audit_events"][0]["identity_proof_sha256"] = "attacker-forged-identity-proof"
    body = {k: v for k, v in audit_rcpt.items() if k != "sha256"}
    audit_rcpt["sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    assert verify_audit_receipt(audit_rcpt, expected_snapshot_hash=VALID_DATASET_HASH) is False

    aligned = _make_balanced_aligned_pairs(120)
    activation_rcpt = create_avm_activation_receipt(VALID_DATASET_HASH, VALID_MODEL_HASH)
    query_rcpt = _make_valid_query_receipt()

    report = compute_avm_outcome_calibration(
        aligned,
        observed_count=120,
        eligible_count=120,
        dataset_snapshot_id="snapshot-002",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
        activation_receipt=activation_rcpt,
        audit_receipt=audit_rcpt,
        query_source_receipt=query_rcpt,
    )

    assert report.is_governed_disabled is True
    assert report.verdict == AVMVerdict.FAIL_CLOSED
    assert report.reason_code == "ACCESS_AUDIT_NOT_VERIFIED"


def test_b28_gate1_generator_rejects_query_receipt_with_mismatched_population_keys() -> None:
    """B28: Gate 1 receipt generator must reject a replacement query receipt with population keys differing from canonical report."""
    aligned = _make_balanced_aligned_pairs(120)
    activation_rcpt = create_avm_activation_receipt(VALID_DATASET_HASH, VALID_MODEL_HASH)
    audit_rcpt = _make_valid_audit_receipt()
    query_rcpt = _make_valid_query_receipt()

    pass_report = compute_avm_outcome_calibration(
        aligned,
        observed_count=120,
        eligible_count=120,
        dataset_snapshot_id="snapshot-002",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
        activation_receipt=activation_rcpt,
        audit_receipt=audit_rcpt,
        query_source_receipt=query_rcpt,
    )
    assert pass_report.verdict == AVMVerdict.PASS
    assert pass_report.population_keys_sha256 != ""

    # Replacement query receipt signed over 120 attacker keys (population hash differs from canonical)
    attacker_keys = [f"attacker-tx-key-{i}" for i in range(120)]
    attacker_query_rcpt = create_avm_query_source_receipt(
        dataset_snapshot_id="snapshot-002",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        observed_labeled_count=120,
        eligible_mature_count=120,
        population_keys=attacker_keys,
    )

    with pytest.raises(AVMOutcomeValidationError, match="population-mismatched"):
        generate_gate1_benchmark_receipt(
            pass_report,
            dataset_snapshot_id="snapshot-002",
            dataset_snapshot_hash=VALID_DATASET_HASH,
            model_artifact_hash=VALID_MODEL_HASH,
            audit_receipt=audit_rcpt,
            activation_receipt=activation_rcpt,
            query_source_receipt=attacker_query_rcpt,
        )
