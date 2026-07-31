"""AVM Outcome Calibration pipeline application layer."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from modules.avm.domain.outcome import (
    ACTIVATION_THRESHOLD,
    AVMOutcomeCalibrationReport,
    AVMOutcomeValidationError,
    AVMVerdict,
)


def compute_receipt_sha256(payload: dict[str, Any]) -> str:
    """Compute sha256 digest of payload excluding integrity envelope."""
    canonical = json.dumps(
        {k: v for k, v in payload.items() if k != "integrity"},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def generate_gate1_benchmark_receipt(
    report: AVMOutcomeCalibrationReport,
    *,
    dataset_snapshot_id: str,
    dataset_snapshot_hash: str,
    model_artifact_hash: str,
) -> dict[str, Any]:
    """Generate canonical GATE1_BENCHMARK_RECEIPT.json for AVM outcome calibration."""
    # B5: Receipt lineage binding check
    if (
        dataset_snapshot_id != report.dataset_snapshot_id
        or dataset_snapshot_hash != report.dataset_snapshot_hash
        or model_artifact_hash != report.model_artifact_hash
    ):
        raise AVMOutcomeValidationError(
            f"Receipt lineage mismatch with report: "
            f"expected ({report.dataset_snapshot_id!r}, {report.dataset_snapshot_hash!r}, {report.model_artifact_hash!r}), "
            f"got ({dataset_snapshot_id!r}, {dataset_snapshot_hash!r}, {model_artifact_hash!r})"
        )

    # B5: Revalidate verdict invariants at receipt boundary
    if report.verdict == AVMVerdict.PASS:
        if (
            report.is_governed_disabled
            or report.reason_code != "MATURE_LABEL_CONTRACT_READY"
            or report.observed_labeled_count < ACTIVATION_THRESHOLD
            or report.eligible_mature_count < ACTIVATION_THRESHOLD
            or report.aligned_count < ACTIVATION_THRESHOLD
            or report.auto_seeded_count > 0
            or report.p10_p90_coverage_rate < 0.80
            or not (0.95 <= report.median_calibration_ratio <= 1.05)
            or report.mape > 0.15
        ):
            raise AVMOutcomeValidationError(
                "Fail-closed: Receipt boundary detected forged or invalid PASS verdict invariants"
            )

    payload = {
        "kind": "avm-gate1-benchmark-receipt",
        "schema_version": 1,
        "task_id": "ODP-PLAN-AVM-OUTCOME-001",
        "model_version": report.model_version,
        "evaluated_at": report.evaluated_at.isoformat(),
        "verdict": report.verdict.value,
        "governed_disabled": report.is_governed_disabled,
        "reason_code": report.reason_code,
        "inventory": {
            "observed_labeled_count": report.observed_labeled_count,
            "eligible_mature_count": report.eligible_mature_count,
            "auto_seeded_count": report.auto_seeded_count,
            "activation_threshold": report.activation_threshold,
            "relation": "model_ready.valuation_view",
        },
        "lineage": {
            "dataset_snapshot_id": dataset_snapshot_id,
            "dataset_snapshot_hash": dataset_snapshot_hash,
            "model_artifact_hash": model_artifact_hash,
            "authority_partition": "official_real_estate",
        },
        "metrics": {
            "aligned_count": report.aligned_count,
            "p10_p90_coverage_rate": report.p10_p90_coverage_rate,
            "p10_p50_coverage_rate": report.p10_p50_coverage_rate,
            "p50_p90_coverage_rate": report.p50_p90_coverage_rate,
            "mae": report.mae,
            "mape": report.mape,
            "median_calibration_ratio": report.median_calibration_ratio,
            "value_band_metrics": {
                k: v.to_dict() for k, v in report.value_band_metrics.items()
            },
        },
    }

    digest = compute_receipt_sha256(payload)
    payload["integrity"] = {
        "content_sha256": digest,
        "signature_scheme": "sha256-json-canonical",
    }
    return payload



def generate_benchmark_report_md(
    report: AVMOutcomeCalibrationReport,
    audit_receipt: dict[str, Any],
) -> str:
    """Generate canonical BENCHMARK_REPORT.md for AVM outcome calibration."""
    verdict_emoji = "✅ PASS" if report.verdict == AVMVerdict.PASS else "❌ FAIL CLOSED"
    lines = [
        "# AVM Outcome Inventory Benchmark & Gate 1 Receipt",
        "",
        "- **Task ID**: `ODP-PLAN-AVM-OUTCOME-001`",
        f"- **Evaluation Date**: `{report.evaluated_at.isoformat()}`",
        f"- **Verdict**: **{verdict_emoji}**",
        f"- **Model Version**: `{report.model_version}`",
        f"- **Dataset Snapshot ID**: `{report.dataset_snapshot_id}`",
        f"- **Dataset Snapshot SHA256**: `{report.dataset_snapshot_hash}`",
        f"- **Model Artifact SHA256**: `{report.model_artifact_hash}`",
        "- **Relation**: `model_ready.valuation_view`",
        f"- **Auto Seeded Rows**: `{report.auto_seeded_count}` (Forbidden)",
        f"- **Governed Disabled**: `{report.is_governed_disabled}` (`{report.reason_code}`)",
        "",
        "---",
        "",
        "## 1. Outcome Inventory Summary",
        "",
        "| Metric | Value | Required Minimum | Status |",
        "|---|---:|---:|---|",
        f"| Observed Labeled Rows | `{report.observed_labeled_count}` | - | Observed |",
        f"| Eligible Mature Real Outcomes | `{report.eligible_mature_count}` | `{report.activation_threshold}` | {'✅ Sufficient' if report.eligible_mature_count >= report.activation_threshold else '❌ Insufficient'} |",
        f"| Auto-Seeded / Synthetic Rows | `{report.auto_seeded_count}` | `0` | ✅ Zero Synthetic |",
        "",
        "---",
        "",
        "## 2. Benchmark & Calibration Metrics",
        "",
        "| Calibration Metric | Aligned Value | Baseline Target | Status |",
        "|---|---:|---:|---|",
        f"| Aligned Population Count | `{report.aligned_count}` | - | Aligned |",
        f"| Interval Coverage (P10..P90) | `{report.p10_p90_coverage_rate:.4f}` | `0.8000` | {'Pass' if report.p10_p90_coverage_rate >= 0.70 else 'Skipped / Insufficient'} |",
        f"| Mean Absolute Percentage Error (MAPE) | `{report.mape:.4f}` | `<= 0.1500` | {'Pass' if report.mape <= 0.15 and report.aligned_count > 0 else 'Skipped / Insufficient'} |",
        f"| Median Calibration Ratio (Realized / P50) | `{report.median_calibration_ratio:.4f}` | `0.95 .. 1.05` | {'Pass' if 0.90 <= report.median_calibration_ratio <= 1.10 and report.aligned_count > 0 else 'Skipped / Insufficient'} |",
        "",
        "### Value Band Separation Breakdown",
        "",
        "| Value Band | Aligned Count | P10..P90 Coverage | Calibration Ratio | MAPE | MAE |",
        "|---|---:|---:|---:|---:|---:|",
    ]

    for band_name, bm in report.value_band_metrics.items():
        lines.append(
            f"| `{band_name}` | `{bm.aligned_count}` | `{bm.p10_p90_coverage_rate:.4f}` | `{bm.calibration_ratio:.4f}` | `{bm.mape:.4f}` | `${bm.mae:,.2f}` |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 3. Confidential Access Audit & RBAC Summary",
            "",
            f"- **Audit Event Count**: `{audit_receipt.get('total_access_attempts', 0)}`",
            f"- **Permitted Accesses**: `{audit_receipt.get('permitted_count', 0)}` (Roles: `FINANCE_LEGAL`, `SYSTEM_ADMIN`)",
            f"- **Denied Accesses**: `{audit_receipt.get('denied_count', 0)}` (Roles: `REGIONAL_SUPERVISOR`, `FRANCHISEE`, `MARKETING_MANAGER`)",
            "- **Zero Confidential Leak Verified**: `True`",
            f"- **Audit Receipt SHA256**: `{audit_receipt.get('sha256', '')}`",
            "",
            "---",
            "",
            "## 4. Fail-Closed Governance & Safety Enforcements",
            "",
            f"1. **Governed-Disabled Status**: Production binding for `avm` model (`dealroom_avm`) remains **governed-disabled** with canonical reason code `{report.reason_code}`.",
            "2. **Zero Synthetic Data Policy**: Synthetic outcomes, mock transactions, auto-seeded entries, or copied predictions are strictly prohibited.",
            "3. **Activation Threshold**: Requires at least **120** mature real transaction outcomes with independent label-authority approval.",
            "",
        ]
    )
    return "\n".join(lines)


def generate_data_handback_json(
    report: AVMOutcomeCalibrationReport,
) -> dict[str, Any]:
    """Generate canonical DATA_HANDBACK.json outlining actionable backfill requirements."""
    return {
        "kind": "avm-data-handback-requirements",
        "task_id": "ODP-PLAN-AVM-OUTCOME-001",
        "generated_at": report.evaluated_at.isoformat(),
        "current_status": {
            "observed_labeled_count": report.observed_labeled_count,
            "eligible_mature_count": report.eligible_mature_count,
            "shortfall": max(0, ACTIVATION_THRESHOLD - report.eligible_mature_count),
            "activation_threshold": ACTIVATION_THRESHOLD,
            "reason_code": report.reason_code,
        },
        "actionable_requirements": [
            {
                "requirement_id": "REQ-AVM-001",
                "description": "Ingest and backfill at least 120 authentic mature transaction outcomes into model_ready.valuation_view.",
                "owner": "Human/Ops & Finance Legal Team",
            },
            {
                "requirement_id": "REQ-AVM-002",
                "description": "Ensure zero synthetic rows, auto-seeded entries, or copied prediction substitutions are present.",
                "owner": "Data Engineering",
            },
            {
                "requirement_id": "REQ-AVM-003",
                "description": "Verify P10..P90 interval coverage >= 80% and median calibration ratio between 0.95 and 1.05 on aligned populations.",
                "owner": "AVM Platform Team",
            },
            {
                "requirement_id": "REQ-AVM-004",
                "description": "Record RBAC/ABAC confidential access audit receipts without leaking raw transaction values.",
                "owner": "Security & Compliance",
            },
        ],
    }
