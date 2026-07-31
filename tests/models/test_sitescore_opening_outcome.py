"""Unit & integration tests for SiteScore opening outcome M6/M12 coverage calibration benchmark & Gate 2 receipt."""

from __future__ import annotations

import json
import math

from models.shared_ml.model_card import ModelCard
from models.sitescore.opening_outcome import (
    GATE2_RECEIPT_KIND,
    GATE2_RECEIPT_SCHEMA_VERSION,
    build_sitescore_gate2_receipt,
    build_sitescore_opening_outcome_model_card,
    compute_gate2_receipt_sha256,
    evaluate_sitescore_opening_outcome_benchmark,
    verify_sitescore_gate2_receipt,
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
    pred_revenue: float | None = 500_000.0,
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
        if include_bounds and pred_revenue is not None:
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
    assert result.reason_code == "MISSING_GOVERNED_LINEAGE"


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
    assert result.reason_code == "MISSING_GOVERNED_LINEAGE"


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


def test_sitescore_opening_outcome_arbitrary_authenticated_governed_records_fails_closed_without_authoritative_resolver():
    # Regression: Arbitrary caller-created records with authenticated_governed_records provenance and caller strings fail closed as GOVERNED_DISABLED
    # until ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001 provides an authoritative prediction-source resolver.
    records = _generate_candidate_records(
        220,
        include_m6_m12_realized=True,
        include_bounds=True,
        dataset_snapshot_id="arbitrary-caller-snapshot",
        model_version="arbitrary-caller-model",
        artifact_lineage_id="arbitrary-caller-artifact",
    )
    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="authenticated_governed_records")

    assert result.mature_label_count == 220
    assert not result.is_lineage_governed
    assert not result.is_gate2_passed
    assert result.status == "GOVERNED_DISABLED"
    assert result.reason_code == "MISSING_GOVERNED_LINEAGE"
    assert result.handback_payload["handback_required"] is True
    assert result.handback_payload["governed_disabled"] is True
    assert "ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001" in result.handback_payload["reasons"][0]


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
    assert result.reason_code == "MISSING_GOVERNED_LINEAGE"

    handback = result.handback_payload
    assert handback["handback_required"] is True
    assert handback["governed_disabled"] is True
    assert handback["missing_labels_delta"] == 150
    assert handback["activation_threshold"] == 200


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
    assert result.reason_code == "MISSING_GOVERNED_LINEAGE"


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
    assert model_card.release_status == "GOVERNED_DISABLED"
    assert not model_card.is_complete
    assert not model_card.is_approved
    assert model_card.privacy_review == "UNVERIFIED"
    assert model_card.security_review == "UNVERIFIED"
    assert len(model_card.approvals) == 0

    dict_card = model_card.to_dict()
    assert dict_card["metrics_summary"]["mature_label_count"] == 220.0
    assert dict_card["metrics_summary"]["m6_coverage_ratio"] == 1.0


def test_sitescore_opening_outcome_null_vs_zero_outcomes():
    # B1 regression test: PostgreSQL NULL realized_90d_net_revenue remains None and is excluded, while numeric 0.0 remains a mature label
    null_record = {
        "entity_id": "tenant-001:store-null",
        "store_id": "store-null",
        "target_format_code": "CONVENIENCE_STANDARD",
        "opened_on": "2025-01-01",
        "is_training_eligible": True,
        "realized_90d_net_revenue": None,
    }
    zero_record = {
        "entity_id": "tenant-001:store-zero",
        "store_id": "store-zero",
        "target_format_code": "CONVENIENCE_STANDARD",
        "opened_on": "2025-01-01",
        "is_training_eligible": True,
        "realized_90d_net_revenue": 0.0,
    }
    result_null = evaluate_sitescore_opening_outcome_benchmark([null_record], provenance="pg16_query")
    assert result_null.mature_label_count == 0

    result_zero = evaluate_sitescore_opening_outcome_benchmark([zero_record], provenance="pg16_query")
    assert result_zero.mature_label_count == 1


def test_sitescore_model_card_governed_disabled_unverified_semantics():
    # B2 regression test: Governed-disabled model card emits UNAVAILABLE/UNVERIFIED governance facts and omits invented approval records
    result = run_benchmark_from_inventory(db_url=None, records=None)
    model_card = build_sitescore_opening_outcome_model_card(result)

    assert model_card.release_status == "GOVERNED_DISABLED"
    assert model_card.dataset_snapshot_id == "UNAVAILABLE"
    assert model_card.model_version == "UNVERIFIED"
    assert model_card.validation_run_id == "UNVERIFIED"
    assert model_card.feature_set_id == "UNVERIFIED"
    assert model_card.label_set_id == "UNVERIFIED"
    assert model_card.training_period == "UNAVAILABLE"
    assert model_card.validation_period == "UNAVAILABLE"
    assert model_card.algorithm == "UNAVAILABLE"
    assert model_card.baseline == "UNAVAILABLE"
    assert model_card.explainability_method == "UNAVAILABLE"
    assert model_card.privacy_review == "UNVERIFIED"
    assert model_card.security_review == "UNVERIFIED"
    assert len(model_card.approvals) == 0
    assert not model_card.is_complete
    assert not model_card.is_approved


def test_sitescore_opening_outcome_age_alone_never_satisfies_coverage():
    # B3 regression test: Store age alone (even store_age_days = 500) NEVER satisfies M6 or M12 outcome coverage when explicit outcome fields are missing
    records = _generate_candidate_records(
        220,
        m6_days=500,
        m12_days=500,
        include_m6_m12_realized=False,
        include_bounds=True,
    )
    for r in records:
        r["store_age_days"] = 500

    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="authenticated_governed_records")

    assert result.mature_label_count == 220
    assert result.m6_coverage_ratio == 0.0
    assert result.m12_coverage_ratio == 0.0
    assert not result.is_coverage_passed
    assert not result.is_gate2_passed
    assert result.reason_code == "MISSING_GOVERNED_LINEAGE"


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


def test_sitescore_opening_outcome_legitimate_zero_outcomes_counted_as_mature_labels():
    # Fix B1 regression test: 220 eligible records with legitimate zero outcomes (0.0) are counted as mature labels and M6/M12 covered
    records = _generate_candidate_records(
        220,
        revenue=0.0,  # Legitimate zero revenue outcome
        include_m6_m12_realized=False,
        include_bounds=True,
        dataset_snapshot_id="snapshot_sitescore_v2",
        model_version="candidate-site-view-v2",
        artifact_lineage_id="art_sitescore_sha256",
    )
    for r in records:
        r["realized_m6_net_revenue"] = 0.0
        r["realized_m12_net_revenue"] = 0.0

    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="authenticated_governed_records")

    assert result.observed_count == 220
    assert result.eligible_count == 220
    assert result.mature_label_count == 220  # Legitimate zero outcomes must count as mature labels!
    assert result.m6_coverage_ratio == 1.0  # Legitimate zero M6 outcomes covered
    assert result.m12_coverage_ratio == 1.0  # Legitimate zero M12 outcomes covered


def test_sitescore_opening_outcome_negative_values_excluded_by_policy():
    # Negative-value policy regression test: Negative revenue outcomes (-50.0) are excluded from mature label evaluation
    records = _generate_candidate_records(
        10,
        revenue=-50.0,
        include_m6_m12_realized=False,
        dataset_snapshot_id="snapshot_sitescore_v2",
    )
    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="authenticated_governed_records")

    assert result.observed_count == 10
    assert result.eligible_count == 10
    assert result.mature_label_count == 0  # Negative outcomes excluded per policy


def test_sitescore_opening_outcome_zero_outcome_cohort_non_zero_mae_fails_closed():
    # Fix B1 regression test: 220 eligible records with zero outcome (0.0) but non-zero predictions (100,000.0) must fail closed as NORMALIZED_MAE_EXCEEDED instead of normalizing to 0.0
    records = _generate_candidate_records(
        220,
        revenue=0.0,
        pred_revenue=100_000.0,
        include_m6_m12_realized=True,
        include_bounds=True,
        dataset_snapshot_id="snapshot_sitescore_v2",
        model_version="candidate-site-view-v2",
        artifact_lineage_id="art_sitescore_sha256",
    )
    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="authenticated_governed_records")

    assert result.mature_label_count == 220
    assert result.normalized_mae == 999.0  # Zero-denominator fail closed
    assert not result.is_mae_passed
    assert not result.is_gate2_passed
    assert result.status == "GOVERNED_DISABLED"
    assert result.reason_code == "MISSING_GOVERNED_LINEAGE"
    assert any("exceeds maximum threshold" in r for r in result.handback_payload["reasons"])


def test_sitescore_opening_outcome_zero_outcome_cohort_zero_mae_allowed():
    # Fix B1 regression test: Zero outcome (0.0) with zero prediction (0.0) yields normalized MAE 0.0
    records = _generate_candidate_records(
        220,
        revenue=0.0,
        pred_revenue=0.0,
        include_m6_m12_realized=True,
        include_bounds=True,
        dataset_snapshot_id="snapshot_sitescore_v2",
        model_version="candidate-site-view-v2",
        artifact_lineage_id="art_sitescore_sha256",
    )
    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="authenticated_governed_records")

    assert result.mature_label_count == 220
    assert result.normalized_mae == 0.0
    assert result.is_mae_passed


def test_sitescore_opening_outcome_handback_contains_backfill_metadata(tmp_path):
    # Fix B2 regression test: Gate 2 handback payload and markdown evidence expose split outcome backfill and prediction source resolver contracts, executable SQL, and receipt requirement
    from pathlib import Path

    result = run_benchmark_from_inventory(db_url=None, records=None)
    handback = result.handback_payload

    assert handback["handback_required"] is True
    assert handback["backfill_owner"] == "Human/Ops"
    assert handback["backfill_task_id"] == "ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001"
    assert handback["prediction_source_task_id"] == "ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001"

    # Verify task registrations against repo-tracked canonical ledger docs/evidence/DEVELOPMENT_PLAN_GAP_EXECUTION_TASKS_2026-07-30.md
    gap_tasks_path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "evidence"
        / "DEVELOPMENT_PLAN_GAP_EXECUTION_TASKS_2026-07-30.md"
    )
    gap_tasks_content = gap_tasks_path.read_text(encoding="utf-8")
    assert handback["backfill_task_id"] in gap_tasks_content
    assert handback["prediction_source_task_id"] in gap_tasks_content

    # Verify split contract payloads
    outcome_contract = handback["outcome_backfill_contract"]
    assert outcome_contract["owner"] == "Human/Ops"
    assert outcome_contract["task_id"] == "ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001"
    assert "realized_180d_net_revenue" in outcome_contract["required_fields"]
    assert "realized_365d_net_revenue" in outcome_contract["required_fields"]

    pred_contract = handback["prediction_source_contract"]
    assert pred_contract["task_id"] == "ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001"
    for col in ["predicted_revenue", "p10", "p90", "dataset_snapshot_id", "model_version", "artifact_lineage_id"]:
        assert col in pred_contract["required_fields"]

    # Verify executable query contract matches current candidate_site_view schema
    query = handback["discovery_inventory_query"]
    assert "model_ready.candidate_site_view" in query
    for baseline_col in [
        "entity_id",
        "store_id",
        "target_format_code",
        "opened_on",
        "is_training_eligible",
        "realized_90d_net_revenue",
    ]:
        assert baseline_col in query

    assert handback["backfill_receipt_required"] is True

    receipt = build_sitescore_gate2_receipt(result)
    output_evidence = tmp_path / "evidence_b2.md"
    write_evidence_markdown(receipt, output_evidence)

    content = output_evidence.read_text(encoding="utf-8")
    assert "- **Backfill Owner**: `Human/Ops`" in content
    assert "- **Backfill Task ID**: `ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001`" in content
    assert "- **Prediction Source Task ID**: `ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001`" in content
    assert "- **Discovery Inventory Query**:" in content
    assert "- **Backfill Receipt Required**: `True`" in content


def test_sitescore_opening_outcome_non_finite_inputs_rejected_b1():
    # B1 regression test: Non-finite outcomes, predictions, and interval bounds are rejected before calculation
    records = _generate_candidate_records(
        220,
        include_m6_m12_realized=True,
        include_bounds=True,
        dataset_snapshot_id="snapshot_sitescore_v2",
        model_version="candidate-site-view-v2",
        artifact_lineage_id="art_sitescore_sha256",
    )
    # Inject NaN / Inf / -Inf into records
    records[0]["predicted_revenue"] = float("nan")
    records[1]["p10"] = float("-inf")
    records[1]["p90"] = float("inf")
    records[2]["realized_90d_net_revenue"] = float("inf")

    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="authenticated_governed_records")

    # Record 2 with realized_90d_net_revenue = inf must be excluded from mature_label_count
    assert result.mature_label_count == 219
    # Record 0 with predicted_revenue = nan must be excluded from matched_prediction_count
    assert result.matched_prediction_count == 218
    # All ratios and metrics must be finite numbers
    assert math.isfinite(result.normalized_mae)
    assert math.isfinite(result.prediction_coverage_ratio)
    assert math.isfinite(result.interval_bounds_coverage_ratio)

    receipt = build_sitescore_gate2_receipt(result)

    # Verify JSON serialization strictness with allow_nan=False
    import json
    json_str = json.dumps(receipt, allow_nan=False)
    assert "NaN" not in json_str
    assert "Infinity" not in json_str


def test_sitescore_opening_outcome_matched_population_alignment_b2():
    # B2 regression test: MAE and mean_y denominator are computed over the exact same matched population
    # 154 matched records (70%) with realized = 100, predicted = 200 (error = 100)
    matched = _generate_candidate_records(
        154,
        revenue=100.0,
        pred_revenue=200.0,
        include_m6_m12_realized=True,
        include_bounds=True,
    )
    # 66 unmatched records with realized = 1,000,000 and predicted = None
    unmatched = _generate_candidate_records(
        66,
        revenue=1_000_000.0,
        pred_revenue=None,
        include_m6_m12_realized=True,
        include_bounds=True,
    )
    for r in unmatched:
        r["predicted_revenue"] = None

    records = matched + unmatched
    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="authenticated_governed_records")

    assert result.mature_label_count == 220
    assert result.matched_prediction_count == 154
    assert result.prediction_coverage_ratio == 0.70
    assert result.matched_mean_y == 100.0
    # Matched MAE = 100.0, matched mean_y = 100.0 => normalized_mae = 1.0 (fails threshold <= 0.25!)
    assert result.normalized_mae == 1.0
    assert not result.is_mae_passed
    assert not result.is_gate2_passed


def test_sitescore_gate2_receipt_verifier_rejects_forged_active_and_drift_b3():
    # B3 regression test: verify_sitescore_gate2_receipt rejects forged ACTIVE verdicts and count/hash drift
    from models.sitescore.opening_outcome import verify_sitescore_gate2_receipt

    result = run_benchmark_from_inventory(db_url=None, records=None)
    receipt = build_sitescore_gate2_receipt(result)

    # Genuine failing receipt verifies as valid (reasons explain it is GOVERNED_DISABLED)
    verif = verify_sitescore_gate2_receipt(receipt)
    assert verif.is_valid is True
    assert verif.reason_code == "RECEIPT_VALIDATED"

    # Forgery attempt 1: Mutate gate_status to PASSED and recompute integrity hash
    forged_receipt_1 = json.loads(json.dumps(receipt))
    forged_receipt_1["gate_status"] = "PASSED"
    from models.sitescore.opening_outcome import compute_gate2_receipt_sha256
    forged_receipt_1["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(forged_receipt_1)

    verif_forged_1 = verify_sitescore_gate2_receipt(forged_receipt_1)
    assert verif_forged_1.is_valid is False
    assert verif_forged_1.reason_code == "FORGED_ACTIVE_OR_MALFORMED_RECEIPT"
    assert any("Forged ACTIVE or PASSED verdict" in e for e in verif_forged_1.errors)

    # Forgery attempt 2: Mutate is_governed_disabled to False and recompute hash
    forged_receipt_2 = json.loads(json.dumps(receipt))
    forged_receipt_2["is_governed_disabled"] = False
    forged_receipt_2["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(forged_receipt_2)

    verif_forged_2 = verify_sitescore_gate2_receipt(forged_receipt_2)
    assert verif_forged_2.is_valid is False
    assert verif_forged_2.reason_code == "FORGED_ACTIVE_OR_MALFORMED_RECEIPT"

    # Corruption test: Hash mismatch without recomputing integrity hash
    corrupted_receipt = json.loads(json.dumps(receipt))
    corrupted_receipt["inventory_version"] = "corrupted-version"

    verif_corrupt = verify_sitescore_gate2_receipt(corrupted_receipt)
    assert verif_corrupt.is_valid is False
    assert verif_corrupt.reason_code == "INTEGRITY_HASH_MISMATCH"


def test_sitescore_model_card_prevents_caller_invented_governance_facts_b4():
    # B4 regression test: Model card ignores caller-supplied governance facts when benchmark is unverified/governed-disabled
    from models.shared_ml.model_card import ModelCardApproval

    result = run_benchmark_from_inventory(db_url=None, records=None)
    fake_approval = ModelCardApproval(
        approver="attacker",
        role="platform_lead",
        approved_at="2026-07-31T00:00:00Z",
        decision="approved",
    )

    model_card = build_sitescore_opening_outcome_model_card(
        result,
        privacy_review="PASSED",
        security_review="PASSED",
        approvals=[fake_approval],
        algorithm="invented_algorithm",
        baseline="invented_baseline",
    )

    assert model_card.release_status == "GOVERNED_DISABLED"
    assert model_card.privacy_review == "UNVERIFIED"
    assert model_card.security_review == "UNVERIFIED"
    assert len(model_card.approvals) == 0
    assert model_card.algorithm == "UNAVAILABLE"
    assert model_card.baseline == "UNAVAILABLE"
    assert not model_card.is_approved
    assert not model_card.is_complete


def test_sitescore_handback_payload_contains_complete_human_ops_contract_b5():
    # B5 regression test: Handback contract exposes complete Human/Ops backfill contract metadata
    result = run_benchmark_from_inventory(db_url=None, records=None)
    contract = result.handback_payload["outcome_backfill_contract"]

    assert contract["owner"] == "Human/Ops"
    assert contract["task_id"] == "ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001"
    assert contract["discovery_source_identity"] == "model_ready.candidate_site_view"
    assert contract["required_source_identity"] == "authoritative_opening_outcome_m6_m12_store_ledger"
    assert contract["source_identity"] == "UNVERIFIED"
    assert contract["required_query_id"] == "sitescore_authoritative_m6_m12_outcome_query_v1"
    assert contract["query_id"] == "UNVERIFIED"
    assert contract["dataset_snapshot_hash"] == "UNVERIFIED"
    assert contract["lineage_id"] == "UNVERIFIED"
    assert contract["freshness_timestamp"] == "UNVERIFIED"
    assert "is_training_eligible" in contract["eligibility_definition"]
    assert "realized_90d_net_revenue" in contract["maturity_definition"]
    assert "realized_180d_net_revenue" in contract["m6_maturity_definition"]
    assert "realized_365d_net_revenue" in contract["m12_maturity_definition"]
    assert contract["observed_count"] == 0
    assert contract["eligible_count"] == 0
    assert contract["mature_count"] == 0
    assert contract["matched_prediction_count"] == 0
    assert "realized_180d_net_revenue" in contract["required_fields"]
    assert "realized_365d_net_revenue" in contract["required_fields"]


def test_sitescore_gate2_receipt_verifier_rejects_self_consistent_count_and_ratio_drift_b1():
    # B1 regression test (Codex6 Addendum): Verifier fails closed on duplicated count/ratio drift and malformed typed metrics
    from models.sitescore.opening_outcome import verify_sitescore_gate2_receipt

    result = run_benchmark_from_inventory(db_url=None, records=None)
    receipt = build_sitescore_gate2_receipt(result)

    # 1. handback.observed_count drift
    drift_1 = json.loads(json.dumps(receipt))
    drift_1["handback"]["observed_count"] = 999
    drift_1["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(drift_1)
    res_1 = verify_sitescore_gate2_receipt(drift_1)
    assert res_1.is_valid is False
    assert res_1.reason_code == "FORGED_ACTIVE_OR_MALFORMED_RECEIPT"
    assert any("handback.observed_count" in e for e in res_1.errors)

    # 2. benchmark_summary.handback_payload.mature_label_count drift
    drift_2 = json.loads(json.dumps(receipt))
    drift_2["benchmark_summary"]["handback_payload"]["mature_label_count"] = 999
    drift_2["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(drift_2)
    res_2 = verify_sitescore_gate2_receipt(drift_2)
    assert res_2.is_valid is False
    assert res_2.reason_code == "FORGED_ACTIVE_OR_MALFORMED_RECEIPT"
    assert any("handback_payload.mature_label_count" in e for e in res_2.errors)

    # 3. handback.prediction_coverage_ratio drift
    drift_3 = json.loads(json.dumps(receipt))
    drift_3["handback"]["prediction_coverage_ratio"] = 1.0
    drift_3["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(drift_3)
    res_3 = verify_sitescore_gate2_receipt(drift_3)
    assert res_3.is_valid is False
    assert res_3.reason_code == "FORGED_ACTIVE_OR_MALFORMED_RECEIPT"
    assert any("handback.prediction_coverage_ratio" in e for e in res_3.errors)

    # 4. Malformed typed metrics: string "not-a-number" must NOT cause TypeError exception
    malformed_1 = json.loads(json.dumps(receipt))
    malformed_1["benchmark_summary"]["normalized_mae"] = "not-a-number"
    malformed_1["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(malformed_1)
    res_m1 = verify_sitescore_gate2_receipt(malformed_1)
    assert res_m1.is_valid is False
    assert res_m1.reason_code == "FORGED_ACTIVE_OR_MALFORMED_RECEIPT"

    # 5. Malformed typed count: boolean True in observed_count must be rejected as invalid integer
    malformed_2 = json.loads(json.dumps(receipt))
    malformed_2["benchmark_summary"]["observed_count"] = True
    malformed_2["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(malformed_2)
    res_m2 = verify_sitescore_gate2_receipt(malformed_2)
    assert res_m2.is_valid is False
    assert res_m2.reason_code == "FORGED_ACTIVE_OR_MALFORMED_RECEIPT"


def test_sitescore_model_card_prevents_caller_invented_facts_on_governed_active_path_b2():
    # B2 regression test (Codex6 Addendum): Even on active/governed paths, caller-invented facts and approvals are isolated and forced to UNVERIFIED/UNAVAILABLE
    from unittest.mock import PropertyMock, patch

    from models.shared_ml.model_card import ModelCardApproval

    records = _generate_candidate_records(
        220,
        include_m6_m12_realized=True,
        include_bounds=True,
        dataset_snapshot_id="snapshot_sitescore_v2",
        model_version="candidate-site-view-v2",
        artifact_lineage_id="art_sitescore_sha256",
    )
    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="authenticated_governed_records")

    fake_approval = ModelCardApproval(
        approver="attacker",
        role="platform_lead",
        approved_at="2026-07-31T00:00:00Z",
        decision="approved",
    )

    # Patch is_lineage_governed to True to simulate active path mutation
    with patch.object(type(result), "is_lineage_governed", new_callable=PropertyMock, return_value=True):
        assert result.is_gate2_passed is True
        model_card = build_sitescore_opening_outcome_model_card(
            result,
            validation_run_id="invented-run",
            privacy_review="PASSED",
            security_review="PASSED",
            approvals=[fake_approval],
            algorithm="invented_algorithm",
            baseline="invented_baseline",
        )

        assert model_card.release_status == "DEV"
        assert model_card.privacy_review == "UNVERIFIED"
        assert model_card.security_review == "UNVERIFIED"
        assert len(model_card.approvals) == 0
        assert model_card.algorithm == "UNAVAILABLE"
        assert model_card.baseline == "UNAVAILABLE"
        assert not model_card.is_approved
        assert not model_card.is_complete


def test_sitescore_handback_payload_authoritative_m6_m12_contract_b3():
    # B3 regression test (Codex6 Addendum): Human/Ops contract specifies authoritative M6/M12 ledger, freshness isolation, and exact required fields
    result = run_benchmark_from_inventory(db_url=None, records=None)
    contract = result.handback_payload["outcome_backfill_contract"]

    assert contract["discovery_source_identity"] == "model_ready.candidate_site_view"
    assert contract["required_source_identity"] == "authoritative_opening_outcome_m6_m12_store_ledger"
    assert contract["source_identity"] == "UNVERIFIED"
    assert contract["required_query_id"] == "sitescore_authoritative_m6_m12_outcome_query_v1"
    assert contract["query_id"] == "UNVERIFIED"
    assert contract["freshness_timestamp"] == "UNVERIFIED"
    assert contract["dataset_snapshot_hash"] == "UNVERIFIED"
    assert contract["lineage_id"] == "UNVERIFIED"
    assert "authoritative_source_identity" in contract["required_fields"]
    assert "query_id" in contract["required_fields"]
    assert "source_freshness_timestamp" in contract["required_fields"]
    assert "m6_maturity_definition" in contract["required_fields"]
    assert "m12_maturity_definition" in contract["required_fields"]


def test_sitescore_opening_outcome_coverage_flags_cannot_bypass_true_maturity_b1():
    # B1 Addendum test: boolean m6_covered/m12_covered flags CANNOT turn under-age (days=1) or missing-age rows into mature outcomes
    records = _generate_candidate_records(
        200,
        include_m6_m12_realized=True,
        include_bounds=True,
        dataset_snapshot_id="snapshot_v1",
        model_version="v1",
        artifact_lineage_id="lin_v1",
    )
    for r in records:
        r["m6_days"] = 1
        r["m12_days"] = 1
        r["m6_covered"] = True
        r["m12_covered"] = True

    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="authenticated_governed_records")

    assert result.m6_coverage_ratio == 0.0
    assert result.m12_coverage_ratio == 0.0
    assert not result.is_coverage_passed
    assert not result.is_gate2_passed


def test_sitescore_gate2_verifier_rejects_governance_drift_and_boolean_threshold_b2():
    # B2 Addendum test: Verifier fails closed on top-level provenance drift, invalid reason_code, invalid status, and boolean activation_threshold
    from models.sitescore.opening_outcome import verify_sitescore_gate2_receipt

    result = run_benchmark_from_inventory(db_url=None, records=None)
    receipt = build_sitescore_gate2_receipt(result)

    # 1. Top-level provenance drift: no_source -> pg16_query
    m1 = json.loads(json.dumps(receipt))
    m1["provenance"] = "pg16_query"
    m1["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m1)
    res_m1 = verify_sitescore_gate2_receipt(m1)
    assert res_m1.is_valid is False
    assert any("top-level provenance" in e for e in res_m1.errors)

    # 2. benchmark_summary.reason_code: NO_SOURCE_INVENTORY -> OTHER
    m2 = json.loads(json.dumps(receipt))
    m2["benchmark_summary"]["reason_code"] = "OTHER"
    m2["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m2)
    res_m2 = verify_sitescore_gate2_receipt(m2)
    assert res_m2.is_valid is False
    assert any("reason_code" in e for e in res_m2.errors)

    # 3. top-level handback.reason_code: NO_SOURCE_INVENTORY -> OTHER
    m3 = json.loads(json.dumps(receipt))
    m3["handback"]["reason_code"] = "OTHER"
    m3["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m3)
    res_m3 = verify_sitescore_gate2_receipt(m3)
    assert res_m3.is_valid is False
    assert any("reason_code" in e for e in res_m3.errors)

    # 4. benchmark_summary.status: GOVERNED_DISABLED -> OTHER
    m4 = json.loads(json.dumps(receipt))
    m4["benchmark_summary"]["status"] = "OTHER"
    m4["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m4)
    res_m4 = verify_sitescore_gate2_receipt(m4)
    assert res_m4.is_valid is False
    assert any("summary.status" in e for e in res_m4.errors)

    # 5. benchmark_summary.activation_threshold: 200 -> True
    m5 = json.loads(json.dumps(receipt))
    m5["benchmark_summary"]["activation_threshold"] = True
    m5["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m5)
    res_m5 = verify_sitescore_gate2_receipt(m5)
    assert res_m5.is_valid is False
    assert any("activation_threshold must be an integer" in e for e in res_m5.errors)


def test_sitescore_handback_payload_discovery_query_and_unverified_source_b3():
    # B3 Addendum test: Discovery query is explicitly labeled discovery_inventory_query, handback_action routes to both backfill & prediction-source receipts, and unobserved dataset snapshot is UNVERIFIED
    result = run_benchmark_from_inventory(db_url=None, records=None)
    hb = result.handback_payload

    assert "discovery_inventory_query" in hb
    assert "backfill_query" not in hb
    assert "ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001" in hb["handback_action"]
    assert "ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001" in hb["handback_action"]

    contract = hb["outcome_backfill_contract"]
    assert contract["required_source_identity"] == "authoritative_opening_outcome_m6_m12_store_ledger"
    assert contract["source_identity"] == "UNVERIFIED"
    assert contract["required_query_id"] == "sitescore_authoritative_m6_m12_outcome_query_v1"
    assert contract["query_id"] == "UNVERIFIED"
    assert contract["dataset_snapshot_hash"] == "UNVERIFIED"
    assert contract["lineage_id"] == "UNVERIFIED"
    assert contract["freshness_timestamp"] == "UNVERIFIED"


def test_sitescore_verifier_rejects_all_7_re_review_b1_mutations():
    # B1 Re-review test matrix (Codex6 2026-07-31): All 7 malformed and self-consistently forged receipt mutations are rejected
    from models.sitescore.opening_outcome import verify_sitescore_gate2_receipt

    result = run_benchmark_from_inventory(db_url=None, records=None)
    receipt = build_sitescore_gate2_receipt(result)

    # Mutation 1: Set all three m6_coverage_ratio copies to 2.0
    m1 = json.loads(json.dumps(receipt))
    m1["benchmark_summary"]["m6_coverage_ratio"] = 2.0
    m1["handback"]["m6_coverage_ratio"] = 2.0
    m1["benchmark_summary"]["handback_payload"]["m6_coverage_ratio"] = 2.0
    m1["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m1)
    res1 = verify_sitescore_gate2_receipt(m1)
    assert res1.is_valid is False

    # Mutation 2: Set all three normalized_mae copies to -1.0
    m2 = json.loads(json.dumps(receipt))
    m2["benchmark_summary"]["normalized_mae"] = -1.0
    m2["handback"]["normalized_mae"] = -1.0
    m2["benchmark_summary"]["handback_payload"]["normalized_mae"] = -1.0
    m2["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m2)
    res2 = verify_sitescore_gate2_receipt(m2)
    assert res2.is_valid is False

    # Mutation 3: Change only benchmark_summary.handback_payload.reason_code to GATE2_CRITERIA_MET
    m3 = json.loads(json.dumps(receipt))
    m3["benchmark_summary"]["handback_payload"]["reason_code"] = "GATE2_CRITERIA_MET"
    m3["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m3)
    res3 = verify_sitescore_gate2_receipt(m3)
    assert res3.is_valid is False

    # Mutation 4: Change both handback copies' governed_disabled to false while top-level remains true
    m4 = json.loads(json.dumps(receipt))
    m4["handback"]["governed_disabled"] = False
    m4["benchmark_summary"]["handback_payload"]["governed_disabled"] = False
    m4["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m4)
    res4 = verify_sitescore_gate2_receipt(m4)
    assert res4.is_valid is False

    # Mutation 5: Change top-level gate_status to BOGUS
    m5 = json.loads(json.dumps(receipt))
    m5["gate_status"] = "BOGUS"
    m5["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m5)
    res5 = verify_sitescore_gate2_receipt(m5)
    assert res5.is_valid is False

    # Mutation 6: Change top-level is_governed_disabled from boolean true to string "yes"
    m6 = json.loads(json.dumps(receipt))
    m6["is_governed_disabled"] = "yes"
    m6["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m6)
    res6 = verify_sitescore_gate2_receipt(m6)
    assert res6.is_valid is False

    # Mutation 7: Change every reason-code copy from NO_SOURCE_INVENTORY to allowed enum GATE2_CRITERIA_MET
    m7 = json.loads(json.dumps(receipt))
    m7["benchmark_summary"]["reason_code"] = "GATE2_CRITERIA_MET"
    m7["handback"]["reason_code"] = "GATE2_CRITERIA_MET"
    m7["benchmark_summary"]["handback_payload"]["reason_code"] = "GATE2_CRITERIA_MET"
    m7["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m7)
    res7 = verify_sitescore_gate2_receipt(m7)
    assert res7.is_valid is False


def test_sitescore_gate2_receipt_artifact_hashes_binding_b3():
    # B3 Re-review test: Receipt binds handback_hash and model_card_hash in integrity envelope and artifact_hashes
    result = run_benchmark_from_inventory(db_url=None, records=None)
    receipt = build_sitescore_gate2_receipt(result)

    assert "artifact_hashes" in receipt
    assert "handback_hash" in receipt["artifact_hashes"]
    assert "model_card_hash" in receipt["artifact_hashes"]
    assert receipt["integrity"]["handback_hash"] == receipt["artifact_hashes"]["handback_hash"]
    assert receipt["integrity"]["model_card_hash"] == receipt["artifact_hashes"]["model_card_hash"]

    verif = verify_sitescore_gate2_receipt(receipt)
    assert verif.is_valid is True
