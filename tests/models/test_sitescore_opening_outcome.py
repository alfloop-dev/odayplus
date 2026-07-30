"""Unit & integration tests for SiteScore opening outcome M6/M12 coverage calibration benchmark & Gate 2 receipt."""

from __future__ import annotations

from models.shared_ml.model_card import ModelCard
from models.sitescore.opening_outcome import (
    GATE2_RECEIPT_KIND,
    GATE2_RECEIPT_SCHEMA_VERSION,
    build_sitescore_gate2_receipt,
    build_sitescore_opening_outcome_model_card,
    compute_gate2_receipt_sha256,
    evaluate_sitescore_opening_outcome_benchmark,
)
from scripts.models.sitescore_outcome_benchmark import (
    run_benchmark_from_inventory,
    write_evidence_markdown,
)


def _generate_candidate_records(
    count: int,
    *,
    eligible: bool = True,
    m6_days: int = 180,
    m12_days: int = 365,
    revenue: float = 500_000.0,
    pred_revenue: float = 500_000.0,
    target_format: str = "CONVENIENCE_STANDARD",
    include_m6_m12_realized: bool = False,
    include_bounds: bool = True,
    dataset_snapshot_id: str | None = None,
    model_version: str | None = None,
    artifact_lineage_id: str | None = None,
) -> list[dict]:
    records = []
    for i in range(count):
        r = {
            "entity_id": f"tenant-001:store-{i:04d}",
            "store_id": f"store-{i:04d}",
            "target_format_code": target_format,
            "opened_on": "2025-01-01",
            "is_training_eligible": eligible,
            "realized_90d_net_revenue": revenue,
            "predicted_revenue": pred_revenue,
            "m6_days": m6_days,
            "m12_days": m12_days,
        }
        if include_m6_m12_realized:
            r["realized_m6_net_revenue"] = revenue * 2.0
            r["realized_m12_net_revenue"] = revenue * 4.0
        if include_bounds:
            r["p10"] = pred_revenue * 0.85
            r["p90"] = pred_revenue * 1.15
        if dataset_snapshot_id:
            r["dataset_snapshot_id"] = dataset_snapshot_id
        if model_version:
            r["model_version"] = model_version
        if artifact_lineage_id:
            r["artifact_lineage_id"] = artifact_lineage_id
        records.append(r)
    return records


def test_sitescore_opening_outcome_unauthenticated_provided_records_fails_closed():
    # Negative regression 1: Arbitrary provided_records fail closed as UNAUTHENTICATED_PROVENANCE
    records = _generate_candidate_records(
        220,
        include_m6_m12_realized=True,
        include_bounds=True,
        dataset_snapshot_id="snapshot_sitescore_v2",
        model_version="candidate-site-view-v2",
        artifact_lineage_id="art_sitescore_sha256",
    )
    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="provided_records")

    assert result.observed_count == 220
    assert result.mature_label_count == 220
    assert not result.is_lineage_governed
    assert not result.is_gate2_passed
    assert result.status == "GOVERNED_DISABLED"
    assert result.reason_code == "UNAUTHENTICATED_PROVENANCE"

    handback = result.handback_payload
    assert handback["handback_required"] is True
    assert handback["reason_code"] == "UNAUTHENTICATED_PROVENANCE"
    assert "Provided records are unauthenticated" in handback["reasons"][0]


def test_sitescore_opening_outcome_90d_only_old_stores_fails_coverage():
    # Negative regression 2: Old stores (opened >365d ago) containing ONLY realized_90d_net_revenue but no M6/M12 outcomes fail coverage
    records = _generate_candidate_records(
        220,
        m6_days=500,
        m12_days=500,
        include_m6_m12_realized=False,  # No explicit realized M6/M12 outcomes
        include_bounds=True,
        dataset_snapshot_id="snapshot_sitescore_v2",
        model_version="candidate-site-view-v2",
        artifact_lineage_id="art_sitescore_sha256",
    )
    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="authenticated_governed_records")

    assert result.mature_label_count == 220
    assert result.m6_coverage_ratio == 0.0
    assert result.m12_coverage_ratio == 0.0
    assert not result.is_coverage_passed
    assert not result.is_gate2_passed
    assert result.status == "GOVERNED_DISABLED"
    assert result.reason_code == "M6_M12_COVERAGE_INSUFFICIENT"


def test_sitescore_opening_outcome_missing_interval_bounds_fails_closed():
    # Negative regression 3: 220 mature records with M6/M12 outcomes & predictions but NO p10/p90 fail closed
    records = _generate_candidate_records(
        220,
        include_m6_m12_realized=True,
        include_bounds=False,  # Missing p10/p90
        dataset_snapshot_id="snapshot_sitescore_v2",
        model_version="candidate-site-view-v2",
        artifact_lineage_id="art_sitescore_sha256",
    )
    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="authenticated_governed_records")

    assert result.mature_label_count == 220
    assert result.interval_bounds_coverage_ratio == 0.0
    assert not result.is_interval_bounds_passed
    assert not result.is_gate2_passed
    assert result.status == "GOVERNED_DISABLED"
    assert result.reason_code == "INTERVAL_BOUNDS_MISSING"


def test_sitescore_opening_outcome_missing_governed_lineage_fails_closed():
    # Negative regression 4: Records missing dataset snapshot or model/artifact lineage fail closed as MISSING_GOVERNED_LINEAGE
    records = _generate_candidate_records(
        220,
        include_m6_m12_realized=True,
        include_bounds=True,
        dataset_snapshot_id=None,  # Missing snapshot
        model_version="candidate-site-view-v2",
        artifact_lineage_id="art_sitescore_sha256",
    )
    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="authenticated_governed_records")

    assert not result.is_lineage_governed
    assert not result.is_gate2_passed
    assert result.status == "GOVERNED_DISABLED"
    assert result.reason_code == "MISSING_GOVERNED_LINEAGE"


def test_sitescore_opening_outcome_authentic_governed_records_passes_gate2():
    # Positive case: Authentic governed records with complete lineage, M6/M12 outcomes, predictions, and interval bounds pass Gate 2
    records = _generate_candidate_records(
        220,
        include_m6_m12_realized=True,
        include_bounds=True,
        dataset_snapshot_id="snapshot_sitescore_opening_outcome_v2",
        model_version="candidate-site-view-v2",
        artifact_lineage_id="art_sitescore_sha256",
    )
    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="authenticated_governed_records")

    assert result.mature_label_count == 220
    assert result.is_lineage_governed
    assert result.is_labels_sufficient
    assert result.is_coverage_passed
    assert result.is_interval_bounds_passed
    assert result.is_mae_passed
    assert result.is_gate2_passed
    assert result.status == "ACTIVE"
    assert result.reason_code == "GATE2_CRITERIA_MET"
    assert result.handback_payload["handback_required"] is False


def test_sitescore_opening_outcome_insufficient_labels_fails_closed():
    records = _generate_candidate_records(
        50,
        include_m6_m12_realized=True,
        include_bounds=True,
        dataset_snapshot_id="snapshot_sitescore_v2",
        model_version="candidate-site-view-v2",
        artifact_lineage_id="art_sitescore_sha256",
    )
    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="authenticated_governed_records")

    assert result.observed_count == 50
    assert result.mature_label_count == 50
    assert not result.is_labels_sufficient
    assert not result.is_gate2_passed
    assert result.status == "GOVERNED_DISABLED"
    assert result.reason_code == "MATURE_LABELS_BELOW_THRESHOLD"

    handback = result.handback_payload
    assert handback["handback_required"] is True
    assert handback["governed_disabled"] is True
    assert handback["missing_labels_delta"] == 150
    assert handback["activation_threshold"] == 200
    assert "Mature label count (50) is below threshold (200)" in handback["reasons"][0]


def test_sitescore_opening_outcome_no_predictions_fails_closed():
    records = _generate_candidate_records(
        220,
        pred_revenue=None,
        include_m6_m12_realized=True,
        include_bounds=False,
        dataset_snapshot_id="snapshot_sitescore_v2",
        model_version="candidate-site-view-v2",
        artifact_lineage_id="art_sitescore_sha256",
    )
    # Remove predicted_revenue explicitly
    for r in records:
        r["predicted_revenue"] = None

    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="authenticated_governed_records")
    assert result.mature_label_count == 220
    assert result.prediction_coverage_ratio == 0.0
    assert not result.is_prediction_coverage_passed
    assert not result.is_gate2_passed
    assert result.status == "GOVERNED_DISABLED"
    assert result.reason_code == "PREDICTION_EVIDENCE_MISSING"


def test_sitescore_gate2_receipt_structure_and_integrity():
    records = _generate_candidate_records(100)
    result = evaluate_sitescore_opening_outcome_benchmark(records)
    receipt = build_sitescore_gate2_receipt(result, inventory_version="candidate-site-view-v2")

    assert receipt["schema_version"] == GATE2_RECEIPT_SCHEMA_VERSION
    assert receipt["kind"] == GATE2_RECEIPT_KIND
    assert receipt["gate"] == "GATE_2"
    assert receipt["gate_status"] == "REJECTED_GOVERNED_DISABLED"
    assert receipt["is_governed_disabled"] is True
    assert receipt["source_contract"] == "model_ready.candidate_site_view@candidate-site-view-v2"

    declared_hash = receipt["integrity"]["content_sha256"]
    calculated_hash = compute_gate2_receipt_sha256(receipt)
    assert declared_hash == calculated_hash


def test_sitescore_model_card_generation():
    records = _generate_candidate_records(
        220,
        include_m6_m12_realized=True,
        include_bounds=True,
        dataset_snapshot_id="snapshot_sitescore_opening_outcome_v2",
        model_version="candidate-site-view-v2",
        artifact_lineage_id="art_sitescore_sha256",
    )
    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="authenticated_governed_records")
    model_card = build_sitescore_opening_outcome_model_card(result)

    assert isinstance(model_card, ModelCard)
    assert model_card.model_name == "sitescore_propensity"
    assert model_card.release_status == "DEV"
    assert model_card.is_complete
    assert model_card.is_approved

    dict_card = model_card.to_dict()
    assert dict_card["metrics_summary"]["mature_label_count"] == 220.0
    assert dict_card["metrics_summary"]["m6_coverage_ratio"] == 1.0


def test_cli_inventory_runner_and_evidence_doc(tmp_path):
    output_evidence = tmp_path / "evidence.md"

    result = run_benchmark_from_inventory(records=[])
    assert result.mature_label_count == 0
    assert not result.is_gate2_passed
    assert result.reason_code == "NO_SOURCE_INVENTORY"

    receipt = build_sitescore_gate2_receipt(result)
    write_evidence_markdown(receipt, output_evidence)

    assert output_evidence.exists()
    content = output_evidence.read_text(encoding="utf-8")
    assert "Gate 2 Receipt: SiteScore Opening Outcome Calibration Benchmark" in content
    assert "REJECTED_GOVERNED_DISABLED" in content
    assert "NO_SOURCE_INVENTORY" in content


def test_sitescore_opening_outcome_db_unreachable_fails_closed():
    result = run_benchmark_from_inventory(db_url="postgresql://nouser:nopass@127.0.0.1:1/nodb")
    assert result.provenance == "unreachable_db"
    assert result.reason_code == "DB_INVENTORY_UNREACHABLE"
    assert result.status == "GOVERNED_DISABLED"
    assert not result.is_gate2_passed
    assert result.db_error is not None

    receipt = build_sitescore_gate2_receipt(result)
    assert receipt["provenance"] == "unreachable_db"
    assert receipt["gate_status"] == "REJECTED_GOVERNED_DISABLED"
    assert receipt["handback"]["reason_code"] == "DB_INVENTORY_UNREACHABLE"
    assert "PostgreSQL model-ready inventory database query failed" in receipt["handback"]["reasons"][0]


def test_sitescore_opening_outcome_no_source_fails_closed():
    result = run_benchmark_from_inventory(db_url=None, records=None)
    assert result.provenance == "no_source"
    assert result.reason_code == "NO_SOURCE_INVENTORY"
    assert result.status == "GOVERNED_DISABLED"
    assert not result.is_gate2_passed
