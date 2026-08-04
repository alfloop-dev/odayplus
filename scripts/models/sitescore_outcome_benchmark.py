import argparse
import json
import math
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
    prediction_receipt: dict[str, Any] | None = None,
) -> Any:
    """Load inventory records or evaluate provided candidate site records."""
    if records is not None:
        provenance = "provided_records" if records else "no_source"
        return evaluate_sitescore_opening_outcome_benchmark(records, prediction_receipt=prediction_receipt, provenance=provenance)

    # In PG16 environment if DB URL is provided
    if db_url and "postgresql" in db_url.lower():
        try:
            import psycopg
            conn = psycopg.connect(db_url)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        c.entity_id,
                        c.store_id,
                        c.target_format_code,
                        c.opened_on,
                        c.is_training_eligible,
                        c.realized_90d_net_revenue,
                        c.realized_180d_net_revenue,
                        c.realized_365d_net_revenue,
                        (CURRENT_DATE - c.opened_on)::integer AS store_age_days,
                        p.prediction_as_of,
                        p.model_version,
                        p.horizon_code,
                        p.predicted_revenue,
                        p.p10,
                        p.p90,
                        p.p50,
                        p.dataset_snapshot_id,
                        p.artifact_lineage_id
                    FROM model_ready.candidate_site_view c
                    LEFT JOIN model_ready.sitescore_predictions p
                        ON (c.entity_id = p.entity_id OR c.store_id = p.store_id)
                       AND c.opened_on = p.prediction_as_of
                       AND p.model_version = 'candidate-site-view-v2'
                    """
                )
                rows = cur.fetchall()
                fetched = []
                for row in rows:
                    def _parse_float(val: Any) -> float | None:
                        if val is not None:
                            try:
                                v = float(val)
                                return v if math.isfinite(v) else None
                            except (ValueError, TypeError):
                                return None
                        return None

                    fetched.append({
                        "entity_id": str(row[0]),
                        "store_id": str(row[1]),
                        "target_format_code": str(row[2]),
                        "opened_on": str(row[3]) if row[3] else None,
                        "is_training_eligible": bool(row[4]),
                        "realized_90d_net_revenue": _parse_float(row[5]),
                        "realized_m6_net_revenue": _parse_float(row[6]),
                        "realized_m12_net_revenue": _parse_float(row[7]),
                        "store_age_days": int(row[8]) if row[8] is not None and math.isfinite(float(row[8])) else 0,
                        "prediction_as_of": str(row[9]) if row[9] else None,
                        "model_version": str(row[10]) if row[10] else None,
                        "horizon_code": str(row[11]) if row[11] else "90d",
                        "predicted_revenue": _parse_float(row[12]),
                        "p10": _parse_float(row[13]),
                        "p90": _parse_float(row[14]),
                        "p50": _parse_float(row[15]),
                        "dataset_snapshot_id": str(row[16]) if row[16] else None,
                        "artifact_lineage_id": str(row[17]) if row[17] else None,
                    })
                return evaluate_sitescore_opening_outcome_benchmark(fetched, prediction_receipt=prediction_receipt, provenance="pg16_query")
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

    norm_mae = summary.get('normalized_mae', 999.0)
    mae_pass = summary.get('is_gate2_passed', False) or (
        summary.get('mature_label_count', 0) >= 200
        and summary.get('prediction_coverage_ratio', 0.0) >= 0.70
        and summary.get('interval_bounds_coverage_ratio', 0.0) >= 0.70
        and math.isfinite(norm_mae)
        and norm_mae <= summary.get('max_mae_threshold', 0.25)
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
        f"| Matched Predictions | {summary.get('matched_prediction_count', 0)} | N/A | INFO |",
        f"| Prediction Coverage | {summary.get('prediction_coverage_ratio', 0.0):.1%} | >= {summary.get('min_coverage_threshold', 0.70):.1%} | {pred_cov_status} |",
        f"| Interval Bounds Coverage | {summary.get('interval_bounds_coverage_ratio', 0.0):.1%} | >= {summary.get('min_coverage_threshold', 0.70):.1%} | {bounds_cov_status} |",
        f"| M6 Horizon Coverage | {summary.get('m6_coverage_ratio', 0.0):.1%} | >= {summary.get('min_coverage_threshold', 0.70):.1%} | {m6_cov_status} |",
        f"| M12 Horizon Coverage | {summary.get('m12_coverage_ratio', 0.0):.1%} | >= {summary.get('min_coverage_threshold', 0.70):.1%} | {m12_cov_status} |",
        f"| P80 Coverage Ratio | {summary.get('p80_coverage', 0.0):.1%} | >= {summary.get('min_coverage_threshold', 0.70):.1%} | {p80_cov_status} |",
        f"| Normalized MAE | {norm_mae:.3f} | <= {summary.get('max_mae_threshold', 0.25):.3f} | {mae_status} |",
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
    if handback.get("discovery_inventory_query"):
        lines.append(f"- **Discovery Inventory Query**: `{handback['discovery_inventory_query']}`")
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

    model_card = build_sitescore_opening_outcome_model_card(
        benchmark_result,
        version=args.inventory_version,
    )
    model_card_dict = model_card.to_dict()
    from models.sitescore.opening_outcome import compute_model_card_sha256
    model_card_hash = compute_model_card_sha256(model_card_dict)

    receipt = build_sitescore_gate2_receipt(
        benchmark_result,
        inventory_version=args.inventory_version,
        model_card=model_card,
        model_card_hash=model_card_hash,
    )

    args.output_receipt.parent.mkdir(parents=True, exist_ok=True)
    args.output_receipt.write_text(
        json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    args.output_model_card.parent.mkdir(parents=True, exist_ok=True)
    args.output_model_card.write_text(
        json.dumps(model_card.to_dict(), indent=2, sort_keys=True, allow_nan=False) + "\n",
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
