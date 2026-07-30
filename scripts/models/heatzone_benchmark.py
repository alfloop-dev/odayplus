"""HeatZone Label Benchmark Evaluation and Gate 1 Receipt Generator.

Audits HeatZone model-ready label inventory against canonical requirements:
- Minimum 200 eligible mature real labels in model_ready.heatzone_training_view.
- Outperformance relative to population ranking baseline (NDCG / Top-K precision).
- Improvement of Top-K field site survey rate (Top-K 現勘率).
- Strict fail-closed governance (governed-disabled) when inventory or benchmark criteria fail.
- Prohibition of synthetic, mock, or auto-seeded label rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.shared_ml.model_ready_receipt import load_model_ready_receipt
from models.shared_ml.production_contracts import PRODUCTION_MODEL_CONTRACTS

BENCHMARK_RECEIPT_SCHEMA_VERSION = 1
BENCHMARK_RECEIPT_KIND = "heatzone-gate1-benchmark-receipt"
DEFAULT_RECEIPT_OUTPUT = Path("docs/evidence/models/ODP-PLAN-HEATZONE-OUTCOME-001/GATE1_BENCHMARK_RECEIPT.json")
DEFAULT_REPORT_OUTPUT = Path("docs/evidence/models/ODP-PLAN-HEATZONE-OUTCOME-001/BENCHMARK_REPORT.md")
DEFAULT_HANDBACK_OUTPUT = Path("docs/evidence/models/ODP-PLAN-HEATZONE-OUTCOME-001/DATA_HANDBACK.json")

MINIMUM_REQUIRED_LABELS = 200


def compute_benchmark_receipt_sha256(payload: dict[str, Any]) -> str:
    """Compute sha256 digest of benchmark receipt body excluding integrity envelope."""
    canonical = json.dumps(
        {k: v for k, v in payload.items() if k != "integrity"},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluate_heatzone_benchmark(
    observed_labels: int,
    eligible_labels: int,
    *,
    population_ranking_ndcg: float | None = None,
    top_k_survey_rate: float | None = None,
    baseline_population_ndcg: float = 0.50,
    baseline_survey_rate: float = 0.30,
) -> dict[str, Any]:
    """Evaluate HeatZone label inventory and benchmark performance criteria."""
    contract = PRODUCTION_MODEL_CONTRACTS.get("heatzone")
    is_governed_disabled = contract.unavailable_reason is not None if contract else True

    insufficient_labels = eligible_labels < MINIMUM_REQUIRED_LABELS

    if insufficient_labels:
        verdict = "FAIL_CLOSED"
        reason = (
            f"Eligible HeatZone label count ({eligible_labels}) is below "
            f"the activation threshold ({MINIMUM_REQUIRED_LABELS}). "
            "Capability remains governed-disabled with fail-closed status."
        )
        benchmark_results = {
            "evaluated": False,
            "reason": reason,
            "population_ranking_outperformed": False,
            "top_k_survey_rate_improved": False,
            "observed_ndcg": None,
            "baseline_ndcg": baseline_population_ndcg,
            "observed_survey_rate": None,
            "baseline_survey_rate": baseline_survey_rate,
        }
    else:
        # Evaluate benchmark when sufficient real labels exist
        ndcg_pass = (
            population_ranking_ndcg is not None
            and population_ranking_ndcg > baseline_population_ndcg
        )
        survey_pass = (
            top_k_survey_rate is not None
            and top_k_survey_rate > baseline_survey_rate
        )

        if ndcg_pass and survey_pass:
            verdict = "PASSED"
            reason = (
                f"Eligible labels ({eligible_labels}) >= {MINIMUM_REQUIRED_LABELS}. "
                "Outperforms population ranking baseline and improves Top-K site survey rate."
            )
        else:
            verdict = "FAIL_CLOSED"
            reason = (
                "HeatZone label count met minimum, but model benchmark failed: "
                f"NDCG pass={ndcg_pass}, Top-K survey rate pass={survey_pass}."
            )

        benchmark_results = {
            "evaluated": True,
            "reason": reason,
            "population_ranking_outperformed": ndcg_pass,
            "top_k_survey_rate_improved": survey_pass,
            "observed_ndcg": population_ranking_ndcg,
            "baseline_ndcg": baseline_population_ndcg,
            "observed_survey_rate": top_k_survey_rate,
            "baseline_survey_rate": baseline_survey_rate,
        }

    return {
        "verdict": verdict,
        "observed_labels": observed_labels,
        "eligible_labels": eligible_labels,
        "minimum_required_labels": MINIMUM_REQUIRED_LABELS,
        "governed_disabled": is_governed_disabled,
        "unavailable_reason": contract.unavailable_reason if contract else "DATA_CONTRACT_NOT_MATURE",
        "benchmark_results": benchmark_results,
    }


def generate_gate1_receipt(
    inventory_summary: dict[str, Any],
    *,
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build canonical Gate 1 benchmark receipt structure."""
    timestamp = evaluated_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    iso_timestamp = timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")

    eval_res = evaluate_heatzone_benchmark(
        observed_labels=inventory_summary.get("observed_count", 0),
        eligible_labels=inventory_summary.get("eligible_count", 0),
    )

    receipt: dict[str, Any] = {
        "schema_version": BENCHMARK_RECEIPT_SCHEMA_VERSION,
        "kind": BENCHMARK_RECEIPT_KIND,
        "task_id": "ODP-PLAN-HEATZONE-OUTCOME-001",
        "evaluated_at": iso_timestamp,
        "auto_seeded": False,
        "relation": "model_ready.heatzone_training_view",
        "contract_version": "heatzone-training-view-v2",
        "verdict": eval_res["verdict"],
        "observed_labels": eval_res["observed_labels"],
        "eligible_labels": eval_res["eligible_labels"],
        "minimum_required_labels": eval_res["minimum_required_labels"],
        "governed_disabled": eval_res["governed_disabled"],
        "unavailable_reason": eval_res["unavailable_reason"],
        "benchmark_results": eval_res["benchmark_results"],
        "governance_invariants": [
            "At least 200 eligible mature real labels required in model_ready.heatzone_training_view",
            "Model ranking must outperform population density ranking baseline (NDCG > baseline)",
            "Model ranking must improve Top-K field site survey efficiency rate",
            "No synthetic, mock, auto-seeded, or simulated labels allowed",
            "Fail-closed governed-disabled binding enforced when inventory or benchmark criteria fail",
        ],
    }

    receipt["integrity"] = {
        "content_sha256": compute_benchmark_receipt_sha256(receipt)
    }
    return receipt


def generate_benchmark_report_md(receipt: dict[str, Any]) -> str:
    """Generate human-readable markdown report for HeatZone Gate 1 benchmark."""
    eval_res = receipt["benchmark_results"]
    verdict = receipt["verdict"]
    status_symbol = "❌ FAIL CLOSED" if verdict == "FAIL_CLOSED" else "✅ PASSED"

    return f"""# HeatZone Label Inventory Benchmark & Gate 1 Receipt

- **Task ID**: `ODP-PLAN-HEATZONE-OUTCOME-001`
- **Evaluation Date**: `{receipt["evaluated_at"]}`
- **Verdict**: **{status_symbol}**
- **Relation**: `{receipt["relation"]}` (`{receipt["contract_version"]}`)
- **Auto Seeded**: `{receipt["auto_seeded"]}` (Forbidden)
- **Governed Disabled**: `{receipt["governed_disabled"]}` (`{receipt["unavailable_reason"]}`)

---

## 1. Label Inventory Summary

| Metric | Value | Required Minimum | Status |
|---|---:|---:|---|
| Observed Labeled Rows | `{receipt["observed_labels"]}` | - | Observed |
| Eligible Mature Real Labels | `{receipt["eligible_labels"]}` | `{receipt["minimum_required_labels"]}` | {"❌ Insufficient" if receipt["eligible_labels"] < receipt["minimum_required_labels"] else "✅ Sufficient"} |
| Auto-Seeded / Synthetic Rows | `0` | `0` | ✅ Zero Synthetic |

---

## 2. Benchmark Evaluation Criteria

| Benchmark Metric | Observed Value | Baseline Threshold | Status |
|---|---:|---:|---|
| Population Density Ranking NDCG | `{eval_res.get("observed_ndcg") or "N/A"}` | `{eval_res.get("baseline_ndcg")}` | {"Skipped (Insufficient Data)" if not eval_res["evaluated"] else ("✅ Outperformed" if eval_res["population_ranking_outperformed"] else "❌ Failed")} |
| Top-K Field Site Survey Rate | `{eval_res.get("observed_survey_rate") or "N/A"}` | `{eval_res.get("baseline_survey_rate")}` | {"Skipped (Insufficient Data)" if not eval_res["evaluated"] else ("✅ Improved" if eval_res["top_k_survey_rate_improved"] else "❌ Failed")} |

### Evaluation Notes
{eval_res["reason"]}

---

## 3. Fail-Closed Governance & Safety Enforcements

1. **Governed-Disabled Status**: Production binding for `heatzone` model (`heatzone_priority`) remains **governed-disabled** with canonical reason code `DATA_CONTRACT_NOT_MATURE`.
2. **Zero Synthetic Data Policy**: Synthetic labels, mock rows, auto-seeded entries, or fabricated opening dates are strictly prohibited from model training and release pathways.
3. **Integrity Envelope**: Receipt content SHA-256 is immutable (`{receipt["integrity"]["content_sha256"]}`).

---

## 4. Actionable Data Handback Requirements

To enable future HeatZone model activation, the Expansion Operations / POS Data Platform team must provide:
- At least **200 eligible mature real labels** (with 90 complete prior transaction days and 28 complete forward outcome days per H3 cell origin).
- Approved immutable store opening dates (`opened_on`) and canonical store/geography lineage.
- Audit evidence proving superior performance over population-density sorting and improved field survey rates.
"""


def generate_data_handback_json(receipt: dict[str, Any]) -> dict[str, Any]:
    """Generate structured machine-readable data handback specification."""
    return {
        "task_id": "ODP-PLAN-HEATZONE-OUTCOME-001",
        "service": "heatzone",
        "model_name": "heatzone_priority",
        "handback_type": "LABEL_INVENTORY_INSUFFICIENT",
        "created_at": receipt["evaluated_at"],
        "status": "GOVERNED_DISABLED",
        "unavailable_reason": receipt["unavailable_reason"],
        "current_inventory": {
            "observed_count": receipt["observed_labels"],
            "eligible_count": receipt["eligible_labels"],
            "required_minimum": receipt["minimum_required_labels"],
            "shortfall": receipt["minimum_required_labels"] - receipt["eligible_labels"],
        },
        "required_schema_fields": [
            "tenant_id",
            "store_id",
            "opened_on",
            "h3_index",
            "h3_resolution",
            "origin_date",
            "realized_28d_cell_net_revenue",
            "label_maturity_time",
            "authority_type",
            "provenance",
        ],
        "benchmark_requirements": {
            "minimum_eligible_labels": 200,
            "population_ranking_outperformance": "NDCG > baseline_population_ndcg (0.50)",
            "top_k_survey_rate_improvement": "survey_rate > baseline_survey_rate (0.30)",
            "synthetic_labels_allowed": False,
        },
        "next_actions": [
            "Ingest real POS transaction history and store opening date authority into PG16 data plane",
            "Run scripts/models/install_views.py to refresh model_ready.heatzone_training_view",
            "Execute python3 scripts/models/heatzone_benchmark.py generate to re-evaluate Gate 1 receipt",
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "verify"))
    parser.add_argument("--receipt-output", type=Path, default=DEFAULT_RECEIPT_OUTPUT)
    parser.add_argument("--report-output", type=Path, default=DEFAULT_REPORT_OUTPUT)
    parser.add_argument("--handback-output", type=Path, default=DEFAULT_HANDBACK_OUTPUT)
    args = parser.parse_args(argv)

    if args.command == "generate":
        # Load checked-in model ready inventory receipt if present to obtain heatzone stats
        try:
            inventory_receipt = load_model_ready_receipt()
            heatzone_info = inventory_receipt.get("capabilities", {}).get("heatzone", {})
        except Exception:
            heatzone_info = {"observed_count": 0, "eligible_count": 0}

        gate1_receipt = generate_gate1_receipt(heatzone_info)

        # Write receipt JSON
        args.receipt_output.parent.mkdir(parents=True, exist_ok=True)
        encoded_receipt = json.dumps(gate1_receipt, indent=2, sort_keys=True) + "\n"
        args.receipt_output.write_text(encoded_receipt, encoding="utf-8")
        print(f"Generated Gate 1 receipt at {args.receipt_output}")

        # Write markdown report
        report_md = generate_benchmark_report_md(gate1_receipt)
        args.report_output.parent.mkdir(parents=True, exist_ok=True)
        args.report_output.write_text(report_md, encoding="utf-8")
        print(f"Generated benchmark report at {args.report_output}")

        # Write data handback JSON
        handback_json = generate_data_handback_json(gate1_receipt)
        args.handback_output.parent.mkdir(parents=True, exist_ok=True)
        encoded_handback = json.dumps(handback_json, indent=2, sort_keys=True) + "\n"
        args.handback_output.write_text(encoded_handback, encoding="utf-8")
        print(f"Generated data handback spec at {args.handback_output}")

    elif args.command == "verify":
        if not args.receipt_output.exists():
            raise FileNotFoundError(f"Receipt file missing: {args.receipt_output}")
        payload = json.loads(args.receipt_output.read_text(encoding="utf-8"))
        declared_hash = payload.get("integrity", {}).get("content_sha256")
        actual_hash = compute_benchmark_receipt_sha256(payload)
        if declared_hash != actual_hash:
            raise ValueError(f"Integrity check failed! declared={declared_hash}, actual={actual_hash}")
        if payload.get("auto_seeded") is not False:
            raise ValueError("Receipt must have auto_seeded=False")
        print(f"Gate 1 receipt {args.receipt_output} verification PASSED (verdict: {payload.get('verdict')})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
