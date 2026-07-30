from __future__ import annotations

from datetime import UTC, datetime

import pytest

from models.shared_ml.production_contracts import PRODUCTION_MODEL_CONTRACTS
from scripts.models.heatzone_benchmark import (
    MINIMUM_REQUIRED_LABELS,
    compute_benchmark_receipt_sha256,
    evaluate_heatzone_benchmark,
    generate_benchmark_report_md,
    generate_data_handback_json,
    generate_gate1_receipt,
    main,
    validate_gate1_receipt,
)


def _mock_inventory_receipt() -> dict:
    return {
        "inventory_version": "test-inventory-v1",
        "observed_at": "2026-07-30T12:00:00Z",
        "auto_seeded": False,
        "capabilities": {
            "heatzone": {
                "relation": "model_ready.heatzone_training_view",
                "view_version": "heatzone-training-view-v2",
                "observed_count": 0,
                "eligible_count": 0,
            }
        },
        "integrity": {
            "content_sha256": "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
        },
    }


def _mock_valid_evidence() -> dict:
    return {
        "dataset_snapshot_hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        "model_artifact_hash": "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3",
        "evaluation_split": "heatzone_test_28d_outcome_v1",
        "governed_baseline_hash": "c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
        "population_ranking_ndcg": 0.75,
        "top_k_survey_rate": 0.60,
        "baseline_population_ndcg": 0.50,
        "baseline_survey_rate": 0.30,
    }


def test_heatzone_benchmark_evaluation_fails_closed_when_labels_insufficient() -> None:
    result = evaluate_heatzone_benchmark(
        observed_labels=50,
        eligible_labels=50,
    )
    assert result["verdict"] == "FAIL_CLOSED"
    assert result["governed_disabled"] is True
    assert result["unavailable_reason"] == "DATA_CONTRACT_NOT_MATURE"
    assert result["minimum_required_labels"] == MINIMUM_REQUIRED_LABELS
    assert result["benchmark_results"]["evaluated"] is False
    assert "below the activation threshold" in result["benchmark_results"]["reason"]


def test_heatzone_benchmark_evaluation_fails_closed_when_evidence_unresolved() -> None:
    result = evaluate_heatzone_benchmark(
        observed_labels=250,
        eligible_labels=250,
        population_ranking_ndcg=0.75,
        top_k_survey_rate=0.60,
        benchmark_evidence=None,
    )
    assert result["verdict"] == "FAIL_CLOSED"
    assert result["governed_disabled"] is True
    assert result["unavailable_reason"] == "BENCHMARK_EVIDENCE_NOT_RESOLVED"
    assert result["benchmark_results"]["evaluated"] is False
    assert "immutable measured benchmark evidence" in result["benchmark_results"]["reason"]


def test_heatzone_benchmark_evaluation_passes_when_sufficient_labels_evidence_and_metrics_met(tmp_path) -> None:
    evidence = _mock_valid_evidence()
    auth_file = tmp_path / "AUTHORITATIVE_EVIDENCE.json"
    import json
    auth_file.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_heatzone_benchmark(
        observed_labels=250,
        eligible_labels=250,
        population_ranking_ndcg=0.75,
        top_k_survey_rate=0.60,
        benchmark_evidence=evidence,
        authoritative_evidence_path=auth_file,
        allow_custom_authority_path=True,
    )
    assert result["verdict"] == "PASSED"
    assert result["governed_disabled"] is False
    assert result["unavailable_reason"] is None
    assert result["benchmark_results"]["evaluated"] is True
    assert result["benchmark_results"]["population_ranking_outperformed"] is True
    assert result["benchmark_results"]["top_k_survey_rate_improved"] is True
    assert result["benchmark_evidence"] == evidence


def test_heatzone_benchmark_evaluation_fails_closed_when_metrics_below_baseline(tmp_path) -> None:
    evidence = _mock_valid_evidence()
    evidence["population_ranking_ndcg"] = 0.40
    evidence["top_k_survey_rate"] = 0.20
    auth_file = tmp_path / "AUTHORITATIVE_EVIDENCE.json"
    import json
    auth_file.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_heatzone_benchmark(
        observed_labels=250,
        eligible_labels=250,
        population_ranking_ndcg=0.40,  # Below 0.50
        top_k_survey_rate=0.20,        # Below 0.30
        benchmark_evidence=evidence,
        authoritative_evidence_path=auth_file,
        allow_custom_authority_path=True,
    )
    assert result["verdict"] == "FAIL_CLOSED"
    assert result["governed_disabled"] is True
    assert result["unavailable_reason"] == "BENCHMARK_METRICS_NOT_MET"
    assert result["benchmark_results"]["evaluated"] is True
    assert result["benchmark_results"]["population_ranking_outperformed"] is False
    assert result["benchmark_results"]["top_k_survey_rate_improved"] is False


def test_evaluate_heatzone_benchmark_rejects_lowered_baselines() -> None:
    with pytest.raises(ValueError, match="cannot be below canonical baseline"):
        evaluate_heatzone_benchmark(
            observed_labels=250,
            eligible_labels=250,
            population_ranking_ndcg=0.75,
            top_k_survey_rate=0.60,
            baseline_population_ndcg=0.01,
        )

    with pytest.raises(ValueError, match="cannot be below canonical baseline"):
        evaluate_heatzone_benchmark(
            observed_labels=250,
            eligible_labels=250,
            population_ranking_ndcg=0.75,
            top_k_survey_rate=0.60,
            baseline_survey_rate=0.01,
        )


def test_heatzone_benchmark_evaluation_rejects_negative_or_impossible_counts() -> None:
    with pytest.raises(ValueError, match="observed_labels must be a non-negative integer"):
        evaluate_heatzone_benchmark(observed_labels=-1, eligible_labels=0)

    with pytest.raises(ValueError, match="eligible_labels must be a non-negative integer"):
        evaluate_heatzone_benchmark(observed_labels=100, eligible_labels=-5)

    with pytest.raises(ValueError, match="cannot exceed observed_labels"):
        evaluate_heatzone_benchmark(observed_labels=50, eligible_labels=100)


def test_evaluate_heatzone_benchmark_rejects_non_finite_and_out_of_domain_metrics() -> None:
    with pytest.raises(ValueError, match="in range"):
        evaluate_heatzone_benchmark(observed_labels=250, eligible_labels=250, population_ranking_ndcg=999.0)

    with pytest.raises(ValueError, match="in range"):
        evaluate_heatzone_benchmark(observed_labels=250, eligible_labels=250, top_k_survey_rate=-0.1)

    with pytest.raises(ValueError, match="finite number"):
        evaluate_heatzone_benchmark(observed_labels=250, eligible_labels=250, population_ranking_ndcg=float("nan"))

    with pytest.raises(ValueError, match="finite number"):
        evaluate_heatzone_benchmark(observed_labels=250, eligible_labels=250, top_k_survey_rate=float("inf"))


def test_generate_gate1_receipt_creates_valid_structure_and_sha256() -> None:
    now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
    inv = _mock_inventory_receipt()
    receipt = generate_gate1_receipt(inv, evaluated_at=now)

    assert receipt["task_id"] == "ODP-PLAN-HEATZONE-OUTCOME-001"
    assert receipt["kind"] == "heatzone-gate1-benchmark-receipt"
    assert receipt["inventory_version"] == inv["inventory_version"]
    assert receipt["inventory_observed_at"] == inv["observed_at"]
    assert receipt["inventory_sha256"] == inv["integrity"]["content_sha256"]
    assert receipt["relation"] == inv["capabilities"]["heatzone"]["relation"]
    assert receipt["contract_version"] == inv["capabilities"]["heatzone"]["view_version"]
    assert receipt["auto_seeded"] is False
    assert receipt["verdict"] == "FAIL_CLOSED"
    assert receipt["governed_disabled"] is True
    assert receipt["unavailable_reason"] == "DATA_CONTRACT_NOT_MATURE"

    # Integrity hash verification
    expected_hash = compute_benchmark_receipt_sha256(receipt)
    assert receipt["integrity"]["content_sha256"] == expected_hash

    # Validate against inventory receipt
    validate_gate1_receipt(receipt, inv)


def test_validate_gate1_receipt_fails_closed_on_inventory_loader_failure(monkeypatch) -> None:
    inv = _mock_inventory_receipt()
    receipt = generate_gate1_receipt(inv)

    def _mock_failing_loader():
        raise OSError("canonical inventory receipt unreadable")

    monkeypatch.setattr("scripts.models.heatzone_benchmark.load_model_ready_receipt", _mock_failing_loader)

    with pytest.raises(ValueError, match="Gate 1 receipt lineage validation failed closed"):
        validate_gate1_receipt(receipt, inventory_receipt=None)


def test_gate1_receipt_validate_catches_tampered_integrity_hash() -> None:
    now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
    inv = _mock_inventory_receipt()
    receipt = generate_gate1_receipt(inv, evaluated_at=now)

    # Tamper with count without recomputing hash
    receipt["eligible_labels"] = 999
    with pytest.raises(ValueError, match="Gate 1 receipt integrity mismatch"):
        validate_gate1_receipt(receipt, inv)


def test_gate1_receipt_validate_rejects_forged_passed_receipt() -> None:
    now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
    inv = _mock_inventory_receipt()
    receipt = generate_gate1_receipt(
        inv,
        evaluated_at=now,
        observed_labels=999,
        eligible_labels=999,
        population_ranking_ndcg=0.9,
        top_k_survey_rate=0.9,
    )
    # Recompute self-consistent hash for forged receipt
    receipt["integrity"]["content_sha256"] = compute_benchmark_receipt_sha256(receipt)

    # validate_gate1_receipt against actual inventory MUST fail due to count mismatch vs inventory!
    with pytest.raises(ValueError, match="Receipt observed_labels 999 does not match inventory 0"):
        validate_gate1_receipt(receipt, inv)


def test_gate1_receipt_validate_rejects_forged_passed_with_ndcg_below_baseline(tmp_path) -> None:
    import json
    evidence = _mock_valid_evidence()
    auth_file = tmp_path / "AUTHORITATIVE_EVIDENCE.json"
    auth_file.write_text(json.dumps(evidence), encoding="utf-8")

    inv = _mock_inventory_receipt()
    inv["capabilities"]["heatzone"]["observed_count"] = 250
    inv["capabilities"]["heatzone"]["eligible_count"] = 250

    receipt = generate_gate1_receipt(
        inv,
        observed_labels=250,
        eligible_labels=250,
        population_ranking_ndcg=0.40,  # Below 0.50 baseline
        top_k_survey_rate=0.60,
        benchmark_evidence=evidence,
        authoritative_evidence_path=auth_file,
        allow_custom_authority_path=True,
    )
    # Force self-consistent PASSED claims with bad ndcg
    receipt["verdict"] = "PASSED"
    receipt["governed_disabled"] = False
    receipt["unavailable_reason"] = None
    receipt["benchmark_results"]["evaluated"] = True
    receipt["benchmark_results"]["population_ranking_outperformed"] = True
    receipt["benchmark_results"]["top_k_survey_rate_improved"] = True
    receipt["integrity"]["content_sha256"] = compute_benchmark_receipt_sha256(receipt)

    with pytest.raises(ValueError, match="Verdict PASSED requires observed_ndcg"):
        validate_gate1_receipt(receipt, inv, authoritative_evidence_path=auth_file, allow_custom_authority_path=True)


def test_gate1_receipt_validate_rejects_forged_passed_with_survey_rate_below_baseline(tmp_path) -> None:
    import json
    evidence = _mock_valid_evidence()
    auth_file = tmp_path / "AUTHORITATIVE_EVIDENCE.json"
    auth_file.write_text(json.dumps(evidence), encoding="utf-8")

    inv = _mock_inventory_receipt()
    inv["capabilities"]["heatzone"]["observed_count"] = 250
    inv["capabilities"]["heatzone"]["eligible_count"] = 250

    receipt = generate_gate1_receipt(
        inv,
        observed_labels=250,
        eligible_labels=250,
        population_ranking_ndcg=0.75,
        top_k_survey_rate=0.20,  # Below 0.30 baseline
        benchmark_evidence=evidence,
        authoritative_evidence_path=auth_file,
        allow_custom_authority_path=True,
    )
    receipt["verdict"] = "PASSED"
    receipt["governed_disabled"] = False
    receipt["unavailable_reason"] = None
    receipt["benchmark_results"]["evaluated"] = True
    receipt["benchmark_results"]["population_ranking_outperformed"] = True
    receipt["benchmark_results"]["top_k_survey_rate_improved"] = True
    receipt["integrity"]["content_sha256"] = compute_benchmark_receipt_sha256(receipt)

    with pytest.raises(ValueError, match="Verdict PASSED requires observed_survey_rate"):
        validate_gate1_receipt(receipt, inv, authoritative_evidence_path=auth_file, allow_custom_authority_path=True)


def test_gate1_receipt_validate_rejects_metric_and_baseline_drift(tmp_path) -> None:
    import json
    evidence = _mock_valid_evidence()
    auth_file = tmp_path / "AUTHORITATIVE_EVIDENCE.json"
    auth_file.write_text(json.dumps(evidence), encoding="utf-8")

    inv = _mock_inventory_receipt()
    inv["capabilities"]["heatzone"]["observed_count"] = 250
    inv["capabilities"]["heatzone"]["eligible_count"] = 250

    # Receipt observed_ndcg (0.85) drifts from authoritative evidence (0.75)
    receipt = generate_gate1_receipt(
        inv,
        observed_labels=250,
        eligible_labels=250,
        population_ranking_ndcg=0.85,
        top_k_survey_rate=0.60,
        benchmark_evidence=evidence,
        authoritative_evidence_path=auth_file,
        allow_custom_authority_path=True,
    )
    receipt["verdict"] = "PASSED"
    receipt["governed_disabled"] = False
    receipt["unavailable_reason"] = None
    receipt["benchmark_results"]["evaluated"] = True
    receipt["benchmark_results"]["population_ranking_outperformed"] = True
    receipt["benchmark_results"]["top_k_survey_rate_improved"] = True
    receipt["integrity"]["content_sha256"] = compute_benchmark_receipt_sha256(receipt)

    with pytest.raises(ValueError, match="exact measured/baseline metrics bound to registered authoritative evidence"):
        validate_gate1_receipt(receipt, inv, authoritative_evidence_path=auth_file, allow_custom_authority_path=True)


def test_gate1_receipt_validate_rejects_arbitrary_authority_path_in_production(tmp_path) -> None:
    import json
    evidence = _mock_valid_evidence()
    auth_file = tmp_path / "AUTHORITATIVE_EVIDENCE.json"
    auth_file.write_text(json.dumps(evidence), encoding="utf-8")

    inv = _mock_inventory_receipt()
    inv["capabilities"]["heatzone"]["observed_count"] = 250
    inv["capabilities"]["heatzone"]["eligible_count"] = 250

    receipt = generate_gate1_receipt(
        inv,
        observed_labels=250,
        eligible_labels=250,
        population_ranking_ndcg=0.75,
        top_k_survey_rate=0.60,
        benchmark_evidence=evidence,
        authoritative_evidence_path=auth_file,
        allow_custom_authority_path=True,
    )

    # Calling validate_gate1_receipt with custom authority path without allow_custom_authority_path=True must raise ValueError
    with pytest.raises(ValueError, match="Arbitrary authoritative_evidence_path"):
        validate_gate1_receipt(receipt, inv, authoritative_evidence_path=auth_file, allow_custom_authority_path=False)


def test_gate1_receipt_validate_rejects_forged_passed_with_invalid_hash_format(tmp_path) -> None:
    inv = _mock_inventory_receipt()
    inv["capabilities"]["heatzone"]["observed_count"] = 250
    inv["capabilities"]["heatzone"]["eligible_count"] = 250

    bad_evidence = {
        "dataset_snapshot_hash": "not_a_64_hex_hash",
        "model_artifact_hash": "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3",
        "evaluation_split": "heatzone_test_28d_outcome_v1",
        "governed_baseline_hash": "c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
    }
    receipt = generate_gate1_receipt(
        inv,
        observed_labels=250,
        eligible_labels=250,
        population_ranking_ndcg=0.75,
        top_k_survey_rate=0.60,
        benchmark_evidence=bad_evidence,
    )
    receipt["verdict"] = "PASSED"
    receipt["governed_disabled"] = False
    receipt["unavailable_reason"] = None
    receipt["benchmark_results"]["evaluated"] = True
    receipt["benchmark_results"]["population_ranking_outperformed"] = True
    receipt["benchmark_results"]["top_k_survey_rate_improved"] = True
    receipt["integrity"]["content_sha256"] = compute_benchmark_receipt_sha256(receipt)

    with pytest.raises(ValueError, match="Verdict PASSED requires valid immutable benchmark_evidence"):
        validate_gate1_receipt(receipt, inv)


def test_gate1_receipt_validate_rejects_passed_without_benchmark_evidence() -> None:
    inv = _mock_inventory_receipt()
    inv["capabilities"]["heatzone"]["observed_count"] = 250
    inv["capabilities"]["heatzone"]["eligible_count"] = 250

    now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
    receipt = generate_gate1_receipt(
        inv,
        evaluated_at=now,
        observed_labels=250,
        eligible_labels=250,
        population_ranking_ndcg=0.80,
        top_k_survey_rate=0.60,
    )
    # Without benchmark_evidence, verdict is FAIL_CLOSED
    assert receipt["verdict"] == "FAIL_CLOSED"
    assert receipt["unavailable_reason"] == "BENCHMARK_EVIDENCE_NOT_RESOLVED"

    # Forcing verdict to PASSED without evidence must fail validation
    receipt["verdict"] = "PASSED"
    receipt["governed_disabled"] = False
    receipt["unavailable_reason"] = None
    receipt["benchmark_results"]["evaluated"] = True
    receipt["benchmark_results"]["population_ranking_outperformed"] = True
    receipt["benchmark_results"]["top_k_survey_rate_improved"] = True
    receipt["integrity"]["content_sha256"] = compute_benchmark_receipt_sha256(receipt)
    with pytest.raises(ValueError, match="Verdict PASSED requires valid immutable benchmark_evidence"):
        validate_gate1_receipt(receipt, inv)


def test_gate1_receipt_validate_rejects_contradictory_verdict_states() -> None:
    inv = _mock_inventory_receipt()
    receipt = generate_gate1_receipt(inv)

    # Forge receipt to claim PASSED while keeping eligible_labels < 200
    receipt["verdict"] = "PASSED"
    receipt["integrity"]["content_sha256"] = compute_benchmark_receipt_sha256(receipt)
    with pytest.raises(ValueError, match="Receipt verdict must be FAIL_CLOSED when eligible_labels"):
        validate_gate1_receipt(receipt)

    # Forge receipt to claim PASSED with governed_disabled=True
    receipt["eligible_labels"] = 250
    receipt["observed_labels"] = 250
    receipt["governed_disabled"] = True
    receipt["integrity"]["content_sha256"] = compute_benchmark_receipt_sha256(receipt)
    with pytest.raises(ValueError, match="Verdict PASSED contradicts governed_disabled=True"):
        validate_gate1_receipt(receipt)


def test_gate1_receipt_validate_rejects_auto_seeded_true() -> None:
    inv = _mock_inventory_receipt()
    receipt = generate_gate1_receipt(inv)
    receipt["auto_seeded"] = True
    receipt["integrity"]["content_sha256"] = compute_benchmark_receipt_sha256(receipt)
    with pytest.raises(ValueError, match="auto_seeded must be False"):
        validate_gate1_receipt(receipt)


def test_report_md_and_handback_json_verdict_aware_when_passed_and_fail_closed(tmp_path) -> None:
    import json
    evidence = _mock_valid_evidence()
    auth_file = tmp_path / "AUTHORITATIVE_EVIDENCE.json"
    auth_file.write_text(json.dumps(evidence), encoding="utf-8")

    inv = _mock_inventory_receipt()
    inv["capabilities"]["heatzone"]["observed_count"] = 250
    inv["capabilities"]["heatzone"]["eligible_count"] = 250

    passed_receipt = generate_gate1_receipt(
        inv,
        observed_labels=250,
        eligible_labels=250,
        population_ranking_ndcg=0.75,
        top_k_survey_rate=0.60,
        benchmark_evidence=evidence,
        authoritative_evidence_path=auth_file,
        allow_custom_authority_path=True,
    )
    assert passed_receipt["verdict"] == "PASSED"

    report_md = generate_benchmark_report_md(passed_receipt)
    assert "**Verdict**: **✅ PASSED**" in report_md
    assert "APPROVED FOR ACTIVATION" in report_md
    assert "DATA_CONTRACT_NOT_MATURE" not in report_md

    handback_json = generate_data_handback_json(passed_receipt)
    assert handback_json["status"] == "PASSED"
    assert handback_json["handback_type"] == "GATE1_BENCHMARK_PASSED"
    assert handback_json["unavailable_reason"] is None
    assert handback_json["current_inventory"]["shortfall"] == 0

    # Fail closed receipt
    fail_receipt = generate_gate1_receipt(_mock_inventory_receipt())
    assert fail_receipt["verdict"] == "FAIL_CLOSED"

    fail_report_md = generate_benchmark_report_md(fail_receipt)
    assert "**Verdict**: **❌ FAIL CLOSED**" in fail_report_md
    assert "DATA_CONTRACT_NOT_MATURE" in fail_report_md

    fail_handback_json = generate_data_handback_json(fail_receipt)
    assert fail_handback_json["status"] == "GOVERNED_DISABLED"
    assert fail_handback_json["handback_type"] == "LABEL_INVENTORY_INSUFFICIENT"
    assert fail_handback_json["current_inventory"]["shortfall"] == 200


def test_heatzone_production_contract_remains_governed_disabled() -> None:
    contract = PRODUCTION_MODEL_CONTRACTS["heatzone"]
    assert contract.service == "heatzone"
    assert contract.model_name == "heatzone_priority"
    assert contract.unavailable_reason == "DATA_CONTRACT_NOT_MATURE"
    assert contract.governed_disabled_binding is not None
    assert contract.governed_disabled_binding.reason_code == "DATA_CONTRACT_NOT_MATURE"
    assert contract.governed_disabled_binding.activation_threshold == MINIMUM_REQUIRED_LABELS


def test_generate_and_verify_cli(tmp_path) -> None:
    receipt_file = tmp_path / "receipt.json"
    report_file = tmp_path / "report.md"
    handback_file = tmp_path / "handback.json"

    # Run generate via CLI
    ret = main([
        "generate",
        "--receipt-output", str(receipt_file),
        "--report-output", str(report_file),
        "--handback-output", str(handback_file),
    ])
    assert ret == 0
    assert receipt_file.exists()
    assert report_file.exists()
    assert handback_file.exists()

    # Run verify via CLI
    ret_verify = main([
        "verify",
        "--receipt-output", str(receipt_file),
    ])
    assert ret_verify == 0
