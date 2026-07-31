"""CLI runner for SiteScore opening outcome M6/M12 inventory coverage calibration benchmark & Gate 2 receipt."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from models.sitescore.opening_outcome import (
    build_sitescore_gate2_receipt,
    build_sitescore_opening_outcome_model_card,
    evaluate_sitescore_opening_outcome_benchmark,
)

DEFAULT_EVIDENCE_DIR = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "models"
DEFAULT_RECEIPT_PATH = DEFAULT_EVIDENCE_DIR / "sitescore_gate2_receipt.json"
DEFAULT_MODEL_CARD_PATH = DEFAULT_EVIDENCE_DIR / "sitescore_model_card.json"
DEFAULT_EVIDENCE_DOC_PATH = DEFAULT_EVIDENCE_DIR / "ODP-PLAN-SITESCORE-OUTCOME-001.md"


def run_benchmark_from_inventory(
    db_url: str | None = None,
    records: Sequence[dict[str, Any]] | None = None,
) -> Any:
    """Load inventory records or evaluate provided candidate site records."""
    if records is not None:
        provenance = "provided_records" if records else "no_source"
        return evaluate_sitescore_opening_outcome_benchmark(records, provenance=provenance)

    # In PG16 environment if DB URL is provided
    if db_url and "postgresql" in db_url.lower():
        try:
            import psycopg
            conn = psycopg.connect(db_url)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        entity_id,
                        store_id,
                        target_format_code,
                        opened_on,
                        is_training_eligible,
                        realized_90d_net_revenue,
                        (CURRENT_DATE - opened_on)::integer AS store_age_days
                    FROM model_ready.candidate_site_view
                    """
                )
                rows = cur.fetchall()
                fetched = []
                for row in rows:
                    fetched.append({
                        "entity_id": row[0],
                        "store_id": str(row[1]),
                        "target_format_code": row[2],
                        "opened_on": str(row[3]) if row[3] else None,
                        "is_training_eligible": bool(row[4]),
                        "realized_90d_net_revenue": float(row[5]) if row[5] is not None else None,
                        "store_age_days": int(row[6]) if row[6] is not None else 0,
                    })
                return evaluate_sitescore_opening_outcome_benchmark(fetched, provenance="pg16_query")
        except Exception as exc:
            print(f"Notice: PostgreSQL inventory query failed ({exc}); failing closed.", file=sys.stderr)
            return evaluate_sitescore_opening_outcome_benchmark(
                [], provenance="unreachable_db", db_error=str(exc)
            )

    # Default inventory check (no source provided)
    return evaluate_sitescore_opening_outcome_benchmark([], provenance="no_source")


def write_evidence_markdown(
    receipt: dict[str, Any],
    output_path: Path,
) -> None:
    """Write human-readable markdown evidence document for Gate 2 receipt."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    status = receipt.get("gate_status", "UNKNOWN")
    summary = receipt.get("benchmark_summary", {})
    handback = receipt.get("handback", {})
    observed_at = receipt.get("observed_at", "")
    provenance = receipt.get("provenance", summary.get("provenance", "unknown"))
    integrity_sha = receipt.get("integrity", {}).get("content_sha256", "")

    labels_status = 'PASS' if summary.get('mature_label_count', 0) >= 200 else 'FAIL (GOVERNED_DISABLED)'
    pred_cov_status = 'PASS' if summary.get('prediction_coverage_ratio', 0.0) >= 0.70 else 'FAIL'
    bounds_cov_status = 'PASS' if summary.get('interval_bounds_coverage_ratio', 0.0) >= 0.70 else 'FAIL'
    m6_cov_status = 'PASS' if summary.get('m6_coverage_ratio', 0.0) >= 0.70 else 'FAIL'
    m12_cov_status = 'PASS' if summary.get('m12_coverage_ratio', 0.0) >= 0.70 else 'FAIL'
    p80_cov_status = 'PASS' if summary.get('p80_coverage', 0.0) >= 0.70 else 'FAIL'

    mae_pass = summary.get('is_gate2_passed', False) or (
        summary.get('mature_label_count', 0) >= 200
        and summary.get('prediction_coverage_ratio', 0.0) >= 0.70
        and summary.get('interval_bounds_coverage_ratio', 0.0) >= 0.70
        and summary.get('normalized_mae', 0.0) <= summary.get('max_mae_threshold', 0.25)
    )
    mae_status = 'PASS' if mae_pass else 'FAIL (GOVERNED_DISABLED)'

    lines = [
        "# Gate 2 Receipt: SiteScore Opening Outcome Calibration Benchmark (ODP-PLAN-SITESCORE-OUTCOME-001)",
        "",
        "- **Task ID**: `ODP-PLAN-SITESCORE-OUTCOME-001`",
        f"- **Observed At**: `{observed_at}`",
        f"- **Gate Status**: `{status}`",
        f"- **Data Provenance**: `{provenance}`",
        f"- **Is Governed Disabled**: `{receipt.get('is_governed_disabled', False)}`",
        f"- **Integrity Content SHA256**: `{integrity_sha}`",
    ]
    if receipt.get("db_error"):
        lines.append(f"- **Database Error**: `{receipt['db_error']}`")

    lines.extend([
        "",
        "## Benchmark Inventory & Coverage Summary",
        "",
        "| Metric | Observed | Threshold / Required | Status |",
        "| --- | --- | --- | --- |",
        f"| Mature Labels | {summary.get('mature_label_count', 0)} | >= {summary.get('activation_threshold', 200)} | {labels_status} |",
        f"| Prediction Coverage | {summary.get('prediction_coverage_ratio', 0.0):.1%} | >= {summary.get('min_coverage_threshold', 0.70):.1%} | {pred_cov_status} |",
        f"| Interval Bounds Coverage | {summary.get('interval_bounds_coverage_ratio', 0.0):.1%} | >= {summary.get('min_coverage_threshold', 0.70):.1%} | {bounds_cov_status} |",
        f"| M6 Horizon Coverage | {summary.get('m6_coverage_ratio', 0.0):.1%} | >= {summary.get('min_coverage_threshold', 0.70):.1%} | {m6_cov_status} |",
        f"| M12 Horizon Coverage | {summary.get('m12_coverage_ratio', 0.0):.1%} | >= {summary.get('min_coverage_threshold', 0.70):.1%} | {m12_cov_status} |",
        f"| P80 Coverage Ratio | {summary.get('p80_coverage', 0.0):.1%} | >= {summary.get('min_coverage_threshold', 0.70):.1%} | {p80_cov_status} |",
        f"| Normalized MAE | {summary.get('normalized_mae', 0.0):.3f} | <= {summary.get('max_mae_threshold', 0.25):.3f} | {mae_status} |",
        "",
        "## Handback & Governance Receipt",
        "",
        f"- **Handback Required**: `{handback.get('handback_required', False)}`",
        f"- **Reason Code**: `{handback.get('reason_code', '')}`",
    ])

    if handback.get("backfill_owner"):
        lines.append(f"- **Backfill Owner**: `{handback['backfill_owner']}`")
    if handback.get("backfill_task_id"):
        lines.append(f"- **Backfill Task ID**: `{handback['backfill_task_id']}`")
    if handback.get("prediction_source_task_id"):
        lines.append(f"- **Prediction Source Task ID**: `{handback['prediction_source_task_id']}`")
    if handback.get("backfill_query"):
        lines.append(f"- **Backfill Query**: `{handback['backfill_query']}`")
    if "backfill_receipt_required" in handback:
        lines.append(f"- **Backfill Receipt Required**: `{handback['backfill_receipt_required']}`")

    if handback.get("reasons"):
        lines.append("- **Audit Reasons**:")
        for r in handback["reasons"]:
            lines.append(f"  - {r}")

    if handback.get("handback_action"):
        lines.append(f"- **Handback Action**: {handback['handback_action']}")

    lines.extend([
        "",
        "## Verification",
        "```bash",
        "pytest -q tests -k \"sitescore or opening_outcome or model_ready\" && git diff --check",
        "```",
        "",
    ])

    output_path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run SiteScore opening outcome inventory coverage calibration benchmark & generate Gate 2 receipt."
    )
    parser.add_argument(
        "--inventory-version",
        default="candidate-site-view-v2",
        help="Model-ready inventory view version",
    )
    parser.add_argument(
        "--output-receipt",
        type=Path,
        default=DEFAULT_RECEIPT_PATH,
        help="Path for Gate 2 receipt JSON output",
    )
    parser.add_argument(
        "--output-model-card",
        type=Path,
        default=DEFAULT_MODEL_CARD_PATH,
        help="Path for Model Card JSON output",
    )
    parser.add_argument(
        "--output-evidence",
        type=Path,
        default=DEFAULT_EVIDENCE_DOC_PATH,
        help="Path for evidence Markdown output",
    )
    parser.add_argument(
        "--db-url",
        help="Optional PostgreSQL database URL",
    )

    args = parser.parse_args(argv)

    db_url = args.db_url or os.getenv("ODAY_DATABASE_URL") or os.getenv("ODP_DATABASE_URL")
    benchmark_result = run_benchmark_from_inventory(db_url=db_url)

    receipt = build_sitescore_gate2_receipt(
        benchmark_result,
        inventory_version=args.inventory_version,
    )
    model_card = build_sitescore_opening_outcome_model_card(
        benchmark_result,
        version=args.inventory_version,
    )

    args.output_receipt.parent.mkdir(parents=True, exist_ok=True)
    args.output_receipt.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    args.output_model_card.parent.mkdir(parents=True, exist_ok=True)
    args.output_model_card.write_text(
        json.dumps(model_card.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    write_evidence_markdown(receipt, args.output_evidence)

    print(f"Generated Gate 2 Receipt: {args.output_receipt}")
    print(f"Generated Model Card: {args.output_model_card}")
    print(f"Generated Evidence Doc: {args.output_evidence}")
    print(f"Status: {receipt['gate_status']} (Reason: {receipt['handback']['reason_code']})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
