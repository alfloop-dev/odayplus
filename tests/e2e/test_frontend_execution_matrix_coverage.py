"""Verify FE fleet dispatch tasks stay tied to product E2E proof.

The design-to-frontend execution matrix is the handoff contract used to split
frontend work across implementation fleets. This test makes that contract part
of CI so future edits cannot remove a workflow from the product E2E gate while
the matrix still claims product-grade acceptance.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs/design/ODAY_PLUS_DESIGN_TO_FRONTEND_EXECUTION_MATRIX.md"
FLEET_DISPATCH = ROOT / "docs/evidence/PRODUCT_VALIDATION_FLEET_DISPATCH.md"
COMPLETION_AUDIT = ROOT / "docs/evidence/FRONTEND_FLEET_COMPLETION_AUDIT.md"
GO_NO_GO = ROOT / "docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md"
READINESS_REPORT = ROOT / "docs/evidence/PRODUCT_E2E_READINESS_REPORT.md"
CLOSEOUT_MANIFEST = ROOT / "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_MANIFEST.md"
CLOSEOUT_PLAYBOOK = ROOT / "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_PLAYBOOK.md"
CLOSEOUT_QUEUE = ROOT / "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_QUEUE.json"
PRODUCT_GRADE_GAP_TASKS = ROOT / "docs/evidence/PRODUCT_GRADE_E2E_GAP_EXECUTION_TASKS.md"
PRODUCT_GRADE_FLEET_DISPATCH = ROOT / "docs/evidence/PRODUCT_GRADE_E2E_FLEET_DISPATCH.md"
PRODUCT_GRADE_FLEET_DISPATCH_PACKET = ROOT / "docs/evidence/PRODUCT_GRADE_E2E_FLEET_DISPATCH.json"
PRODUCT_GRADE_FLEET_DISPATCH_QUEUE = ROOT / "docs/evidence/PRODUCT_GRADE_E2E_FLEET_DISPATCH_QUEUE.json"
PRODUCT_GRADE_FLEET_KICKOFF_RUNBOOK = ROOT / "docs/evidence/PRODUCT_GRADE_E2E_FLEET_KICKOFF_RUNBOOK.md"
PRODUCT_GRADE_FLEET_BRIEF_DIR = ROOT / "docs/evidence/fleet_dispatch"
RUNNER = ROOT / "scripts/e2e/run_product_e2e.sh"
RELEASE_GATE = ROOT / "scripts/e2e/check_product_release_gate.py"
CLOSEOUT_QUEUE_CHECK = ROOT / "scripts/e2e/check_product_closeout_queue.py"
GRADE_FLEET_DISPATCH_CHECK = ROOT / "scripts/e2e/check_product_grade_fleet_dispatch.py"
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
        CLOSEOUT_PLAYBOOK,
        CLOSEOUT_QUEUE,
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
        "check_external_proof_issue_sync.py --require-assignees",
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


def test_closeout_playbook_gives_actionable_commands_for_each_actor() -> None:
    playbook_text = CLOSEOUT_PLAYBOOK.read_text(encoding="utf-8")

    for command in (
        "scripts/ai_status.py approve",
        "scripts/ai_status.py reopen",
        "scripts/ai_status.py done",
        "check_external_proof_issue_sync.py --require-assignees",
        "gh pr view 82",
        "check_product_release_gate.py",
    ):
        assert command in playbook_text

    for actor in ("Human/Ops", "Claude", "Claude2", "Codex", "Codex2"):
        assert actor in playbook_text

    for boundary in (
        "External data proof is deterministic source-stub/fixture coverage",
        "Map proof is deterministic local MapLibre/deck/H3 coverage",
        "Remote staging rollout remains conditional",
        "Do not mark the active objective complete",
    ):
        assert boundary in playbook_text


def test_product_grade_gap_execution_tasks_are_actionable() -> None:
    gap_text = PRODUCT_GRADE_GAP_TASKS.read_text(encoding="utf-8")
    release_gate_text = RELEASE_GATE.read_text(encoding="utf-8")

    assert "docs/evidence/PRODUCT_GRADE_E2E_GAP_EXECUTION_TASKS.md" in release_gate_text
    assert "docs/evidence/PRODUCT_GRADE_E2E_FLEET_DISPATCH.md" in release_gate_text
    assert "PR #82" in gap_text
    assert "headRefOid" in gap_text
    assert "attached checks" in gap_text

    for task_id in (
        "ODP-PV-LIVE-SRC-001",
        "ODP-PV-LIVE-SRC-002",
        "ODP-PV-LIVE-SRC-003",
        "ODP-PV-LIVE-MAP-001",
        "ODP-PV-LIVE-MAP-002",
        "ODP-PV-LIVE-MAP-003",
        "ODP-PV-STAGE-001",
        "ODP-PV-STAGE-002",
    ):
        assert task_id in gap_text

    for boundary in (
        "provider credential/OAuth",
        "scheduled external fetch",
        "quota/rate-limit",
        "production licensing",
        "live tile rollout",
        "live geocoder rollout",
        "full keyboard accessibility",
        "direct map picking",
        "remote staging host/url/secret",
        "Deterministic fixture/source-stub tests must remain as CI defaults",
    ):
        assert boundary in gap_text

    for alias in (
        "ODP-EXT-001",
        "ODP-EXT-002",
        "ODP-EXT-003",
        "ODP-EXT-004",
        "ODP-EXT-005",
        "ODP-EXT-006",
        "ODP-EXT-007",
        "ODP-EXT-008",
        "ODP-MAP-E2E-001",
        "ODP-MAP-E2E-002",
        "ODP-MAP-E2E-003",
        "ODP-MAP-E2E-004",
        "ODP-MAP-A11Y-001",
        "ODP-MAP-E2E-005",
        "ODP-MAP-E2E-006",
    ):
        assert alias in gap_text


def test_product_grade_fleet_dispatch_names_all_live_gap_aliases() -> None:
    gap_text = PRODUCT_GRADE_GAP_TASKS.read_text(encoding="utf-8")
    dispatch_text = PRODUCT_GRADE_FLEET_DISPATCH.read_text(encoding="utf-8")
    dispatch_packet = json.loads(PRODUCT_GRADE_FLEET_DISPATCH_PACKET.read_text(encoding="utf-8"))

    assert "PR #82" in dispatch_text
    assert "headRefOid" in dispatch_text
    assert "attached checks" in dispatch_text
    assert "deterministic product E2E proof" in dispatch_text
    assert "document-only PR must not close" in dispatch_text

    for alias in (
        "ODP-EXT-001",
        "ODP-EXT-002",
        "ODP-EXT-003",
        "ODP-EXT-004",
        "ODP-EXT-005",
        "ODP-EXT-006",
        "ODP-EXT-007",
        "ODP-EXT-008",
        "ODP-MAP-E2E-001",
        "ODP-MAP-E2E-002",
        "ODP-MAP-E2E-003",
        "ODP-MAP-E2E-004",
        "ODP-MAP-A11Y-001",
        "ODP-MAP-E2E-005",
        "ODP-MAP-E2E-006",
        "ODP-PV-STAGE-001",
        "ODP-PV-STAGE-002",
    ):
        assert alias in gap_text
        assert alias in dispatch_text
        assert alias in {task["id"] for task in dispatch_packet["tasks"]}

    for lane in (
        "External provider foundation",
        "External source operations",
        "Live map provider gate",
        "Map accessibility and resilience",
        "Remote staging rollout",
    ):
        assert lane in dispatch_text

    for proof_boundary in (
        "live-provider proof",
        "live-map proof",
        "remote-staging proof",
        "Deterministic fixture/source-stub tests",
        "provider secrets",
        "staging version matches PR #82 `headRefOid`",
    ):
        assert proof_boundary in dispatch_text


def test_product_grade_fleet_dispatch_packet_is_machine_actionable() -> None:
    release_gate_text = RELEASE_GATE.read_text(encoding="utf-8")
    checker_text = GRADE_FLEET_DISPATCH_CHECK.read_text(encoding="utf-8")
    packet = json.loads(PRODUCT_GRADE_FLEET_DISPATCH_PACKET.read_text(encoding="utf-8"))
    queue = json.loads(PRODUCT_GRADE_FLEET_DISPATCH_QUEUE.read_text(encoding="utf-8"))
    runbook_text = PRODUCT_GRADE_FLEET_KICKOFF_RUNBOOK.read_text(encoding="utf-8")

    assert "docs/evidence/PRODUCT_GRADE_E2E_FLEET_DISPATCH.json" in release_gate_text
    assert "scripts/e2e/check_product_grade_fleet_dispatch.py" in release_gate_text
    assert "Product-grade fleet dispatch checks passed." in checker_text

    assert packet["release_target"]["pr"] == 82
    assert packet["release_target"]["must_not_hardcode_dev_hash"] is True
    assert "headRefOid" in packet["release_target"]["authority"]

    expected_boundaries = {"external_data_sources", "maps", "remote_staging"}
    assert set(packet["scope_boundaries"]) == expected_boundaries

    task_ids = {task["id"] for task in packet["tasks"]}
    lane_aliases = {
        alias
        for lane in packet["dispatch_lanes"]
        for alias in lane["aliases"]
    }
    queue_ids = {entry["task_id"] for entry in queue["queue"]}
    assert task_ids == lane_aliases
    assert task_ids == queue_ids
    assert queue["status"] == "ready_for_fleet_pickup"
    assert queue["queue_role"] == "historical_initial_dispatch"
    assert queue["current_remaining_queue"] == "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
    assert "Product-Grade E2E Fleet Kickoff Runbook" in runbook_text
    assert "historical_initial_dispatch" in runbook_text
    assert "PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json" in runbook_text
    assert "Fleet Pickup Sequence" in runbook_text
    assert "Completion Handback" in runbook_text

    for task in packet["tasks"]:
        assert task["status"] == "open"
        assert task["scope_boundary"] in expected_boundaries
        assert task["owner_lane"]
        assert task["reviewer_lane"]
        assert task["implementation_evidence"]
        assert task["verification_evidence"]
        assert task["acceptance_criteria"]
        assert task["suggested_branch"].startswith(f"task/{task['id']}")
        assert task["handoff_artifacts"]
        assert task["id"] in runbook_text
        assert task["suggested_branch"] in runbook_text

    for entry in queue["queue"]:
        assert entry["dispatch_status"] == "ready_for_fleet"
        assert entry["dispatch_command"] == (
            f"python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task {entry['task_id']}"
        )
        assert (ROOT / entry["brief_path"]).exists()
        assert entry["minimum_completion_signal"]["implementation_evidence"]
        assert entry["minimum_completion_signal"]["verification_evidence"]
        assert entry["minimum_completion_signal"]["acceptance_criteria"]
        assert entry["minimum_completion_signal"]["handoff_artifacts"]
        assert entry["dispatch_command"] in runbook_text


def test_product_grade_fleet_dispatch_checker_runs() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/e2e/check_product_grade_fleet_dispatch.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Product-grade fleet dispatch checks passed." in result.stdout


def test_product_grade_fleet_dispatch_report_and_task_brief_run() -> None:
    report_result = subprocess.run(
        [sys.executable, "scripts/e2e/check_product_grade_fleet_dispatch.py", "--report"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    report = report_result.stdout
    assert "# Product-Grade E2E Fleet Dispatch Report" in report
    assert "Dispatch Lanes" in report
    assert "Task Brief Commands" in report
    assert "ODP-EXT-001" in report
    assert "ODP-MAP-E2E-001" in report
    assert "ODP-PV-STAGE-001" in report

    brief_result = subprocess.run(
        [sys.executable, "scripts/e2e/check_product_grade_fleet_dispatch.py", "--task", "ODP-MAP-E2E-003"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    brief = brief_result.stdout
    assert "# Fleet Execution Brief: ODP-MAP-E2E-003" in brief
    assert "Suggested branch: `task/ODP-MAP-E2E-003-direct-map-picking`" in brief
    assert "Direct map picking" in brief
    assert "Implementation Evidence Required" in brief
    assert "Verification Evidence Required" in brief
    assert "Acceptance Criteria" in brief
    assert "Handoff Artifacts" in brief

    generated_index = (PRODUCT_GRADE_FLEET_BRIEF_DIR / "README.md").read_text(encoding="utf-8")
    generated_brief = (PRODUCT_GRADE_FLEET_BRIEF_DIR / "ODP-MAP-E2E-003.md").read_text(encoding="utf-8")
    assert report.strip() == generated_index.strip()
    assert brief.strip() == generated_brief.strip()


def test_closeout_queue_is_machine_readable_and_complete() -> None:
    queue_payload = json.loads(CLOSEOUT_QUEUE.read_text(encoding="utf-8"))
    queue_entries = queue_payload["queue"]

    assert queue_payload["release_target"]["pr"] == 82
    assert queue_payload["release_target"]["must_not_hardcode_dev_hash"] is True
    assert "gh pr view 82 --json headRefOid,isDraft,state,mergeStateStatus,statusCheckRollup,url" in queue_payload[
        "global_preflight"
    ]

    required_task_ids = {
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
    }
    assert required_task_ids <= {entry["task_id"] for entry in queue_entries}

    required_blocking_types = {
        "human_signoff",
        "owner_status_closeout",
        "reviewer_status_closeout",
    }
    assert required_blocking_types <= {entry["blocking_type"] for entry in queue_entries}

    for entry in queue_entries:
        assert entry["actor"]
        assert entry["action_type"]
        assert entry["allowed_commands"]
        assert entry["evidence_refs"]
        for evidence_ref in entry["evidence_refs"]:
            evidence_path = ROOT / evidence_ref
            assert evidence_path.exists(), f"{entry['task_id']} evidence ref is missing: {evidence_ref}"

    queue_text = CLOSEOUT_QUEUE.read_text(encoding="utf-8")
    for boundary in (
        "provider credential/OAuth wiring",
        "scheduled external fetch",
        "quota/rate-limit handling",
        "live tile rollout",
        "full keyboard accessibility",
        "remote staging host/url/secret configuration",
    ):
        assert boundary in queue_text


def test_release_gate_runs_closeout_queue_checker() -> None:
    release_gate_text = RELEASE_GATE.read_text(encoding="utf-8")
    queue_check_text = CLOSEOUT_QUEUE_CHECK.read_text(encoding="utf-8")

    assert "scripts/e2e/check_product_closeout_queue.py" in release_gate_text
    assert "scripts/e2e/check_external_proof_issue_sync.py" in release_gate_text
    assert "Product closeout queue checks passed." in queue_check_text
    assert "ai-status.json" in queue_check_text
    assert "waiting_for_" in queue_check_text
    assert "waiting_for_review_after_handoff" in CLOSEOUT_QUEUE.read_text(encoding="utf-8")
    assert "--report" in queue_check_text
    assert "Product Release Closeout Queue Report" in queue_check_text


def test_closeout_queue_report_runs_without_live_ai_status() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/e2e/check_product_closeout_queue.py", "--report"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    report = result.stdout

    assert "# Product Release Closeout Queue Report" in report
    assert "PR: #82" in report
    assert "ai-status.json loaded: false" in report
    assert "| Task | Queue Status | Live Status | Actor | Action | Blocking Type | State |" in report
    assert "ODP-PV-008" in report
    assert "queued_no_live_status" in report
    assert "external_data_sources" in report
    assert "provider credential/OAuth wiring" in report
    assert "maps" in report
    assert "full keyboard accessibility" in report
    assert "remote_staging" in report


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
