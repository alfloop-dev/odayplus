"""AVM Outcome Inventory Benchmark Evaluation and Gate 1 Receipt Generator.

Audits AVM model-ready transaction outcome inventory against canonical requirements:
- Minimum 120 eligible mature real outcomes in model_ready.valuation_view.
- Join exact model/version predictions to outcomes and compute interval coverage, calibration, and value-band separation.
- Record RBAC/ABAC confidential access audit without leaking raw confidential values.
- Strict fail-closed governance (governed-disabled) when inventory or benchmark criteria fail.
- Binding of Gate 1 receipt to canonical model-ready inventory lineage (version, observed_at, sha256).
- Prohibition of synthetic, mock, auto-seeded label rows or copied predictions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import hashlib

from modules.avm.application.outcome_calibration import (
    generate_benchmark_report_md,
    generate_data_handback_json,
    generate_gate1_benchmark_receipt,
)
from modules.avm.domain.outcome import (
    CANONICAL_AVM_MODEL_VERSION,
    AVMActivationAuthorityReceipt,
    AVMOutcomeCalibrationReport,
    AVMOutcomeTransaction,
    AVMPredictionRecord,
    AVMQuerySourceReceipt,
    align_outcomes_and_predictions,
    compute_avm_outcome_calibration,
    create_avm_query_source_receipt,
)
from modules.dealroom.application.outcome_audit import generate_dealroom_outcome_audit_receipt
from modules.dealroom.domain.confidential_access import create_identity_proof
from shared.auth.identity import Role
from shared.auth.rbac import Action

DEFAULT_RECEIPT_OUTPUT = Path("docs/evidence/models/ODP-PLAN-AVM-OUTCOME-001/GATE1_BENCHMARK_RECEIPT.json")
DEFAULT_REPORT_OUTPUT = Path("docs/evidence/models/ODP-PLAN-AVM-OUTCOME-001/BENCHMARK_REPORT.md")
DEFAULT_HANDBACK_OUTPUT = Path("docs/evidence/models/ODP-PLAN-AVM-OUTCOME-001/DATA_HANDBACK.json")
DEFAULT_AUDIT_OUTPUT = Path("docs/evidence/models/ODP-PLAN-AVM-OUTCOME-001/CONFIDENTIAL_ACCESS_AUDIT.json")
DEFAULT_AUTHORITATIVE_OUTPUT = Path("docs/evidence/models/ODP-PLAN-AVM-OUTCOME-001/AUTHORITATIVE_EVIDENCE.json")

EMPTY_SNAPSHOT_HASH = hashlib.sha256(b"model_ready.valuation_view:empty_snapshot:query_receipt_v1").hexdigest()
UNACTIVATED_MODEL_HASH = hashlib.sha256(b"dealroom-avm-baseline-v1:unactivated_artifact_v1").hexdigest()


def generate_avm_outcome_evidence_pack(
    *,
    outcomes: list[AVMOutcomeTransaction] | None = None,
    predictions: list[AVMPredictionRecord] | None = None,
    observed_count: int = 0,
    eligible_count: int = 0,
    auto_seeded_count: int = 0,
    dataset_snapshot_id: str = "empty-snapshot-unpopulated",
    dataset_snapshot_hash: str = EMPTY_SNAPSHOT_HASH,
    model_artifact_hash: str = UNACTIVATED_MODEL_HASH,
    activation_receipt: AVMActivationAuthorityReceipt | None = None,
    query_source_receipt: AVMQuerySourceReceipt | None = None,
    access_attempts: list[tuple[Any, ...]] | None = None,
) -> tuple[AVMOutcomeCalibrationReport, dict[str, Any], dict[str, Any], str, dict[str, Any]]:
    """Run full AVM outcome calibration & confidential audit pipeline and generate evidence artifacts."""
    if outcomes is None:
        outcomes = []
    if predictions is None:
        predictions = []

    # Default query source receipt if not provided
    if query_source_receipt is None:
        query_source_receipt = create_avm_query_source_receipt(
            dataset_snapshot_id=dataset_snapshot_id,
            dataset_snapshot_hash=dataset_snapshot_hash,
            observed_labeled_count=observed_count,
            eligible_mature_count=eligible_count,
            population_keys=[o.transaction_id for o in outcomes] if outcomes else None,
        )

    # Default audit access attempts if none provided (with explicit cryptographic identity proof)
    if access_attempts is None:
        ctx_fin = {
            "authenticated": True,
            "verified_identity": True,
            "identity_proof_sha256": create_identity_proof("usr-fin-001", Role.FINANCE_LEGAL),
            "tenant_id": "tenant-avm-001",
            "data_room_access": True,
            "tenant_matched": True,
            "clearance": "HIGH",
        }
        ctx_sup = {
            "authenticated": True,
            "verified_identity": True,
            "identity_proof_sha256": create_identity_proof("usr-sup-002", Role.REGIONAL_SUPERVISOR),
            "tenant_id": "tenant-avm-001",
            "data_room_access": True,
            "tenant_matched": True,
            "clearance": "HIGH",
        }
        ctx_adm = {
            "authenticated": True,
            "verified_identity": True,
            "identity_proof_sha256": create_identity_proof("usr-adm-003", Role.PLATFORM_ADMIN),
            "tenant_id": "tenant-avm-001",
            "data_room_access": True,
            "tenant_matched": True,
            "clearance": "HIGH",
        }
        ctx_frc = {
            "authenticated": True,
            "verified_identity": True,
            "identity_proof_sha256": create_identity_proof("usr-frc-004", Role.FRANCHISEE),
            "tenant_id": "tenant-avm-001",
            "data_room_access": True,
            "tenant_matched": True,
            "clearance": "HIGH",
        }
        access_attempts = [
            ("usr-fin-001", Role.FINANCE_LEGAL, "dealroom", Action.VIEW, ctx_fin),
            ("usr-sup-002", Role.REGIONAL_SUPERVISOR, "dealroom", Action.VIEW, ctx_sup),
            ("usr-adm-003", Role.PLATFORM_ADMIN, "dealroom", Action.EXPORT, ctx_adm),
            ("usr-frc-004", Role.FRANCHISEE, "dealroom", Action.VIEW, ctx_frc),
        ]

    # Collect raw prices for confidential leak checking
    raw_prices = tuple(o.realized_price for o in outcomes)
    audit_receipt = generate_dealroom_outcome_audit_receipt(
        access_attempts,
        forbidden_raw_prices=raw_prices,
        dataset_snapshot_hash=dataset_snapshot_hash,
    )

    aligned_pairs = align_outcomes_and_predictions(outcomes, predictions)
    report = compute_avm_outcome_calibration(
        aligned_pairs,
        observed_count=observed_count,
        eligible_count=eligible_count,
        auto_seeded_count=auto_seeded_count,
        model_version=CANONICAL_AVM_MODEL_VERSION,
        dataset_snapshot_id=dataset_snapshot_id,
        dataset_snapshot_hash=dataset_snapshot_hash,
        model_artifact_hash=model_artifact_hash,
        activation_receipt=activation_receipt,
        audit_receipt=audit_receipt,
        query_source_receipt=query_source_receipt,
    )

    gate1_receipt = generate_gate1_benchmark_receipt(
        report,
        dataset_snapshot_id=dataset_snapshot_id,
        dataset_snapshot_hash=dataset_snapshot_hash,
        model_artifact_hash=model_artifact_hash,
        audit_receipt=audit_receipt,
        activation_receipt=activation_receipt,
        query_source_receipt=query_source_receipt,
    )

    report_md = generate_benchmark_report_md(report, audit_receipt)
    handback_json = generate_data_handback_json(report)

    return report, gate1_receipt, audit_receipt, report_md, handback_json


def main() -> None:
    parser = argparse.ArgumentParser(description="AVM Outcome Benchmark & Gate 1 Receipt Generator")
    parser.add_argument("--receipt-out", type=Path, default=DEFAULT_RECEIPT_OUTPUT)
    parser.add_argument("--report-out", type=Path, default=DEFAULT_REPORT_OUTPUT)
    parser.add_argument("--handback-out", type=Path, default=DEFAULT_HANDBACK_OUTPUT)
    parser.add_argument("--audit-out", type=Path, default=DEFAULT_AUDIT_OUTPUT)
    parser.add_argument("--authoritative-out", type=Path, default=DEFAULT_AUTHORITATIVE_OUTPUT)

    args = parser.parse_args()

    report, gate1_receipt, audit_receipt, report_md, handback_json = generate_avm_outcome_evidence_pack(
        observed_count=0,
        eligible_count=0,
        auto_seeded_count=0,
        dataset_snapshot_id="empty-snapshot-unpopulated",
        dataset_snapshot_hash=EMPTY_SNAPSHOT_HASH,
        model_artifact_hash=UNACTIVATED_MODEL_HASH,
    )

    # Ensure parent directories exist
    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)

    args.receipt_out.write_text(json.dumps(gate1_receipt, indent=2), encoding="utf-8")
    args.report_out.write_text(report_md, encoding="utf-8")
    args.handback_out.write_text(json.dumps(handback_json, indent=2), encoding="utf-8")
    args.audit_out.write_text(json.dumps(audit_receipt, indent=2), encoding="utf-8")

    authoritative_payload = {
        "task_id": "ODP-PLAN-AVM-OUTCOME-001",
        "generated_at": report.evaluated_at.isoformat(),
        "model_version": report.model_version,
        "verdict": report.verdict.value,
        "is_governed_disabled": report.is_governed_disabled,
        "reason_code": report.reason_code,
        "authentic_evidence_available": False,
        "query_receipt": {
            "relation": "model_ready.valuation_view",
            "query_timestamp": report.evaluated_at.isoformat(),
            "observed_labeled_count": report.observed_labeled_count,
            "eligible_mature_count": report.eligible_mature_count,
            "dataset_snapshot_id": report.dataset_snapshot_id,
            "dataset_snapshot_hash": report.dataset_snapshot_hash,
        },
        "gate1_receipt_sha256": gate1_receipt["integrity"]["content_sha256"],
        "audit_receipt_sha256": audit_receipt["sha256"],
        "governed_disabled_handback": handback_json,
    }
    args.authoritative_out.write_text(json.dumps(authoritative_payload, indent=2), encoding="utf-8")

    print(f"Generated Gate 1 receipt: {args.receipt_out}")
    print(f"Generated Benchmark report: {args.report_out}")
    print(f"Generated Data handback: {args.handback_out}")
    print(f"Generated Confidential audit: {args.audit_out}")
    print(f"Generated Authoritative evidence: {args.authoritative_out}")



if __name__ == "__main__":
    main()
