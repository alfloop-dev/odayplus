"""Unit and mutation tests for SiteScore prediction source resolver and model registry lineage verifier."""

from __future__ import annotations

from models.sitescore.opening_outcome import (
    build_sitescore_gate2_receipt,
    build_sitescore_opening_outcome_model_card,
    evaluate_sitescore_opening_outcome_benchmark,
    verify_sitescore_gate2_receipt,
)
from models.sitescore.prediction_source import (
    CANONICAL_MODEL_VERSION,
    CANONICAL_PREDICTION_MODEL_NAME,
    CANONICAL_PREDICTION_SERVICE,
    PREDICTION_SOURCE_RECEIPT_KIND,
    build_sitescore_prediction_source_receipt,
    compute_prediction_source_receipt_sha256,
    verify_sitescore_prediction_source,
)


def _generate_valid_prediction_records(
    count: int = 220,
    *,
    dataset_snapshot_id: str = "sitescore-snapshot-2026-07-31-v1",
    model_version: str = CANONICAL_MODEL_VERSION,
    artifact_lineage_id: str = "sitescore-artifact-sha256-v1",
    base_revenue: float = 500_000.0,
    include_outcomes: bool = True,
) -> list[dict]:
    records = []
    for i in range(count):
        rev = base_revenue + (i * 1000.0)
        pred = rev * 0.95
        r = {
            "entity_id": f"tenant-001:store-{i:04d}",
            "store_id": f"store-{i:04d}",
            "target_format_code": "CONVENIENCE_STANDARD",
            "opened_on": "2025-01-01",
            "prediction_as_of": "2025-01-01",
            "is_training_eligible": True,
            "predicted_revenue": pred,
            "p10": pred * 0.85,
            "p90": pred * 1.15,
            "p50": pred * 1.0,
            "m6_days": 200,
            "m12_days": 380,
            "dataset_snapshot_id": dataset_snapshot_id,
            "model_version": model_version,
            "artifact_lineage_id": artifact_lineage_id,
        }
        if include_outcomes:
            r["realized_90d_net_revenue"] = rev
            r["realized_m6_net_revenue"] = rev * 2.15 + (i % 7) * 500.0
            r["realized_m12_net_revenue"] = rev * 4.30 + (i % 11) * 1000.0
        records.append(r)
    return records


def test_verify_sitescore_prediction_source_passes_valid_records():
    records = _generate_valid_prediction_records(220)
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    res = verify_sitescore_prediction_source(
        records,
        prediction_receipt=receipt,
        expected_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        expected_model_version=CANONICAL_MODEL_VERSION,
        expected_lineage_id="sitescore-artifact-sha256-v1",
    )
    assert res.is_valid is True
    assert res.reason_code == "PREDICTION_SOURCE_VERIFIED"
    assert res.matched_count == 220
    assert res.unmatched_count == 0
    assert res.duplicate_count == 0
    assert res.malformed_interval_count == 0
    assert res.prediction_receipt_hash is not None
    assert len(res.prediction_receipt_hash) == 64


def test_sitescore_gate2_active_path_when_prediction_and_outcome_criteria_satisfied():
    records = _generate_valid_prediction_records(220, include_outcomes=True)
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    result = evaluate_sitescore_opening_outcome_benchmark(
        records,
        prediction_receipt=receipt,
        provenance="authenticated_governed_records",
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )

    assert result.mature_label_count == 220
    assert result.matched_prediction_count == 220
    assert result.prediction_source_verified is True
    assert result.is_lineage_governed is True
    assert result.is_labels_sufficient is True
    assert result.is_coverage_passed is True
    assert result.is_interval_bounds_passed is True
    assert result.is_mae_passed is True
    assert result.is_gate2_passed is True
    assert result.status == "ACTIVE"
    assert result.reason_code == "GATE2_CRITERIA_MET"

    model_card = build_sitescore_opening_outcome_model_card(result, version=CANONICAL_MODEL_VERSION)
    gate2_receipt = build_sitescore_gate2_receipt(result, inventory_version=CANONICAL_MODEL_VERSION, model_card=model_card)

    assert gate2_receipt["gate_status"] == "PASSED"
    assert gate2_receipt["is_governed_disabled"] is False

    verif_res = verify_sitescore_gate2_receipt(gate2_receipt, model_card_artifact=model_card, dataset_manifest=records)
    assert verif_res.is_valid is True
    assert verif_res.reason_code == "RECEIPT_VALIDATED"


def test_verify_sitescore_prediction_source_fails_on_duplicate_entities():
    records = _generate_valid_prediction_records(10)
    records.append(dict(records[0]))
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    res = verify_sitescore_prediction_source(records, prediction_receipt=receipt)
    assert res.is_valid is False
    assert res.reason_code == "DUPLICATE_PREDICTION_SOURCE"
    assert res.duplicate_count == 1
    assert any("Duplicate prediction record" in err for err in res.errors)


def test_verify_sitescore_prediction_source_fails_on_malformed_intervals_p10_greater_than_p90():
    records = _generate_valid_prediction_records(10)
    records[3]["p10"] = 600_000.0
    records[3]["p90"] = 400_000.0
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    res = verify_sitescore_prediction_source(records, prediction_receipt=receipt)
    assert res.is_valid is False
    assert res.reason_code == "MALFORMED_INTERVAL_BOUNDS"
    assert res.malformed_interval_count >= 1
    assert any("p10 (600000.0) > p90 (400000.0)" in err for err in res.errors)


def test_verify_sitescore_prediction_source_fails_on_malformed_intervals_negative_bounds():
    records = _generate_valid_prediction_records(10)
    records[2]["p10"] = -100.0
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    res = verify_sitescore_prediction_source(records, prediction_receipt=receipt)
    assert res.is_valid is False
    assert res.reason_code == "MALFORMED_INTERVAL_BOUNDS"
    assert any("Negative interval bounds" in err for err in res.errors)


def test_verify_sitescore_prediction_source_fails_on_malformed_intervals_p50_outside_bounds():
    records = _generate_valid_prediction_records(10)
    records[4]["p10"] = 400_000.0
    records[4]["p50"] = 600_000.0
    records[4]["p90"] = 500_000.0
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    res = verify_sitescore_prediction_source(records, prediction_receipt=receipt)
    assert res.is_valid is False
    assert res.reason_code == "MALFORMED_INTERVAL_BOUNDS"
    assert any("p50 (600000.0) outside [400000.0, 500000.0]" in err for err in res.errors)


def test_verify_sitescore_prediction_source_fails_on_ypred_ytrue_substitution():
    records = _generate_valid_prediction_records(20, include_outcomes=True)
    for r in records:
        r["predicted_revenue"] = r["realized_90d_net_revenue"]
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    res = verify_sitescore_prediction_source(records, prediction_receipt=receipt)
    assert res.is_valid is False
    assert res.reason_code == "SYNTHETIC_SUBSTITUTION_REJECTED"
    assert any("Illegal y_pred=y_true substitution detected" in err for err in res.errors)


def test_verify_sitescore_prediction_source_fails_on_fixed_multiplier_horizon():
    records = _generate_valid_prediction_records(20, include_outcomes=True)
    for r in records:
        r["realized_m6_net_revenue"] = r["realized_90d_net_revenue"] * 2.0
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    res = verify_sitescore_prediction_source(records, prediction_receipt=receipt)
    assert res.is_valid is False
    assert res.reason_code == "SYNTHETIC_SUBSTITUTION_REJECTED"
    assert any("Illegal fixed multiplier horizon metric detected" in err for err in res.errors)


def test_verify_sitescore_prediction_source_fails_on_store_age_alias():
    records = _generate_valid_prediction_records(20, include_outcomes=False)
    for r in records:
        r["store_age_days"] = 250
        r["m6_covered"] = True
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    res = verify_sitescore_prediction_source(records, prediction_receipt=receipt)
    assert res.is_valid is False
    assert res.reason_code == "SYNTHETIC_SUBSTITUTION_REJECTED"
    assert any("Illegal store age substitution detected" in err for err in res.errors)


def test_verify_sitescore_prediction_source_fails_on_missing_or_mismatched_model_version():
    records = _generate_valid_prediction_records(10, model_version="wrong-model-v99")
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version="wrong-model-v99",
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    res = verify_sitescore_prediction_source(records, prediction_receipt=receipt, expected_model_version=CANONICAL_MODEL_VERSION)

    assert res.is_valid is False
    assert res.reason_code == "MISSING_GOVERNED_LINEAGE"
    assert any("Model version mismatch" in err or "model_version" in err for err in res.errors)


def test_verify_sitescore_prediction_source_fails_on_missing_or_mismatched_snapshot_id():
    records = _generate_valid_prediction_records(10, dataset_snapshot_id="wrong-snapshot-hash")
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="wrong-snapshot-hash",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    res = verify_sitescore_prediction_source(records, prediction_receipt=receipt, expected_snapshot_id="expected-snapshot-hash")

    assert res.is_valid is False
    assert res.reason_code == "MISSING_GOVERNED_LINEAGE"
    assert any("Dataset snapshot ID mismatch" in err or "dataset_snapshot_id" in err for err in res.errors)


def test_prediction_source_receipt_integrity():
    records = _generate_valid_prediction_records(10)
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="snap-test-01",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="lin-test-01",
    )

    assert receipt["kind"] == PREDICTION_SOURCE_RECEIPT_KIND
    assert receipt["model_name"] == CANONICAL_PREDICTION_MODEL_NAME
    assert receipt["service"] == CANONICAL_PREDICTION_SERVICE
    assert receipt["record_count"] == 10
    assert "integrity" in receipt
    assert "content_sha256" in receipt["integrity"]

    expected_sha = compute_prediction_source_receipt_sha256(receipt)
    assert receipt["integrity"]["content_sha256"] == expected_sha


def test_sitescore_prediction_source_rejects_disallowed_self_attested_and_stale_aliases():
    records = _generate_valid_prediction_records(
        10,
        dataset_snapshot_id="caller-self-attested",
        model_version="stale-alias-v0",
        artifact_lineage_id="not-a-hash",
    )
    res = verify_sitescore_prediction_source(records)

    assert res.is_valid is False
    assert res.reason_code == "MISSING_GOVERNED_LINEAGE"
    assert any("disallowed" in err.lower() or "unapproved" in err.lower() for err in res.errors)


def test_sitescore_prediction_source_rejects_future_opened_on_dates():
    records = _generate_valid_prediction_records(10)
    for r in records:
        r["opened_on"] = "2099-01-01"
        r["prediction_as_of"] = "2099-01-01"
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    res = verify_sitescore_prediction_source(records, prediction_receipt=receipt)

    assert res.is_valid is False
    assert res.reason_code == "MISSING_GOVERNED_LINEAGE"
    assert any("Future or invalid" in err for err in res.errors)


def test_sitescore_prediction_source_rejects_constant_y_pred_equals_y_true_substitution():
    records = _generate_valid_prediction_records(20, include_outcomes=True)
    for r in records:
        r["predicted_revenue"] = r["realized_90d_net_revenue"]
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    res = verify_sitescore_prediction_source(records, prediction_receipt=receipt)

    assert res.is_valid is False
    assert res.reason_code == "SYNTHETIC_SUBSTITUTION_REJECTED"
    assert any("y_pred=y_true substitution detected" in err for err in res.errors)


def test_sitescore_opening_outcome_220_record_bypass_attempt_fails_closed():
    # Mutation test reproducing reviewer rejection bypass attempt:
    # 220-row mutation with caller values snapshot=fresh-caller-snapshot-42, version=forged-approved-v42, lineage=opaque-caller-lineage-42
    # WITHOUT a verified prediction receipt or model registry readback MUST fail closed as GOVERNED_DISABLED.
    records = _generate_valid_prediction_records(
        220,
        include_outcomes=True,
        dataset_snapshot_id="fresh-caller-snapshot-42",
        model_version="forged-approved-v42",
        artifact_lineage_id="opaque-caller-lineage-42",
    )
    result = evaluate_sitescore_opening_outcome_benchmark(
        records,
        provenance="authenticated_governed_records",
    )

    assert result.is_gate2_passed is False
    assert result.prediction_source_verified is False
    assert result.is_lineage_governed is False
    assert result.status == "GOVERNED_DISABLED"
    assert result.reason_code == "UNAUTHENTICATED_PREDICTION_PROVENANCE" or result.reason_code == "MISSING_GOVERNED_LINEAGE"


def test_sitescore_gate2_receipt_tampered_prediction_receipt_hash_rejected():
    records = _generate_valid_prediction_records(220, include_outcomes=True)
    pred_receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    result = evaluate_sitescore_opening_outcome_benchmark(
        records,
        prediction_receipt=pred_receipt,
        provenance="authenticated_governed_records",
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    model_card = build_sitescore_opening_outcome_model_card(result, version=CANONICAL_MODEL_VERSION)
    receipt = build_sitescore_gate2_receipt(result, inventory_version=CANONICAL_MODEL_VERSION, model_card=model_card)

    receipt["artifact_hashes"]["prediction_receipt_hash"] = "a" * 64
    verif_res = verify_sitescore_gate2_receipt(receipt, model_card_artifact=model_card, dataset_manifest=records)

    assert verif_res.is_valid is False
    assert any("prediction_receipt_hash" in err for err in verif_res.errors)
