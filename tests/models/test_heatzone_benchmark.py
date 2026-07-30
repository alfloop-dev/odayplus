from __future__ import annotations

import json
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
)


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


def test_heatzone_benchmark_evaluation_passes_when_sufficient_labels_and_metrics_met() -> None:
    result = evaluate_heatzone_benchmark(
        observed_labels=250,
        eligible_labels=250,
        population_ranking_ndcg=0.75,
        top_k_survey_rate=0.60,
    )
    assert result["verdict"] == "PASSED"
    assert result["benchmark_results"]["evaluated"] is True
    assert result["benchmark_results"]["population_ranking_outperformed"] is True
    assert result["benchmark_results"]["top_k_survey_rate_improved"] is True


def test_heatzone_benchmark_evaluation_fails_closed_when_metrics_below_baseline() -> None:
    result = evaluate_heatzone_benchmark(
        observed_labels=250,
        eligible_labels=250,
        population_ranking_ndcg=0.40,  # Below 0.50
        top_k_survey_rate=0.20,        # Below 0.30
    )
    assert result["verdict"] == "FAIL_CLOSED"
    assert result["benchmark_results"]["evaluated"] is True
    assert result["benchmark_results"]["population_ranking_outperformed"] is False
    assert result["benchmark_results"]["top_k_survey_rate_improved"] is False


def test_generate_gate1_receipt_creates_valid_structure_and_sha256() -> None:
    now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
    inventory = {"observed_count": 0, "eligible_count": 0}
    receipt = generate_gate1_receipt(inventory, evaluated_at=now)

    assert receipt["task_id"] == "ODP-PLAN-HEATZONE-OUTCOME-001"
    assert receipt["kind"] == "heatzone-gate1-benchmark-receipt"
    assert receipt["auto_seeded"] is False
    assert receipt["verdict"] == "FAIL_CLOSED"
    assert receipt["governed_disabled"] is True
    assert receipt["unavailable_reason"] == "DATA_CONTRACT_NOT_MATURE"

    # Integrity hash verification
    expected_hash = compute_benchmark_receipt_sha256(receipt)
    assert receipt["integrity"]["content_sha256"] == expected_hash


def test_gate1_receipt_sha256_detects_tampering() -> None:
    now = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
    receipt = generate_gate1_receipt({"observed_count": 0, "eligible_count": 0}, evaluated_at=now)
    original_hash = receipt["integrity"]["content_sha256"]

    # Tamper with count
    receipt["eligible_labels"] = 999
    tampered_hash = compute_benchmark_receipt_sha256(receipt)
    assert tampered_hash != original_hash


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
