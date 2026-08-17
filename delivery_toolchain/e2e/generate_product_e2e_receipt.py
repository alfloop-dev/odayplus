#!/usr/bin/env python3
"""Generate a product E2E receipt from two sealed, exact-source raw artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from delivery_toolchain.e2e.product_e2e_receipt import (
    RAW_PLAYWRIGHT_PATH,
    RAW_PYTEST_PATH,
    RECEIPT_PATH,
    SCHEMA_VERSION,
    bind_scenarios,
    expected_aggregate_counts,
    iso_now,
    read_json,
    seal_normalized,
    sha256_bytes,
    validate_raw_artifact,
    verify_evidence_relationship,
)


def generate_receipt(root: Path = ROOT) -> dict[str, Any]:
    paths = {
        "playwright": root / RAW_PLAYWRIGHT_PATH,
        "pytest": root / RAW_PYTEST_PATH,
    }
    artifacts: dict[str, dict[str, Any]] = {}
    artifact_hashes: dict[str, str] = {}
    validation_errors: list[str] = []
    for runner, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"raw {runner} artifact missing: {path}")
        raw_bytes = path.read_bytes()
        artifact_hashes[runner] = sha256_bytes(raw_bytes)
        artifact = read_json(path)
        artifacts[runner] = artifact
        validation_errors.extend(validate_raw_artifact(artifact, runner))

    sources = [artifact.get("source") for artifact in artifacts.values()]
    tested_source = sources[0]
    if tested_source != sources[1]:
        validation_errors.append(
            "Playwright and Pytest artifacts do not share the exact source/tree"
        )
    evidence_proof: dict[str, Any] = {}
    if isinstance(tested_source, dict):
        evidence_proof, proof_errors = verify_evidence_relationship(
            root, tested_source, allow_worktree_evidence=True
        )
        validation_errors.extend(proof_errors)
    else:
        validation_errors.append("raw artifacts have no valid tested source")
        tested_source = {}

    scenario_results, binding_errors = bind_scenarios(artifacts, artifact_hashes)
    validation_errors.extend(binding_errors)
    runner_counts = {runner: artifact.get("counts") for runner, artifact in artifacts.items()}
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "receipt_id": "ODP-PRODUCT-E2E-RECEIPT-002",
        "task_id": "ODP-PLAN-ACCEPTANCE-REAL-EXEC-001",
        "run_id": f"product-e2e-{iso_now()}",
        "generated_at": iso_now(),
        "tested_source": tested_source,
        "evidence_proof_at_generation": evidence_proof,
        "artifacts": [
            {
                "runner": runner,
                "path": (RAW_PLAYWRIGHT_PATH if runner == "playwright" else RAW_PYTEST_PATH),
                "sha256": artifact_hashes[runner],
                "normalized_artifact_sha256": artifact.get("normalized_artifact_sha256"),
                **{
                    field: artifact.get("run", {}).get(field)
                    for field in (
                        "command",
                        "version",
                        "started_at",
                        "ended_at",
                        "exit_code",
                        "environment",
                    )
                },
            }
            for runner, artifact in artifacts.items()
        ],
        "runner_counts": runner_counts,
        "aggregate_counts": expected_aggregate_counts(artifacts),
        "scenario_results": scenario_results,
        "validation_errors": validation_errors,
        "exit_code": 0 if not validation_errors else 1,
        "status": "passed" if not validation_errors else "failed",
    }
    seal_normalized(receipt, "normalized_receipt_sha256")
    output_path = root / RECEIPT_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Receipt written to {output_path} "
        f"(status={receipt['status']}, errors={len(validation_errors)})"
    )
    return receipt


if __name__ == "__main__":
    generated = generate_receipt()
    raise SystemExit(int(generated["exit_code"]))
