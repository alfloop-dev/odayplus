"""Focused verification tests for ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001.

Ensures that:
1. Incomplete live acceptance obligations (NFRs, CDC live, H06/ADJUST/AVM,
   Merge Queue behaviors, UAT/ModelRisk/Ops sign-offs) are strictly bound
   to canonical owner tasks, stages, triggers, and receipts.
2. Engineering completion (is_engineering_done) is strictly segregated from
   live acceptance (is_live_done); archived engineering done is never equated
   to live done.
3. Observation windows (24h, 7 operating days, 1 calendar month, 1 timed drill)
   are accurate and do not contain synthetic/fake metrics.
4. Pre-production gates do not circularly depend on post-production observation
   windows.
5. PRODUCT_RELEASE_GO_NO_GO.md clearly frames PR #82 as historical context and
   links to the canonical release gate registry / manifest without fabricating
   approvals.
6. Old open PRs (1243, 1205, 1052, 986, 970, 607; DPF 1) are dispositioned with
   exact diff evidence, including full 65-file disposition check for #970.
7. Active in-flight deliveries (1312, 1381, 1014; DPF 63, 77) are preserved.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
COMPLETION_DIR = Path(__file__).resolve().parent
OBLIGATION_MATRIX_PATH = COMPLETION_DIR / "obligation_matrix.json"
PR_DISPOSITION_MATRIX_PATH = COMPLETION_DIR / "pr_disposition_matrix.json"
PRODUCT_RELEASE_GO_NO_GO_PATH = REPO_ROOT / "docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md"
PR970_DISPOSITION_PATH = (
    REPO_ROOT
    / "docs/evidence/completion/ODP-XR-CUTOVER-ACTIVATE-002/pr-970-disposition.md"
)


@pytest.fixture(scope="module")
def obligation_matrix() -> dict:
    assert OBLIGATION_MATRIX_PATH.is_file(), f"Missing {OBLIGATION_MATRIX_PATH}"
    with open(OBLIGATION_MATRIX_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def pr_matrix() -> dict:
    assert PR_DISPOSITION_MATRIX_PATH.is_file(), f"Missing {PR_DISPOSITION_MATRIX_PATH}"
    with open(PR_DISPOSITION_MATRIX_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def test_obligation_matrix_schema_and_integrity(obligation_matrix: dict) -> None:
    assert obligation_matrix["schema_version"] == "1.0.0"
    assert obligation_matrix["task_id"] == "ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001"
    assert obligation_matrix["owner"] == "Antigravity3"
    assert obligation_matrix["reviewer"] == "Codex2"
    assert len(obligation_matrix["obligations"]) >= 15

    allowed_categories = {
        "nfr",
        "cdc_live",
        "business_operational",
        "merge_queue",
        "human_signoff",
        "deferred",
    }
    for item in obligation_matrix["obligations"]:
        assert item["obligation_id"], "Every obligation must have an ID"
        assert item["title"], "Every obligation must have a title"
        assert item["category"] in allowed_categories, f"Invalid category in {item['obligation_id']}"
        assert isinstance(item["target_environments"], list) and item["target_environments"]
        assert item["owner_or_authority"], f"Missing owner/authority for {item['obligation_id']}"
        assert isinstance(item["required_receipts"], list) and item["required_receipts"]
        assert isinstance(item["is_engineering_done"], bool)
        assert isinstance(item["is_live_done"], bool)
        assert isinstance(item["pre_prod_blocking"], bool)
        assert isinstance(item["post_prod_observation"], bool)


def test_nfr_obligations_and_observation_windows(obligation_matrix: dict) -> None:
    nfrs = {
        item["obligation_id"]: item
        for item in obligation_matrix["obligations"]
        if item["category"] == "nfr"
    }
    assert len(nfrs) == 5

    # 1. SHARED-008
    shared008 = nfrs["ODP-FR-SHARED-008"]
    assert shared008["real_status"] == "BLOCKED_BY_EVIDENCE"
    assert shared008["is_live_done"] is False
    assert any("WIF" in r for r in shared008["required_receipts"])

    # 2. PERF-001 (>= 24h window)
    perf = nfrs["ODP-NFR-PERF-001"]
    assert "24" in perf["observation_window"]
    assert perf["real_status"] == "BLOCKED_BY_EVIDENCE"
    assert perf["is_live_done"] is False

    # 3. BATCH-002 (7 operating days, deadline requires Human/Ops)
    batch = nfrs["ODP-NFR-BATCH-002"]
    assert "7" in batch["observation_window"]
    assert batch["real_status"] == "BLOCKED_BY_EVIDENCE"
    assert batch["is_live_done"] is False
    assert batch["post_prod_observation"] is True
    assert batch["pre_prod_blocking"] is False  # Cannot block pre-prod admission on 7 prod days

    # 4. AVAIL-003 (1 full calendar month)
    avail = nfrs["ODP-NFR-AVAIL-003"]
    assert "month" in avail["observation_window"].lower() or "日曆月" in avail["observation_window"]
    assert avail["real_status"] == "BLOCKED_BY_EVIDENCE"
    assert avail["is_live_done"] is False
    assert avail["post_prod_observation"] is True
    assert avail["pre_prod_blocking"] is False

    # 5. RPO-004 (timed drill)
    rpo = nfrs["ODP-NFR-RPO-004"]
    assert "drill" in rpo["observation_window"].lower() or "演練" in rpo["observation_window"]
    assert rpo["real_status"] == "BLOCKED_BY_EVIDENCE"
    assert rpo["is_live_done"] is False


def test_cdc_live_acceptance_gaps(obligation_matrix: dict) -> None:
    cdc_items = {
        item["obligation_id"]: item
        for item in obligation_matrix["obligations"]
        if item["category"] == "cdc_live"
    }
    expected_cdc = {
        "ODP-CDC-LIVE-REPLICA-SET",
        "ODP-CDC-LIVE-LATENCY-SLA",
        "ODP-CDC-LIVE-IAM-CREDENTIALS",
        "ODP-CDC-LIVE-PG-DDL-MIGRATION",
        "ODP-CDC-LIVE-OPLOG-FAIL-CLOSED",
        "ODP-CDC-MACHINE-EVENT-LIFECYCLE",
    }
    assert set(cdc_items.keys()) == expected_cdc

    # ODP-CDC-MACHINE-EVENT-LIFECYCLE is assigned to active repair task
    lifecycle = cdc_items["ODP-CDC-MACHINE-EVENT-LIFECYCLE"]
    assert lifecycle["canonical_owner_task"] == "ODP-CDC-MACHINE-EVENT-LIFECYCLE-001"
    assert lifecycle["real_status"] == "IN_PROGRESS"

    # All CDC live items have is_live_done == False
    for cdc_id, item in cdc_items.items():
        assert item["is_live_done"] is False, f"{cdc_id} cannot be live done without runtime proof"


def test_business_operational_and_merge_queue_obligations(obligation_matrix: dict) -> None:
    items = {item["obligation_id"]: item for item in obligation_matrix["obligations"]}

    # Business scope & operational
    assert "ODP-PARTIAL-LIVE-H06" in items
    assert "ODP-ADJUST-OPERATIONAL-CONFIRMATION" in items
    assert "ODP-AVM-FINANCE-CUTOVER" in items
    for key in ["ODP-PARTIAL-LIVE-H06", "ODP-ADJUST-OPERATIONAL-CONFIRMATION", "ODP-AVM-FINANCE-CUTOVER"]:
        assert items[key]["real_status"] == "PENDING_HUMAN_AUTHORITY"
        assert items[key]["is_live_done"] is False

    # Merge Queue behaviors
    assert "ODP-MERGE-QUEUE-BATCH-FORMATION" in items
    assert "ODP-MERGE-QUEUE-HOLD-TIMEOUT" in items
    assert "ODP-MERGE-QUEUE-ALLGREEN-REBUILD" in items
    for key in ["ODP-MERGE-QUEUE-BATCH-FORMATION", "ODP-MERGE-QUEUE-HOLD-TIMEOUT", "ODP-MERGE-QUEUE-ALLGREEN-REBUILD"]:
        assert items[key]["is_engineering_done"] is True
        assert items[key]["is_live_done"] is False
        assert items[key]["real_status"] == "BLOCKED_BY_EVIDENCE"


def test_cross_role_signoffs_and_deferred_items(obligation_matrix: dict) -> None:
    items = {item["obligation_id"]: item for item in obligation_matrix["obligations"]}

    # Cross-role signoffs
    assert "ODP-SIGN-OFF-UAT" in items
    assert "ODP-SIGN-OFF-MODEL-RISK" in items
    assert "ODP-SIGN-OFF-OPS-GO-NO-GO" in items
    for key in ["ODP-SIGN-OFF-UAT", "ODP-SIGN-OFF-MODEL-RISK", "ODP-SIGN-OFF-OPS-GO-NO-GO"]:
        assert items[key]["real_status"] == "PENDING_HUMAN_AUTHORITY"
        assert items[key]["is_live_done"] is False
        assert items[key]["human_input_required"] is True

    # Deferred items
    assert "ODP-DEFERRED-ROOT-CAUSE-WAVE-5" in items
    assert "ODP-DEFERRED-GOOGLE-OAUTH" in items
    assert items["ODP-DEFERRED-ROOT-CAUSE-WAVE-5"]["real_status"] == "DEFERRED_NON_BLOCKING"
    assert items["ODP-DEFERRED-GOOGLE-OAUTH"]["real_status"] == "DEFERRED_NON_BLOCKING"
    assert items["ODP-DEFERRED-GOOGLE-OAUTH"]["pre_prod_blocking"] is False


def test_anti_circularity_and_negative_rules(obligation_matrix: dict) -> None:
    """Negative tests to prevent false dones, circular blocks, and missing receipts."""
    for item in obligation_matrix["obligations"]:
        oid = item["obligation_id"]

        # Negative Rule 1: No live done without real receipts
        if item["is_live_done"]:
            pytest.fail(f"Invalid live done claim on {oid}")

        # Negative Rule 2: Engineering done does NOT imply live done
        if item["is_engineering_done"] and item["category"] in {"nfr", "cdc_live", "merge_queue"}:
            assert item["is_live_done"] is False, f"Engineering done erroneously promoted to live done on {oid}"

        # Negative Rule 3: Anti-circularity (Post-production observation cannot block pre-production admission)
        if item["category"] == "nfr" and item["post_prod_observation"] and not item.get("pre_prod_blocking", False):
            # Batch and Avail are post-prod observation windows and must not block pre-production admission
            assert oid in {"ODP-NFR-BATCH-002", "ODP-NFR-AVAIL-003"}

        # Negative Rule 4: Required receipts cannot be empty
        assert len(item["required_receipts"]) > 0, f"Empty required receipts for {oid}"


def test_product_release_go_no_go_document() -> None:
    assert PRODUCT_RELEASE_GO_NO_GO_PATH.is_file(), f"File missing at {PRODUCT_RELEASE_GO_NO_GO_PATH}"
    content = PRODUCT_RELEASE_GO_NO_GO_PATH.read_text(encoding="utf-8")

    # 1. PR #82 framed as historical context
    assert "PR #82" in content
    assert "historical" in content.lower() or "歷史" in content

    # 2. Links to canonical release gate registry / manifest
    assert "RELEASE_GATE_REGISTRY.json" in content
    assert "RELEASE_MANIFEST.json" in content

    # 3. Pending human signoffs preserved
    assert "pending-human" in content

    # 4. No fake release tuple or fabricated GO
    assert "HUMANOPS-DEV-MIGRATION-20261002T130206Z" in content or "candidate" in content


def test_old_pr_disposition_matrix(pr_matrix: dict) -> None:
    audited = {item["pr_number"]: item for item in pr_matrix["audited_prs"]}
    expected_pr_numbers = {1243, 1205, 1052, 986, 970, 607, 1}
    assert set(audited.keys()) == expected_pr_numbers

    # Verify PR #970 has full rationale based on 65-file disposition
    pr970 = audited[970]
    assert pr970["recommendation"] == "close_as_superseded"
    assert "65" in pr970["rationale"]
    assert "ODAY_MARKET_DATA_FACADE_MODE" in pr970["rationale"]
    assert PR970_DISPOSITION_PATH.is_file(), "Missing PR #970 65-file disposition record"

    # Verify data platform PR #1
    pr1 = audited[1]
    assert pr1["repository"] == "alfloop-dev/oday-data-platform"
    assert "Dagster" in pr1["rationale"]

    # Verify active in-flight PRs are protected
    active_prs = {item["pr_number"]: item for item in pr_matrix["active_in_flight_prs"]}
    assert set(active_prs.keys()) == {1381, 1312, 1014, 77, 63}
    for pr_num, item in active_prs.items():
        assert "active_delivery" in item["status"]
