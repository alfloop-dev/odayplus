"""AVM authoritative transaction-outcome inventory, alignment, calibration, and fail-closed governance."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

ACTIVATION_THRESHOLD = 120
CANONICAL_AVM_MODEL_VERSION = "dealroom-avm-baseline-v1"


class AVMOutcomeValidationError(RuntimeError):
    """Raised when AVM outcome inventory or alignment violates fail-closed rules."""


class AVMVerdict(StrEnum):
    PASS = "PASS"
    FAIL_CLOSED = "FAIL_CLOSED"


@dataclass(frozen=True)
class AVMOutcomeTransaction:
    transaction_id: str
    store_id: str
    realized_price: float
    transaction_date: datetime
    is_mature: bool
    is_synthetic: bool = False
    authority_partition: str = "official_real_estate"
    source_variant_id: str = "v1"
    raw_record_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "store_id": self.store_id,
            "realized_price": self.realized_price,
            "transaction_date": self.transaction_date.isoformat(),
            "is_mature": self.is_mature,
            "is_synthetic": self.is_synthetic,
            "authority_partition": self.authority_partition,
            "source_variant_id": self.source_variant_id,
            "raw_record_sha256": self.raw_record_sha256,
        }


@dataclass(frozen=True)
class AVMPredictionRecord:
    prediction_id: str
    store_id: str
    p10: float
    p50: float
    p90: float
    model_version: str = CANONICAL_AVM_MODEL_VERSION
    predicted_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "store_id": self.store_id,
            "p10": self.p10,
            "p50": self.p50,
            "p90": self.p90,
            "model_version": self.model_version,
            "predicted_at": self.predicted_at.isoformat(),
        }


@dataclass(frozen=True)
class AlignedOutcomePredictionPair:
    transaction_id: str
    store_id: str
    realized_price: float
    p10: float
    p50: float
    p90: float
    model_version: str
    is_covered_p10_p90: bool
    is_covered_p10_p50: bool
    is_covered_p50_p90: bool
    abs_error: float
    abs_percentage_error: float
    calibration_ratio: float
    value_band: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "store_id": self.store_id,
            "realized_price": self.realized_price,
            "p10": self.p10,
            "p50": self.p50,
            "p90": self.p90,
            "model_version": self.model_version,
            "is_covered_p10_p90": self.is_covered_p10_p90,
            "is_covered_p10_p50": self.is_covered_p10_p50,
            "is_covered_p50_p90": self.is_covered_p50_p90,
            "abs_error": self.abs_error,
            "abs_percentage_error": self.abs_percentage_error,
            "calibration_ratio": self.calibration_ratio,
            "value_band": self.value_band,
        }


@dataclass(frozen=True)
class ValueBandMetrics:
    band_name: str
    aligned_count: int
    p10_p90_coverage_rate: float
    calibration_ratio: float
    mape: float
    mae: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "band_name": self.band_name,
            "aligned_count": self.aligned_count,
            "p10_p90_coverage_rate": self.p10_p90_coverage_rate,
            "calibration_ratio": self.calibration_ratio,
            "mape": self.mape,
            "mae": self.mae,
        }


@dataclass(frozen=True)
class AVMOutcomeCalibrationReport:
    model_version: str
    observed_labeled_count: int
    eligible_mature_count: int
    auto_seeded_count: int
    activation_threshold: int
    is_governed_disabled: bool
    reason_code: str
    verdict: AVMVerdict
    aligned_count: int
    p10_p90_coverage_rate: float
    p10_p50_coverage_rate: float
    p50_p90_coverage_rate: float
    mae: float
    mape: float
    median_calibration_ratio: float
    value_band_metrics: dict[str, ValueBandMetrics]
    dataset_snapshot_id: str
    dataset_snapshot_hash: str
    model_artifact_hash: str
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "observed_labeled_count": self.observed_labeled_count,
            "eligible_mature_count": self.eligible_mature_count,
            "auto_seeded_count": self.auto_seeded_count,
            "activation_threshold": self.activation_threshold,
            "is_governed_disabled": self.is_governed_disabled,
            "reason_code": self.reason_code,
            "verdict": self.verdict.value,
            "aligned_count": self.aligned_count,
            "p10_p90_coverage_rate": self.p10_p90_coverage_rate,
            "p10_p50_coverage_rate": self.p10_p50_coverage_rate,
            "p50_p90_coverage_rate": self.p50_p90_coverage_rate,
            "mae": self.mae,
            "mape": self.mape,
            "median_calibration_ratio": self.median_calibration_ratio,
            "value_band_metrics": {k: v.to_dict() for k, v in self.value_band_metrics.items()},
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "dataset_snapshot_hash": self.dataset_snapshot_hash,
            "model_artifact_hash": self.model_artifact_hash,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


def assign_value_band(price: float) -> str:
    """Assign transaction price to canonical value band."""
    if price < 10_000_000.0:
        return "band_low_lt10m"
    if price <= 30_000_000.0:
        return "band_mid_10m_to_30m"
    return "band_high_gt30m"


def align_outcomes_and_predictions(
    outcomes: list[AVMOutcomeTransaction],
    predictions: list[AVMPredictionRecord],
) -> list[AlignedOutcomePredictionPair]:
    """Join exact model predictions to realized outcomes with strict fail-closed validations."""
    seen_transactions: set[str] = set()
    seen_predictions: set[str] = set()
    pred_by_store: dict[str, AVMPredictionRecord] = {}

    for pred in predictions:
        if pred.prediction_id in seen_predictions:
            raise AVMOutcomeValidationError(f"Duplicate prediction record {pred.prediction_id!r}")
        seen_predictions.add(pred.prediction_id)
        pred_by_store[pred.store_id] = pred

    aligned_pairs: list[AlignedOutcomePredictionPair] = []
    zero_error_count = 0

    for outcome in outcomes:
        if outcome.is_synthetic:
            raise AVMOutcomeValidationError(
                f"Synthetic transaction row detected: {outcome.transaction_id!r}"
            )
        if not outcome.is_mature:
            raise AVMOutcomeValidationError(
                f"Immature transaction row detected: {outcome.transaction_id!r}"
            )
        if outcome.transaction_id in seen_transactions:
            raise AVMOutcomeValidationError(
                f"Duplicate transaction join detected: {outcome.transaction_id!r}"
            )
        seen_transactions.add(outcome.transaction_id)

        pred = pred_by_store.get(outcome.store_id)
        if pred is None:
            continue

        if not (pred.p10 <= pred.p50 <= pred.p90):
            raise AVMOutcomeValidationError(
                f"Prediction interval bounds invalid for store {pred.store_id!r}: "
                f"p10={pred.p10}, p50={pred.p50}, p90={pred.p90}"
            )

        # Check for prediction copied from outcome (zero-error substitution fraud)
        diff = abs(outcome.realized_price - pred.p50)
        if diff < 1e-6:
            zero_error_count += 1

        is_covered_p10_p90 = pred.p10 <= outcome.realized_price <= pred.p90
        is_covered_p10_p50 = pred.p10 <= outcome.realized_price <= pred.p50
        is_covered_p50_p90 = pred.p50 <= outcome.realized_price <= pred.p90

        abs_err = abs(outcome.realized_price - pred.p50)
        mape_val = abs_err / outcome.realized_price if outcome.realized_price > 0 else 0.0
        calib_ratio = outcome.realized_price / pred.p50 if pred.p50 > 0 else 1.0
        band = assign_value_band(outcome.realized_price)

        aligned_pairs.append(
            AlignedOutcomePredictionPair(
                transaction_id=outcome.transaction_id,
                store_id=outcome.store_id,
                realized_price=outcome.realized_price,
                p10=pred.p10,
                p50=pred.p50,
                p90=pred.p90,
                model_version=pred.model_version,
                is_covered_p10_p90=is_covered_p10_p90,
                is_covered_p10_p50=is_covered_p10_p50,
                is_covered_p50_p90=is_covered_p50_p90,
                abs_error=abs_err,
                abs_percentage_error=mape_val,
                calibration_ratio=calib_ratio,
                value_band=band,
            )
        )

    # Fail closed if prediction was copied from outcome across all aligned rows
    if aligned_pairs and zero_error_count == len(aligned_pairs):
        raise AVMOutcomeValidationError(
            "Fail-closed: Prediction values were directly copied from realized outcomes"
        )

    return aligned_pairs


def compute_avm_outcome_calibration(
    aligned_pairs: list[AlignedOutcomePredictionPair],
    *,
    observed_count: int,
    eligible_count: int,
    auto_seeded_count: int = 0,
    model_version: str = CANONICAL_AVM_MODEL_VERSION,
    dataset_snapshot_id: str = "",
    dataset_snapshot_hash: str = "",
    model_artifact_hash: str = "",
) -> AVMOutcomeCalibrationReport:
    """Compute coverage, calibration, and value-band metrics with fail-closed assertions."""
    # Fail-closed validations
    if auto_seeded_count > 0:
        raise AVMOutcomeValidationError("Fail-closed: Auto-seeded or synthetic rows present")

    if not dataset_snapshot_hash or len(dataset_snapshot_hash) != 64:
        raise AVMOutcomeValidationError("Fail-closed: Unbound or invalid dataset_snapshot_hash")

    if not model_artifact_hash or len(model_artifact_hash) != 64:
        raise AVMOutcomeValidationError("Fail-closed: Unbound or invalid model_artifact_hash")

    # Determine activation maturity
    is_governed_disabled = eligible_count < ACTIVATION_THRESHOLD
    reason_code = (
        "DATA_CONTRACT_NOT_MATURE" if is_governed_disabled else "MATURE_LABEL_CONTRACT_READY"
    )
    verdict = AVMVerdict.FAIL_CLOSED if is_governed_disabled else AVMVerdict.PASS

    if not aligned_pairs:
        empty_value_bands = {
            band: ValueBandMetrics(
                band_name=band,
                aligned_count=0,
                p10_p90_coverage_rate=0.0,
                calibration_ratio=0.0,
                mape=0.0,
                mae=0.0,
            )
            for band in ("band_low_lt10m", "band_mid_10m_to_30m", "band_high_gt30m")
        }
        return AVMOutcomeCalibrationReport(
            model_version=model_version,
            observed_labeled_count=observed_count,
            eligible_mature_count=eligible_count,
            auto_seeded_count=auto_seeded_count,
            activation_threshold=ACTIVATION_THRESHOLD,
            is_governed_disabled=is_governed_disabled,
            reason_code=reason_code,
            verdict=verdict,
            aligned_count=0,
            p10_p90_coverage_rate=0.0,
            p10_p50_coverage_rate=0.0,
            p50_p90_coverage_rate=0.0,
            mae=0.0,
            mape=0.0,
            median_calibration_ratio=0.0,
            value_band_metrics=empty_value_bands,
            dataset_snapshot_id=dataset_snapshot_id,
            dataset_snapshot_hash=dataset_snapshot_hash,
            model_artifact_hash=model_artifact_hash,
        )

    n = len(aligned_pairs)
    cov_p10_p90 = sum(1 for p in aligned_pairs if p.is_covered_p10_p90) / n
    cov_p10_p50 = sum(1 for p in aligned_pairs if p.is_covered_p10_p50) / n
    cov_p50_p90 = sum(1 for p in aligned_pairs if p.is_covered_p50_p90) / n

    mae = sum(p.abs_error for p in aligned_pairs) / n
    mape = sum(p.abs_percentage_error for p in aligned_pairs) / n

    ratios = sorted(p.calibration_ratio for p in aligned_pairs)
    med_ratio = ratios[n // 2] if n % 2 != 0 else (ratios[n // 2 - 1] + ratios[n // 2]) / 2.0

    # Ensure all calculated metrics are finite
    for name, val in (
        ("cov_p10_p90", cov_p10_p90),
        ("cov_p10_p50", cov_p10_p50),
        ("cov_p50_p90", cov_p50_p90),
        ("mae", mae),
        ("mape", mape),
        ("med_ratio", med_ratio),
    ):
        if math.isnan(val) or math.isinf(val):
            raise AVMOutcomeValidationError(f"Fail-closed: Non-finite metric calculated for {name}")

    # Value band breakdown
    band_groups: dict[str, list[AlignedOutcomePredictionPair]] = {
        "band_low_lt10m": [],
        "band_mid_10m_to_30m": [],
        "band_high_gt30m": [],
    }
    for pair in aligned_pairs:
        band_groups.setdefault(pair.value_band, []).append(pair)

    value_band_metrics: dict[str, ValueBandMetrics] = {}
    for band_name, items in band_groups.items():
        if not items:
            value_band_metrics[band_name] = ValueBandMetrics(
                band_name=band_name,
                aligned_count=0,
                p10_p90_coverage_rate=0.0,
                calibration_ratio=0.0,
                mape=0.0,
                mae=0.0,
            )
            continue
        bn = len(items)
        bcov = sum(1 for p in items if p.is_covered_p10_p90) / bn
        bmae = sum(p.abs_error for p in items) / bn
        bmape = sum(p.abs_percentage_error for p in items) / bn
        bratios = sorted(p.calibration_ratio for p in items)
        bmed = bratios[bn // 2] if bn % 2 != 0 else (bratios[bn // 2 - 1] + bratios[bn // 2]) / 2.0

        for bval_name, bval in (("bcov", bcov), ("bmae", bmae), ("bmape", bmape), ("bmed", bmed)):
            if math.isnan(bval) or math.isinf(bval):
                raise AVMOutcomeValidationError(
                    f"Fail-closed: Non-finite metric for band {band_name} ({bval_name})"
                )

        value_band_metrics[band_name] = ValueBandMetrics(
            band_name=band_name,
            aligned_count=bn,
            p10_p90_coverage_rate=round(bcov, 4),
            calibration_ratio=round(bmed, 4),
            mape=round(bmape, 4),
            mae=round(bmae, 2),
        )

    # Fail closed if someone attempts to forge an ACTIVE verdict when eligible_count < 120
    if eligible_count < ACTIVATION_THRESHOLD and verdict == AVMVerdict.PASS:
        raise AVMOutcomeValidationError(
            "Fail-closed: Forged ACTIVE verdict when eligible outcome count < 120"
        )

    return AVMOutcomeCalibrationReport(
        model_version=model_version,
        observed_labeled_count=observed_count,
        eligible_mature_count=eligible_count,
        auto_seeded_count=auto_seeded_count,
        activation_threshold=ACTIVATION_THRESHOLD,
        is_governed_disabled=is_governed_disabled,
        reason_code=reason_code,
        verdict=verdict,
        aligned_count=n,
        p10_p90_coverage_rate=round(cov_p10_p90, 4),
        p10_p50_coverage_rate=round(cov_p10_p50, 4),
        p50_p90_coverage_rate=round(cov_p50_p90, 4),
        mae=round(mae, 2),
        mape=round(mape, 4),
        median_calibration_ratio=round(med_ratio, 4),
        value_band_metrics=value_band_metrics,
        dataset_snapshot_id=dataset_snapshot_id,
        dataset_snapshot_hash=dataset_snapshot_hash,
        model_artifact_hash=model_artifact_hash,
    )
