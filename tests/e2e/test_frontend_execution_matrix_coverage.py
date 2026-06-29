"""Verify FE fleet dispatch tasks stay tied to product E2E proof.

The design-to-frontend execution matrix is the handoff contract used to split
frontend work across implementation fleets. This test makes that contract part
of CI so future edits cannot remove a workflow from the product E2E gate while
the matrix still claims product-grade acceptance.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs/design/ODAY_PLUS_DESIGN_TO_FRONTEND_EXECUTION_MATRIX.md"
FLEET_DISPATCH = ROOT / "docs/evidence/PRODUCT_VALIDATION_FLEET_DISPATCH.md"
COMPLETION_AUDIT = ROOT / "docs/evidence/FRONTEND_FLEET_COMPLETION_AUDIT.md"
GO_NO_GO = ROOT / "docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md"
READINESS_REPORT = ROOT / "docs/evidence/PRODUCT_E2E_READINESS_REPORT.md"
CLOSEOUT_MANIFEST = ROOT / "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_MANIFEST.md"
RUNNER = ROOT / "scripts/e2e/run_product_e2e.sh"
RELEASE_GATE = ROOT / "scripts/e2e/check_product_release_gate.py"
HARDCODED_DEV_RELEASE_REF = re.compile(r"dev@[0-9a-f]{7,40}")
STALE_RELEASE_REFS = (
    "dev@8834cc819051c2ebda8f531f467a67b07cc547e4",
    "dev@d9d637a351cdacfa98184a91b64a403098aabfa6",
    "dev@27f5ba0301b143e3b1ca544d44de3ecac4f97cfa",
    "PR #80",
)


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


ODP_FE_TASKS = {
    "ODP-FE-R0-001": (("FE-R0-001", "FE-R0-002"), ("tests/e2e/e2e-api-bound-ui.spec.ts",)),
    "ODP-FE-EXP-001": (
        ("FE-EXP-001", "FE-EXP-002", "FE-EXP-003"),
        ("tests/e2e/e2e-map.spec.ts", "tests/e2e/e2e-expansion-product.spec.ts"),
    ),
    "ODP-FE-OPS-001": (
        ("FE-OPS-001", "FE-INT-001"),
        ("tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts",),
    ),
    "ODP-FE-PRICE-001": (
        ("FE-PRICE-001", "FE-AD-001"),
        ("tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts",),
    ),
    "ODP-FE-ASSET-001": (
        ("FE-AVM-001", "FE-NET-001"),
        ("tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts",),
    ),
    "ODP-FE-LEARN-001": (
        ("FE-LEARN-001", "FE-AUDIT-001"),
        ("tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts",),
    ),
    "ODP-FE-XCUT-001": (
        ("FE-XCUT-001", "FE-XCUT-002", "FE-XCUT-003", "FE-XCUT-004", "FE-XCUT-005", "FE-XCUT-006"),
        ("tests/e2e/test_frontend_execution_matrix_coverage.py",),
    ),
}


def test_frontend_execution_matrix_names_all_fleet_tasks() -> None:
    matrix_text = MATRIX.read_text(encoding="utf-8")

    for task_id, expectation in FE_TASKS.items():
        assert task_id in matrix_text
        for keyword in expectation["keywords"]:
            assert keyword in matrix_text


def test_product_validation_dispatch_names_odp_frontend_lanes() -> None:
    dispatch_text = FLEET_DISPATCH.read_text(encoding="utf-8")
    matrix_text = MATRIX.read_text(encoding="utf-8")

    for odp_task_id, (matrix_task_ids, e2e_specs) in ODP_FE_TASKS.items():
        assert odp_task_id in dispatch_text
        for matrix_task_id in matrix_task_ids:
            assert matrix_task_id in matrix_text
            assert matrix_task_id in dispatch_text
        for e2e_spec in e2e_specs:
            assert e2e_spec in dispatch_text


def test_frontend_completion_audit_cites_lanes_and_runtime_evidence() -> None:
    audit_text = COMPLETION_AUDIT.read_text(encoding="utf-8")

    required_evidence = {
        "ODP-FE-R0-001": "tests/e2e/opsboard-shell.spec.ts",
        "ODP-FE-EXP-001": "tests/e2e/e2e-expansion-product.spec.ts",
        "ODP-FE-OPS-001": "tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts",
        "ODP-FE-PRICE-001": "tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts",
        "ODP-FE-ASSET-001": "tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts",
        "ODP-FE-LEARN-001": "tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts",
        "ODP-FE-XCUT-001": "tests/e2e/test_frontend_execution_matrix_coverage.py",
    }

    for lane, evidence_ref in required_evidence.items():
        assert lane in audit_text
        assert evidence_ref in audit_text
    assert "evidence-ready" in audit_text
    for xcut_evidence in [
        "tests/contract/test_frontend_domain_type_coverage.py",
        "tests/contract/test_ui_core_component_exports.py",
        "packages/ui-domain",
        "PR #87",
        "PR #88",
        "PR #89",
    ]:
        assert xcut_evidence in audit_text
    assert "ODP-PV-008" in audit_text


def test_release_evidence_documents_use_pr82_head_as_authoritative_candidate() -> None:
    evidence_docs = [
        FLEET_DISPATCH,
        COMPLETION_AUDIT,
        GO_NO_GO,
        READINESS_REPORT,
        CLOSEOUT_MANIFEST,
    ]

    for evidence_doc in evidence_docs:
        text = evidence_doc.read_text(encoding="utf-8")
        assert "PR #82" in text, evidence_doc
        assert "headRefOid" in text, evidence_doc
        assert "attached checks" in text, evidence_doc
        assert not HARDCODED_DEV_RELEASE_REF.search(text), evidence_doc
        for stale_ref in STALE_RELEASE_REFS:
            assert stale_ref not in text, f"{evidence_doc} still cites stale release ref {stale_ref}"
        for pr_ref in ("PR #87", "PR #88", "PR #89", "PR #90", "PR #91"):
            assert pr_ref in text, f"{evidence_doc} does not cite {pr_ref}"


def test_closeout_manifest_names_remaining_workflow_gates() -> None:
    manifest_text = CLOSEOUT_MANIFEST.read_text(encoding="utf-8")

    required_tasks = (
        "ODP-PV-008",
        "ODP-FE-XCUT-001",
        "ODP-FE-R0-001",
        "ODP-FE-XCUT-UI-001",
        "ODP-FE-EXP-001",
        "ODP-FE-OPS-001",
        "ODP-FE-PRICE-001",
        "ODP-FE-ASSET-001",
        "ODP-FE-LEARN-001",
        "ODP-FE-XCUT-DOMAIN-001",
        "ODP-FE-XCUT-TYPES-001",
    )
    for task_id in required_tasks:
        assert task_id in manifest_text

    for invariant in (
        "Do not mark the release complete while PR #82 is draft",
        "Do not claim live external provider integration",
        "Do not claim live remote staging rollout",
        "provider credential/OAuth",
        "scheduled external fetch",
        "quota/rate-limit",
        "production licensing",
        "thin or stale `main` checkout",
        "scripts/ai_status.py",
        "Human/Ops",
        "reviewer status closeout",
        "owner status closeout",
    ):
        assert invariant in manifest_text


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
