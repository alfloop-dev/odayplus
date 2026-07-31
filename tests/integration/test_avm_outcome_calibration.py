from __future__ import annotations

from datetime import UTC, datetime

import pytest

from modules.avm.domain.outcome import (
    AVMOutcomeTransaction,
    AVMOutcomeValidationError,
    AVMPredictionRecord,
    AVMVerdict,
    align_outcomes_and_predictions,
    compute_avm_outcome_calibration,
)
from modules.dealroom.domain.confidential_access import (
    ConfidentialLeakError,
    assert_no_confidential_leak,
)

VALID_DATASET_HASH = "a1b2c3d4e5f60718293a4b5c6d7e8f901234567890abcdef1234567890abcdef"
VALID_MODEL_HASH = "b2c3d4e5f60718293a4b5c6d7e8f901234567890abcdef1234567890abcdef01"


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
    now = datetime.now(UTC)
    outcomes = [
        AVMOutcomeTransaction("tx-1", "s-1", 8_000_000.0, now, is_mature=True),
        AVMOutcomeTransaction("tx-2", "s-2", 20_000_000.0, now, is_mature=True),
        AVMOutcomeTransaction("tx-3", "s-3", 40_000_000.0, now, is_mature=True),
    ]
    predictions = [
        AVMPredictionRecord("pred-1", "s-1", 7_000_000.0, 8_200_000.0, 9_500_000.0),
        AVMPredictionRecord("pred-2", "s-2", 17_000_000.0, 20_500_000.0, 24_000_000.0),
        AVMPredictionRecord("pred-3", "s-3", 35_000_000.0, 42_000_000.0, 48_000_000.0),
    ]

    aligned = align_outcomes_and_predictions(outcomes, predictions)
    assert len(aligned) == 3

    report = compute_avm_outcome_calibration(
        aligned,
        observed_count=3,
        eligible_count=130,  # >= 120 threshold
        auto_seeded_count=0,
        dataset_snapshot_id="snapshot-002",
        dataset_snapshot_hash=VALID_DATASET_HASH,
        model_artifact_hash=VALID_MODEL_HASH,
    )

    assert report.is_governed_disabled is False
    assert report.verdict == AVMVerdict.PASS
    assert report.p10_p90_coverage_rate == 1.0
    assert 0.90 <= report.median_calibration_ratio <= 1.10
    assert "band_low_lt10m" in report.value_band_metrics
    assert "band_mid_10m_to_30m" in report.value_band_metrics
    assert "band_high_gt30m" in report.value_band_metrics
    assert report.value_band_metrics["band_low_lt10m"].aligned_count == 1
    assert report.value_band_metrics["band_mid_10m_to_30m"].aligned_count == 1
    assert report.value_band_metrics["band_high_gt30m"].aligned_count == 1


# --- Mutation Tests for Fail-Closed Enforcement ---

def test_mutation_synthetic_row_fails_closed() -> None:
    now = datetime.now(UTC)
    outcomes = [
        AVMOutcomeTransaction("tx-synth", "s-1", 15_000_000.0, now, is_mature=True, is_synthetic=True)
    ]
    predictions = [AVMPredictionRecord("pred-1", "s-1", 12_000_000.0, 15_000_000.0, 18_000_000.0)]

    with pytest.raises(AVMOutcomeValidationError, match="Synthetic transaction row detected"):
        align_outcomes_and_predictions(outcomes, predictions)


def test_mutation_immature_transaction_fails_closed() -> None:
    now = datetime.now(UTC)
    outcomes = [
        AVMOutcomeTransaction("tx-immature", "s-1", 15_000_000.0, now, is_mature=False)
    ]
    predictions = [AVMPredictionRecord("pred-1", "s-1", 12_000_000.0, 15_000_000.0, 18_000_000.0)]

    with pytest.raises(AVMOutcomeValidationError, match="Immature transaction row detected"):
        align_outcomes_and_predictions(outcomes, predictions)


def test_mutation_copied_prediction_substitution_fails_closed() -> None:
    now = datetime.now(UTC)
    outcomes = [
        AVMOutcomeTransaction("tx-1", "s-1", 15_000_000.0, now, is_mature=True),
        AVMOutcomeTransaction("tx-2", "s-2", 25_000_000.0, now, is_mature=True),
    ]
    # Exact copy of outcome price to p50 (zero-error substitution fraud)
    predictions = [
        AVMPredictionRecord("pred-1", "s-1", 10_000_000.0, 15_000_000.0, 20_000_000.0),
        AVMPredictionRecord("pred-2", "s-2", 20_000_000.0, 25_000_000.0, 30_000_000.0),
    ]

    with pytest.raises(AVMOutcomeValidationError, match="Prediction values were directly copied"):
        align_outcomes_and_predictions(outcomes, predictions)


def test_mutation_duplicate_join_fails_closed() -> None:
    now = datetime.now(UTC)
    outcomes = [
        AVMOutcomeTransaction("tx-1", "s-1", 15_000_000.0, now, is_mature=True),
        AVMOutcomeTransaction("tx-1", "s-1", 15_000_000.0, now, is_mature=True),  # duplicate
    ]
    predictions = [AVMPredictionRecord("pred-1", "s-1", 12_000_000.0, 15_000_000.0, 18_000_000.0)]

    with pytest.raises(AVMOutcomeValidationError, match="Duplicate transaction join detected"):
        align_outcomes_and_predictions(outcomes, predictions)


def test_mutation_unbound_dataset_hash_fails_closed() -> None:
    with pytest.raises(AVMOutcomeValidationError, match="Unbound or invalid dataset_snapshot_hash"):
        compute_avm_outcome_calibration(
            [],
            observed_count=0,
            eligible_count=0,
            dataset_snapshot_hash="invalid_hash",
            model_artifact_hash=VALID_MODEL_HASH,
        )


def test_mutation_unbound_model_artifact_hash_fails_closed() -> None:
    with pytest.raises(AVMOutcomeValidationError, match="Unbound or invalid model_artifact_hash"):
        compute_avm_outcome_calibration(
            [],
            observed_count=0,
            eligible_count=0,
            dataset_snapshot_hash=VALID_DATASET_HASH,
            model_artifact_hash="",
        )


def test_mutation_forged_active_verdict_fails_closed() -> None:
    # Insufficient count (100 < 120) cannot pass ACTIVE verdict
    with pytest.raises(AVMOutcomeValidationError, match="Auto-seeded or synthetic rows present"):
        compute_avm_outcome_calibration(
            [],
            observed_count=100,
            eligible_count=100,
            auto_seeded_count=5,  # synthetic rows forbidden
            dataset_snapshot_hash=VALID_DATASET_HASH,
            model_artifact_hash=VALID_MODEL_HASH,
        )


def test_mutation_confidential_raw_value_leak_fails_closed() -> None:
    leaking_payload = {
        "kind": "receipt",
        "realized_price": 25000000.0,  # unmasked confidential field
    }
    with pytest.raises(ConfidentialLeakError):
        assert_no_confidential_leak(leaking_payload)
