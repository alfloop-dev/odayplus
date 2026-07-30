"""HeatZone Label Benchmark Evaluation and Gate 1 Receipt Generator.

Audits HeatZone model-ready label inventory against canonical requirements:
- Minimum 200 eligible mature real labels in model_ready.heatzone_training_view.
- Outperformance relative to population ranking baseline (NDCG / Top-K precision).
- Improvement of Top-K field site survey rate (Top-K 現勘率).
- Strict fail-closed governance (governed-disabled) when inventory or benchmark criteria fail.
- Binding of Gate 1 receipt to canonical model-ready inventory lineage (version, observed_at, sha256).
- Prohibition of synthetic, mock, or auto-seeded label rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
AUTHORITATIVE_EVIDENCE_PATH = (
    ROOT / "docs/evidence/models/ODP-PLAN-HEATZONE-OUTCOME-001/AUTHORITATIVE_EVIDENCE.json"
)

MINIMUM_REQUIRED_LABELS = 200
HEX_64_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def compute_benchmark_receipt_sha256(payload: dict[str, Any]) -> str:
    """Compute sha256 digest of benchmark receipt body excluding integrity envelope."""
    canonical = json.dumps(
        {k: v for k, v in payload.items() if k != "integrity"},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_metric_value(name: str, value: float | None) -> None:
    """Ensure metric values are finite numbers in [0.0, 1.0]."""
    if value is None:
        return
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number in [0.0, 1.0], got {value!r}")
    val_float = float(value)
    if math.isnan(val_float) or math.isinf(val_float):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    if not (0.0 <= val_float <= 1.0):
        raise ValueError(f"{name} must be in range [0.0, 1.0], got {value!r}")


def resolve_heatzone_benchmark_evidence(
    evidence: dict[str, Any] | None,
    *,
    authoritative_path: Path | None = None,
) -> bool:
    """Resolve and hash-check benchmark evidence against authoritative immutable evidence.

    Returns True if evidence is structurally valid (all 64-hex SHA-256 hashes and non-empty evaluation split)
    AND matches the registered authoritative benchmark evidence artifact on disk.
    If no registered authoritative evidence artifact exists or the evidence does not match, returns False.
    """
    if not isinstance(evidence, dict) or not evidence:
        return False

    required_hashes = (
        "dataset_snapshot_hash",
        "model_artifact_hash",
        "governed_baseline_hash",
    )
    for field in required_hashes:
        val = evidence.get(field)
        if not isinstance(val, str) or not HEX_64_PATTERN.match(val):
            return False

    split = evidence.get("evaluation_split")
    if not isinstance(split, str) or not split.strip():
        return False

    auth_path = authoritative_path or AUTHORITATIVE_EVIDENCE_PATH
    if not auth_path.exists():
        return False

    try:
        content = json.loads(auth_path.read_text(encoding="utf-8"))
        if not isinstance(content, dict):
            return False
        for field in (
            "dataset_snapshot_hash",
            "model_artifact_hash",
            "evaluation_split",
            "governed_baseline_hash",
        ):
            if evidence.get(field) != content.get(field):
                return False
        return True
    except Exception:
        return False


def _validate_benchmark_evidence(
    evidence: dict[str, Any] | None,
    *,
    authoritative_path: Path | None = None,
) -> bool:
    """Validate presence, 64-hex format, and authoritative resolution of benchmark evidence."""
    return resolve_heatzone_benchmark_evidence(evidence, authoritative_path=authoritative_path)


def evaluate_heatzone_benchmark(
    observed_labels: int,
    eligible_labels: int,
    *,
    population_ranking_ndcg: float | None = None,
    top_k_survey_rate: float | None = None,
    baseline_population_ndcg: float = 0.50,
    baseline_survey_rate: float = 0.30,
    benchmark_evidence: dict[str, Any] | None = None,
    authoritative_evidence_path: Path | None = None,
) -> dict[str, Any]:
    """Evaluate HeatZone label inventory and benchmark performance criteria."""
    if (
        not isinstance(observed_labels, int)
        or isinstance(observed_labels, bool)
        or observed_labels < 0
    ):
        raise ValueError(f"observed_labels must be a non-negative integer, got {observed_labels!r}")
    if (
        not isinstance(eligible_labels, int)
        or isinstance(eligible_labels, bool)
        or eligible_labels < 0
    ):
        raise ValueError(f"eligible_labels must be a non-negative integer, got {eligible_labels!r}")
    if eligible_labels > observed_labels:
        raise ValueError(
            f"eligible_labels ({eligible_labels}) cannot exceed observed_labels ({observed_labels})"
        )

    _validate_metric_value("population_ranking_ndcg", population_ranking_ndcg)
    _validate_metric_value("top_k_survey_rate", top_k_survey_rate)
    _validate_metric_value("baseline_population_ndcg", baseline_population_ndcg)
    _validate_metric_value("baseline_survey_rate", baseline_survey_rate)

    contract = PRODUCTION_MODEL_CONTRACTS.get("heatzone")
    contract_reason = contract.unavailable_reason if contract else "DATA_CONTRACT_NOT_MATURE"

    insufficient_labels = eligible_labels < MINIMUM_REQUIRED_LABELS

    if insufficient_labels:
        verdict = "FAIL_CLOSED"
        governed_disabled = True
        unavailable_reason = contract_reason or "DATA_CONTRACT_NOT_MATURE"
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
        evidence_valid = _validate_benchmark_evidence(
            benchmark_evidence, authoritative_path=authoritative_evidence_path
        )
        ndcg_pass = (
            population_ranking_ndcg is not None
            and population_ranking_ndcg > baseline_population_ndcg
        )
        survey_pass = (
            top_k_survey_rate is not None
            and top_k_survey_rate > baseline_survey_rate
        )

        if not evidence_valid:
            verdict = "FAIL_CLOSED"
            governed_disabled = True
            unavailable_reason = "BENCHMARK_EVIDENCE_NOT_RESOLVED"
            reason = (
                f"Eligible labels ({eligible_labels}) >= {MINIMUM_REQUIRED_LABELS}, "
                "but immutable measured benchmark evidence (64-hex snapshot/model/baseline hashes, "
                "evaluation split) is unresolved against authoritative evidence."
            )
            benchmark_results = {
                "evaluated": False,
                "reason": reason,
                "population_ranking_outperformed": False,
                "top_k_survey_rate_improved": False,
                "observed_ndcg": population_ranking_ndcg,
                "baseline_ndcg": baseline_population_ndcg,
                "observed_survey_rate": top_k_survey_rate,
                "baseline_survey_rate": baseline_survey_rate,
            }
        elif ndcg_pass and survey_pass:
            verdict = "PASSED"
            governed_disabled = False
            unavailable_reason = None
            reason = (
                f"Eligible labels ({eligible_labels}) >= {MINIMUM_REQUIRED_LABELS}. "
                "Outperforms population ranking baseline and improves Top-K site survey rate "
                "with resolved immutable benchmark evidence."
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
        else:
            verdict = "FAIL_CLOSED"
            governed_disabled = True
            unavailable_reason = "BENCHMARK_METRICS_NOT_MET"
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

    res: dict[str, Any] = {
        "verdict": verdict,
        "observed_labels": observed_labels,
        "eligible_labels": eligible_labels,
        "minimum_required_labels": MINIMUM_REQUIRED_LABELS,
        "governed_disabled": governed_disabled,
        "unavailable_reason": unavailable_reason,
        "benchmark_results": benchmark_results,
    }
    if benchmark_evidence is not None:
        res["benchmark_evidence"] = benchmark_evidence
    return res


def generate_gate1_receipt(
    inventory_receipt: dict[str, Any] | None = None,
    *,
    evaluated_at: datetime | None = None,
    observed_labels: int | None = None,
    eligible_labels: int | None = None,
    population_ranking_ndcg: float | None = None,
    top_k_survey_rate: float | None = None,
    benchmark_evidence: dict[str, Any] | None = None,
    authoritative_evidence_path: Path | None = None,
) -> dict[str, Any]:
    """Build canonical Gate 1 benchmark receipt structure bound to inventory lineage."""
    if inventory_receipt is None:
        inventory_receipt = load_model_ready_receipt()

    inventory_version = inventory_receipt.get("inventory_version")
    if not isinstance(inventory_version, str) or not inventory_version.strip():
        raise ValueError("inventory_receipt missing valid inventory_version")

    inventory_observed_at = inventory_receipt.get("observed_at")
    if not isinstance(inventory_observed_at, str) or not inventory_observed_at.strip():
        raise ValueError("inventory_receipt missing valid observed_at")

    inventory_integrity = inventory_receipt.get("integrity", {})
    inventory_sha256 = (
        inventory_integrity.get("content_sha256")
        if isinstance(inventory_integrity, dict)
        else None
    )
    if not isinstance(inventory_sha256, str) or not inventory_sha256.strip():
        raise ValueError("inventory_receipt missing valid integrity.content_sha256")

    heatzone_cap = inventory_receipt.get("capabilities", {}).get("heatzone", {})
    if not isinstance(heatzone_cap, dict):
        raise ValueError("inventory_receipt capabilities missing heatzone entry")

    relation = heatzone_cap.get("relation", "model_ready.heatzone_training_view")
    contract_version = heatzone_cap.get("view_version", "heatzone-training-view-v2")

    inv_observed = heatzone_cap.get("observed_count", 0)
    inv_eligible = heatzone_cap.get("eligible_count", 0)

    obs_cnt = observed_labels if observed_labels is not None else inv_observed
    elg_cnt = eligible_labels if eligible_labels is not None else inv_eligible

    timestamp = evaluated_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    iso_timestamp = timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")

    eval_res = evaluate_heatzone_benchmark(
        observed_labels=obs_cnt,
        eligible_labels=elg_cnt,
        population_ranking_ndcg=population_ranking_ndcg,
        top_k_survey_rate=top_k_survey_rate,
        benchmark_evidence=benchmark_evidence,
        authoritative_evidence_path=authoritative_evidence_path,
    )

    receipt: dict[str, Any] = {
        "schema_version": BENCHMARK_RECEIPT_SCHEMA_VERSION,
        "kind": BENCHMARK_RECEIPT_KIND,
        "task_id": "ODP-PLAN-HEATZONE-OUTCOME-001",
        "evaluated_at": iso_timestamp,
        "inventory_version": inventory_version,
        "inventory_observed_at": inventory_observed_at,
        "inventory_sha256": inventory_sha256,
        "auto_seeded": False,
        "relation": relation,
        "contract_version": contract_version,
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
            "Gate 1 receipt must bind immutably to the PG16 model-ready inventory receipt lineage",
        ],
    }
    if "benchmark_evidence" in eval_res:
        receipt["benchmark_evidence"] = eval_res["benchmark_evidence"]

    receipt["integrity"] = {
        "content_sha256": compute_benchmark_receipt_sha256(receipt)
    }
    return receipt


def validate_gate1_receipt(
    receipt: dict[str, Any],
    inventory_receipt: dict[str, Any] | None = None,
    *,
    authoritative_evidence_path: Path | None = None,
) -> None:
    """Fail-closed validation of Gate 1 receipt schema, integrity, governance, and lineage."""
    if not isinstance(receipt, dict):
        raise ValueError("Gate 1 receipt must be a JSON dictionary")

    # 1. Integrity hash
    integrity = receipt.get("integrity")
    declared_hash = (
        integrity.get("content_sha256") if isinstance(integrity, dict) else None
    )
    actual_hash = compute_benchmark_receipt_sha256(receipt)
    if declared_hash != actual_hash:
        raise ValueError(
            f"Gate 1 receipt integrity mismatch: declared={declared_hash!r}, actual={actual_hash!r}"
        )

    # 2. Schema and task metadata
    if receipt.get("schema_version") != BENCHMARK_RECEIPT_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported receipt schema_version={receipt.get('schema_version')!r}"
        )
    if receipt.get("kind") != BENCHMARK_RECEIPT_KIND:
        raise ValueError(f"Unexpected receipt kind={receipt.get('kind')!r}")
    if receipt.get("task_id") != "ODP-PLAN-HEATZONE-OUTCOME-001":
        raise ValueError(f"Unexpected task_id={receipt.get('task_id')!r}")
    if receipt.get("auto_seeded") is not False:
        raise ValueError("Gate 1 receipt auto_seeded must be False; synthetic labels forbidden")

    # 3. Lineage fields
    for text_field in (
        "inventory_version",
        "inventory_observed_at",
        "inventory_sha256",
        "relation",
        "contract_version",
    ):
        val = receipt.get(text_field)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"Gate 1 receipt field {text_field!r} is missing or empty")

    # 4. Count invariants
    observed_labels = receipt.get("observed_labels")
    eligible_labels = receipt.get("eligible_labels")
    minimum_required = receipt.get("minimum_required_labels")

    if (
        not isinstance(observed_labels, int)
        or isinstance(observed_labels, bool)
        or observed_labels < 0
    ):
        raise ValueError(f"observed_labels must be a non-negative integer, got {observed_labels!r}")
    if (
        not isinstance(eligible_labels, int)
        or isinstance(eligible_labels, bool)
        or eligible_labels < 0
    ):
        raise ValueError(f"eligible_labels must be a non-negative integer, got {eligible_labels!r}")
    if eligible_labels > observed_labels:
        raise ValueError(
            f"eligible_labels ({eligible_labels}) cannot exceed observed_labels ({observed_labels})"
        )
    if minimum_required != MINIMUM_REQUIRED_LABELS:
        raise ValueError(
            f"minimum_required_labels must be {MINIMUM_REQUIRED_LABELS}, got {minimum_required!r}"
        )

    # 5. Verdict and Governance invariants
    verdict = receipt.get("verdict")
    governed_disabled = receipt.get("governed_disabled")
    unavailable_reason = receipt.get("unavailable_reason")
    benchmark_results = receipt.get("benchmark_results")

    if not isinstance(benchmark_results, dict):
        raise ValueError("Gate 1 receipt benchmark_results must be a dictionary")

    # Reject non-finite / out-of-domain metric values
    obs_ndcg = benchmark_results.get("observed_ndcg")
    base_ndcg = benchmark_results.get("baseline_ndcg")
    obs_survey = benchmark_results.get("observed_survey_rate")
    base_survey = benchmark_results.get("baseline_survey_rate")
    _validate_metric_value("benchmark_results.observed_ndcg", obs_ndcg)
    _validate_metric_value("benchmark_results.baseline_ndcg", base_ndcg)
    _validate_metric_value("benchmark_results.observed_survey_rate", obs_survey)
    _validate_metric_value("benchmark_results.baseline_survey_rate", base_survey)

    if eligible_labels < MINIMUM_REQUIRED_LABELS:
        if verdict != "FAIL_CLOSED":
            raise ValueError(
                f"Receipt verdict must be FAIL_CLOSED when eligible_labels ({eligible_labels}) "
                f"< {MINIMUM_REQUIRED_LABELS}, got {verdict!r}"
            )
        if governed_disabled is not True:
            raise ValueError(
                "governed_disabled must be True when eligible_labels < MINIMUM_REQUIRED_LABELS"
            )
        if not isinstance(unavailable_reason, str) or not unavailable_reason.strip():
            raise ValueError("unavailable_reason is required when governed_disabled is True")

    if verdict == "PASSED":
        if eligible_labels < MINIMUM_REQUIRED_LABELS:
            raise ValueError(f"Verdict PASSED requires eligible_labels >= {MINIMUM_REQUIRED_LABELS}")
        if governed_disabled is not False:
            raise ValueError("Verdict PASSED contradicts governed_disabled=True")
        if unavailable_reason is not None:
            raise ValueError(f"Verdict PASSED contradicts unavailable_reason={unavailable_reason!r}")
        if benchmark_results.get("evaluated") is not True:
            raise ValueError("Verdict PASSED requires benchmark_results.evaluated=True")

        if obs_ndcg is None or base_ndcg is None:
            raise ValueError("Verdict PASSED requires non-null observed_ndcg and baseline_ndcg")
        if obs_survey is None or base_survey is None:
            raise ValueError("Verdict PASSED requires non-null observed_survey_rate and baseline_survey_rate")

        if not (obs_ndcg > base_ndcg):
            raise ValueError(
                f"Verdict PASSED requires observed_ndcg ({obs_ndcg}) > baseline_ndcg ({base_ndcg})"
            )
        if not (obs_survey > base_survey):
            raise ValueError(
                f"Verdict PASSED requires observed_survey_rate ({obs_survey}) > baseline_survey_rate ({base_survey})"
            )

        if benchmark_results.get("population_ranking_outperformed") is not True:
            raise ValueError("Verdict PASSED requires population_ranking_outperformed=True")
        if benchmark_results.get("top_k_survey_rate_improved") is not True:
            raise ValueError("Verdict PASSED requires top_k_survey_rate_improved=True")

        benchmark_evidence = receipt.get("benchmark_evidence")
        if not _validate_benchmark_evidence(
            benchmark_evidence, authoritative_path=authoritative_evidence_path
        ):
            raise ValueError(
                "Verdict PASSED requires valid immutable benchmark_evidence containing valid 64-hex "
                "dataset_snapshot_hash, model_artifact_hash, evaluation_split, and governed_baseline_hash "
                "resolved against registered authoritative evidence"
            )
    elif verdict == "FAIL_CLOSED":
        if governed_disabled is not True:
            raise ValueError("Verdict FAIL_CLOSED requires governed_disabled=True")
        if not isinstance(unavailable_reason, str) or not unavailable_reason.strip():
            raise ValueError("Verdict FAIL_CLOSED requires a non-empty unavailable_reason")
    else:
        raise ValueError(f"Invalid receipt verdict={verdict!r}")

    # 6. Lineage cross-validation against canonical inventory receipt (FAIL CLOSED IF UNLOADABLE)
    if inventory_receipt is None:
        try:
            inventory_receipt = load_model_ready_receipt()
        except Exception as exc:
            raise ValueError(
                f"Gate 1 receipt lineage validation failed closed: canonical model-ready inventory receipt could not be loaded ({exc})"
            ) from exc

    if not isinstance(inventory_receipt, dict):
        raise ValueError("Canonical inventory_receipt must be a JSON dictionary")

    if receipt["inventory_version"] != inventory_receipt.get("inventory_version"):
        raise ValueError(
            f"Receipt inventory_version {receipt['inventory_version']!r} does not match "
            f"inventory receipt {inventory_receipt.get('inventory_version')!r}"
        )
    if receipt["inventory_observed_at"] != inventory_receipt.get("observed_at"):
        raise ValueError(
            f"Receipt inventory_observed_at {receipt['inventory_observed_at']!r} does not match "
            f"inventory receipt {inventory_receipt.get('observed_at')!r}"
        )
    inv_sha = inventory_receipt.get("integrity", {}).get("content_sha256")
    if receipt["inventory_sha256"] != inv_sha:
        raise ValueError(
            f"Receipt inventory_sha256 {receipt['inventory_sha256']!r} does not match "
            f"inventory receipt SHA {inv_sha!r}"
        )

    heatzone_cap = inventory_receipt.get("capabilities", {}).get("heatzone", {})
    if receipt["relation"] != heatzone_cap.get("relation"):
        raise ValueError(
            f"Receipt relation {receipt['relation']!r} does not match inventory {heatzone_cap.get('relation')!r}"
        )
    if receipt["contract_version"] != heatzone_cap.get("view_version"):
        raise ValueError(
            f"Receipt contract_version {receipt['contract_version']!r} does not match inventory {heatzone_cap.get('view_version')!r}"
        )
    if receipt["observed_labels"] != heatzone_cap.get("observed_count"):
        raise ValueError(
            f"Receipt observed_labels {receipt['observed_labels']!r} does not match inventory {heatzone_cap.get('observed_count')!r}"
        )
    if receipt["eligible_labels"] != heatzone_cap.get("eligible_count"):
        raise ValueError(
            f"Receipt eligible_labels {receipt['eligible_labels']!r} does not match inventory {heatzone_cap.get('eligible_count')!r}"
        )


def generate_benchmark_report_md(receipt: dict[str, Any]) -> str:
    """Generate human-readable markdown report for HeatZone Gate 1 benchmark."""
    eval_res = receipt["benchmark_results"]
    verdict = receipt["verdict"]
    is_passed = verdict == "PASSED"
    status_symbol = "✅ PASSED" if is_passed else "❌ FAIL CLOSED"

    gov_disabled_str = str(receipt["governed_disabled"])
    reason_str = receipt.get("unavailable_reason") or "None (Gate 1 Passed)"

    if is_passed:
        sec3_governance = (
            "1. **Capability Binding Status**: Production binding for `heatzone` model (`heatzone_priority`) "
            "is **APPROVED FOR ACTIVATION** (`governed_disabled = False`).\n"
            "2. **Zero Synthetic Data Policy**: Synthetic labels, mock rows, auto-seeded entries, or "
            "fabricated opening dates are strictly prohibited. Verified real mature labels only.\n"
            f"3. **Integrity Envelope**: Receipt content SHA-256 is immutable (`{receipt['integrity']['content_sha256']}`)."
        )
        sec4_handback = (
            "## 4. Activation Status & Next Steps\n\n"
            "- HeatZone Gate 1 label inventory and benchmark performance criteria are **PASSED**.\n"
            "- Capability is ready for downstream model deployment and release workflows.\n"
        )
    else:
        sec3_governance = (
            f"1. **Governed-Disabled Status**: Production binding for `heatzone` model (`heatzone_priority`) "
            f"remains **governed-disabled** with canonical reason code `{receipt['unavailable_reason']}`.\n"
            "2. **Zero Synthetic Data Policy**: Synthetic labels, mock rows, auto-seeded entries, or "
            "fabricated opening dates are strictly prohibited from model training and release pathways.\n"
            f"3. **Integrity Envelope**: Receipt content SHA-256 is immutable (`{receipt['integrity']['content_sha256']}`)."
        )
        sec4_handback = (
            "## 4. Actionable Data Handback Requirements\n\n"
            "To enable future HeatZone model activation, the Expansion Operations / POS Data Platform team must provide:\n"
            "- At least **200 eligible mature real labels** (with 90 complete prior transaction days and 28 complete forward outcome days per H3 cell origin).\n"
            "- Approved immutable store opening dates (`opened_on`) and canonical store/geography lineage.\n"
            "- Audit evidence proving superior performance over population-density sorting and improved field survey rates.\n"
        )

    obs_ndcg_str = (
        f"{eval_res.get('observed_ndcg')}"
        if eval_res.get("observed_ndcg") is not None
        else "N/A"
    )
    obs_survey_str = (
        f"{eval_res.get('observed_survey_rate')}"
        if eval_res.get("observed_survey_rate") is not None
        else "N/A"
    )

    return f"""# HeatZone Label Inventory Benchmark & Gate 1 Receipt

- **Task ID**: `ODP-PLAN-HEATZONE-OUTCOME-001`
- **Evaluation Date**: `{receipt["evaluated_at"]}`
- **Verdict**: **{status_symbol}**
- **Inventory Lineage Version**: `{receipt["inventory_version"]}`
- **Inventory Observed At**: `{receipt["inventory_observed_at"]}`
- **Inventory SHA256**: `{receipt["inventory_sha256"]}`
- **Relation**: `{receipt["relation"]}` (`{receipt["contract_version"]}`)
- **Auto Seeded**: `{receipt["auto_seeded"]}` (Forbidden)
- **Governed Disabled**: `{gov_disabled_str}` (`{reason_str}`)

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
| Population Density Ranking NDCG | `{obs_ndcg_str}` | `{eval_res.get("baseline_ndcg")}` | {"Skipped (Insufficient Data)" if not eval_res["evaluated"] else ("✅ Outperformed" if eval_res["population_ranking_outperformed"] else "❌ Failed")} |
| Top-K Field Site Survey Rate | `{obs_survey_str}` | `{eval_res.get("baseline_survey_rate")}` | {"Skipped (Insufficient Data)" if not eval_res["evaluated"] else ("✅ Improved" if eval_res["top_k_survey_rate_improved"] else "❌ Failed")} |

### Evaluation Notes
{eval_res["reason"]}

---

## 3. Fail-Closed Governance & Safety Enforcements

{sec3_governance}

---

{sec4_handback.rstrip()}
"""


def generate_data_handback_json(receipt: dict[str, Any]) -> dict[str, Any]:
    """Generate structured machine-readable data handback specification."""
    verdict = receipt["verdict"]
    is_passed = verdict == "PASSED"
    eligible_labels = receipt["eligible_labels"]
    min_required = receipt["minimum_required_labels"]
    shortfall = max(0, min_required - eligible_labels)

    if is_passed:
        handback_type = "GATE1_BENCHMARK_PASSED"
        status = "PASSED"
        unavailable_reason = None
        next_actions = [
            "Proceed to downstream HeatZone model training and release pathways",
        ]
    else:
        status = "GOVERNED_DISABLED"
        unavailable_reason = receipt["unavailable_reason"]
        if eligible_labels < min_required:
            handback_type = "LABEL_INVENTORY_INSUFFICIENT"
        elif unavailable_reason == "BENCHMARK_EVIDENCE_NOT_RESOLVED":
            handback_type = "BENCHMARK_EVIDENCE_NOT_RESOLVED"
        else:
            handback_type = "BENCHMARK_METRICS_NOT_MET"
        next_actions = [
            "Ingest real POS transaction history and store opening date authority into PG16 data plane",
            "Run scripts/models/install_views.py to refresh model_ready.heatzone_training_view",
            "Execute python3 scripts/models/heatzone_benchmark.py generate to re-evaluate Gate 1 receipt",
        ]

    return {
        "task_id": "ODP-PLAN-HEATZONE-OUTCOME-001",
        "service": "heatzone",
        "model_name": "heatzone_priority",
        "handback_type": handback_type,
        "created_at": receipt["evaluated_at"],
        "status": status,
        "unavailable_reason": unavailable_reason,
        "inventory_lineage": {
            "inventory_version": receipt["inventory_version"],
            "inventory_observed_at": receipt["inventory_observed_at"],
            "inventory_sha256": receipt["inventory_sha256"],
            "relation": receipt["relation"],
            "contract_version": receipt["contract_version"],
        },
        "current_inventory": {
            "observed_count": receipt["observed_labels"],
            "eligible_count": eligible_labels,
            "required_minimum": min_required,
            "shortfall": shortfall,
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
        "next_actions": next_actions,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "verify"))
    parser.add_argument("--receipt-output", type=Path, default=DEFAULT_RECEIPT_OUTPUT)
    parser.add_argument("--report-output", type=Path, default=DEFAULT_REPORT_OUTPUT)
    parser.add_argument("--handback-output", type=Path, default=DEFAULT_HANDBACK_OUTPUT)
    args = parser.parse_args(argv)

    if args.command == "generate":
        inventory_receipt = load_model_ready_receipt()
        gate1_receipt = generate_gate1_receipt(inventory_receipt)

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

        # Fail-closed validate the generated receipt
        validate_gate1_receipt(gate1_receipt, inventory_receipt)

    elif args.command == "verify":
        if not args.receipt_output.exists():
            raise FileNotFoundError(f"Receipt file missing: {args.receipt_output}")
        payload = json.loads(args.receipt_output.read_text(encoding="utf-8"))
        validate_gate1_receipt(payload)
        print(f"Gate 1 receipt {args.receipt_output} verification PASSED (verdict: {payload.get('verdict')})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
