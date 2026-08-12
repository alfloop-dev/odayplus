#!/usr/bin/env python3
"""Static release gate checks for the product E2E evidence packet.

The Docker-backed runner proves runtime behavior. This script blocks release
earlier when the runner or evidence packet silently drops required product
surfaces: deterministic environment/source stub, map rendering, PV-005
expansion, PV-006 ops/price/ad, PV-007 AVM/NetPlan/Learning/Audit, and the
product environment smoke.
"""

from __future__ import annotations

import argparse
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
    "raw playwright results": "docs/evidence/e2e/raw_playwright_results.json",
    "raw pytest results": "docs/evidence/e2e/raw_pytest_results.json",
    "playwright result recorder": "scripts/e2e/record_playwright_results.py",
    "python acceptance runner": "scripts/e2e/run_python_e2e_tests.py",
    "e2e receipt validation library": "scripts/e2e/product_e2e_receipt.py",
    "e2e receipt generator": "scripts/e2e/generate_product_e2e_receipt.py",
    "product e2e execution receipt": "docs/evidence/e2e/PRODUCT_E2E_EXECUTION_RECEIPT.json",
    "readiness report": "docs/evidence/PRODUCT_E2E_READINESS_REPORT.md",
    "go no-go": "docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md",
    "go no-go checker": "scripts/e2e/check_product_go_no_go.py",
    "release gate registry": "docs/evidence/gates/RELEASE_GATE_REGISTRY.json",
    "release gate registry guide": "docs/evidence/gates/README.md",
    "release gate registry checker": "scripts/e2e/check_release_gate_registry.py",
    "product-grade gate reconciliation checker": "scripts/e2e/check_product_grade_gate_reconciliation.py",
    "closeout manifest": "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_MANIFEST.md",
    "closeout playbook": "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PLAYBOOK.md",
    "closeout queue": "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_QUEUE.json",
    "closeout pickup board": "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PICKUP_BOARD.md",
    "closeout pickup board checker": "scripts/e2e/check_product_closeout_pickup_board.py",
    "closeout action checker": "scripts/e2e/check_product_closeout_action.py",
    "closeout action matrix checker": "scripts/e2e/check_product_closeout_action_matrix.py",
    "product closeout fleet comment syncer": "scripts/e2e/sync_product_closeout_fleet_comment.py",
    "product closeout fleet notification checker": "scripts/e2e/check_product_closeout_fleet_notification.py",
    "release fleet dispatch status checker": "scripts/e2e/check_release_fleet_dispatch_status.py",
    "external proof closeout queue": "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json",
    "external proof handback status board": "docs/evidence/EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json",
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
    "external proof handback status board checker": "scripts/e2e/check_external_proof_handback_status_board.py",
    "external proof handback status board updater": "scripts/e2e/update_external_proof_handback_status_board.py",
    "external proof acceptance readiness checker": "scripts/e2e/check_external_proof_acceptance_readiness.py",
    "external proof live blocker checker": "scripts/e2e/check_external_proof_live_blockers.py",
    "external proof fleet notification checker": "scripts/e2e/check_external_proof_fleet_notifications.py",
    "external proof fleet issue syncer": "scripts/e2e/sync_external_proof_fleet_issues.py",
    "external proof handback skeleton generator": "scripts/e2e/generate_external_proof_handback_skeleton.py",
    "external proof issue sync checker": "scripts/e2e/check_external_proof_issue_sync.py",
    "external proof issue handback scanner": "scripts/e2e/check_external_proof_issue_handback_scan.py",
    "external proof escalation comment syncer": "scripts/e2e/sync_external_proof_escalation_comments.py",
    "external proof follow-up workflow checker": "scripts/e2e/check_external_proof_followup_workflow.py",
    "external proof follow-up workflow": ".github/workflows/external-proof-followup.yml",
    "remote staging workflow": ".github/workflows/deploy-staging.yml",
}

REQUIRED_RUNNER_SPECS = (
    "tests/e2e/e2e-network-find-areas-api-binding.spec.ts",
    "tests/e2e/e2e-operator-console.spec.ts",
    "tests/e2e/operator-assisted-listing-intake-a11y.spec.ts",
    "tests/e2e/operator-assisted-listing-intake-mobile.spec.ts",
    "tests/e2e/operator-assisted-listing-intake.spec.ts",
    "tests/e2e/operator-governance.spec.ts",
    "tests/e2e/operator-growth.spec.ts",
    "tests/e2e/operator-network-assisted-intake.spec.ts",
    "tests/e2e/operator-network-listings.spec.ts",
    "tests/e2e/operator-network-rebalance.spec.ts",
    "tests/e2e/operator-network-review.spec.ts",
    "tests/e2e/operator-network-scoring.spec.ts",
    "tests/e2e/operator-shell-today.spec.ts",
    "tests/e2e/operator-store-ops.spec.ts",
    "tests/e2e/product-e2e-env.spec.ts",
    "tests/e2e/shell-resource-binding.spec.ts",
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


def run_checker(script: str, *arguments: str) -> tuple[int, str]:
    """Run a sibling checker and return its code plus compact diagnostics."""
    result = subprocess.run(
        [sys.executable, script, *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    output = "\n".join(
        line for line in (result.stdout + result.stderr).splitlines() if line.strip()
    )
    return result.returncode, output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate product E2E surfaces for dev merge or production release."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dev-merge",
        action="store_true",
        help=(
            "validate CI/E2E structure while accepting an internally valid NO-GO "
            "registry; the subsequent runner must emit a fresh exact-source receipt"
        ),
    )
    mode.add_argument(
        "--require-go",
        action="store_true",
        help="fail unless the Gate 0-6 registry records an authentic GO decision",
    )
    parser.add_argument(
        "--expected-sha",
        help=(
            "fail unless release gate registry candidate_sha matches or is an "
            "evidence-only ancestor of this exact SHA"
        ),
    )
    args = parser.parse_args(argv)
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

    external_followup_workflow = ROOT / ".github/workflows/external-proof-followup.yml"
    external_followup_workflow_text = (
        external_followup_workflow.read_text(encoding="utf-8")
        if external_followup_workflow.exists()
        else ""
    )
    for required_token in (
        "External Proof Follow-up",
        "workflow_dispatch",
        "schedule:",
        "GH_TOKEN",
        "gh pr view 82",
        "check_external_proof_issue_sync.py --require-assignees",
        "check_external_proof_fleet_notifications.py",
        "check_external_proof_live_blockers.py --require-assignees",
        "check_external_proof_handback_status_board.py",
        "check_external_proof_issue_handback_scan.py",
        "--fail-on-escalation",
        "sync_external_proof_escalation_comments.py",
        "actions/upload-artifact@v4",
    ):
        if required_token not in external_followup_workflow_text:
            errors.append(f"external proof follow-up workflow missing token: {required_token}")

    checker_status, output = run_checker("scripts/e2e/check_external_proof_followup_workflow.py")
    if checker_status != 0:
        errors.append(f"external proof follow-up workflow check failed: {output}")

    registry_arguments: list[str] = []
    if args.require_go:
        registry_arguments.append("--require-go")
    if args.expected_sha:
        registry_arguments.extend(["--expected-sha", args.expected_sha])
    registry_status, output = run_checker(
        "scripts/e2e/check_release_gate_registry.py",
        *registry_arguments,
    )
    if registry_status != 0:
        errors.append(f"release gate registry check failed: {output}")

    # Keep the product-grade evidence queues and handback board on one truth
    # surface. This used to be a standalone checker, which meant the release
    # gate could pass while the quoted blocker/ACK counts had drifted apart.
    reconciliation_status, output = run_checker(
        "scripts/e2e/check_product_grade_gate_reconciliation.py", "--skip-runtime"
    )
    if reconciliation_status != 0:
        errors.append(f"product-grade gate reconciliation check failed: {output}")

    runner = ROOT / "scripts/e2e/run_product_e2e.sh"
    runner_text = runner.read_text(encoding="utf-8") if runner.exists() else ""
    for spec in REQUIRED_RUNNER_SPECS:
        if spec not in runner_text:
            errors.append(f"product runner does not include {spec}")
    for required_runner_token in (
        "record_playwright_results.py",
        "run_python_e2e_tests.py",
        "generate_product_e2e_receipt.py",
        'playwright_record_status=$?',
        'pytest_status=$?',
        'receipt_status=$?',
    ):
        if required_runner_token not in runner_text:
            errors.append(
                f"product runner does not propagate required stage: {required_runner_token}"
            )

    readiness = ROOT / "docs/evidence/PRODUCT_E2E_READINESS_REPORT.md"
    readiness_text = readiness.read_text(encoding="utf-8") if readiness.exists() else ""
    for token in REQUIRED_REPORT_TOKENS:
        if token not in readiness_text:
            errors.append(f"readiness report does not mention {token}")

    # Executable acceptance scenario and test inventory validator
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from scripts.e2e.product_e2e_receipt import (
            validate_acceptance_scenarios_and_inventory,
            validate_receipt_packet,
        )
        scenario_errors = validate_acceptance_scenarios_and_inventory(ROOT)
        if scenario_errors:
            errors.extend(scenario_errors)
        if not args.dev_merge:
            errors.extend(validate_receipt_packet(ROOT))
    except Exception as exc:
        errors.append(f"acceptance scenario/inventory validator error: {exc}")

    checker_status, output = run_checker("scripts/e2e/check_product_closeout_queue.py")
    if checker_status != 0:
        errors.append(f"closeout queue check failed: {output}")

    checker_status, output = run_checker("scripts/e2e/check_product_closeout_pickup_board.py")
    if checker_status != 0:
        errors.append(f"closeout pickup board check failed: {output}")

    checker_status, output = run_checker("scripts/e2e/check_product_grade_fleet_dispatch.py")
    if checker_status != 0:
        errors.append(f"product-grade fleet dispatch check failed: {output}")

    checker_status, output = run_checker("scripts/e2e/check_external_proof_closeout_queue.py")
    if checker_status != 0:
        errors.append(f"external proof closeout queue check failed: {output}")

    checker_status, output = run_checker("scripts/e2e/check_external_proof_fleet_pickup_board.py")
    if checker_status != 0:
        errors.append(f"external proof fleet pickup board check failed: {output}")

    checker_status, output = run_checker("scripts/e2e/check_external_proof_handback_template.py")
    if checker_status != 0:
        errors.append(f"external proof handback template check failed: {output}")

    checker_status, output = run_checker("scripts/e2e/check_external_proof_handback_status_board.py")
    if checker_status != 0:
        errors.append(f"external proof handback status board check failed: {output}")

    checker_status, output = run_checker("scripts/e2e/check_product_go_no_go.py")
    if checker_status != 0:
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
            "check_external_proof_handback_status_board.py",
            "update_external_proof_handback_status_board.py",
            "check_external_proof_live_blockers.py",
            "check_external_proof_fleet_notifications.py",
            "check_external_proof_issue_handback_scan.py",
            "--fail-on-escalation",
            "sync_external_proof_escalation_comments.py",
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
        "check_external_proof_issue_handback_scan.py --report --fail-on-escalation",
        "sync_external_proof_escalation_comments.py --apply",
        "--force --comment-dir",
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

    # Scan production code for forbidden x-test-mock switches
    production_directories = [
        ROOT / "apps/api",
        ROOT / "apps/web/src",
        ROOT / "apps/web/features",
    ]
    for directory in production_directories:
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".ts", ".tsx", ".js", ".jsx"):
                try:
                    content = path.read_text(encoding="utf-8")
                    if "x-test-mock" in content:
                        errors.append(
                            f"forbidden mock switch found in production code: {path.relative_to(ROOT)}"
                        )
                except Exception:
                    pass

    if errors:
        gate_name = "dev merge gate" if args.dev_merge else "production release gate"
        print(f"Product {gate_name} failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    if args.dev_merge:
        print(
            "Product dev merge gate static checks passed; release authorization "
            "remains governed independently by the Gate 0-6 registry."
        )
    else:
        print("Product production release gate static checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
