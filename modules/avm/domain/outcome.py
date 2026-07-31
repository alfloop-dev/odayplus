"""AVM authoritative transaction-outcome inventory, alignment, calibration, and fail-closed governance."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

ACTIVATION_THRESHOLD = 120
MAX_TRANSACTION_FRESHNESS_DAYS = 365
MIN_TRANSACTION_MATURITY_DAYS = 1
CANONICAL_AVM_MODEL_VERSION = "dealroom-avm-baseline-v1"
ALLOWED_AUTHORITY_PARTITIONS = frozenset(
    {"official_real_estate", "authoritative_real_estate_transaction"}
)
CANONICAL_HUMAN_OPS_ACTIVATION_KEY = "human-ops-avm-outcome-activation-key-v1"
SHA256_REGEX = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class AVMActivationAuthorityReceipt:
    authority_id: str
    approval_status: str
    dataset_snapshot_hash: str
    model_artifact_hash: str
    issued_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    signature_digest: str = ""

    def __post_init__(self) -> None:
        if self.authority_id not in ("Human/Ops", "human-ops-label-authority"):
            raise AVMOutcomeValidationError(f"Invalid activation authority_id: {self.authority_id!r}")
        if self.approval_status != "APPROVED":
            raise AVMOutcomeValidationError(f"Activation authority status not APPROVED: {self.approval_status!r}")

    def verify_attestation(
        self,
        expected_dataset_snapshot_hash: str,
        expected_model_artifact_hash: str,
        authority_key: str = CANONICAL_HUMAN_OPS_ACTIVATION_KEY,
    ) -> bool:
        if self.authority_id not in ("Human/Ops", "human-ops-label-authority"):
            return False
        if self.approval_status != "APPROVED":
            return False
        if self.dataset_snapshot_hash != expected_dataset_snapshot_hash:
            return False
        if self.model_artifact_hash != expected_model_artifact_hash:
            return False
        if not self.signature_digest or not SHA256_REGEX.match(self.signature_digest):
            return False
        if not authority_key or authority_key != CANONICAL_HUMAN_OPS_ACTIVATION_KEY:
            return False
        canonical = f"{self.authority_id}:{self.approval_status}:{self.dataset_snapshot_hash}:{self.model_artifact_hash}:{authority_key}"
        expected_sig = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if self.signature_digest != expected_sig:
            return False
        return True


def create_avm_activation_receipt(
    dataset_snapshot_hash: str,
    model_artifact_hash: str,
    authority_id: str = "Human/Ops",
    approval_status: str = "APPROVED",
    authority_key: str = CANONICAL_HUMAN_OPS_ACTIVATION_KEY,
) -> AVMActivationAuthorityReceipt:
    if not authority_key or authority_key != CANONICAL_HUMAN_OPS_ACTIVATION_KEY:
        raise AVMOutcomeValidationError("Fail-closed: Invalid or missing authority key for activation receipt creation")
    canonical = f"{authority_id}:{approval_status}:{dataset_snapshot_hash}:{model_artifact_hash}:{authority_key}"
    sig = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return AVMActivationAuthorityReceipt(
        authority_id=authority_id,
        approval_status=approval_status,
        dataset_snapshot_hash=dataset_snapshot_hash,
        model_artifact_hash=model_artifact_hash,
        signature_digest=sig,
    )


@dataclass(frozen=True)
class AVMQuerySourceReceipt:
    relation: str
    query_timestamp: datetime
    dataset_snapshot_id: str
    dataset_snapshot_hash: str
    observed_labeled_count: int
    eligible_mature_count: int
    population_keys_sha256: str = ""
    authority_id: str = "Human/Ops"
    receipt_sha256: str = ""

    def verify_query_receipt(
        self,
        expected_snapshot_hash: str,
        expected_snapshot_id: str = "",
        expected_observed: int = 0,
        expected_eligible: int = 0,
        expected_aligned: int = 0,
        expected_population_sha256: str = "",
        max_age_seconds: int = 86400,
        authority_key: str = CANONICAL_HUMAN_OPS_ACTIVATION_KEY,
    ) -> bool:
        if self.relation != "model_ready.valuation_view":
            return False
        if self.authority_id not in ("Human/Ops", "human-ops-label-authority"):
            return False
        if self.dataset_snapshot_hash != expected_snapshot_hash:
            return False
        if not SHA256_REGEX.match(self.dataset_snapshot_hash):
            return False
        if expected_snapshot_id and self.dataset_snapshot_id != expected_snapshot_id:
            return False
        # Exact population reconciliation assertion
        if self.observed_labeled_count != self.eligible_mature_count:
            return False
        if expected_observed > 0 and self.observed_labeled_count != expected_observed:
            return False
        if expected_eligible > 0 and self.eligible_mature_count != expected_eligible:
            return False
        if expected_aligned > 0 and self.observed_labeled_count != expected_aligned:
            return False
        if expected_population_sha256 and self.population_keys_sha256 != expected_population_sha256:
            return False
        if self.observed_labeled_count < ACTIVATION_THRESHOLD or self.eligible_mature_count < ACTIVATION_THRESHOLD:
            return False
        if not self.receipt_sha256 or not SHA256_REGEX.match(self.receipt_sha256):
            return False
        if not authority_key or authority_key != CANONICAL_HUMAN_OPS_ACTIVATION_KEY:
            return False
        ts_utc = self.query_timestamp.astimezone(UTC) if self.query_timestamp.tzinfo else self.query_timestamp.replace(tzinfo=UTC)
        now_utc = datetime.now(UTC)
        age = (now_utc - ts_utc).total_seconds()
        if age < -60 or age > max_age_seconds:
            return False
        canonical = f"{self.authority_id}:model_ready.valuation_view:{self.dataset_snapshot_id}:{self.dataset_snapshot_hash}:{self.observed_labeled_count}:{self.eligible_mature_count}:{self.population_keys_sha256}:{self.query_timestamp.isoformat()}:{authority_key}"
        expected_sig = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if self.receipt_sha256 != expected_sig:
            return False
        return True


def create_avm_query_source_receipt(
    dataset_snapshot_id: str,
    dataset_snapshot_hash: str,
    observed_labeled_count: int = 0,
    eligible_mature_count: int = 0,
    population_keys: list[str] | tuple[str, ...] | None = None,
    query_timestamp: datetime | None = None,
    authority_id: str = "Human/Ops",
    authority_key: str = CANONICAL_HUMAN_OPS_ACTIVATION_KEY,
) -> AVMQuerySourceReceipt:
    if not authority_key or authority_key != CANONICAL_HUMAN_OPS_ACTIVATION_KEY:
        raise AVMOutcomeValidationError("Fail-closed: Invalid authority key for query source receipt creation")
    ts = query_timestamp or datetime.now(UTC)
    pop_sha = hashlib.sha256(",".join(sorted(population_keys)).encode("utf-8")).hexdigest() if population_keys else ""
    canonical = f"{authority_id}:model_ready.valuation_view:{dataset_snapshot_id}:{dataset_snapshot_hash}:{observed_labeled_count}:{eligible_mature_count}:{pop_sha}:{ts.isoformat()}:{authority_key}"
    sig = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return AVMQuerySourceReceipt(
        relation="model_ready.valuation_view",
        query_timestamp=ts,
        dataset_snapshot_id=dataset_snapshot_id,
        dataset_snapshot_hash=dataset_snapshot_hash,
        observed_labeled_count=observed_labeled_count,
        eligible_mature_count=eligible_mature_count,
        population_keys_sha256=pop_sha,
        authority_id=authority_id,
        receipt_sha256=sig,
    )


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
    authentic_data_activated: bool = False
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
            "authentic_data_activated": self.authentic_data_activated,
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
    *,
    expected_model_version: str = CANONICAL_AVM_MODEL_VERSION,
    evaluation_reference_time: datetime | None = None,
) -> list[AlignedOutcomePredictionPair]:
    """Join exact model predictions to realized outcomes with strict fail-closed validations."""
    ref_time = evaluation_reference_time or datetime.now(UTC)

    # B15: Population reconciliation - enforce exact count and key parity
    if len(predictions) != len(outcomes):
        raise AVMOutcomeValidationError(
            f"Fail-closed: Population drift detected: predictions count ({len(predictions)}) != outcomes count ({len(outcomes)})"
        )

    pred_store_ids = {p.store_id for p in predictions}
    outcome_store_ids = {o.store_id for o in outcomes}
    if pred_store_ids != outcome_store_ids:
        missing_in_outcomes = pred_store_ids - outcome_store_ids
        missing_in_preds = outcome_store_ids - pred_store_ids
        raise AVMOutcomeValidationError(
            f"Fail-closed: Population key reconciliation mismatch: "
            f"predictions without outcomes={missing_in_outcomes}, outcomes without predictions={missing_in_preds}"
        )

    seen_transactions: set[str] = set()
    seen_predictions: set[str] = set()
    pred_by_store: dict[str, AVMPredictionRecord] = {}

    for pred in predictions:
        if pred.prediction_id in seen_predictions:
            raise AVMOutcomeValidationError(f"Duplicate prediction record {pred.prediction_id!r}")
        seen_predictions.add(pred.prediction_id)

        if pred.model_version != expected_model_version:
            raise AVMOutcomeValidationError(
                f"Mixed or wrong model version {pred.model_version!r} in prediction {pred.prediction_id!r} (expected {expected_model_version!r})"
            )

        if pred.store_id in pred_by_store:
            raise AVMOutcomeValidationError(
                f"Duplicate prediction record for store_id {pred.store_id!r}"
            )
        pred_by_store[pred.store_id] = pred

    aligned_pairs: list[AlignedOutcomePredictionPair] = []

    for outcome in outcomes:
        if outcome.is_synthetic:
            raise AVMOutcomeValidationError(
                f"Synthetic transaction row detected: {outcome.transaction_id!r}"
            )
        if not outcome.is_mature:
            raise AVMOutcomeValidationError(
                f"Immature transaction row detected: {outcome.transaction_id!r}"
            )

        # B3: Authority partition validation
        if outcome.authority_partition not in ALLOWED_AUTHORITY_PARTITIONS:
            raise AVMOutcomeValidationError(
                f"Non-authoritative partition {outcome.authority_partition!r} for transaction {outcome.transaction_id!r}"
            )

        # B3: Raw record SHA-256 validation
        if not SHA256_REGEX.match(outcome.raw_record_sha256):
            raise AVMOutcomeValidationError(
                f"Invalid or missing raw_record_sha256 {outcome.raw_record_sha256!r} for transaction {outcome.transaction_id!r}"
            )

        # B3 & B16: Maturity boundary & freshness check (future or stale transaction cannot be mature)
        tx_dt = outcome.transaction_date.astimezone(UTC) if outcome.transaction_date.tzinfo else outcome.transaction_date.replace(tzinfo=UTC)
        if tx_dt > ref_time:
            raise AVMOutcomeValidationError(
                f"Future transaction date {outcome.transaction_date.isoformat()} cannot be marked mature: {outcome.transaction_id!r}"
            )

        age_days = (ref_time - tx_dt).total_seconds() / 86400.0
        if age_days > MAX_TRANSACTION_FRESHNESS_DAYS:
            raise AVMOutcomeValidationError(
                f"Fail-closed: Stale transaction row detected: transaction_date {outcome.transaction_date.isoformat()} "
                f"is {age_days:.1f} days old (exceeds max freshness policy of {MAX_TRANSACTION_FRESHNESS_DAYS} days) for transaction {outcome.transaction_id!r}"
            )
        if age_days < MIN_TRANSACTION_MATURITY_DAYS:
            raise AVMOutcomeValidationError(
                f"Fail-closed: Immature transaction row detected: transaction_date {outcome.transaction_date.isoformat()} "
                f"does not satisfy minimum maturity policy of {MIN_TRANSACTION_MATURITY_DAYS} days for transaction {outcome.transaction_id!r}"
            )

        if outcome.transaction_id in seen_transactions:
            raise AVMOutcomeValidationError(
                f"Duplicate transaction join detected: {outcome.transaction_id!r}"
            )
        seen_transactions.add(outcome.transaction_id)

        # B2: Missing prediction rejection (do not silently drop)
        pred = pred_by_store.get(outcome.store_id)
        if pred is None:
            raise AVMOutcomeValidationError(
                f"Missing prediction record for transaction {outcome.transaction_id!r} / store {outcome.store_id!r}"
            )

        # B9: Temporal leakage check - prediction must be strictly before transaction date
        pred_dt = pred.predicted_at.astimezone(UTC) if pred.predicted_at.tzinfo else pred.predicted_at.replace(tzinfo=UTC)
        if pred_dt >= tx_dt:
            raise AVMOutcomeValidationError(
                f"Temporal leakage detected: prediction timestamp {pred.predicted_at.isoformat()} "
                f"is not strictly before transaction date {outcome.transaction_date.isoformat()} for store {outcome.store_id!r}"
            )

        # B20: Positive finite economic value validations
        if not math.isfinite(outcome.realized_price) or outcome.realized_price <= 0:
            raise AVMOutcomeValidationError(
                f"Fail-closed: Non-positive or non-finite realized_price {outcome.realized_price} for transaction {outcome.transaction_id!r}"
            )
        if (
            not (math.isfinite(pred.p10) and math.isfinite(pred.p50) and math.isfinite(pred.p90))
            or pred.p10 <= 0 or pred.p50 <= 0 or pred.p90 <= 0
        ):
            raise AVMOutcomeValidationError(
                f"Fail-closed: Non-positive or non-finite prediction quantiles for store {pred.store_id!r} "
                f"(p10={pred.p10}, p50={pred.p50}, p90={pred.p90})"
            )

        if not (pred.p10 <= pred.p50 <= pred.p90):
            raise AVMOutcomeValidationError(
                f"Prediction interval bounds invalid for store {pred.store_id!r}: "
                f"p10={pred.p10}, p50={pred.p50}, p90={pred.p90}"
            )

        # B4: Fail closed if ANY single prediction was copied from outcome (zero-error substitution fraud)
        diff = abs(outcome.realized_price - pred.p50)
        if diff < 1e-6:
            raise AVMOutcomeValidationError(
                f"Fail-closed: Prediction value p50={pred.p50} was directly copied from outcome realized_price={outcome.realized_price} for transaction {outcome.transaction_id!r}"
            )

        is_covered_p10_p90 = pred.p10 <= outcome.realized_price <= pred.p90
        is_covered_p10_p50 = pred.p10 <= outcome.realized_price <= pred.p50
        is_covered_p50_p90 = pred.p50 <= outcome.realized_price <= pred.p90

        abs_err = abs(outcome.realized_price - pred.p50)
        mape_val = abs_err / outcome.realized_price
        calib_ratio = outcome.realized_price / pred.p50
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
    activation_receipt: AVMActivationAuthorityReceipt | None = None,
    audit_receipt: dict[str, Any] | None = None,
    query_source_receipt: AVMQuerySourceReceipt | None = None,
) -> AVMOutcomeCalibrationReport:
    """Compute coverage, calibration, and value-band metrics with fail-closed assertions."""
    from modules.dealroom.application.outcome_audit import verify_audit_receipt

    # Fail-closed validations
    if auto_seeded_count > 0:
        raise AVMOutcomeValidationError("Fail-closed: Auto-seeded or synthetic rows present")

    # B5: Lowercase SHA-256 validation
    if not dataset_snapshot_hash or not SHA256_REGEX.match(dataset_snapshot_hash):
        raise AVMOutcomeValidationError("Fail-closed: Unbound or invalid dataset_snapshot_hash")

    if not model_artifact_hash or not SHA256_REGEX.match(model_artifact_hash):
        raise AVMOutcomeValidationError("Fail-closed: Unbound or invalid model_artifact_hash")

    # B12 & B17: Activation authority receipt verification (require Human/Ops attestation with valid signature key)
    authentic_data_activated = False
    if activation_receipt is not None:
        authentic_data_activated = activation_receipt.verify_attestation(
            expected_dataset_snapshot_hash=dataset_snapshot_hash,
            expected_model_artifact_hash=model_artifact_hash,
        )

    # B14 & B18: Confidential access audit verification with full body integrity, count reconciliation, and snapshot lineage
    access_audit_verified = verify_audit_receipt(
        audit_receipt,
        expected_snapshot_hash=dataset_snapshot_hash,
    )

    aligned_count = len(aligned_pairs)

    # B19 & B25: Query source receipt verification bound to exact snapshot population
    query_receipt_verified = False
    if query_source_receipt is not None:
        query_receipt_verified = query_source_receipt.verify_query_receipt(
            expected_snapshot_hash=dataset_snapshot_hash,
            expected_snapshot_id=dataset_snapshot_id,
            expected_observed=observed_count,
            expected_eligible=eligible_count,
            expected_aligned=aligned_count,
        )

    # B1 & B25: Reconcile exact counts fail-closed
    if observed_count < aligned_count or eligible_count < aligned_count or (aligned_count > 0 and (observed_count != aligned_count or eligible_count != aligned_count)):
        raise AVMOutcomeValidationError(
            f"Fail-closed: Reconciled count mismatch (observed={observed_count}, eligible={eligible_count}, aligned={aligned_count})"
        )

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

    if not aligned_pairs:
        return AVMOutcomeCalibrationReport(
            model_version=model_version,
            observed_labeled_count=observed_count,
            eligible_mature_count=eligible_count,
            auto_seeded_count=auto_seeded_count,
            activation_threshold=ACTIVATION_THRESHOLD,
            is_governed_disabled=True,
            reason_code="DATA_CONTRACT_NOT_MATURE",
            verdict=AVMVerdict.FAIL_CLOSED,
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
            authentic_data_activated=authentic_data_activated,
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

    # Value band breakdown and per-band calibration policy (B21)
    band_groups: dict[str, list[AlignedOutcomePredictionPair]] = {
        "band_low_lt10m": [],
        "band_mid_10m_to_30m": [],
        "band_high_gt30m": [],
    }
    for pair in aligned_pairs:
        band_groups.setdefault(pair.value_band, []).append(pair)

    value_band_metrics: dict[str, ValueBandMetrics] = {}
    value_band_calibration_met = True
    MIN_BAND_POPULATION = 15

    for band_name in ("band_low_lt10m", "band_mid_10m_to_30m", "band_high_gt30m"):
        items = band_groups.get(band_name, [])
        if not items or len(items) < MIN_BAND_POPULATION:
            value_band_calibration_met = False
            value_band_metrics[band_name] = ValueBandMetrics(
                band_name=band_name,
                aligned_count=len(items),
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

        if bcov < 0.75 or not (0.90 <= bmed <= 1.10) or bmape > 0.20:
            value_band_calibration_met = False

        value_band_metrics[band_name] = ValueBandMetrics(
            band_name=band_name,
            aligned_count=bn,
            p10_p90_coverage_rate=round(bcov, 4),
            calibration_ratio=round(bmed, 4),
            mape=round(bmape, 4),
            mae=round(bmae, 2),
        )

    # B1, B8, B12, B14, B17-B21: Evaluate full activation, calibration targets, access audit, query source, and value band gates
    count_sufficient = (
        observed_count >= ACTIVATION_THRESHOLD
        and eligible_count >= ACTIVATION_THRESHOLD
        and n >= ACTIVATION_THRESHOLD
    )
    calibration_targets_met = (
        cov_p10_p90 >= 0.80
        and (0.95 <= med_ratio <= 1.05)
        and mape <= 0.15
    )

    if not count_sufficient:
        is_governed_disabled = True
        reason_code = "DATA_CONTRACT_NOT_MATURE"
        verdict = AVMVerdict.FAIL_CLOSED
    elif not calibration_targets_met:
        is_governed_disabled = True
        reason_code = "CALIBRATION_TARGET_NOT_MET"
        verdict = AVMVerdict.FAIL_CLOSED
    elif not value_band_calibration_met:
        is_governed_disabled = True
        reason_code = "VALUE_BAND_CALIBRATION_NOT_MET"
        verdict = AVMVerdict.FAIL_CLOSED
    elif not query_receipt_verified:
        is_governed_disabled = True
        reason_code = "QUERY_SOURCE_RECEIPT_NOT_VERIFIED"
        verdict = AVMVerdict.FAIL_CLOSED
    elif not access_audit_verified:
        is_governed_disabled = True
        reason_code = "ACCESS_AUDIT_NOT_VERIFIED"
        verdict = AVMVerdict.FAIL_CLOSED
    elif not authentic_data_activated:
        is_governed_disabled = True
        reason_code = "AUTHENTIC_DATA_ACTIVATION_PENDING"
        verdict = AVMVerdict.FAIL_CLOSED
    else:
        is_governed_disabled = False
        reason_code = "MATURE_LABEL_CONTRACT_READY"
        verdict = AVMVerdict.PASS

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
        authentic_data_activated=authentic_data_activated,
    )
