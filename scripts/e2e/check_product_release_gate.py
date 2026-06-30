#!/usr/bin/env python3
"""Static release gate checks for the product E2E evidence packet.

The Docker-backed runner proves runtime behavior. This script blocks release
earlier when the runner or evidence packet silently drops required product
surfaces: deterministic environment/source stub, map rendering, PV-005
expansion, PV-006 ops/price/ad, PV-007 AVM/NetPlan/Learning/Audit, and the
product environment smoke.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_FILES = {
    "product runner": "scripts/e2e/run_product_e2e.sh",
    "deterministic env doc": "docs/testing/PRODUCT_E2E_ENVIRONMENT.md",
    "expansion evidence": "docs/evidence/e2e/EXPANSION_E2E_EVIDENCE.md",
    "ops price ad evidence": "docs/evidence/e2e/OPS_INTERVENTION_PRICE_AD_E2E_EVIDENCE.md",
    "avm netplan learning audit evidence": "docs/evidence/e2e/AVM_NETPLAN_LEARNING_AUDIT_E2E_EVIDENCE.md",
    "readiness report": "docs/evidence/PRODUCT_E2E_READINESS_REPORT.md",
    "go no-go": "docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md",
    "go no-go checker": "scripts/e2e/check_product_go_no_go.py",
    "closeout manifest": "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_MANIFEST.md",
    "closeout playbook": "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PLAYBOOK.md",
    "closeout queue": "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_QUEUE.json",
    "closeout pickup board": "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PICKUP_BOARD.md",
    "closeout pickup board checker": "scripts/e2e/check_product_closeout_pickup_board.py",
    "external proof closeout queue": "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json",
    "external proof handback template": "docs/evidence/EXTERNAL_PROOF_HANDBACK_TEMPLATE.json",
    "external proof handback example": "docs/evidence/EXTERNAL_PROOF_HANDBACK_EXAMPLE.json",
    "external proof fleet pickup board": "docs/evidence/EXTERNAL_PROOF_FLEET_PICKUP_BOARD.md",
    "remote staging runbook": "docs/evidence/REMOTE_STAGING_PROOF_RUNBOOK.md",
    "product grade gap execution tasks": "docs/evidence/PRODUCT_GRADE_E2E_GAP_EXECUTION_TASKS.md",
    "product grade e2e fleet dispatch": "docs/evidence/PRODUCT_GRADE_E2E_FLEET_DISPATCH.md",
    "product grade e2e fleet dispatch packet": "docs/evidence/PRODUCT_GRADE_E2E_FLEET_DISPATCH.json",
    "product grade e2e fleet assignment ledger": "docs/evidence/PRODUCT_GRADE_E2E_FLEET_ASSIGNMENT_LEDGER.md",
    "external provider foundation worker evidence": "docs/evidence/fleet_dispatch/ODP-EXT-001-003_WORKER_EVIDENCE.md",
    "external source operations worker evidence": "docs/evidence/fleet_dispatch/ODP-EXT-004-008_WORKER_EVIDENCE.md",
    "live map provider gate worker evidence": "docs/evidence/fleet_dispatch/ODP-MAP-E2E-001-004_WORKER_EVIDENCE.md",
    "remote staging worker evidence": "docs/evidence/fleet_dispatch/ODP-PV-STAGE-001-002_WORKER_EVIDENCE.md",
    "remote staging missing env report": "docs/evidence/fleet_dispatch/ODP-PV-STAGE-001_MISSING_ENV_REPORT.json",
    "listing source fixture": "tests/fixtures/source_data/external/listing_raw_snapshot.valid.json",
    "poi source fixture": "tests/fixtures/source_data/external/poi_snapshot.valid.json",
    "competitor source fixture": "tests/fixtures/source_data/external/competitor_store_snapshot.valid.json",
    "compose e2e stack": "infra/docker/docker-compose.e2e.yml",
    "remote staging proof checker": "scripts/e2e/check_remote_staging_proof.py",
    "external proof closeout queue checker": "scripts/e2e/check_external_proof_closeout_queue.py",
    "external proof fleet pickup board checker": "scripts/e2e/check_external_proof_fleet_pickup_board.py",
    "external proof handback template checker": "scripts/e2e/check_external_proof_handback_template.py",
    "external proof handback artifact checker": "scripts/e2e/check_external_proof_handback_artifact.py",
    "external proof handback bundle checker": "scripts/e2e/check_external_proof_handback_bundle.py",
    "external proof handback skeleton generator": "scripts/e2e/generate_external_proof_handback_skeleton.py",
    "external proof issue sync checker": "scripts/e2e/check_external_proof_issue_sync.py",
    "remote staging workflow": ".github/workflows/deploy-staging.yml",
}

REQUIRED_RUNNER_SPECS = (
    "tests/e2e/e2e-api-bound-ui.spec.ts",
    "tests/e2e/e2e-map.spec.ts",
    "tests/e2e/e2e-expansion-product.spec.ts",
    "tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts",
    "tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts",
    "tests/e2e/product-e2e-env.spec.ts",
)

REQUIRED_REPORT_TOKENS = (
    "E2E-EXP-001",
    "E2E-EXP-002",
    "E2E-OPS-001",
    "E2E-INT-001",
    "E2E-PRICE-001",
    "E2E-AD-001",
    "E2E-AVM-001",
    "E2E-NET-001",
    "E2E-LEARN-001",
    "E2E-LEARN-002",
    "E2E-AUDIT-001",
    "corr-product-e2e-seed-001",
    "corr-pv006-ops-intervention-price-ad",
    "corr-pv007-avm-netplan-learning-audit",
)


def main() -> int:
    errors: list[str] = []

    for label, relative_path in REQUIRED_FILES.items():
        path = ROOT / relative_path
        if not path.exists():
            errors.append(f"missing {label}: {relative_path}")

    assignment_ledger = ROOT / "docs/evidence/PRODUCT_GRADE_E2E_FLEET_ASSIGNMENT_LEDGER.md"
    assignment_text = assignment_ledger.read_text(encoding="utf-8") if assignment_ledger.exists() else ""
    for required_token in (
        "External provider foundation",
        "External source operations",
        "Live map provider gate",
        "Remote staging rollout",
        "handback received",
        "rejected handback",
        "externally blocked",
    ):
        if required_token not in assignment_text:
            errors.append(f"fleet assignment ledger missing token: {required_token}")

    staging_workflow = ROOT / ".github/workflows/deploy-staging.yml"
    staging_workflow_text = staging_workflow.read_text(encoding="utf-8") if staging_workflow.exists() else ""
    for required_token in (
        "Deploy/Verify Staging",
        "workflow_dispatch",
        "ODAY_RELEASE_SHA",
        "ODP_STAGING_DEPLOY_URL",
        "ODP_STAGING_API_URL",
        "ODP_STAGING_SECRET_OWNER",
        "scripts/e2e/check_remote_staging_proof.py",
        "actions/upload-artifact@v4",
    ):
        if required_token not in staging_workflow_text:
            errors.append(f"remote staging workflow missing token: {required_token}")
    if "TODO: replace with real deploy" in staging_workflow_text:
        errors.append("remote staging workflow still contains placeholder deploy TODO")

    runner = ROOT / "scripts/e2e/run_product_e2e.sh"
    runner_text = runner.read_text(encoding="utf-8") if runner.exists() else ""
    for spec in REQUIRED_RUNNER_SPECS:
        if spec not in runner_text:
            errors.append(f"product runner does not include {spec}")

    readiness = ROOT / "docs/evidence/PRODUCT_E2E_READINESS_REPORT.md"
    readiness_text = readiness.read_text(encoding="utf-8") if readiness.exists() else ""
    for token in REQUIRED_REPORT_TOKENS:
        if token not in readiness_text:
            errors.append(f"readiness report does not mention {token}")

    closeout_queue_check = subprocess.run(
        [sys.executable, "scripts/e2e/check_product_closeout_queue.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if closeout_queue_check.returncode != 0:
        output = "\n".join(
            line
            for line in (closeout_queue_check.stdout + closeout_queue_check.stderr).splitlines()
            if line.strip()
        )
        errors.append(f"closeout queue check failed: {output}")

    closeout_pickup_board_check = subprocess.run(
        [sys.executable, "scripts/e2e/check_product_closeout_pickup_board.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if closeout_pickup_board_check.returncode != 0:
        output = "\n".join(
            line
            for line in (closeout_pickup_board_check.stdout + closeout_pickup_board_check.stderr).splitlines()
            if line.strip()
        )
        errors.append(f"closeout pickup board check failed: {output}")

    fleet_dispatch_check = subprocess.run(
        [sys.executable, "scripts/e2e/check_product_grade_fleet_dispatch.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if fleet_dispatch_check.returncode != 0:
        output = "\n".join(
            line
            for line in (fleet_dispatch_check.stdout + fleet_dispatch_check.stderr).splitlines()
            if line.strip()
        )
        errors.append(f"product-grade fleet dispatch check failed: {output}")

    external_proof_check = subprocess.run(
        [sys.executable, "scripts/e2e/check_external_proof_closeout_queue.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if external_proof_check.returncode != 0:
        output = "\n".join(
            line
            for line in (external_proof_check.stdout + external_proof_check.stderr).splitlines()
            if line.strip()
        )
        errors.append(f"external proof closeout queue check failed: {output}")

    external_pickup_board_check = subprocess.run(
        [sys.executable, "scripts/e2e/check_external_proof_fleet_pickup_board.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if external_pickup_board_check.returncode != 0:
        output = "\n".join(
            line
            for line in (external_pickup_board_check.stdout + external_pickup_board_check.stderr).splitlines()
            if line.strip()
        )
        errors.append(f"external proof fleet pickup board check failed: {output}")

    external_handback_template_check = subprocess.run(
        [sys.executable, "scripts/e2e/check_external_proof_handback_template.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if external_handback_template_check.returncode != 0:
        output = "\n".join(
            line
            for line in (external_handback_template_check.stdout + external_handback_template_check.stderr).splitlines()
            if line.strip()
        )
        errors.append(f"external proof handback template check failed: {output}")

    go_no_go_check = subprocess.run(
        [sys.executable, "scripts/e2e/check_product_go_no_go.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if go_no_go_check.returncode != 0:
        output = "\n".join(
            line
            for line in (go_no_go_check.stdout + go_no_go_check.stderr).splitlines()
            if line.strip()
        )
        errors.append(f"product go/no-go guard check failed: {output}")

    for doc_label, relative_path in (
        ("closeout manifest", "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_MANIFEST.md"),
        ("go/no-go packet", "docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md"),
        ("closeout playbook", "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PLAYBOOK.md"),
        ("closeout pickup board", "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PICKUP_BOARD.md"),
        ("external proof fleet pickup board", "docs/evidence/EXTERNAL_PROOF_FLEET_PICKUP_BOARD.md"),
    ):
        doc_path = ROOT / relative_path
        doc_text = doc_path.read_text(encoding="utf-8") if doc_path.exists() else ""
        for required_token in (
            "check_external_proof_issue_sync.py",
            "--require-assignees",
            "check_external_proof_handback_artifact.py",
            "check_external_proof_handback_bundle.py",
            "check_product_go_no_go.py",
        ):
            if required_token not in doc_text:
                errors.append(f"{doc_label} missing external proof issue sync token: {required_token}")

    pickup_board = ROOT / "docs/evidence/EXTERNAL_PROOF_FLEET_PICKUP_BOARD.md"
    pickup_text = pickup_board.read_text(encoding="utf-8") if pickup_board.exists() else ""
    for required_token in (
        "External Proof Fleet Pickup Board",
        "PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json",
        "generate_external_proof_handback_skeleton.py",
        "check_external_proof_fleet_pickup_board.py",
        "ODP-EXT-PROD-001",
        "ODP-EXT-PROD-002",
        "ODP-EXT-PROD-003",
        "ODP-MAP-STAGE-001",
        "ODP-MAP-STAGE-002",
        "ODP-PV-STAGE-001",
        "ODP-PV-STAGE-002",
        "#132",
        "#133",
        "#134",
        "#135",
        "#136",
        "#137",
        "#138",
        "mock://",
        "localhost",
        "127.0.0.1",
        "check_external_proof_handback_bundle.py",
    ):
        if required_token not in pickup_text:
            errors.append(f"external proof fleet pickup board missing token: {required_token}")

    closeout_pickup_board = ROOT / "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PICKUP_BOARD.md"
    closeout_pickup_text = closeout_pickup_board.read_text(encoding="utf-8") if closeout_pickup_board.exists() else ""
    for required_token in (
        "Product Release Closeout Pickup Board",
        "PRODUCT_RELEASE_CLOSEOUT_QUEUE.json",
        "check_product_closeout_queue.py --report",
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
        "Human/Ops",
        "Claude",
        "Claude2",
        "Codex",
        "Codex2",
        "owner_status_closeout",
        "reviewer_status_closeout",
        "human_signoff",
        "scripts/ai_status.py approve",
        "scripts/ai_status.py reopen",
        "scripts/ai_status.py done",
        "provider-specific production credential",
        "remote-staging live tile",
        "remote staging host/url/secret",
    ):
        if required_token not in closeout_pickup_text:
            errors.append(f"closeout pickup board missing token: {required_token}")

    if errors:
        print("Product release gate failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Product release gate static checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
