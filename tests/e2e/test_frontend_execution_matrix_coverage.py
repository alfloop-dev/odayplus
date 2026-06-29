"""Verify FE fleet dispatch tasks stay tied to product E2E proof.

The design-to-frontend execution matrix is the handoff contract used to split
frontend work across implementation fleets. This test makes that contract part
of CI so future edits cannot remove a workflow from the product E2E gate while
the matrix still claims product-grade acceptance.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs/design/ODAY_PLUS_DESIGN_TO_FRONTEND_EXECUTION_MATRIX.md"
RUNNER = ROOT / "scripts/e2e/run_product_e2e.sh"
RELEASE_GATE = ROOT / "scripts/e2e/check_product_release_gate.py"


FE_TASKS = {
    "FE-R0-001": {
        "keywords": ("OpsBoard App Shell", "Task Center"),
        "specs": ("tests/e2e/e2e-api-bound-ui.spec.ts",),
    },
    "FE-EXP-001": {
        "keywords": ("HeatZone Map and Ranking",),
        "specs": ("tests/e2e/e2e-map.spec.ts", "tests/e2e/e2e-expansion-product.spec.ts"),
    },
    "FE-EXP-002": {
        "keywords": ("Listing to Candidate Site Workflow",),
        "specs": ("tests/e2e/e2e-expansion-product.spec.ts",),
    },
    "FE-EXP-003": {
        "keywords": ("SiteScore Report and Opening Approval",),
        "specs": ("tests/e2e/e2e-expansion-product.spec.ts",),
    },
    "FE-OPS-001": {
        "keywords": ("Operations Alert Workbench",),
        "specs": ("tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts",),
    },
    "FE-INT-001": {
        "keywords": ("Intervention Lifecycle",),
        "specs": ("tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts",),
    },
    "FE-PRICE-001": {
        "keywords": ("PriceOps Simulation", "Pricing approval and rollback"),
        "specs": ("tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts",),
    },
    "FE-AD-001": {
        "keywords": ("AdLift Candidate", "AdLift incrementality"),
        "specs": ("tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts",),
    },
    "FE-AVM-001": {
        "keywords": ("Asset Valuation and DataRoom", "AVM valuation"),
        "specs": ("tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts",),
    },
    "FE-NET-001": {
        "keywords": ("NetPlan Scenario Builder", "NetPlan solve"),
        "specs": ("tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts",),
    },
    "FE-LEARN-001": {
        "keywords": ("Learning Hub Model Governance", "Model release and rollback"),
        "specs": ("tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts",),
    },
    "FE-AUDIT-001": {
        "keywords": ("Audit Decision Log", "Decision audit export"),
        "specs": ("tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts",),
    },
    "FE-XCUT-001": {
        "keywords": ("Design token package",),
        "specs": ("tests/e2e/product-e2e-env.spec.ts",),
    },
    "FE-XCUT-005": {
        "keywords": ("Job and audit UX",),
        "specs": ("tests/e2e/product-e2e-env.spec.ts",),
    },
    "FE-XCUT-006": {
        "keywords": ("Map and chart fallback",),
        "specs": ("tests/e2e/e2e-map.spec.ts",),
    },
}


def test_frontend_execution_matrix_names_all_fleet_tasks() -> None:
    matrix_text = MATRIX.read_text(encoding="utf-8")

    for task_id, expectation in FE_TASKS.items():
        assert task_id in matrix_text
        for keyword in expectation["keywords"]:
            assert keyword in matrix_text


def test_product_e2e_runner_includes_specs_for_each_dispatch_workflow() -> None:
    runner_text = RUNNER.read_text(encoding="utf-8")

    for task_id, expectation in FE_TASKS.items():
        missing = [spec for spec in expectation["specs"] if spec not in runner_text]
        assert not missing, f"{task_id} is missing product E2E runner specs: {missing}"


def test_release_gate_static_check_tracks_same_product_e2e_specs() -> None:
    runner_text = RUNNER.read_text(encoding="utf-8")
    release_gate_text = RELEASE_GATE.read_text(encoding="utf-8")

    runner_specs = {
        line.strip().rstrip(" \\")
        for line in runner_text.splitlines()
        if line.strip().startswith("tests/e2e/") and line.strip().endswith((".spec.ts", ".spec.ts \\"))
    }

    for spec in runner_specs:
        assert spec in release_gate_text
