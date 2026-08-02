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
    gate2_receipt = build_sitescore_gate2_receipt(result, inventory_version=CANONICAL_MODEL_VERSION, model_card=model_card, prediction_receipt=receipt)

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
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="caller-self-attested",
        model_version="stale-alias-v0",
        artifact_lineage_id="not-a-hash",
    )
    res = verify_sitescore_prediction_source(records, prediction_receipt=receipt)

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


def test_sitescore_prediction_source_general_no_receipt_plausible_hex_bypass_fails_closed():
    # Mutation test: caller passes plausible 64-hex IDs in records, but NO receipt and NO registry evidence.
    # Must fail closed as MISSING_GOVERNED_LINEAGE and GOVERNED_DISABLED.
    records = _generate_valid_prediction_records(
        220,
        include_outcomes=True,
        dataset_snapshot_id="a1b2c3d4" * 8,
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="e5f67890" * 8,
    )
    verif_res = verify_sitescore_prediction_source(records, prediction_receipt=None, model_registry_evidence=None)
    assert verif_res.is_valid is False
    assert verif_res.reason_code == "MISSING_GOVERNED_LINEAGE"
    assert verif_res.prediction_receipt_hash is None
    assert any("Missing authoritative prediction receipt" in err for err in verif_res.errors)

    result = evaluate_sitescore_opening_outcome_benchmark(
        records,
        prediction_receipt=None,
        model_registry_evidence=None,
        provenance="authenticated_governed_records",
    )
    assert result.is_gate2_passed is False
    assert result.prediction_source_verified is False
    assert result.is_lineage_governed is False
    assert result.status == "GOVERNED_DISABLED"
    assert result.reason_code == "MISSING_GOVERNED_LINEAGE"


def test_sitescore_outcome_benchmark_pg16_query_path_with_receipt_active(monkeypatch):
    # PG-path mutation test: run_benchmark_from_inventory under pg16_query with a valid receipt achieves ACTIVE
    from scripts.models.sitescore_outcome_benchmark import run_benchmark_from_inventory

    records = _generate_valid_prediction_records(220, include_outcomes=True)
    for r in records:
        r["opened_on"] = "2024-01-01"
        r["prediction_as_of"] = "2024-01-01"
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )

    db_rows = []
    for r in records:
        db_rows.append((
            r["entity_id"],
            r["store_id"],
            r["target_format_code"],
            r["opened_on"],
            r["is_training_eligible"],
            r["realized_90d_net_revenue"],
            r["realized_m6_net_revenue"],
            r["realized_m12_net_revenue"],
            380,
            r["prediction_as_of"],
            r["model_version"],
            r.get("horizon_code", "90d"),
            r["predicted_revenue"],
            r["p10"],
            r["p90"],
            r["p50"],
            r["dataset_snapshot_id"],
            r["artifact_lineage_id"],
        ))

    class MockCursor:
        def execute(self, query):
            pass
        def fetchall(self):
            return db_rows
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    class MockConn:
        def cursor(self):
            return MockCursor()

    import psycopg
    monkeypatch.setattr(psycopg, "connect", lambda url: MockConn())

    res = run_benchmark_from_inventory(db_url="postgresql://localhost:5432/test_db", prediction_receipt=receipt)
    assert res.provenance == "pg16_query"
    assert res.prediction_source_verified is True
    assert res.is_lineage_governed is True
    assert res.is_gate2_passed is True
    assert res.status == "ACTIVE"
    assert res.reason_code == "GATE2_CRITERIA_MET"


def test_sitescore_outcome_benchmark_pg16_query_path_without_receipt_governed_disabled(monkeypatch):
    # PG-path mutation test: run_benchmark_from_inventory under pg16_query WITHOUT a receipt stays GOVERNED_DISABLED
    from scripts.models.sitescore_outcome_benchmark import run_benchmark_from_inventory

    records = _generate_valid_prediction_records(220, include_outcomes=True)
    for r in records:
        r["opened_on"] = "2024-01-01"
        r["prediction_as_of"] = "2024-01-01"
    db_rows = []
    for r in records:
        db_rows.append((
            r["entity_id"],
            r["store_id"],
            r["target_format_code"],
            r["opened_on"],
            r["is_training_eligible"],
            r["realized_90d_net_revenue"],
            r["realized_m6_net_revenue"],
            r["realized_m12_net_revenue"],
            380,
            r["prediction_as_of"],
            r["model_version"],
            r.get("horizon_code", "90d"),
            r["predicted_revenue"],
            r["p10"],
            r["p90"],
            r["p50"],
            r["dataset_snapshot_id"],
            r["artifact_lineage_id"],
        ))

    class MockCursor:
        def execute(self, query):
            pass
        def fetchall(self):
            return db_rows
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    class MockConn:
        def cursor(self):
            return MockCursor()

    import psycopg
    monkeypatch.setattr(psycopg, "connect", lambda url: MockConn())

    res = run_benchmark_from_inventory(db_url="postgresql://localhost:5432/test_db", prediction_receipt=None)
    assert res.provenance == "pg16_query"
    assert res.prediction_source_verified is False
    assert res.is_lineage_governed is False
    assert res.is_gate2_passed is False
    assert res.status == "GOVERNED_DISABLED"
    assert res.reason_code == "MISSING_GOVERNED_LINEAGE"


def test_sitescore_prediction_source_b1_arbitrary_caller_receipt_lacking_authority_attestation_rejected():
    # B1 Re-audit mutation test: Receipt with disallowed or self-attested authority fails closed as UNAUTHENTICATED_PREDICTION_PROVENANCE
    records = _generate_valid_prediction_records(220, include_outcomes=True)
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    receipt["authority_attestation"] = "caller-self-attested"
    receipt["integrity"]["content_sha256"] = compute_prediction_source_receipt_sha256(receipt)
    res = verify_sitescore_prediction_source(records, prediction_receipt=receipt)
    assert res.is_valid is False
    assert res.reason_code == "UNAUTHENTICATED_PREDICTION_PROVENANCE"
    assert any("authority attestation" in err for err in res.errors)

    result = evaluate_sitescore_opening_outcome_benchmark(
        records,
        prediction_receipt=receipt,
        provenance="authenticated_governed_records",
    )
    assert result.is_gate2_passed is False
    assert result.prediction_source_verified is False
    assert result.status == "GOVERNED_DISABLED"


def test_sitescore_prediction_source_b1_registry_evidence_without_explicit_receipt_hash_fails_closed():
    # B1 Re-audit: Registry evidence with approved strings BUT no explicit 64-hex prediction_receipt_hash fails closed
    records = _generate_valid_prediction_records(220, include_outcomes=True)
    reg_evidence = {
        "model_name": CANONICAL_PREDICTION_MODEL_NAME,
        "authority_attestation": "authenticated_prediction_registry",
        "provider_identity": "model_ready.sitescore_predictions",
        "versions": [
            {
                "version": CANONICAL_MODEL_VERSION,
                "dataset_snapshot_id": "sitescore-snapshot-2026-07-31-v1",
                "git_sha": "sitescore-artifact-sha256-v1",
            }
        ],
    }
    res = verify_sitescore_prediction_source(records, model_registry_evidence=reg_evidence)
    assert res.is_valid is False
    assert res.prediction_receipt_hash is None
    assert any("prediction_receipt_hash" in err for err in res.errors)

    result = evaluate_sitescore_opening_outcome_benchmark(
        records,
        model_registry_evidence=reg_evidence,
        provenance="authenticated_prediction_registry",
    )
    assert result.is_gate2_passed is False
    assert result.status == "GOVERNED_DISABLED"


def test_sitescore_prediction_source_b2_unapproved_provider_identity_rejected():
    # B2 Re-audit: Unapproved provider identity like evil.attacker fails closed
    records = _generate_valid_prediction_records(220, include_outcomes=True)
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    receipt["provider_identity"] = "evil.attacker"
    receipt["integrity"]["content_sha256"] = compute_prediction_source_receipt_sha256(receipt)
    res = verify_sitescore_prediction_source(records, prediction_receipt=receipt)
    assert res.is_valid is False
    assert res.reason_code == "UNAUTHENTICATED_PREDICTION_PROVENANCE"
    assert any("provider identity" in err for err in res.errors)


def test_sitescore_prediction_source_b3_registry_wrong_version_fallback_rejected():
    # B3 Re-audit: Registry evidence containing candidate-site-view-v1 fails closed when v2 requested
    records = _generate_valid_prediction_records(220, include_outcomes=True)
    reg_evidence = {
        "model_name": CANONICAL_PREDICTION_MODEL_NAME,
        "authority_attestation": "authenticated_prediction_registry",
        "provider_identity": "model_ready.sitescore_predictions",
        "prediction_receipt_hash": "a" * 64,
        "versions": [
            {
                "version": "candidate-site-view-v1",
                "dataset_snapshot_id": "sitescore-snapshot-2026-07-31-v1",
                "git_sha": "sitescore-artifact-sha256-v1",
            }
        ],
    }
    res = verify_sitescore_prediction_source(
        records,
        model_registry_evidence=reg_evidence,
        expected_model_version=CANONICAL_MODEL_VERSION,
    )
    assert res.is_valid is False
    assert any("exact requested model version" in err for err in res.errors)


def test_sitescore_prediction_source_b4_prediction_as_of_mismatch_opened_on_rejected():
    # B4 Re-audit: prediction_as_of != opened_on fails closed
    records = _generate_valid_prediction_records(220, include_outcomes=True)
    records[0]["prediction_as_of"] = "2025-06-01"  # opened_on is 2025-01-01
    receipt = build_sitescore_prediction_source_receipt(
        _generate_valid_prediction_records(220, include_outcomes=True),
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    res = verify_sitescore_prediction_source(records, prediction_receipt=receipt)
    assert res.is_valid is False
    assert any("prediction_as_of" in err for err in res.errors)


def test_sitescore_prediction_source_b5_disallowed_horizon_code_rejected():
    # B5 Re-audit: arbitrary horizon_code legacy-unknown fails closed
    records = _generate_valid_prediction_records(220, include_outcomes=True)
    records[0]["horizon_code"] = "legacy-unknown"
    receipt = build_sitescore_prediction_source_receipt(
        _generate_valid_prediction_records(220, include_outcomes=True),
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    res = verify_sitescore_prediction_source(records, prediction_receipt=receipt)
    assert res.is_valid is False
    assert any("horizon_code" in err for err in res.errors)


def test_sitescore_prediction_source_b6_receipt_record_count_mismatch_rejected():
    # B6 Re-audit: record_count=999 with 220 records fails closed
    records = _generate_valid_prediction_records(220, include_outcomes=True)
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    receipt["record_count"] = 999
    receipt["integrity"]["content_sha256"] = compute_prediction_source_receipt_sha256(receipt)
    res = verify_sitescore_prediction_source(records, prediction_receipt=receipt)
    assert res.is_valid is False
    assert any("record_count mismatch" in err for err in res.errors)


def test_sitescore_opening_outcome_b2_caller_record_prediction_drift_from_verified_receipt_fails_closed():
    # B2 Re-audit mutation test: Receipt has 2.0x predictions (1,000,000), caller passes 0.95x predictions (475,000).
    # Benchmark binds verified receipt values (1,000,000), causing MAE/P80 to evaluate on verified receipt predictions.
    receipt_records = _generate_valid_prediction_records(
        220,
        base_revenue=500_000.0,
        include_outcomes=True,
    )
    # Mutate receipt_records to have 2.0x predictions and non-covering intervals
    for r in receipt_records:
        r["predicted_revenue"] = 1_000_000.0
        r["p10"] = 990_000.0
        r["p90"] = 1_010_000.0

    receipt = build_sitescore_prediction_source_receipt(
        receipt_records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )

    # Caller records have 0.95x predictions and wide intervals
    caller_records = _generate_valid_prediction_records(
        220,
        base_revenue=500_000.0,
        include_outcomes=True,
    )

    result = evaluate_sitescore_opening_outcome_benchmark(
        caller_records,
        prediction_receipt=receipt,
        provenance="authenticated_governed_records",
    )

    # Because receipt has 2.0x predictions (1.0M) while realized revenue is ~500k,
    # the bound receipt predictions produce normalized MAE ~1.0 > 0.25 threshold, so Gate 2 FAILS!
    assert result.is_gate2_passed is False
    assert result.status == "GOVERNED_DISABLED"
    assert result.reason_code in ("UNMATCHED_PREDICTION_SOURCE", "MISSING_GOVERNED_LINEAGE", "NORMALIZED_MAE_EXCEEDED", "INTERVAL_BOUNDS_MISSING")


def test_sitescore_prediction_source_b3_unauthenticated_dict_model_registry_evidence_rejected():
    # B3 Re-audit mutation test: Caller dict passed as model_registry_evidence without authority attestation
    # fails closed and does NOT self-synthesize a verified_receipt_hash.
    records = _generate_valid_prediction_records(220, include_outcomes=True)
    fake_registry_dict = {
        "model_name": CANONICAL_PREDICTION_MODEL_NAME,
        "authority_attestation": "caller-self-attested",
        "versions": [
            {
                "version": CANONICAL_MODEL_VERSION,
                "dataset_snapshot_id": "sitescore-snapshot-2026-07-31-v1",
                "git_sha": "sitescore-artifact-sha256-v1",
            }
        ],
    }
    res = verify_sitescore_prediction_source(records, model_registry_evidence=fake_registry_dict)
    assert res.is_valid is False
    assert res.reason_code == "UNAUTHENTICATED_PREDICTION_PROVENANCE"
    assert res.prediction_receipt_hash is None


def test_b1_re_audit_model_registry_evidence_duplicate_keys_and_cardinality():
    records = _generate_valid_prediction_records(10)
    reg_records = _generate_valid_prediction_records(10)
    reg_records.append(dict(reg_records[0]))
    reg_evidence = {
        "model_name": CANONICAL_PREDICTION_MODEL_NAME,
        "authority_attestation": "authenticated_prediction_registry",
        "provider_identity": "model_ready.sitescore_predictions",
        "prediction_receipt_hash": "a" * 64,
        "versions": [
            {
                "version": CANONICAL_MODEL_VERSION,
                "dataset_snapshot_id": "sitescore-snapshot-2026-07-31-v1",
                "git_sha": "sitescore-artifact-sha256-v1",
            }
        ],
        "prediction_records": reg_records,
    }
    res = verify_sitescore_prediction_source(records, model_registry_evidence=reg_evidence)
    assert res.is_valid is False
    assert res.duplicate_count >= 1 or any("Duplicate" in e or "cardinality" in e or "count" in e for e in res.errors)


def test_b2_re_audit_model_registry_evidence_predictions_bind_to_benchmark():
    records = _generate_valid_prediction_records(220, include_outcomes=True)
    reg_records = _generate_valid_prediction_records(220, include_outcomes=True)
    for r in reg_records:
        r["predicted_revenue"] = 10_000_000.0

    reg_evidence = {
        "model_name": CANONICAL_PREDICTION_MODEL_NAME,
        "authority_attestation": "authenticated_prediction_registry",
        "provider_identity": "model_ready.sitescore_predictions",
        "prediction_receipt_hash": "a" * 64,
        "versions": [
            {
                "version": CANONICAL_MODEL_VERSION,
                "dataset_snapshot_id": "sitescore-snapshot-2026-07-31-v1",
                "git_sha": "sitescore-artifact-sha256-v1",
            }
        ],
        "prediction_records": reg_records,
    }

    result = evaluate_sitescore_opening_outcome_benchmark(
        records,
        model_registry_evidence=reg_evidence,
        provenance="authenticated_prediction_registry",
    )
    assert result.is_gate2_passed is False
    assert result.status == "GOVERNED_DISABLED"
    assert result.reason_code == "NORMALIZED_MAE_EXCEEDED"


def test_b3_re_audit_exact_horizon_join_no_fallback_to_90d():
    records = _generate_valid_prediction_records(10)
    for r in records:
        r["horizon_code"] = "M6"
    receipt_records = _generate_valid_prediction_records(10)
    for r in receipt_records:
        r["horizon_code"] = "90d"

    receipt = build_sitescore_prediction_source_receipt(
        receipt_records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version=CANONICAL_MODEL_VERSION,
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    res = verify_sitescore_prediction_source(records, prediction_receipt=receipt)
    assert res.is_valid is False
    assert res.unmatched_count == 10
    assert res.reason_code == "UNMATCHED_PREDICTION_SOURCE"


def test_b4_re_audit_non_hex_receipt_hash_rejected_by_is_lineage_governed():
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
    )
    from models.sitescore.opening_outcome import SiteScoreOpeningOutcomeBenchmarkResult
    mutated_res = SiteScoreOpeningOutcomeBenchmarkResult(
        observed_count=result.observed_count,
        eligible_count=result.eligible_count,
        mature_label_count=result.mature_label_count,
        m6_coverage_ratio=result.m6_coverage_ratio,
        m12_coverage_ratio=result.m12_coverage_ratio,
        normalized_mae=result.normalized_mae,
        p80_coverage=result.p80_coverage,
        prediction_coverage_ratio=result.prediction_coverage_ratio,
        interval_bounds_coverage_ratio=result.interval_bounds_coverage_ratio,
        matched_prediction_count=result.matched_prediction_count,
        m6_mature_count=result.m6_mature_count,
        m12_mature_count=result.m12_mature_count,
        interval_bounds_count=result.interval_bounds_count,
        in_p80_count=result.in_p80_count,
        dataset_snapshot_id=result.dataset_snapshot_id,
        model_version=result.model_version,
        artifact_lineage_id=result.artifact_lineage_id,
        provenance=result.provenance,
        prediction_source_verified=True,
        prediction_receipt_hash="z" * 64,
    )
    assert mutated_res.is_lineage_governed is False
    assert mutated_res.status == "GOVERNED_DISABLED"


def test_b5_re_audit_receipt_v1_fails_without_expected_version():
    records = _generate_valid_prediction_records(220, include_outcomes=True, model_version="candidate-site-view-v1")
    receipt = build_sitescore_prediction_source_receipt(
        records,
        dataset_snapshot_id="sitescore-snapshot-2026-07-31-v1",
        model_version="candidate-site-view-v1",
        artifact_lineage_id="sitescore-artifact-sha256-v1",
    )
    res = verify_sitescore_prediction_source(records, prediction_receipt=receipt)
    assert res.is_valid is False
    assert any("candidate-site-view-v2" in e for e in res.errors)
