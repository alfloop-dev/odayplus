"""Unit & integration tests for SiteScore opening outcome M6/M12 coverage calibration benchmark & Gate 2 receipt."""

from __future__ import annotations

import json
import math
from typing import Any

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
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)

    # Genuine failing receipt verifies as valid (reasons explain it is GOVERNED_DISABLED)
    verif = verify_sitescore_gate2_receipt(receipt, model_card_artifact=model_card)
    assert verif.is_valid is True
    assert verif.reason_code == "RECEIPT_VALIDATED"

    # Forgery attempt 1: Mutate gate_status to PASSED and recompute integrity hash
    forged_receipt_1 = json.loads(json.dumps(receipt))
    forged_receipt_1["gate_status"] = "PASSED"
    from models.sitescore.opening_outcome import compute_gate2_receipt_sha256
    forged_receipt_1["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(forged_receipt_1)

    verif_forged_1 = verify_sitescore_gate2_receipt(forged_receipt_1, model_card_artifact=model_card)
    assert verif_forged_1.is_valid is False
    assert verif_forged_1.reason_code == "FORGED_ACTIVE_OR_MALFORMED_RECEIPT"
    assert any("Forged ACTIVE or PASSED verdict" in e for e in verif_forged_1.errors)

    # Forgery attempt 2: Mutate is_governed_disabled to False and recompute hash
    forged_receipt_2 = json.loads(json.dumps(receipt))
    forged_receipt_2["is_governed_disabled"] = False
    forged_receipt_2["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(forged_receipt_2)

    verif_forged_2 = verify_sitescore_gate2_receipt(forged_receipt_2, model_card_artifact=model_card)
    assert verif_forged_2.is_valid is False
    assert verif_forged_2.reason_code == "FORGED_ACTIVE_OR_MALFORMED_RECEIPT"

    # Corruption test: Hash mismatch without recomputing integrity hash
    corrupted_receipt = json.loads(json.dumps(receipt))
    corrupted_receipt["inventory_version"] = "corrupted-version"

    verif_corrupt = verify_sitescore_gate2_receipt(corrupted_receipt, model_card_artifact=model_card)
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
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)

    # 1. handback.observed_count drift
    drift_1 = json.loads(json.dumps(receipt))
    drift_1["handback"]["observed_count"] = 999
    drift_1["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(drift_1)
    res_1 = verify_sitescore_gate2_receipt(drift_1, model_card_artifact=model_card)
    assert res_1.is_valid is False
    assert res_1.reason_code == "FORGED_ACTIVE_OR_MALFORMED_RECEIPT"
    assert any("handback.observed_count" in e for e in res_1.errors)

    # 2. benchmark_summary.handback_payload.mature_label_count drift
    drift_2 = json.loads(json.dumps(receipt))
    drift_2["benchmark_summary"]["handback_payload"]["mature_label_count"] = 999
    drift_2["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(drift_2)
    res_2 = verify_sitescore_gate2_receipt(drift_2, model_card_artifact=model_card)
    assert res_2.is_valid is False
    assert res_2.reason_code == "FORGED_ACTIVE_OR_MALFORMED_RECEIPT"
    assert any("handback_payload.mature_label_count" in e for e in res_2.errors)

    # 3. handback.prediction_coverage_ratio drift
    drift_3 = json.loads(json.dumps(receipt))
    drift_3["handback"]["prediction_coverage_ratio"] = 1.0
    drift_3["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(drift_3)
    res_3 = verify_sitescore_gate2_receipt(drift_3, model_card_artifact=model_card)
    assert res_3.is_valid is False
    assert res_3.reason_code == "FORGED_ACTIVE_OR_MALFORMED_RECEIPT"
    assert any("handback.prediction_coverage_ratio" in e for e in res_3.errors)

    # 4. Malformed typed metrics: string "not-a-number" must NOT cause TypeError exception
    malformed_1 = json.loads(json.dumps(receipt))
    malformed_1["benchmark_summary"]["normalized_mae"] = "not-a-number"
    malformed_1["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(malformed_1)
    res_m1 = verify_sitescore_gate2_receipt(malformed_1, model_card_artifact=model_card)
    assert res_m1.is_valid is False
    assert res_m1.reason_code == "FORGED_ACTIVE_OR_MALFORMED_RECEIPT"

    # 5. Malformed typed count: boolean True in observed_count must be rejected as invalid integer
    malformed_2 = json.loads(json.dumps(receipt))
    malformed_2["benchmark_summary"]["observed_count"] = True
    malformed_2["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(malformed_2)
    res_m2 = verify_sitescore_gate2_receipt(malformed_2, model_card_artifact=model_card)
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
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)

    # 1. Top-level provenance drift: no_source -> pg16_query
    m1 = json.loads(json.dumps(receipt))
    m1["provenance"] = "pg16_query"
    m1["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m1)
    res_m1 = verify_sitescore_gate2_receipt(m1, model_card_artifact=model_card)
    assert res_m1.is_valid is False
    assert any("top-level provenance" in e for e in res_m1.errors)

    # 2. benchmark_summary.reason_code: NO_SOURCE_INVENTORY -> OTHER
    m2 = json.loads(json.dumps(receipt))
    m2["benchmark_summary"]["reason_code"] = "OTHER"
    m2["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m2)
    res_m2 = verify_sitescore_gate2_receipt(m2, model_card_artifact=model_card)
    assert res_m2.is_valid is False
    assert any("reason_code" in e for e in res_m2.errors)

    # 3. top-level handback.reason_code: NO_SOURCE_INVENTORY -> OTHER
    m3 = json.loads(json.dumps(receipt))
    m3["handback"]["reason_code"] = "OTHER"
    m3["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m3)
    res_m3 = verify_sitescore_gate2_receipt(m3, model_card_artifact=model_card)
    assert res_m3.is_valid is False
    assert any("reason_code" in e for e in res_m3.errors)

    # 4. benchmark_summary.status: GOVERNED_DISABLED -> OTHER
    m4 = json.loads(json.dumps(receipt))
    m4["benchmark_summary"]["status"] = "OTHER"
    m4["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m4)
    res_m4 = verify_sitescore_gate2_receipt(m4, model_card_artifact=model_card)
    assert res_m4.is_valid is False
    assert any("summary.status" in e for e in res_m4.errors)

    # 5. benchmark_summary.activation_threshold: 200 -> True
    m5 = json.loads(json.dumps(receipt))
    m5["benchmark_summary"]["activation_threshold"] = True
    m5["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m5)
    res_m5 = verify_sitescore_gate2_receipt(m5, model_card_artifact=model_card)
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
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)

    # Mutation 1: Set all three m6_coverage_ratio copies to 2.0
    m1 = json.loads(json.dumps(receipt))
    m1["benchmark_summary"]["m6_coverage_ratio"] = 2.0
    m1["handback"]["m6_coverage_ratio"] = 2.0
    m1["benchmark_summary"]["handback_payload"]["m6_coverage_ratio"] = 2.0
    m1["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m1)
    res1 = verify_sitescore_gate2_receipt(m1, model_card_artifact=model_card)
    assert res1.is_valid is False

    # Mutation 2: Set all three normalized_mae copies to -1.0
    m2 = json.loads(json.dumps(receipt))
    m2["benchmark_summary"]["normalized_mae"] = -1.0
    m2["handback"]["normalized_mae"] = -1.0
    m2["benchmark_summary"]["handback_payload"]["normalized_mae"] = -1.0
    m2["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m2)
    res2 = verify_sitescore_gate2_receipt(m2, model_card_artifact=model_card)
    assert res2.is_valid is False

    # Mutation 3: Change only benchmark_summary.handback_payload.reason_code to GATE2_CRITERIA_MET
    m3 = json.loads(json.dumps(receipt))
    m3["benchmark_summary"]["handback_payload"]["reason_code"] = "GATE2_CRITERIA_MET"
    m3["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m3)
    res3 = verify_sitescore_gate2_receipt(m3, model_card_artifact=model_card)
    assert res3.is_valid is False

    # Mutation 4: Change both handback copies' governed_disabled to false while top-level remains true
    m4 = json.loads(json.dumps(receipt))
    m4["handback"]["governed_disabled"] = False
    m4["benchmark_summary"]["handback_payload"]["governed_disabled"] = False
    m4["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m4)
    res4 = verify_sitescore_gate2_receipt(m4, model_card_artifact=model_card)
    assert res4.is_valid is False

    # Mutation 5: Change top-level gate_status to BOGUS
    m5 = json.loads(json.dumps(receipt))
    m5["gate_status"] = "BOGUS"
    m5["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m5)
    res5 = verify_sitescore_gate2_receipt(m5, model_card_artifact=model_card)
    assert res5.is_valid is False

    # Mutation 6: Change top-level is_governed_disabled from boolean true to string "yes"
    m6 = json.loads(json.dumps(receipt))
    m6["is_governed_disabled"] = "yes"
    m6["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m6)
    res6 = verify_sitescore_gate2_receipt(m6, model_card_artifact=model_card)
    assert res6.is_valid is False

    # Mutation 7: Change every reason-code copy from NO_SOURCE_INVENTORY to allowed enum GATE2_CRITERIA_MET
    m7 = json.loads(json.dumps(receipt))
    m7["benchmark_summary"]["reason_code"] = "GATE2_CRITERIA_MET"
    m7["handback"]["reason_code"] = "GATE2_CRITERIA_MET"
    m7["benchmark_summary"]["handback_payload"]["reason_code"] = "GATE2_CRITERIA_MET"
    m7["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m7)
    res7 = verify_sitescore_gate2_receipt(m7, model_card_artifact=model_card)
    assert res7.is_valid is False


def test_sitescore_gate2_receipt_artifact_hashes_binding_b3():
    # B3 Re-review test: Receipt binds handback_hash and model_card_hash in integrity envelope and artifact_hashes
    result = run_benchmark_from_inventory(db_url=None, records=None)
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)

    assert "artifact_hashes" in receipt
    assert "handback_hash" in receipt["artifact_hashes"]
    assert "model_card_hash" in receipt["artifact_hashes"]
    assert receipt["integrity"]["handback_hash"] == receipt["artifact_hashes"]["handback_hash"]
    assert receipt["integrity"]["model_card_hash"] == receipt["artifact_hashes"]["model_card_hash"]

    verif = verify_sitescore_gate2_receipt(receipt, model_card_artifact=model_card)
    assert verif.is_valid is True


def test_sitescore_opening_outcome_non_empty_population_counts_populated_and_verifies_b1():
    # B1 Re-review test: Non-empty records populate m6_mature_count, m12_mature_count, interval_bounds_count, in_p80_count
    records = _generate_candidate_records(
        10,
        include_m6_m12_realized=True,
        include_bounds=True,
        dataset_snapshot_id="snapshot_sitescore_v2",
        model_version="candidate-site-view-v2",
        artifact_lineage_id="art_sitescore_sha256",
    )
    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="authenticated_governed_records")

    assert result.observed_count == 10
    assert result.eligible_count == 10
    assert result.mature_label_count == 10
    assert result.matched_prediction_count == 10
    assert result.m6_mature_count == 10
    assert result.m12_mature_count == 10
    assert result.interval_bounds_count == 10
    assert result.in_p80_count == 10

    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)
    assert receipt["benchmark_summary"]["m6_mature_count"] == 10
    assert receipt["benchmark_summary"]["m12_mature_count"] == 10
    assert receipt["benchmark_summary"]["interval_bounds_count"] == 10
    assert receipt["benchmark_summary"]["in_p80_count"] == 10

    verif = verify_sitescore_gate2_receipt(receipt, model_card_artifact=model_card, dataset_manifest=records)
    assert verif.is_valid is True
    assert verif.reason_code == "RECEIPT_VALIDATED"


def test_sitescore_verifier_rejects_negative_counts_and_invalid_hierarchy_b1():
    # B1 Re-review test: Verifier fails closed on negative counts and invalid subset hierarchy
    from models.sitescore.opening_outcome import compute_handback_sha256

    records = _generate_candidate_records(
        10,
        include_m6_m12_realized=True,
        include_bounds=True,
    )
    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="authenticated_governed_records")
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)

    # 1. Negative count test: set m6_mature_count = -1
    m1 = json.loads(json.dumps(receipt))
    m1["benchmark_summary"]["m6_mature_count"] = -1
    m1["handback"]["m6_mature_count"] = -1
    m1["benchmark_summary"]["handback_payload"]["m6_mature_count"] = -1
    m1["handback"]["outcome_backfill_contract"]["m6_mature_count"] = -1
    hb_hash_1 = compute_handback_sha256(m1["handback"])
    m1["artifact_hashes"]["handback_hash"] = hb_hash_1
    m1["integrity"]["handback_hash"] = hb_hash_1
    m1["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m1)

    res1 = verify_sitescore_gate2_receipt(m1, model_card_artifact=model_card)
    assert res1.is_valid is False
    assert any("cannot be negative" in e for e in res1.errors)

    # 2. Subset hierarchy test: interval_bounds_count (15) > matched_prediction_count (10)
    m2 = json.loads(json.dumps(receipt))
    m2["benchmark_summary"]["interval_bounds_count"] = 15
    m2["handback"]["interval_bounds_count"] = 15
    m2["benchmark_summary"]["handback_payload"]["interval_bounds_count"] = 15
    m2["handback"]["outcome_backfill_contract"]["interval_bounds_count"] = 15
    hb_hash_2 = compute_handback_sha256(m2["handback"])
    m2["artifact_hashes"]["handback_hash"] = hb_hash_2
    m2["integrity"]["handback_hash"] = hb_hash_2
    m2["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m2)

    res2 = verify_sitescore_gate2_receipt(m2, model_card_artifact=model_card)
    assert res2.is_valid is False
    assert any("Interval bounds count" in e for e in res2.errors)

    # 3. Subset hierarchy test: in_p80_count (15) > interval_bounds_count (10)
    m3 = json.loads(json.dumps(receipt))
    m3["benchmark_summary"]["in_p80_count"] = 15
    m3["handback"]["in_p80_count"] = 15
    m3["benchmark_summary"]["handback_payload"]["in_p80_count"] = 15
    m3["handback"]["outcome_backfill_contract"]["in_p80_count"] = 15
    hb_hash_3 = compute_handback_sha256(m3["handback"])
    m3["artifact_hashes"]["handback_hash"] = hb_hash_3
    m3["integrity"]["handback_hash"] = hb_hash_3
    m3["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m3)

    res3 = verify_sitescore_gate2_receipt(m3, model_card_artifact=model_card)
    assert res3.is_valid is False
    assert any("In P80 count" in e for e in res3.errors)


def test_sitescore_verifier_rejects_artifact_hashes_drift_and_model_card_mismatch_b2():
    # B2 Re-review test: Verifier fails closed on artifact_hashes drift and model card mismatch

    result = run_benchmark_from_inventory(db_url=None, records=None)
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)

    # 1. artifact_hashes.model_card_hash drift from integrity.model_card_hash
    m1 = json.loads(json.dumps(receipt))
    m1["artifact_hashes"]["model_card_hash"] = "a" * 64
    m1["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m1)

    res1 = verify_sitescore_gate2_receipt(m1, model_card_artifact=model_card)
    assert res1.is_valid is False
    assert any("Integrity model_card_hash drift" in e for e in res1.errors)

    # 2. Model card artifact mismatch
    mc_dict = model_card.to_dict()
    modified_mc_dict = json.loads(json.dumps(mc_dict))
    modified_mc_dict["model_name"] = "tampered_model_name"

    res2 = verify_sitescore_gate2_receipt(receipt, model_card_artifact=modified_mc_dict)
    assert res2.is_valid is False
    assert any("Model card artifact hash mismatch" in e for e in res2.errors)


def test_sitescore_committed_evidence_files_round_trip_verification_b2():
    # B2 Re-review test: Committed evidence files in docs/evidence/models/ are consistent and verify clean
    from pathlib import Path

    from models.sitescore.opening_outcome import compute_model_card_sha256

    evidence_dir = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "models"
    receipt_path = evidence_dir / "sitescore_gate2_receipt.json"
    card_path = evidence_dir / "sitescore_model_card.json"

    with receipt_path.open(encoding="utf-8") as f:
        receipt = json.load(f)
    with card_path.open(encoding="utf-8") as f:
        model_card = json.load(f)

    mc_hash = compute_model_card_sha256(model_card)
    assert receipt["artifact_hashes"]["model_card_hash"] == mc_hash
    assert receipt["integrity"]["model_card_hash"] == mc_hash

    verif = verify_sitescore_gate2_receipt(receipt, model_card_artifact=model_card)
    assert verif.is_valid is True
    assert verif.reason_code == "RECEIPT_VALIDATED"


def test_sitescore_verifier_mandates_model_card_and_rejects_forged_governed_disabled_semantics_b1():
    # B1 Re-review test: verify_sitescore_gate2_receipt fails closed when model_card_artifact is missing or carries forged governed-disabled semantics

    result = run_benchmark_from_inventory(db_url=None, records=None)
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)

    # 1. Missing model_card_artifact fails closed
    res_none = verify_sitescore_gate2_receipt(receipt, model_card_artifact=None)
    assert res_none.is_valid is False
    assert any("Missing required model_card_artifact" in e for e in res_none.errors)

    # 2. Forged release_status="ACTIVE" on governed-disabled card fails closed
    mc_dict_1 = json.loads(json.dumps(model_card.to_dict()))
    mc_dict_1["release_status"] = "ACTIVE"
    res_forged_1 = verify_sitescore_gate2_receipt(receipt, model_card_artifact=mc_dict_1)
    assert res_forged_1.is_valid is False
    assert any("Governed-disabled receipt requires model_card_artifact release_status to be 'GOVERNED_DISABLED'" in e for e in res_forged_1.errors)

    # 3. Forged privacy_review="PASSED" fails closed
    mc_dict_2 = json.loads(json.dumps(model_card.to_dict()))
    mc_dict_2["privacy_review"] = "PASSED"
    res_forged_2 = verify_sitescore_gate2_receipt(receipt, model_card_artifact=mc_dict_2)
    assert res_forged_2.is_valid is False
    assert any("privacy_review must be 'UNVERIFIED'" in e for e in res_forged_2.errors)

    # 4. Forged approval record fails closed
    mc_dict_3 = json.loads(json.dumps(model_card.to_dict()))
    mc_dict_3["approvals"] = [{
        "approver": "attacker",
        "role": "platform_lead",
        "approved_at": "2026-07-31T00:00:00Z",
        "decision": "approved",
    }]
    res_forged_3 = verify_sitescore_gate2_receipt(receipt, model_card_artifact=mc_dict_3)
    assert res_forged_3.is_valid is False
    assert any("Governed-disabled model card cannot contain approval records" in e for e in res_forged_3.errors)


def test_sitescore_verifier_mandates_handoff_contracts_and_reconciles_counts_b2():
    # B2 Re-review test: verify_sitescore_gate2_receipt fails closed if contracts/actions are missing or counts drift
    result = run_benchmark_from_inventory(db_url=None, records=None)
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)

    # 1. Missing outcome_backfill_contract
    m1 = json.loads(json.dumps(receipt))
    del m1["handback"]["outcome_backfill_contract"]
    m1["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m1)
    res_1 = verify_sitescore_gate2_receipt(m1, model_card_artifact=model_card)
    assert res_1.is_valid is False
    assert any("outcome_backfill_contract must be a dictionary" in e for e in res_1.errors)

    # 2. Missing prediction_source_contract
    m2 = json.loads(json.dumps(receipt))
    del m2["handback"]["prediction_source_contract"]
    m2["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m2)
    res_2 = verify_sitescore_gate2_receipt(m2, model_card_artifact=model_card)
    assert res_2.is_valid is False
    assert any("prediction_source_contract must be a dictionary" in e for e in res_2.errors)

    # 3. Missing handback_action
    m3 = json.loads(json.dumps(receipt))
    m3["handback"]["handback_action"] = ""
    m3["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m3)
    res_3 = verify_sitescore_gate2_receipt(m3, model_card_artifact=model_card)
    assert res_3.is_valid is False
    assert any("handback.handback_action must be a non-empty string" in e for e in res_3.errors)

    # 4. Count drift in outcome_backfill_contract.interval_bounds_count
    m4 = json.loads(json.dumps(receipt))
    m4["handback"]["outcome_backfill_contract"]["interval_bounds_count"] = 999
    m4["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m4)
    res_4 = verify_sitescore_gate2_receipt(m4, model_card_artifact=model_card)
    assert res_4.is_valid is False
    assert any("outcome_backfill_contract.interval_bounds_count" in e for e in res_4.errors)


def test_sitescore_verifier_rejects_synthetic_horizon_calibration_fields_b3():
    # B3 Re-review test: verify_sitescore_gate2_receipt fails closed when synthetic horizon calibration fields are injected
    result = run_benchmark_from_inventory(db_url=None, records=None)
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)

    m1 = json.loads(json.dumps(receipt))
    m1["benchmark_summary"]["calibration_summary"]["m1_interval_mae"] = 10.0
    m1["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m1)
    res_1 = verify_sitescore_gate2_receipt(m1, model_card_artifact=model_card)
    assert res_1.is_valid is False
    assert any("Forbidden or unsupported synthetic horizon calibration field" in e for e in res_1.errors)

    m2 = json.loads(json.dumps(receipt))
    m2["benchmark_summary"]["calibration_summary"]["m6_interval_mae"] = 5.0
    m2["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(m2)
    res_2 = verify_sitescore_gate2_receipt(m2, model_card_artifact=model_card)
    assert res_2.is_valid is False
    assert any("Forbidden or unsupported synthetic horizon calibration field" in e for e in res_2.errors)


def _rebind_receipt_hashes(receipt: dict[str, Any], mc_dict: dict[str, Any]) -> dict[str, Any]:
    from models.sitescore.opening_outcome import (
        compute_gate2_receipt_sha256,
        compute_handback_sha256,
        compute_model_card_sha256,
    )
    r = json.loads(json.dumps(receipt))
    hb_hash = compute_handback_sha256(r["handback"])
    mc_hash = compute_model_card_sha256(mc_dict)
    r["artifact_hashes"] = {
        "handback_hash": hb_hash,
        "model_card_hash": mc_hash,
    }
    r["integrity"] = {
        "content_sha256": "",
        "handback_hash": hb_hash,
        "model_card_hash": mc_hash,
    }
    r["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(r)
    return r


def test_sitescore_verifier_rejects_rebound_model_card_governance_and_metrics_drift_b1():
    # Codex6 B1 re-review test: hash-bound model card cannot invent validation_run_id or drift metrics/calibration
    result = run_benchmark_from_inventory(db_url=None, records=None)
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)

    # 1. Invented validation_run_id with rebound hashes
    mc1 = json.loads(json.dumps(model_card.to_dict()))
    mc1["validation_run_id"] = "invented-run"
    r1 = _rebind_receipt_hashes(receipt, mc1)
    res1 = verify_sitescore_gate2_receipt(r1, model_card_artifact=mc1)
    assert res1.is_valid is False
    assert any("validation_run_id must be 'UNVERIFIED'" in e for e in res1.errors)

    # 2. Drifting mature_label_count and normalized_mae in model_card.metrics_summary
    mc2 = json.loads(json.dumps(model_card.to_dict()))
    mc2["metrics_summary"]["mature_label_count"] = 999.0
    mc2["metrics_summary"]["normalized_mae"] = 0.123
    r2 = _rebind_receipt_hashes(receipt, mc2)
    res2 = verify_sitescore_gate2_receipt(r2, model_card_artifact=mc2)
    assert res2.is_valid is False
    assert any("drifts from summary" in e for e in res2.errors)

    # 3. Drifting calibration_summary in model_card
    mc3 = json.loads(json.dumps(model_card.to_dict()))
    mc3["calibration_summary"] = {"measured_90d_mae": 777.0}
    r3 = _rebind_receipt_hashes(receipt, mc3)
    res3 = verify_sitescore_gate2_receipt(r3, model_card_artifact=mc3)
    assert res3.is_valid is False
    assert any("model_card.calibration_summary drifts from summary.calibration_summary" in e for e in res3.errors)


def test_sitescore_verifier_rejects_rebound_no_handback_required_and_payload_drift_b2():
    # Codex6 B2 re-review test: self-consistent false booleans and payload drift are rejected
    result = run_benchmark_from_inventory(db_url=None, records=None)
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)

    # 1. Self-consistent mutation setting all receipt-required booleans to False in both handback copies
    mc_dict = model_card.to_dict()
    r1 = json.loads(json.dumps(receipt))
    for hb_copy in [r1["handback"], r1["benchmark_summary"]["handback_payload"]]:
        hb_copy["handback_required"] = False
        hb_copy["backfill_receipt_required"] = False
        hb_copy["outcome_backfill_contract"]["receipt_required"] = False
        hb_copy["prediction_source_contract"]["receipt_required"] = False
    r1_rebound = _rebind_receipt_hashes(r1, mc_dict)
    res1 = verify_sitescore_gate2_receipt(r1_rebound, model_card_artifact=mc_dict)
    assert res1.is_valid is False
    assert any("handback.handback_required to be True" in e for e in res1.errors)

    # 2. Deleting contracts only from benchmark_summary.handback_payload
    r2 = json.loads(json.dumps(receipt))
    del r2["benchmark_summary"]["handback_payload"]["outcome_backfill_contract"]
    del r2["benchmark_summary"]["handback_payload"]["prediction_source_contract"]
    r2_rebound = _rebind_receipt_hashes(r2, mc_dict)
    res2 = verify_sitescore_gate2_receipt(r2_rebound, model_card_artifact=mc_dict)
    assert res2.is_valid is False
    assert any("benchmark_summary.handback_payload drifts from handback" in e for e in res2.errors)

    # 3. Changing handback_action only in benchmark_summary.handback_payload
    r3 = json.loads(json.dumps(receipt))
    r3["benchmark_summary"]["handback_payload"]["handback_action"] = "forged action"
    r3_rebound = _rebind_receipt_hashes(r3, mc_dict)
    res3 = verify_sitescore_gate2_receipt(r3_rebound, model_card_artifact=mc_dict)
    assert res3.is_valid is False
    assert any("benchmark_summary.handback_payload drifts from handback" in e for e in res3.errors)


def test_sitescore_verifier_rejects_rebound_synthetic_segment_metrics_b3():
    # Codex6 B3 re-review test: synthetic horizon metric in segment_metrics is rejected across all surfaces
    recs = [
        {
            "entity_id": f"e_{i}",
            "store_id": f"s_{i}",
            "target_format_code": "CONVENIENCE",
            "opened_on": "2024-01-01",
            "is_training_eligible": True,
            "realized_90d_net_revenue": 100000.0,
            "store_age_days": 200,
            "predicted_revenue": 105000.0,
            "p10": 80000.0,
            "p90": 120000.0,
        }
        for i in range(10)
    ]
    res_bench = evaluate_sitescore_opening_outcome_benchmark(recs, provenance="authenticated_governed_records")
    mc = build_sitescore_opening_outcome_model_card(res_bench)
    rec = build_sitescore_gate2_receipt(res_bench, model_card=mc)
    mc_dict = mc.to_dict()

    r1 = json.loads(json.dumps(rec))
    r1["benchmark_summary"]["segment_metrics"][0]["metrics"]["m6_interval_mae"] = 1234.5
    r1["handback"]["segment_metrics"] = r1["benchmark_summary"]["segment_metrics"]
    r1_rebound = _rebind_receipt_hashes(r1, mc_dict)
    res1 = verify_sitescore_gate2_receipt(r1_rebound, model_card_artifact=mc_dict)
    assert res1.is_valid is False
    assert any("Forbidden or unsupported synthetic horizon calibration field" in e or "Forbidden or unsupported metric field" in e for e in res1.errors)


def test_sitescore_verifier_rejects_rebound_empty_model_card_metrics_and_missing_calibration_b1():
    # Codex6 B1 8e7ad006 re-review test: emptying model_card.metrics_summary or removing calibration_summary/segment_metrics fails closed
    result = run_benchmark_from_inventory(db_url=None, records=None)
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)

    # 1. Empty model_card.metrics_summary
    mc1 = json.loads(json.dumps(model_card.to_dict()))
    mc1["metrics_summary"] = {}
    r1 = _rebind_receipt_hashes(receipt, mc1)
    res1 = verify_sitescore_gate2_receipt(r1, model_card_artifact=mc1)
    assert res1.is_valid is False
    assert any("model_card.metrics_summary missing required metric key" in e for e in res1.errors)

    # 2. Removed calibration_summary from model_card
    mc2 = json.loads(json.dumps(model_card.to_dict()))
    del mc2["calibration_summary"]
    r2 = _rebind_receipt_hashes(receipt, mc2)
    res2 = verify_sitescore_gate2_receipt(r2, model_card_artifact=mc2)
    assert res2.is_valid is False
    assert any("model_card.calibration_summary must be a dictionary" in e for e in res2.errors)


def test_sitescore_verifier_rejects_rebound_invented_outcome_authority_lineage_freshness_b2():
    # Codex6 B2 8e7ad006 re-review test: invented outcome authority, lineage, freshness, or invalid timestamp fail closed
    result = run_benchmark_from_inventory(db_url=None, records=None)
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)
    mc_dict = model_card.to_dict()

    # 1. Invented outcome_backfill_contract placeholders in both handback copies
    r1 = json.loads(json.dumps(receipt))
    for hb_copy in [r1["handback"], r1["benchmark_summary"]["handback_payload"]]:
        hb_copy["outcome_backfill_contract"]["source_identity"] = "invented_source_identity"
        hb_copy["outcome_backfill_contract"]["freshness_timestamp"] = "2028-01-01T00:00:00Z"
        hb_copy["outcome_backfill_contract"]["evidence_owner"] = "attacker"
    r1_rebound = _rebind_receipt_hashes(r1, mc_dict)
    res1 = verify_sitescore_gate2_receipt(r1_rebound, model_card_artifact=mc_dict)
    assert res1.is_valid is False
    assert any("Governed-disabled outcome_backfill_contract" in e for e in res1.errors)

    # 2. Invented dataset_snapshot_id in benchmark_summary while governed-disabled
    r2 = json.loads(json.dumps(receipt))
    r2["benchmark_summary"]["dataset_snapshot_id"] = "invented_snapshot_id"
    r2_rebound = _rebind_receipt_hashes(r2, mc_dict)
    res2 = verify_sitescore_gate2_receipt(r2_rebound, model_card_artifact=mc_dict)
    assert res2.is_valid is False
    assert any("Governed-disabled receipt requires summary.dataset_snapshot_id to be None or 'UNAVAILABLE'" in e for e in res2.errors)

    # 3. Invalid top-level observed_at timestamp
    r3 = json.loads(json.dumps(receipt))
    r3["observed_at"] = "not-a-timestamp"
    r3_rebound = _rebind_receipt_hashes(r3, mc_dict)
    res3 = verify_sitescore_gate2_receipt(r3_rebound, model_card_artifact=mc_dict)
    assert res3.is_valid is False
    assert any("Invalid observed_at timestamp format" in e for e in res3.errors)


def test_sitescore_verifier_rejects_renamed_synthetic_horizon_metrics_b3():
    # Codex6 B3 8e7ad006 re-review test: renamed synthetic horizon metric m6_interval_mae_v2 in benchmark_summary is rejected
    result = run_benchmark_from_inventory(db_url=None, records=None)
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)
    mc_dict = model_card.to_dict()

    r1 = json.loads(json.dumps(receipt))
    r1["benchmark_summary"]["m6_interval_mae_v2"] = 12.34
    r1_rebound = _rebind_receipt_hashes(r1, mc_dict)
    res1 = verify_sitescore_gate2_receipt(r1_rebound, model_card_artifact=mc_dict)
    assert res1.is_valid is False
    assert any("Forbidden or unknown metric field in benchmark_summary" in e for e in res1.errors)


def test_sitescore_verifier_rejects_codex6_c2136d4c_probes_b1_b2_b3():
    """Explicitly verify that all 8 Codex6 c2136d4c re-review mutation probes fail closed after hash rebinding."""
    result = run_benchmark_from_inventory(db_url=None, records=None)
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)
    mc_dict = model_card.to_dict()

    # B1 Probe 1: empty calibration_summary dictionaries
    r_b1_1 = json.loads(json.dumps(receipt))
    mc_b1_1 = json.loads(json.dumps(mc_dict))
    r_b1_1["benchmark_summary"]["calibration_summary"] = {}
    mc_b1_1["calibration_summary"] = {}
    if "calibration_summary" in r_b1_1["handback"]:
        r_b1_1["handback"]["calibration_summary"] = {}
    if "calibration_summary" in r_b1_1["benchmark_summary"]["handback_payload"]:
        r_b1_1["benchmark_summary"]["handback_payload"]["calibration_summary"] = {}
    rebound_b1_1 = _rebind_receipt_hashes(r_b1_1, mc_b1_1)
    res_b1_1 = verify_sitescore_gate2_receipt(rebound_b1_1, model_card_artifact=mc_b1_1)
    assert res_b1_1.is_valid is False

    # B1 Probe 2: every allowed calibration value replaced with string "invented"
    r_b1_2 = json.loads(json.dumps(receipt))
    mc_b1_2 = json.loads(json.dumps(mc_dict))
    for k in r_b1_2["benchmark_summary"]["calibration_summary"].keys():
        r_b1_2["benchmark_summary"]["calibration_summary"][k] = "invented"
        mc_b1_2["calibration_summary"][k] = "invented"
    rebound_b1_2 = _rebind_receipt_hashes(r_b1_2, mc_b1_2)
    res_b1_2 = verify_sitescore_gate2_receipt(rebound_b1_2, model_card_artifact=mc_b1_2)
    assert res_b1_2.is_valid is False

    # B1 Probe 3: segment arrays replaced with [{"metrics": "invented"}]
    r_b1_3 = json.loads(json.dumps(receipt))
    mc_b1_3 = json.loads(json.dumps(mc_dict))
    r_b1_3["benchmark_summary"]["segment_metrics"] = [{"metrics": "invented"}]
    mc_b1_3["segment_metrics"] = [{"metrics": "invented"}]
    rebound_b1_3 = _rebind_receipt_hashes(r_b1_3, mc_b1_3)
    res_b1_3 = verify_sitescore_gate2_receipt(rebound_b1_3, model_card_artifact=mc_b1_3)
    assert res_b1_3.is_valid is False

    # B2 Probe 4: inventory_version="invented-authority-v99", source_contract="model_ready.candidate_site_view@invented-authority-v99", observed_at="2099-12-31T23:59:59Z"
    r_b2_4 = json.loads(json.dumps(receipt))
    mc_b2_4 = json.loads(json.dumps(mc_dict))
    r_b2_4["inventory_version"] = "invented-authority-v99"
    r_b2_4["source_contract"] = "model_ready.candidate_site_view@invented-authority-v99"
    r_b2_4["observed_at"] = "2099-12-31T23:59:59Z"
    mc_b2_4["created_at"] = "2099-12-31T23:59:59Z"
    rebound_b2_4 = _rebind_receipt_hashes(r_b2_4, mc_b2_4)
    res_b2_4 = verify_sitescore_gate2_receipt(rebound_b2_4, model_card_artifact=mc_b2_4)
    assert res_b2_4.is_valid is False

    # B3 Probe 5: m6_interval_mae_v2 added to both complete handback copies
    r_b3_5 = json.loads(json.dumps(receipt))
    mc_b3_5 = json.loads(json.dumps(mc_dict))
    r_b3_5["handback"]["m6_interval_mae_v2"] = 12.34
    r_b3_5["benchmark_summary"]["handback_payload"]["m6_interval_mae_v2"] = 12.34
    rebound_b3_5 = _rebind_receipt_hashes(r_b3_5, mc_b3_5)
    res_b3_5 = verify_sitescore_gate2_receipt(rebound_b3_5, model_card_artifact=mc_b3_5)
    assert res_b3_5.is_valid is False

    # B3 Probe 6: m6_interval_mae_v2 added inside both outcome_backfill_contract copies
    r_b3_6 = json.loads(json.dumps(receipt))
    mc_b3_6 = json.loads(json.dumps(mc_dict))
    r_b3_6["handback"]["outcome_backfill_contract"]["m6_interval_mae_v2"] = 12.34
    r_b3_6["benchmark_summary"]["handback_payload"]["outcome_backfill_contract"]["m6_interval_mae_v2"] = 12.34
    rebound_b3_6 = _rebind_receipt_hashes(r_b3_6, mc_b3_6)
    res_b3_6 = verify_sitescore_gate2_receipt(rebound_b3_6, model_card_artifact=mc_b3_6)
    assert res_b3_6.is_valid is False

    # B3 Probe 7: m6_interval_mae_v2 added at model-card top level
    r_b3_7 = json.loads(json.dumps(receipt))
    mc_b3_7 = json.loads(json.dumps(mc_dict))
    mc_b3_7["m6_interval_mae_v2"] = 12.34
    rebound_b3_7 = _rebind_receipt_hashes(r_b3_7, mc_b3_7)
    res_b3_7 = verify_sitescore_gate2_receipt(rebound_b3_7, model_card_artifact=mc_b3_7)
    assert res_b3_7.is_valid is False

    # B3 Probe 8: m6_interval_mae_v2 added at receipt top level
    r_b3_8 = json.loads(json.dumps(receipt))
    mc_b3_8 = json.loads(json.dumps(mc_dict))
    r_b3_8["m6_interval_mae_v2"] = 12.34
    rebound_b3_8 = _rebind_receipt_hashes(r_b3_8, mc_b3_8)
    res_b3_8 = verify_sitescore_gate2_receipt(rebound_b3_8, model_card_artifact=mc_b3_8)
    assert res_b3_8.is_valid is False


def test_sitescore_gate2_receipt_verifier_re_review_f3584866_probes_b1_b2_b3():
    # Negative regression test for Codex6 Re-review (f3584866 head) B1, B2, B3 findings
    result = run_benchmark_from_inventory(db_url=None, records=None)
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)
    mc_dict = model_card.to_dict()

    # B1 Probe 1: Remove top-level 'gate', 'model_name', or 'service' from receipt
    for missing_key in ["gate", "model_name", "service"]:
        r_b1_1 = json.loads(json.dumps(receipt))
        mc_b1_1 = json.loads(json.dumps(mc_dict))
        del r_b1_1[missing_key]
        rebound_b1_1 = _rebind_receipt_hashes(r_b1_1, mc_b1_1)
        res_b1_1 = verify_sitescore_gate2_receipt(rebound_b1_1, model_card_artifact=mc_b1_1)
        assert res_b1_1.is_valid is False
        assert any(f"Missing required field in top-level receipt: '{missing_key}'" in e for e in res_b1_1.errors)

    # B1 Probe 2: Remove prediction_source_contract.scope from handback
    r_b1_2 = json.loads(json.dumps(receipt))
    mc_b1_2 = json.loads(json.dumps(mc_dict))
    del r_b1_2["handback"]["prediction_source_contract"]["scope"]
    del r_b1_2["benchmark_summary"]["handback_payload"]["prediction_source_contract"]["scope"]
    rebound_b1_2 = _rebind_receipt_hashes(r_b1_2, mc_b1_2)
    res_b1_2 = verify_sitescore_gate2_receipt(rebound_b1_2, model_card_artifact=mc_b1_2)
    assert res_b1_2.is_valid is False
    assert any("Missing required field in prediction_source_contract: 'scope'" in e for e in res_b1_2.errors)

    # B1 Probe 3: Scalar typing - replace values in model_card.metrics_summary with numeric strings
    r_b1_3 = json.loads(json.dumps(receipt))
    mc_b1_3 = json.loads(json.dumps(mc_dict))
    for k in mc_b1_3["metrics_summary"]:
        mc_b1_3["metrics_summary"][k] = str(mc_b1_3["metrics_summary"][k])
    rebound_b1_3 = _rebind_receipt_hashes(r_b1_3, mc_b1_3)
    res_b1_3 = verify_sitescore_gate2_receipt(rebound_b1_3, model_card_artifact=mc_b1_3)
    assert res_b1_3.is_valid is False
    assert any("must be a real number (got str" in e for e in res_b1_3.errors)

    # B2 Probe 4: Stale evidence timestamp (2000-01-01T00:00:00Z) exceeds max evidence age (30 days)
    r_b2_4 = json.loads(json.dumps(receipt))
    mc_b2_4 = json.loads(json.dumps(mc_dict))
    r_b2_4["observed_at"] = "2000-01-01T00:00:00Z"
    r_b2_4["benchmark_summary"]["observed_at"] = "2000-01-01T00:00:00Z"
    mc_b2_4["created_at"] = "2000-01-01T00:00:00Z"
    rebound_b2_4 = _rebind_receipt_hashes(r_b2_4, mc_b2_4)
    res_b2_4 = verify_sitescore_gate2_receipt(rebound_b2_4, model_card_artifact=mc_b2_4)
    assert res_b2_4.is_valid is False
    assert any("is older than maximum evidence age" in e for e in res_b2_4.errors)

    # B3 Probe 5: Add m6_interval_mae_v2 inside artifact_hashes envelope
    r_b3_5 = json.loads(json.dumps(receipt))
    mc_b3_5 = json.loads(json.dumps(mc_dict))
    rebound_b3_5 = _rebind_receipt_hashes(r_b3_5, mc_b3_5)
    rebound_b3_5["artifact_hashes"]["m6_interval_mae_v2"] = 12.34
    rebound_b3_5["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(rebound_b3_5)
    res_b3_5 = verify_sitescore_gate2_receipt(rebound_b3_5, model_card_artifact=mc_b3_5)
    assert res_b3_5.is_valid is False
    assert any("Forbidden or unknown field in artifact_hashes: 'm6_interval_mae_v2'" in e for e in res_b3_5.errors)

    # B3 Probe 6: Add m6_interval_mae_v2 inside integrity envelope
    r_b3_6 = json.loads(json.dumps(receipt))
    mc_b3_6 = json.loads(json.dumps(mc_dict))
    rebound_b3_6 = _rebind_receipt_hashes(r_b3_6, mc_b3_6)
    rebound_b3_6["integrity"]["m6_interval_mae_v2"] = 12.34
    res_b3_6 = verify_sitescore_gate2_receipt(rebound_b3_6, model_card_artifact=mc_b3_6)
    assert res_b3_6.is_valid is False
    assert any("Forbidden or unknown field in integrity: 'm6_interval_mae_v2'" in e for e in res_b3_6.errors)


def test_sitescore_gate2_receipt_verifier_re_review_89bc4bd9_probes_b1_b2_b3():
    # Negative regression test for Codex6 Re-review (89bc4bd9 head) B1, B2, B3 findings
    result = run_benchmark_from_inventory(db_url=None, records=None)
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)
    mc_dict = model_card.to_dict()

    from models.sitescore.opening_outcome import compute_handback_sha256, compute_model_card_sha256

    def _rebind_hashes(r_obj: dict, mc_obj: dict) -> dict:
        hb_h = compute_handback_sha256(r_obj["handback"])
        mc_h = compute_model_card_sha256(mc_obj)
        r_obj["artifact_hashes"]["handback_hash"] = hb_h
        r_obj["artifact_hashes"]["model_card_hash"] = mc_h
        r_obj["integrity"]["handback_hash"] = hb_h
        r_obj["integrity"]["model_card_hash"] = mc_h
        r_obj["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(r_obj)
        return r_obj

    # 1. B1 Probe: Invented gate identity and model identity
    r1 = json.loads(json.dumps(receipt))
    mc1 = json.loads(json.dumps(mc_dict))
    r1["gate"] = "GATE_99"
    r1["model_name"] = "invented_model"
    r1["service"] = "invented_service"
    mc1["model_name"] = "invented_model"
    rebound1 = _rebind_hashes(r1, mc1)
    res1 = verify_sitescore_gate2_receipt(rebound1, model_card_artifact=mc1)
    assert res1.is_valid is False
    assert any("Forbidden or unauthenticated gate" in e or "model_name" in e for e in res1.errors)

    # 2. B1 Probe: Altered governed thresholds
    r2 = json.loads(json.dumps(receipt))
    mc2 = json.loads(json.dumps(mc_dict))
    r2["benchmark_summary"]["activation_threshold"] = 1
    r2["benchmark_summary"]["min_coverage_threshold"] = 0.0
    r2["benchmark_summary"]["max_mae_threshold"] = 999.0
    r2["handback"]["activation_threshold"] = 1
    r2["benchmark_summary"]["handback_payload"]["activation_threshold"] = 1
    rebound2 = _rebind_hashes(r2, mc2)
    res2 = verify_sitescore_gate2_receipt(rebound2, model_card_artifact=mc2)
    assert res2.is_valid is False
    assert any("drifts from governed constant" in e for e in res2.errors)

    # 3. B2 Probe: Stale/drifting benchmark_summary.observed_at
    r3 = json.loads(json.dumps(receipt))
    mc3 = json.loads(json.dumps(mc_dict))
    r3["benchmark_summary"]["observed_at"] = "2000-01-01T00:00:00Z"
    rebound3 = _rebind_hashes(r3, mc3)
    res3 = verify_sitescore_gate2_receipt(rebound3, model_card_artifact=mc3)
    assert res3.is_valid is False
    assert any("benchmark_summary.observed_at" in e for e in res3.errors)

    # 4. B2 Probe: Store-age-only M6/M12 maturity definition in outcome_backfill_contract
    r4 = json.loads(json.dumps(receipt))
    mc4 = json.loads(json.dumps(mc_dict))
    r4["handback"]["outcome_backfill_contract"]["m6_maturity_definition"] = "store_age_days >= 180; no realized outcome required"
    r4["handback"]["outcome_backfill_contract"]["m12_maturity_definition"] = "store_age_days >= 365; no realized outcome required"
    r4["benchmark_summary"]["handback_payload"]["outcome_backfill_contract"]["m6_maturity_definition"] = "store_age_days >= 180; no realized outcome required"
    r4["benchmark_summary"]["handback_payload"]["outcome_backfill_contract"]["m12_maturity_definition"] = "store_age_days >= 365; no realized outcome required"
    rebound4 = _rebind_hashes(r4, mc4)
    res4 = verify_sitescore_gate2_receipt(rebound4, model_card_artifact=mc4)
    assert res4.is_valid is False
    assert any("mismatch" in e for e in res4.errors)

    # 5. B3 Probe: Non-float string matched_mean_y
    r5 = json.loads(json.dumps(receipt))
    mc5 = json.loads(json.dumps(mc_dict))
    r5["benchmark_summary"]["matched_mean_y"] = "invented"
    r5["handback"]["matched_mean_y"] = "invented"
    r5["benchmark_summary"]["handback_payload"]["matched_mean_y"] = "invented"
    rebound5 = _rebind_hashes(r5, mc5)
    res5 = verify_sitescore_gate2_receipt(rebound5, model_card_artifact=mc5)
    assert res5.is_valid is False
    assert any("must be a real number" in e for e in res5.errors)

    # 6. B3 Probe: Calibration summary matched prediction count drift when main mature_label_count is 0
    r6 = json.loads(json.dumps(receipt))
    mc6 = json.loads(json.dumps(mc_dict))
    r6["benchmark_summary"]["calibration_summary"]["matched_prediction_count"] = 999
    mc6["calibration_summary"]["matched_prediction_count"] = 999
    rebound6 = _rebind_hashes(r6, mc6)
    res6 = verify_sitescore_gate2_receipt(rebound6, model_card_artifact=mc6)
    assert res6.is_valid is False
    assert any("drifts from summary.matched_prediction_count" in e for e in res6.errors)

    # 7. B3 Probe: Segment metric record count exceeds main mature_label_count
    r7 = json.loads(json.dumps(receipt))
    mc7 = json.loads(json.dumps(mc_dict))
    bogus_seg = [{
        "segment_name": "target_format_code",
        "segment_value": "CONVENIENCE_STANDARD",
        "record_count": 999,
        "metrics": {"mae": 0.0, "m6_coverage": 0.0, "m12_coverage": 0.0, "prediction_coverage": 0.0}
    }]
    r7["benchmark_summary"]["segment_metrics"] = bogus_seg
    r7["handback"]["segment_metrics"] = bogus_seg
    r7["benchmark_summary"]["handback_payload"]["segment_metrics"] = bogus_seg
    mc7["segment_metrics"] = bogus_seg
    rebound7 = _rebind_hashes(r7, mc7)
    res7 = verify_sitescore_gate2_receipt(rebound7, model_card_artifact=mc7)
    assert res7.is_valid is False
    assert any("exceeds mature_label_count" in e or "> 0 when mature_label_count is 0" in e for e in res7.errors)


def test_sitescore_gate2_receipt_verifier_re_review_58be4d4e_probes_b1_b2_b3():
    # Negative regression test for Codex6 Re-review (58be4d4e head) B1, B2, B3 findings:
    # B1: benchmark_summary.observed_at missing / optional or drifting
    # B2: calibration mean_realized_revenue=999999 or measured_90d_mae=999999
    # B3: segment total incomplete / smaller than mature_label_count, arbitrary segment MAE/coverage, duplicate segment values
    from models.sitescore.opening_outcome import compute_handback_sha256, compute_model_card_sha256

    result = run_benchmark_from_inventory(db_url=None, records=None)
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)
    mc_dict = model_card.to_dict()

    def _rebind_hashes(r_obj: dict, mc_obj: dict) -> dict:
        hb_h = compute_handback_sha256(r_obj["handback"])
        mc_h = compute_model_card_sha256(mc_obj)
        r_obj["artifact_hashes"]["handback_hash"] = hb_h
        r_obj["artifact_hashes"]["model_card_hash"] = mc_h
        r_obj["integrity"]["handback_hash"] = hb_h
        r_obj["integrity"]["model_card_hash"] = mc_h
        r_obj["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(r_obj)
        return r_obj

    # 1. B1 Probe: Missing benchmark_summary.observed_at
    r_b1_1 = json.loads(json.dumps(receipt))
    mc_b1_1 = json.loads(json.dumps(mc_dict))
    del r_b1_1["benchmark_summary"]["observed_at"]
    rebound_b1_1 = _rebind_hashes(r_b1_1, mc_b1_1)
    res_b1_1 = verify_sitescore_gate2_receipt(rebound_b1_1, model_card_artifact=mc_b1_1)
    assert res_b1_1.is_valid is False
    assert any("Missing required field in benchmark_summary: 'observed_at'" in e for e in res_b1_1.errors)

    # 2. B1 Probe: Drifting benchmark_summary.observed_at from receipt.observed_at
    r_b1_2 = json.loads(json.dumps(receipt))
    mc_b1_2 = json.loads(json.dumps(mc_dict))
    r_b1_2["benchmark_summary"]["observed_at"] = "2026-07-31T00:00:00Z"
    r_b1_2["observed_at"] = "2026-07-31T12:00:00Z"
    rebound_b1_2 = _rebind_hashes(r_b1_2, mc_b1_2)
    res_b1_2 = verify_sitescore_gate2_receipt(rebound_b1_2, model_card_artifact=mc_b1_2)
    assert res_b1_2.is_valid is False
    assert any("drifts from top-level observed_at" in e for e in res_b1_2.errors)

    # 3. B2 Probe: mean_realized_revenue set to 999999.0 when mature_label_count is 0
    r_b2_1 = json.loads(json.dumps(receipt))
    mc_b2_1 = json.loads(json.dumps(mc_dict))
    r_b2_1["benchmark_summary"]["calibration_summary"]["mean_realized_revenue"] = 999999.0
    if "calibration_summary" in r_b2_1["handback"]:
        r_b2_1["handback"]["calibration_summary"]["mean_realized_revenue"] = 999999.0
    if "calibration_summary" in r_b2_1["benchmark_summary"]["handback_payload"]:
        r_b2_1["benchmark_summary"]["handback_payload"]["calibration_summary"]["mean_realized_revenue"] = 999999.0
    mc_b2_1["calibration_summary"]["mean_realized_revenue"] = 999999.0
    rebound_b2_1 = _rebind_hashes(r_b2_1, mc_b2_1)
    res_b2_1 = verify_sitescore_gate2_receipt(rebound_b2_1, model_card_artifact=mc_b2_1)
    assert res_b2_1.is_valid is False
    assert any("mean_realized_revenue must be 0.0 when mature_label_count is 0" in e for e in res_b2_1.errors)

    # 4. B2 Probe: On a 2-record benchmark, measured_90d_mae modified to 999999.0 while leaving normalized_mae and matched_mean_y unchanged
    rec_sample = [
        {
            "entity_id": "site-1",
            "store_id": "101",
            "target_format_code": "STANDARD",
            "opened_on": "2024-01-01",
            "is_training_eligible": True,
            "realized_90d_net_revenue": 100.0,
            "realized_180d_net_revenue": 200.0,
            "realized_365d_net_revenue": 400.0,
            "store_age_days": 400,
            "predicted_revenue": 110.0,
            "p10": 80.0,
            "p90": 120.0,
        },
        {
            "entity_id": "site-2",
            "store_id": "102",
            "target_format_code": "STANDARD",
            "opened_on": "2024-01-01",
            "is_training_eligible": True,
            "realized_90d_net_revenue": 200.0,
            "realized_180d_net_revenue": 400.0,
            "realized_365d_net_revenue": 800.0,
            "store_age_days": 400,
            "predicted_revenue": 190.0,
            "p10": 160.0,
            "p90": 240.0,
        },
    ]
    res_2rec = evaluate_sitescore_opening_outcome_benchmark(rec_sample, provenance="provided_records")
    mc_2rec = build_sitescore_opening_outcome_model_card(res_2rec)
    r_2rec = build_sitescore_gate2_receipt(res_2rec, model_card=mc_2rec)
    mc_2rec_dict = mc_2rec.to_dict()

    r_b2_2 = json.loads(json.dumps(r_2rec))
    mc_b2_2 = json.loads(json.dumps(mc_2rec_dict))
    r_b2_2["benchmark_summary"]["calibration_summary"]["measured_90d_mae"] = 999999.0
    if "calibration_summary" in r_b2_2["handback"]:
        r_b2_2["handback"]["calibration_summary"]["measured_90d_mae"] = 999999.0
    if "calibration_summary" in r_b2_2["benchmark_summary"]["handback_payload"]:
        r_b2_2["benchmark_summary"]["handback_payload"]["calibration_summary"]["measured_90d_mae"] = 999999.0
    mc_b2_2["calibration_summary"]["measured_90d_mae"] = 999999.0
    rebound_b2_2 = _rebind_hashes(r_b2_2, mc_b2_2)
    res_b2_2 = verify_sitescore_gate2_receipt(rebound_b2_2, model_card_artifact=mc_b2_2)
    assert res_b2_2.is_valid is False
    assert any("drifts from expected MAE" in e for e in res_b2_2.errors)

    # 5. B3 Probe: Dropping one segment record on a 2-record benchmark (total_seg_cnt != mature_label_count)
    r_b3_1 = json.loads(json.dumps(r_2rec))
    mc_b3_1 = json.loads(json.dumps(mc_2rec_dict))
    seg_dropped = [{
        "segment_name": "target_format_code",
        "segment_value": "STANDARD",
        "record_count": 1,
        "metrics": {"mae": 10.0, "m6_coverage": 1.0, "m12_coverage": 1.0, "prediction_coverage": 1.0}
    }]
    r_b3_1["benchmark_summary"]["segment_metrics"] = seg_dropped
    r_b3_1["handback"]["segment_metrics"] = seg_dropped
    r_b3_1["benchmark_summary"]["handback_payload"]["segment_metrics"] = seg_dropped
    mc_b3_1["segment_metrics"] = seg_dropped
    rebound_b3_1 = _rebind_hashes(r_b3_1, mc_b3_1)
    res_b3_1 = verify_sitescore_gate2_receipt(rebound_b3_1, model_card_artifact=mc_b3_1)
    assert res_b3_1.is_valid is False
    assert any("does not match mature_label_count" in e for e in res_b3_1.errors)

    # 6. B3 Probe: Replacing segment MAE and coverage with arbitrary values
    r_b3_2 = json.loads(json.dumps(r_2rec))
    mc_b3_2 = json.loads(json.dumps(mc_2rec_dict))
    seg_arbitrary = [{
        "segment_name": "target_format_code",
        "segment_value": "STANDARD",
        "record_count": 2,
        "metrics": {"mae": 999.0, "m6_coverage": 0.5, "m12_coverage": 0.5, "prediction_coverage": 0.5}
    }]
    r_b3_2["benchmark_summary"]["segment_metrics"] = seg_arbitrary
    r_b3_2["handback"]["segment_metrics"] = seg_arbitrary
    r_b3_2["benchmark_summary"]["handback_payload"]["segment_metrics"] = seg_arbitrary
    mc_b3_2["segment_metrics"] = seg_arbitrary
    rebound_b3_2 = _rebind_hashes(r_b3_2, mc_b3_2)
    res_b3_2 = verify_sitescore_gate2_receipt(rebound_b3_2, model_card_artifact=mc_b3_2)
    assert res_b3_2.is_valid is False
    assert any("drifts from" in e for e in res_b3_2.errors)

    # 7. B3 Probe: Duplicate segment_value in same partition
    r_b3_3 = json.loads(json.dumps(r_2rec))
    mc_b3_3 = json.loads(json.dumps(mc_2rec_dict))
    seg_dupes = [
        {
            "segment_name": "target_format_code",
            "segment_value": "STANDARD",
            "record_count": 1,
            "metrics": {"mae": 10.0, "m6_coverage": 1.0, "m12_coverage": 1.0, "prediction_coverage": 1.0}
        },
        {
            "segment_name": "target_format_code",
            "segment_value": "STANDARD",
            "record_count": 1,
            "metrics": {"mae": 10.0, "m6_coverage": 1.0, "m12_coverage": 1.0, "prediction_coverage": 1.0}
        },
    ]
    r_b3_3["benchmark_summary"]["segment_metrics"] = seg_dupes
    r_b3_3["handback"]["segment_metrics"] = seg_dupes
    r_b3_3["benchmark_summary"]["handback_payload"]["segment_metrics"] = seg_dupes
    mc_b3_3["segment_metrics"] = seg_dupes
    rebound_b3_3 = _rebind_hashes(r_b3_3, mc_b3_3)
    res_b3_3 = verify_sitescore_gate2_receipt(rebound_b3_3, model_card_artifact=mc_b3_3)
    assert res_b3_3.is_valid is False
    assert any("Duplicate segment_value" in e for e in res_b3_3.errors)


def test_sitescore_gate2_receipt_verifier_re_review_e94db743_probes_b1_b2_b3_b4():
    # Negative regression test for Codex6 Re-review (e94db743 head) B1, B2, B3, B4 findings
    from models.sitescore.opening_outcome import compute_handback_sha256, compute_model_card_sha256

    result = run_benchmark_from_inventory(db_url=None, records=None)
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)
    mc_dict = model_card.to_dict()

    def _rebind_hashes(r_obj: dict, mc_obj: dict) -> dict:
        hb_h = compute_handback_sha256(r_obj["handback"])
        mc_h = compute_model_card_sha256(mc_obj)
        r_obj["artifact_hashes"]["handback_hash"] = hb_h
        r_obj["artifact_hashes"]["model_card_hash"] = mc_h
        r_obj["integrity"]["handback_hash"] = hb_h
        r_obj["integrity"]["model_card_hash"] = mc_h
        r_obj["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(r_obj)
        return r_obj

    # 1. B1 Probe: Partially matched population mean_realized_revenue reconciliation
    rec_partial = [
        {
            "entity_id": "site-1",
            "store_id": "101",
            "target_format_code": "STANDARD",
            "opened_on": "2024-01-01",
            "is_training_eligible": True,
            "realized_90d_net_revenue": 100.0,
            "realized_180d_net_revenue": 200.0,
            "realized_365d_net_revenue": 400.0,
            "store_age_days": 400,
            "predicted_revenue": 110.0,
            "p10": 80.0,
            "p90": 120.0,
        },
        {
            "entity_id": "site-2",
            "store_id": "102",
            "target_format_code": "STANDARD",
            "opened_on": "2024-01-01",
            "is_training_eligible": True,
            "realized_90d_net_revenue": 300.0,
            "realized_180d_net_revenue": 600.0,
            "realized_365d_net_revenue": 1200.0,
            "store_age_days": 400,
            "predicted_revenue": None,
        },
    ]
    res_partial = evaluate_sitescore_opening_outcome_benchmark(rec_partial, provenance="provided_records")
    assert res_partial.mature_label_count == 2
    assert res_partial.matched_prediction_count == 1
    assert res_partial.mean_realized_revenue == 200.0

    mc_part = build_sitescore_opening_outcome_model_card(res_partial)
    r_part = build_sitescore_gate2_receipt(res_partial, model_card=mc_part)
    mc_part_dict = mc_part.to_dict()

    r_b1 = json.loads(json.dumps(r_part))
    mc_b1 = json.loads(json.dumps(mc_part_dict))
    r_b1["benchmark_summary"]["mean_realized_revenue"] = 999999.0
    r_b1["handback"]["mean_realized_revenue"] = 999999.0
    r_b1["benchmark_summary"]["handback_payload"]["mean_realized_revenue"] = 999999.0
    r_b1["benchmark_summary"]["calibration_summary"]["mean_realized_revenue"] = 999999.0
    if "calibration_summary" in r_b1["handback"]:
        r_b1["handback"]["calibration_summary"]["mean_realized_revenue"] = 999999.0
    if "calibration_summary" in r_b1["benchmark_summary"]["handback_payload"]:
        r_b1["benchmark_summary"]["handback_payload"]["calibration_summary"]["mean_realized_revenue"] = 999999.0
    mc_b1["calibration_summary"]["mean_realized_revenue"] = 999999.0

    rebound_b1 = _rebind_hashes(r_b1, mc_b1)
    res_b1 = verify_sitescore_gate2_receipt(rebound_b1, model_card_artifact=mc_b1)
    assert res_b1.is_valid is False
    assert any("mean_realized_revenue" in e for e in res_b1.errors)

    # 2. B2 Probe: Empty segment set [] on mature population (mature_label_count = 2)
    r_b2 = json.loads(json.dumps(r_part))
    mc_b2 = json.loads(json.dumps(mc_part_dict))
    r_b2["benchmark_summary"]["segment_metrics"] = []
    r_b2["handback"]["segment_metrics"] = []
    r_b2["benchmark_summary"]["handback_payload"]["segment_metrics"] = []
    mc_b2["segment_metrics"] = []
    rebound_b2 = _rebind_hashes(r_b2, mc_b2)
    res_b2 = verify_sitescore_gate2_receipt(rebound_b2, model_card_artifact=mc_b2)
    assert res_b2.is_valid is False
    assert any("cannot be empty when mature_label_count" in e for e in res_b2.errors)

    # 3. B3 Probe: Integer substitution for is_gate2_passed (0 instead of False)
    r_b3 = json.loads(json.dumps(receipt))
    mc_b3 = json.loads(json.dumps(mc_dict))
    r_b3["benchmark_summary"]["is_gate2_passed"] = 0
    rebound_b3 = _rebind_hashes(r_b3, mc_b3)
    res_b3 = verify_sitescore_gate2_receipt(rebound_b3, model_card_artifact=mc_b3)
    assert res_b3.is_valid is False
    assert any("is_gate2_passed must be a boolean" in e for e in res_b3.errors)

    # 4. B4 Probe: Erased/corrupted handback fields
    # 4a: missing_labels_delta = 0 when mature_label_count = 0 (expected 200)
    r_b4a = json.loads(json.dumps(receipt))
    mc_b4a = json.loads(json.dumps(mc_dict))
    r_b4a["handback"]["missing_labels_delta"] = 0
    r_b4a["benchmark_summary"]["handback_payload"]["missing_labels_delta"] = 0
    rebound_b4a = _rebind_hashes(r_b4a, mc_b4a)
    res_b4a = verify_sitescore_gate2_receipt(rebound_b4a, model_card_artifact=mc_b4a)
    assert res_b4a.is_valid is False
    assert any("missing_labels_delta" in e for e in res_b4a.errors)

    # 4b: reasons = [] on governed-disabled receipt
    r_b4b = json.loads(json.dumps(receipt))
    mc_b4b = json.loads(json.dumps(mc_dict))
    r_b4b["handback"]["reasons"] = []
    r_b4b["benchmark_summary"]["handback_payload"]["reasons"] = []
    rebound_b4b = _rebind_hashes(r_b4b, mc_b4b)
    res_b4b = verify_sitescore_gate2_receipt(rebound_b4b, model_card_artifact=mc_b4b)
    assert res_b4b.is_valid is False
    assert any("Governed-disabled receipt requires a non-empty handback.reasons list" in e for e in res_b4b.errors)

    # 4c: handback_action = "x" (generic placeholder missing task IDs)
    r_b4c = json.loads(json.dumps(receipt))
    mc_b4c = json.loads(json.dumps(mc_dict))
    r_b4c["handback"]["handback_action"] = "x"
    r_b4c["benchmark_summary"]["handback_payload"]["handback_action"] = "x"
    rebound_b4c = _rebind_hashes(r_b4c, mc_b4c)
    res_b4c = verify_sitescore_gate2_receipt(rebound_b4c, model_card_artifact=mc_b4c)
    assert res_b4c.is_valid is False
    assert any("drifts from expected re-derived action" in e or "handback_action must identify both governed task IDs" in e for e in res_b4c.errors)


def test_sitescore_gate2_receipt_verifier_re_review_97043588_probes_b1_b2_b3():
    # Negative regression test for Codex6 Re-review (97043588 head) B1, B2, B3 findings
    from models.sitescore.opening_outcome import (
        compute_handback_sha256,
        compute_model_card_sha256,
    )

    result = run_benchmark_from_inventory(db_url=None, records=None)
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)
    mc_dict = model_card.to_dict()

    def _rebind_hashes(r_obj: dict, mc_obj: dict) -> dict:
        hb_h = compute_handback_sha256(r_obj["handback"])
        mc_h = compute_model_card_sha256(mc_obj)
        r_obj["artifact_hashes"]["handback_hash"] = hb_h
        r_obj["artifact_hashes"]["model_card_hash"] = mc_h
        r_obj["integrity"]["handback_hash"] = hb_h
        r_obj["integrity"]["model_card_hash"] = mc_h
        r_obj["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(r_obj)
        return r_obj

    # 1. B1 Probe: Partially matched population revenue sum forging attempt
    rec_partial = [
        {
            "entity_id": "site-1",
            "store_id": "101",
            "target_format_code": "STANDARD",
            "opened_on": "2024-01-01",
            "is_training_eligible": True,
            "realized_90d_net_revenue": 100.0,
            "realized_180d_net_revenue": 200.0,
            "realized_365d_net_revenue": 400.0,
            "store_age_days": 400,
            "predicted_revenue": 110.0,
            "p10": 80.0,
            "p90": 120.0,
        },
        {
            "entity_id": "site-2",
            "store_id": "102",
            "target_format_code": "STANDARD",
            "opened_on": "2024-01-01",
            "is_training_eligible": True,
            "realized_90d_net_revenue": 300.0,
            "realized_180d_net_revenue": 600.0,
            "realized_365d_net_revenue": 1200.0,
            "store_age_days": 400,
            "predicted_revenue": None,
        },
    ]
    res_partial = evaluate_sitescore_opening_outcome_benchmark(rec_partial, provenance="provided_records")
    assert res_partial.mature_label_count == 2
    assert res_partial.matched_prediction_count == 1
    assert res_partial.realized_revenue_sum == 400.0
    assert res_partial.matched_mean_y == 100.0
    assert res_partial.unmatched_mean_y == 300.0

    mc_part = build_sitescore_opening_outcome_model_card(res_partial)
    r_part = build_sitescore_gate2_receipt(res_partial, model_card=mc_part)
    mc_part_dict = mc_part.to_dict()

    r_b1 = json.loads(json.dumps(r_part))
    mc_b1 = json.loads(json.dumps(mc_part_dict))
    r_b1["benchmark_summary"]["realized_revenue_sum"] = 200000.0
    r_b1["benchmark_summary"]["mean_realized_revenue"] = 100000.0
    r_b1["handback"]["realized_revenue_sum"] = 200000.0
    r_b1["handback"]["mean_realized_revenue"] = 100000.0
    r_b1["benchmark_summary"]["handback_payload"]["realized_revenue_sum"] = 200000.0
    r_b1["benchmark_summary"]["handback_payload"]["mean_realized_revenue"] = 100000.0
    r_b1["benchmark_summary"]["calibration_summary"]["mean_realized_revenue"] = 100000.0
    if "calibration_summary" in r_b1["handback"]:
        r_b1["handback"]["calibration_summary"]["mean_realized_revenue"] = 100000.0
    if "calibration_summary" in r_b1["benchmark_summary"]["handback_payload"]:
        r_b1["benchmark_summary"]["handback_payload"]["calibration_summary"]["mean_realized_revenue"] = 100000.0
    mc_b1["calibration_summary"]["mean_realized_revenue"] = 100000.0

    rebound_b1 = _rebind_hashes(r_b1, mc_b1)
    res_b1 = verify_sitescore_gate2_receipt(rebound_b1, model_card_artifact=mc_b1)
    assert res_b1.is_valid is False
    assert any("drifts from expected sum" in e for e in res_b1.errors)

    # 2. B2 Probe: Negative segment population record_count (-2) in mixed partition [2, 2, -2]
    r_b2 = json.loads(json.dumps(r_part))
    mc_b2 = json.loads(json.dumps(mc_part_dict))
    bogus_seg_b2 = [
        {
            "segment_name": "target_format_code",
            "segment_value": "STANDARD_A",
            "record_count": 2,
            "metrics": {"mae": 10.0, "m6_coverage": 1.0, "m12_coverage": 1.0, "prediction_coverage": 0.5},
        },
        {
            "segment_name": "target_format_code",
            "segment_value": "STANDARD_B",
            "record_count": 2,
            "metrics": {"mae": 10.0, "m6_coverage": 1.0, "m12_coverage": 1.0, "prediction_coverage": 0.5},
        },
        {
            "segment_name": "target_format_code",
            "segment_value": "STANDARD_C",
            "record_count": -2,
            "metrics": {"mae": 10.0, "m6_coverage": 1.0, "m12_coverage": 1.0, "prediction_coverage": 0.5},
        },
    ]
    r_b2["benchmark_summary"]["segment_metrics"] = bogus_seg_b2
    r_b2["handback"]["segment_metrics"] = bogus_seg_b2
    r_b2["benchmark_summary"]["handback_payload"]["segment_metrics"] = bogus_seg_b2
    mc_b2["segment_metrics"] = bogus_seg_b2
    rebound_b2 = _rebind_hashes(r_b2, mc_b2)
    res_b2 = verify_sitescore_gate2_receipt(rebound_b2, model_card_artifact=mc_b2)
    assert res_b2.is_valid is False
    assert any("must be positive (> 0)" in e for e in res_b2.errors)

    # 3. B3 Probe: Tampering with handback reasons and handback_action text to pretend Gate 2 passed
    r_b3 = json.loads(json.dumps(receipt))
    mc_b3 = json.loads(json.dumps(mc_dict))
    r_b3["handback"]["reasons"] = ["All evidence is authoritative and Gate 2 passed"]
    r_b3["benchmark_summary"]["handback_payload"]["reasons"] = ["All evidence is authoritative and Gate 2 passed"]
    r_b3["handback"]["handback_action"] = "Close ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001 and ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001 because no work is required"
    r_b3["benchmark_summary"]["handback_payload"]["handback_action"] = "Close ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001 and ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001 because no work is required"
    rebound_b3 = _rebind_hashes(r_b3, mc_b3)
    res_b3 = verify_sitescore_gate2_receipt(rebound_b3, model_card_artifact=mc_b3)
    assert res_b3.is_valid is False
    assert any("contains contradictory active status text" in e or "handback.reasons mismatch" in e for e in res_b3.errors)


def test_sitescore_gate2_receipt_verifier_re_review_d20d7483_probes_b1_b2_b3():
    # Negative regression test for Codex6 Re-review (d20d7483 review anchor) B1, B2, B3 findings
    from models.sitescore.opening_outcome import (
        compute_handback_sha256,
        compute_model_card_sha256,
    )

    result = run_benchmark_from_inventory(db_url=None, records=None)
    model_card = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=model_card)
    mc_dict = model_card.to_dict()

    def _rebind_hashes(r_obj: dict, mc_obj: dict) -> dict:
        hb_h = compute_handback_sha256(r_obj["handback"])
        mc_h = compute_model_card_sha256(mc_obj)
        r_obj["artifact_hashes"]["handback_hash"] = hb_h
        r_obj["artifact_hashes"]["model_card_hash"] = mc_h
        r_obj["integrity"]["handback_hash"] = hb_h
        r_obj["integrity"]["model_card_hash"] = mc_h
        r_obj["integrity"]["content_sha256"] = compute_gate2_receipt_sha256(r_obj)
        return r_obj

    # 1. B1 Probe: Self-attested unmatched population aggregate mean/sum forgery attempt
    rec_partial = [
        {
            "entity_id": "site-1",
            "store_id": "101",
            "target_format_code": "STANDARD",
            "opened_on": "2024-01-01",
            "is_training_eligible": True,
            "realized_90d_net_revenue": 100.0,
            "realized_180d_net_revenue": 200.0,
            "realized_365d_net_revenue": 400.0,
            "store_age_days": 400,
            "predicted_revenue": 100.0,
            "p10": 80.0,
            "p90": 120.0,
        },
        {
            "entity_id": "site-2",
            "store_id": "102",
            "target_format_code": "STANDARD",
            "opened_on": "2024-01-01",
            "is_training_eligible": True,
            "realized_90d_net_revenue": 300.0,
            "realized_180d_net_revenue": 600.0,
            "realized_365d_net_revenue": 1200.0,
            "store_age_days": 400,
            "predicted_revenue": None,
        },
    ]
    res_partial = evaluate_sitescore_opening_outcome_benchmark(rec_partial, provenance="provided_records")
    assert res_partial.mature_label_count == 2
    assert res_partial.matched_prediction_count == 1
    assert res_partial.realized_revenue_sum == 400.0
    assert res_partial.matched_mean_y == 100.0
    assert res_partial.unmatched_mean_y == 300.0

    mc_part = build_sitescore_opening_outcome_model_card(res_partial)
    r_part = build_sitescore_gate2_receipt(res_partial, model_card=mc_part)
    mc_part_dict = mc_part.to_dict()

    # Mutate unmatched_mean_y to 999999.0 and adjust realized_revenue_sum to match arithmetic self-consistency
    r_b1 = json.loads(json.dumps(r_part))
    mc_b1 = json.loads(json.dumps(mc_part_dict))
    r_b1["benchmark_summary"]["unmatched_mean_y"] = 999999.0
    r_b1["handback"]["unmatched_mean_y"] = 999999.0
    r_b1["benchmark_summary"]["handback_payload"]["unmatched_mean_y"] = 999999.0
    for key in ("realized_revenue_sum", "mean_realized_revenue"):
        r_b1["benchmark_summary"][key] = 1000099.0 if key == "realized_revenue_sum" else 500049.5
        r_b1["handback"][key] = 1000099.0 if key == "realized_revenue_sum" else 500049.5
        r_b1["benchmark_summary"]["handback_payload"][key] = 1000099.0 if key == "realized_revenue_sum" else 500049.5
        r_b1["handback"]["outcome_backfill_contract"][key] = 1000099.0 if key == "realized_revenue_sum" else 500049.5
        r_b1["benchmark_summary"]["handback_payload"]["outcome_backfill_contract"][key] = 1000099.0 if key == "realized_revenue_sum" else 500049.5

    rebound_b1 = _rebind_hashes(r_b1, mc_b1)
    res_b1 = verify_sitescore_gate2_receipt(rebound_b1, model_card_artifact=mc_b1, dataset_manifest=rec_partial)
    assert res_b1.is_valid is False
    assert any("population_aggregate_digest mismatch" in e for e in res_b1.errors)

    # 2. B2 Probe: Handback reasons and handback_action semantic alteration attempt
    r_b2 = json.loads(json.dumps(receipt))
    mc_b2 = json.loads(json.dumps(mc_dict))
    r_b2["handback"]["reasons"] = ["All authoritative evidence is complete; both governed tasks may be closed."]
    r_b2["benchmark_summary"]["handback_payload"]["reasons"] = ["All authoritative evidence is complete; both governed tasks may be closed."]
    r_b2["handback"]["handback_action"] = "All authoritative evidence is complete; both governed tasks may be closed."
    r_b2["benchmark_summary"]["handback_payload"]["handback_action"] = "All authoritative evidence is complete; both governed tasks may be closed."
    rebound_b2 = _rebind_hashes(r_b2, mc_b2)
    res_b2 = verify_sitescore_gate2_receipt(rebound_b2, model_card_artifact=mc_b2)
    assert res_b2.is_valid is False
    assert any("handback.reasons mismatch" in e or "handback.handback_action mismatch" in e for e in res_b2.errors)

    # 3. B3 Probe: Strict boolean non-boolean eligibility check
    rec_truthy_non_bool = [
        {
            "entity_id": "site-str-false",
            "store_id": "1",
            "target_format_code": "STANDARD",
            "opened_on": "2024-01-01",
            "is_training_eligible": "false",
            "realized_90d_net_revenue": 100.0,
        },
        {
            "entity_id": "site-str-true",
            "store_id": "2",
            "target_format_code": "STANDARD",
            "opened_on": "2024-01-01",
            "is_training_eligible": "true",
            "realized_90d_net_revenue": 100.0,
        },
        {
            "entity_id": "site-int-1",
            "store_id": "3",
            "target_format_code": "STANDARD",
            "opened_on": "2024-01-01",
            "is_training_eligible": 1,
            "realized_90d_net_revenue": 100.0,
        },
        {
            "entity_id": "site-int-0",
            "store_id": "4",
            "target_format_code": "STANDARD",
            "opened_on": "2024-01-01",
            "is_training_eligible": 0,
            "realized_90d_net_revenue": 100.0,
        },
        {
            "entity_id": "site-list-true",
            "store_id": "5",
            "target_format_code": "STANDARD",
            "opened_on": "2024-01-01",
            "is_training_eligible": [True],
            "realized_90d_net_revenue": 100.0,
        },
        {
            "entity_id": "site-conflict",
            "store_id": "6",
            "target_format_code": "STANDARD",
            "opened_on": "2024-01-01",
            "is_training_eligible": True,
            "eligible": False,
            "realized_90d_net_revenue": 100.0,
        },
    ]
    res_truthy = evaluate_sitescore_opening_outcome_benchmark(rec_truthy_non_bool, provenance="provided_records")
    assert res_truthy.eligible_count == 0
    assert res_truthy.mature_label_count == 0


def test_sitescore_opening_outcome_forged_active_with_absent_lineage_fails_closed():
    # B1 Regression test: A receipt trying to forge ACTIVE by submitting reason_code GATE2_CRITERIA_MET or status ACTIVE fails closed
    records = _generate_candidate_records(
        220,
        include_m6_m12_realized=True,
        include_bounds=True,
    )
    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="provided_records")
    mc = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=mc)
    mc_dict = mc.to_dict()

    r_forged = json.loads(json.dumps(receipt))
    r_forged["gate_status"] = "PASSED"
    r_forged["is_governed_disabled"] = False
    r_forged["handback"]["governed_disabled"] = False
    r_forged["benchmark_summary"]["status"] = "ACTIVE"
    r_forged["handback"]["status"] = "ACTIVE"
    r_forged["benchmark_summary"]["handback_payload"]["status"] = "ACTIVE"
    r_forged["benchmark_summary"]["reason_code"] = "GATE2_CRITERIA_MET"
    r_forged["handback"]["reason_code"] = "GATE2_CRITERIA_MET"
    r_forged["benchmark_summary"]["handback_payload"]["reason_code"] = "GATE2_CRITERIA_MET"
    r_forged["benchmark_summary"]["is_gate2_passed"] = True

    rebound = _rebind_receipt_hashes(r_forged, mc_dict)
    res = verify_sitescore_gate2_receipt(rebound, model_card_artifact=mc_dict)
    assert res.is_valid is False
    assert any("Forged ACTIVE or PASSED verdict detected" in e or "mismatch" in e for e in res.errors)


def test_sitescore_opening_outcome_forged_population_digest_fails_closed():
    # B2 Regression test: Mutating mature_population_digest fails closed when verified against dataset_manifest or when unverified
    records = _generate_candidate_records(
        220,
        include_m6_m12_realized=True,
        include_bounds=True,
    )
    result = evaluate_sitescore_opening_outcome_benchmark(records, provenance="provided_records")
    mc = build_sitescore_opening_outcome_model_card(result)
    receipt = build_sitescore_gate2_receipt(result, model_card=mc)
    mc_dict = mc.to_dict()

    r_forged = json.loads(json.dumps(receipt))
    fake_digest = "a" * 64
    r_forged["benchmark_summary"]["mature_population_digest"] = fake_digest
    r_forged["handback"]["mature_population_digest"] = fake_digest
    r_forged["benchmark_summary"]["handback_payload"]["mature_population_digest"] = fake_digest
    r_forged["handback"]["outcome_backfill_contract"]["mature_population_digest"] = fake_digest
    r_forged["benchmark_summary"]["handback_payload"]["outcome_backfill_contract"]["mature_population_digest"] = fake_digest

    from models.sitescore.opening_outcome import compute_population_aggregate_digest
    fake_agg_dig = compute_population_aggregate_digest(
        fake_digest,
        result.mature_label_count,
        result.matched_prediction_count,
        result.realized_revenue_sum,
        result.matched_mean_y,
        result.unmatched_mean_y,
    )
    r_forged["benchmark_summary"]["population_aggregate_digest"] = fake_agg_dig
    r_forged["handback"]["population_aggregate_digest"] = fake_agg_dig
    r_forged["benchmark_summary"]["handback_payload"]["population_aggregate_digest"] = fake_agg_dig
    r_forged["handback"]["outcome_backfill_contract"]["population_aggregate_digest"] = fake_agg_dig
    r_forged["benchmark_summary"]["handback_payload"]["outcome_backfill_contract"]["population_aggregate_digest"] = fake_agg_dig

    rebound = _rebind_receipt_hashes(r_forged, mc_dict)

    # Path A: Verify with dataset_manifest
    res_manifest = verify_sitescore_gate2_receipt(rebound, model_card_artifact=mc_dict, dataset_manifest=records)
    assert res_manifest.is_valid is False
    assert any("mature_population_digest" in e and "does not match digest derived from authoritative dataset manifest" in e for e in res_manifest.errors)

    # Path B: Verify without dataset_manifest when dataset snapshot is UNVERIFIED
    res_unverified = verify_sitescore_gate2_receipt(rebound, model_card_artifact=mc_dict)
    assert res_unverified.is_valid is False
    assert any("mature_population_digest must be 'UNAVAILABLE'" in e for e in res_unverified.errors)


def test_sitescore_verifier_rejects_rebound_aggregate_forgery_against_unchanged_manifest_b1():
    # B1 Re-review test (ebe994b1 head): Producer reports matched mean 100, unmatched mean 300, revenue sum 400, overall mean 200.
    # Attacker keeps dataset_manifest unchanged, replaces unmatched mean with 999999, revenue sum with 1000099, overall mean with 500049.5,
    # recomputes population_aggregate_digest and all artifact hashes. Verifier MUST fail closed.
    from models.sitescore.opening_outcome import compute_population_aggregate_digest

    rec_manifest = [
        {
            "entity_id": "site-1",
            "store_id": "101",
            "target_format_code": "STANDARD",
            "opened_on": "2024-01-01",
            "is_training_eligible": True,
            "realized_90d_net_revenue": 100.0,
            "realized_180d_net_revenue": 200.0,
            "realized_365d_net_revenue": 400.0,
            "store_age_days": 400,
            "predicted_revenue": 100.0,
            "p10": 80.0,
            "p90": 120.0,
        },
        {
            "entity_id": "site-2",
            "store_id": "102",
            "target_format_code": "STANDARD",
            "opened_on": "2024-01-01",
            "is_training_eligible": True,
            "realized_90d_net_revenue": 300.0,
            "realized_180d_net_revenue": 600.0,
            "realized_365d_net_revenue": 1200.0,
            "store_age_days": 400,
            "predicted_revenue": None,
        },
    ]
    res_bench = evaluate_sitescore_opening_outcome_benchmark(rec_manifest, provenance="provided_records")
    assert res_bench.mature_label_count == 2
    assert res_bench.matched_prediction_count == 1
    assert res_bench.matched_mean_y == 100.0
    assert res_bench.unmatched_mean_y == 300.0
    assert res_bench.realized_revenue_sum == 400.0
    assert res_bench.mean_realized_revenue == 200.0

    mc = build_sitescore_opening_outcome_model_card(res_bench)
    receipt = build_sitescore_gate2_receipt(res_bench, model_card=mc)
    mc_dict = mc.to_dict()

    r_forged = json.loads(json.dumps(receipt))
    mc_forged = json.loads(json.dumps(mc_dict))

    r_forged["benchmark_summary"]["unmatched_mean_y"] = 999999.0
    r_forged["handback"]["unmatched_mean_y"] = 999999.0
    r_forged["benchmark_summary"]["handback_payload"]["unmatched_mean_y"] = 999999.0

    for key in ("realized_revenue_sum", "mean_realized_revenue"):
        val = 1000099.0 if key == "realized_revenue_sum" else 500049.5
        r_forged["benchmark_summary"][key] = val
        r_forged["handback"][key] = val
        r_forged["benchmark_summary"]["handback_payload"][key] = val
        r_forged["handback"]["outcome_backfill_contract"][key] = val
        r_forged["benchmark_summary"]["handback_payload"]["outcome_backfill_contract"][key] = val

    fake_agg_dig = compute_population_aggregate_digest(
        res_bench.mature_population_digest,
        2,
        1,
        1000099.0,
        100.0,
        999999.0,
    )
    r_forged["benchmark_summary"]["population_aggregate_digest"] = fake_agg_dig
    r_forged["handback"]["population_aggregate_digest"] = fake_agg_dig
    r_forged["benchmark_summary"]["handback_payload"]["population_aggregate_digest"] = fake_agg_dig
    r_forged["handback"]["outcome_backfill_contract"]["population_aggregate_digest"] = fake_agg_dig
    r_forged["benchmark_summary"]["handback_payload"]["outcome_backfill_contract"]["population_aggregate_digest"] = fake_agg_dig

    rebound = _rebind_receipt_hashes(r_forged, mc_forged)
    verif = verify_sitescore_gate2_receipt(rebound, model_card_artifact=mc_forged, dataset_manifest=rec_manifest)

    assert verif.is_valid is False
    assert any("drifts from authoritative dataset manifest" in e or "population_aggregate_digest mismatch" in e for e in verif.errors)


def test_sitescore_verifier_rejects_rebound_m6_coverage_forgery_against_unchanged_manifest_b2():
    # B2 Re-review test (ebe994b1 head): 2-record manifest where both records contain valid mature M6/M12 outcomes and intervals.
    # Attacker changes m6_mature_count and m6_coverage_ratio from 2/100% to 0/0% in every receipt, handback, model-card, segment copy,
    # and rebinds all hashes. Verifier MUST fail closed.
    rec_manifest = [
        {
            "entity_id": "site-1",
            "store_id": "101",
            "target_format_code": "STANDARD",
            "opened_on": "2024-01-01",
            "is_training_eligible": True,
            "realized_90d_net_revenue": 100.0,
            "realized_180d_net_revenue": 200.0,
            "realized_365d_net_revenue": 400.0,
            "store_age_days": 400,
            "predicted_revenue": 100.0,
            "p10": 80.0,
            "p90": 120.0,
        },
        {
            "entity_id": "site-2",
            "store_id": "102",
            "target_format_code": "STANDARD",
            "opened_on": "2024-01-01",
            "is_training_eligible": True,
            "realized_90d_net_revenue": 300.0,
            "realized_180d_net_revenue": 600.0,
            "realized_365d_net_revenue": 1200.0,
            "store_age_days": 400,
            "predicted_revenue": 300.0,
            "p10": 240.0,
            "p90": 360.0,
        },
    ]
    res_bench = evaluate_sitescore_opening_outcome_benchmark(rec_manifest, provenance="provided_records")
    assert res_bench.mature_label_count == 2
    assert res_bench.m6_mature_count == 2
    assert res_bench.m6_coverage_ratio == 1.0

    mc = build_sitescore_opening_outcome_model_card(res_bench)
    receipt = build_sitescore_gate2_receipt(res_bench, model_card=mc)
    mc_dict = mc.to_dict()

    r_forged = json.loads(json.dumps(receipt))
    mc_forged = json.loads(json.dumps(mc_dict))

    r_forged["benchmark_summary"]["m6_mature_count"] = 0
    r_forged["handback"]["m6_mature_count"] = 0
    r_forged["benchmark_summary"]["handback_payload"]["m6_mature_count"] = 0
    r_forged["handback"]["outcome_backfill_contract"]["m6_mature_count"] = 0
    r_forged["benchmark_summary"]["handback_payload"]["outcome_backfill_contract"]["m6_mature_count"] = 0

    r_forged["benchmark_summary"]["m6_coverage_ratio"] = 0.0
    r_forged["handback"]["m6_coverage_ratio"] = 0.0
    r_forged["benchmark_summary"]["handback_payload"]["m6_coverage_ratio"] = 0.0

    mc_forged["metrics_summary"]["m6_coverage_ratio"] = 0.0
    r_forged["benchmark_summary"]["segment_metrics"][0]["metrics"]["m6_coverage"] = 0.0
    if "segment_metrics" in r_forged["handback"]:
        r_forged["handback"]["segment_metrics"][0]["metrics"]["m6_coverage"] = 0.0
    if "segment_metrics" in r_forged["benchmark_summary"]["handback_payload"]:
        r_forged["benchmark_summary"]["handback_payload"]["segment_metrics"][0]["metrics"]["m6_coverage"] = 0.0
    mc_forged["segment_metrics"][0]["metrics"]["m6_coverage"] = 0.0

    rebound = _rebind_receipt_hashes(r_forged, mc_forged)
    verif = verify_sitescore_gate2_receipt(rebound, model_card_artifact=mc_forged, dataset_manifest=rec_manifest)

    assert verif.is_valid is False
    assert any("drifts from authoritative dataset manifest" in e or "mature_population_digest" in e for e in verif.errors)
