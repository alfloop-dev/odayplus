"""Focused verification tests for ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001.

Ensures that:
1. Incomplete live acceptance obligations (NFRs, CDC live, H06/ADJUST/AVM,
   Merge Queue behaviors, UAT/ModelRisk/Ops sign-offs) are strictly bound
   to active canonical owner tasks, stages, triggers, and receipts, while
   historical completed engineering tasks are segregated as source evidence.
2. Engineering completion (is_engineering_done) is strictly segregated from
   live acceptance (is_live_done); archived engineering done is never equated
   to live done.
3. Observation windows (24h, 7 operating days, 1 calendar month, 1 timed drill)
   are accurate and do not contain synthetic/fake metrics.
4. Pre-production gates do not circularly depend on post-production observation
   windows.
5. Shared validator enforces negative guards against missing/archived owners,
   blank receipts, fabricated dones, and invalid flag combinations.
6. PRODUCT_RELEASE_GO_NO_GO.md clearly frames PR #82 as historical context and
   links to the canonical release gate registry / manifest without fabricating
   approvals.
7. Old open PRs (1243, 1205, 1052, 986, 970, 607; DPF 1) are dispositioned with
   exact head SHAs, comparison bases, diff evidence, and unlanded code truth.
8. Active in-flight deliveries (1381, 1312, 1014; DPF 63, 77) are preserved.
"""

from __future__ import annotations

import copy
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

ALLOWED_CATEGORIES = {
    "nfr",
    "cdc_live",
    "business_operational",
    "merge_queue",
    "human_signoff",
    "deferred",
}

ALLOWED_STATUSES = {
    "BLOCKED_BY_EVIDENCE",
    "PENDING_HUMAN_AUTHORITY",
    "IN_PROGRESS",
    "DEFERRED_NON_BLOCKING",
}

ALLOWED_WINDOW_TYPES = {
    "instant_readback",
    "continuous_time_window",
    "multi_day_window",
    "full_calendar_month",
    "drill_event",
    "event_stream",
}

ARCHIVED_HISTORICAL_TASKS = {
    "ODP-NFR-RUNTIME-EVIDENCE-001",
    "ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001",
    "ODP-MERGE-QUEUE-H08-ACTIVATION-001",
    "ODP-DURABLE-PARTIAL-IMPL-001",
    "ODP-AVM-DEPRECIATION-INTEGRATION-001",
}

PROHIBITED_UNKNOWN_OWNERS = {
    "ODP-ROOT-CAUSE-WAVE5-001",
    "ODP-AUTH-GOOGLE-OAUTH-001",
}


def validate_obligation(item: dict) -> None:
    """Validate schema and invariant constraints on a single carryforward obligation."""
    # 1. Non-empty string fields
    string_fields = [
        "obligation_id",
        "title",
        "category",
        "stage",
        "phase",
        "canonical_owner_task",
        "owner_or_authority",
        "original_acceptance_ref",
        "trigger_condition",
        "observation_window",
        "observation_window_type",
        "real_status",
        "disposition_notes",
    ]
    for field in string_fields:
        val = item.get(field)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"Obligation {item.get('obligation_id')} has invalid/empty '{field}': {val!r}")

    # 2. Category check
    if item["category"] not in ALLOWED_CATEGORIES:
        raise ValueError(f"Invalid category '{item['category']}' in {item['obligation_id']}")

    # 3. Status check
    if item["real_status"] not in ALLOWED_STATUSES:
        raise ValueError(f"Invalid real_status '{item['real_status']}' in {item['obligation_id']}")

    # 4. Window type check
    if item["observation_window_type"] not in ALLOWED_WINDOW_TYPES:
        raise ValueError(f"Invalid observation_window_type '{item['observation_window_type']}' in {item['obligation_id']}")

    # 5. Target environments check
    envs = item.get("target_environments")
    if not isinstance(envs, list) or not envs or not all(isinstance(e, str) and e.strip() for e in envs):
        raise ValueError(f"Invalid target_environments in {item['obligation_id']}: {envs!r}")

    # 6. Required receipts check: list of non-empty strings
    receipts = item.get("required_receipts")
    if not isinstance(receipts, list) or not receipts:
        raise ValueError(f"Empty required_receipts in {item['obligation_id']}")
    for r in receipts:
        if not isinstance(r, str) or not r.strip():
            raise ValueError(f"Blank/invalid receipt entry in {item['obligation_id']}: {r!r}")

    # 7. Boolean flags check
    for bool_field in ["is_engineering_done", "is_live_done", "pre_prod_blocking", "post_prod_observation", "human_input_required"]:
        if not isinstance(item.get(bool_field), bool):
            raise ValueError(f"Field '{bool_field}' must be a boolean in {item['obligation_id']}")

    # 8. Historical vs Active owner guard: canonical_owner_task must not be an archived historical task
    owner = item["canonical_owner_task"].strip()
    if owner in ARCHIVED_HISTORICAL_TASKS:
        raise ValueError(
            f"Archived historical task '{owner}' cannot be used as canonical_owner_task in {item['obligation_id']}; "
            f"set historical_source_task instead and bind canonical_owner_task to an active execution lane."
        )

    # 9. Prohibited unknown / fabricated owners
    if owner in PROHIBITED_UNKNOWN_OWNERS:
        raise ValueError(f"Prohibited unknown owner ID '{owner}' in {item['obligation_id']}")

    # 10. Anti-circularity: An obligation cannot be both pre_prod_blocking AND post_prod_observation
    if item["pre_prod_blocking"] and item["post_prod_observation"]:
        raise ValueError(
            f"Circular dependency error in {item['obligation_id']}: cannot be both pre_prod_blocking=True "
            f"and post_prod_observation=True. Staging readiness and post-production observation must be separate phases."
        )

    # 11. No fake live done claims without live receipts
    if item["is_live_done"]:
        raise ValueError(f"Invalid live done claim in {item['obligation_id']}; live verification requires runtime receipts")

    # 12. Human authority consistency
    if item["real_status"] == "PENDING_HUMAN_AUTHORITY" and not item["human_input_required"]:
        raise ValueError(f"PENDING_HUMAN_AUTHORITY obligation {item['obligation_id']} must have human_input_required=True")

    # 13. Deferred consistency
    if item["real_status"] == "DEFERRED_NON_BLOCKING" and (item["pre_prod_blocking"] or item["post_prod_observation"]):
        raise ValueError(f"DEFERRED_NON_BLOCKING obligation {item['obligation_id']} cannot be pre_prod_blocking or post_prod_observation")


@pytest.fixture(scope="module")
def obligation_matrix() -> dict:
    assert OBLIGATION_MATRIX_PATH.is_file(), f"Missing {OBLIGATION_MATRIX_PATH}"
    with open(OBLIGATION_MATRIX_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def pr_matrix() -> dict:
    assert PR_DISPOSITION_MATRIX_PATH.is_file(), f"Missing {PR_DISPOSITION_MATRIX_PATH}"
    with open(PR_DISPOSITION_MATRIX_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_obligation_matrix_schema_and_integrity(obligation_matrix: dict) -> None:
    assert obligation_matrix["schema_version"] == "1.0.0"
    assert obligation_matrix["task_id"] == "ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001"
    assert obligation_matrix["owner"] == "Antigravity3"
    assert obligation_matrix["reviewer"] == "Codex2"
    assert len(obligation_matrix["obligations"]) >= 15

    for item in obligation_matrix["obligations"]:
        validate_obligation(item)


def test_nfr_obligations_and_observation_windows(obligation_matrix: dict) -> None:
    nfrs = {
        item["obligation_id"]: item
        for item in obligation_matrix["obligations"]
        if item["category"] == "nfr"
    }
    assert len(nfrs) == 6  # SHARED-008, PERF-STAGING, PERF-PROD, BATCH, AVAIL, RPO

    # 1. SHARED-008
    shared008 = nfrs["ODP-FR-SHARED-008"]
    assert shared008["real_status"] == "BLOCKED_BY_EVIDENCE"
    assert shared008["is_live_done"] is False
    assert shared008["pre_prod_blocking"] is True
    assert shared008["post_prod_observation"] is False
    assert shared008["canonical_owner_task"] == "ODP-EPHEMERAL-STAGING-ROLLOUT-001"
    assert any("WIF" in r for r in shared008["required_receipts"])

    # 2. PERF Staging (load test pass)
    perf_staging = nfrs["ODP-NFR-PERF-001-STAGING"]
    assert perf_staging["real_status"] == "BLOCKED_BY_EVIDENCE"
    assert perf_staging["is_live_done"] is False
    assert perf_staging["pre_prod_blocking"] is True
    assert perf_staging["post_prod_observation"] is False
    assert perf_staging["canonical_owner_task"] == "ODP-EPHEMERAL-STAGING-ROLLOUT-001"
    assert any("concurrency 10/20/50" in r for r in perf_staging["required_receipts"])

    # 3. PERF Prod (24h traffic monitoring)
    perf_prod = nfrs["ODP-NFR-PERF-001-PROD"]
    assert "24" in perf_prod["observation_window"]
    assert perf_prod["real_status"] == "BLOCKED_BY_EVIDENCE"
    assert perf_prod["is_live_done"] is False
    assert perf_prod["pre_prod_blocking"] is False
    assert perf_prod["post_prod_observation"] is True
    assert perf_prod["canonical_owner_task"] == "ODP-POSTDEPLOY-WATCH-CLOSEOUT-001"

    # 4. BATCH-002 (7 operating days, deadline requires Human/Ops)
    batch = nfrs["ODP-NFR-BATCH-002"]
    assert "7" in batch["observation_window"]
    assert batch["real_status"] == "BLOCKED_BY_EVIDENCE"
    assert batch["is_live_done"] is False
    assert batch["post_prod_observation"] is True
    assert batch["pre_prod_blocking"] is False
    assert batch["canonical_owner_task"] == "ODP-POSTDEPLOY-WATCH-CLOSEOUT-001"
    assert any("cutoff time" in r for r in batch["required_receipts"])

    # 5. AVAIL-003 (1 full calendar month)
    avail = nfrs["ODP-NFR-AVAIL-003"]
    assert "month" in avail["observation_window"].lower() or "日曆月" in avail["observation_window"]
    assert avail["real_status"] == "BLOCKED_BY_EVIDENCE"
    assert avail["is_live_done"] is False
    assert avail["post_prod_observation"] is True
    assert avail["pre_prod_blocking"] is False
    assert avail["canonical_owner_task"] == "ODP-POSTDEPLOY-WATCH-CLOSEOUT-001"

    # 6. RPO-004 (timed drill)
    rpo = nfrs["ODP-NFR-RPO-004"]
    assert "drill" in rpo["observation_window"].lower() or "演練" in rpo["observation_window"]
    assert rpo["real_status"] == "BLOCKED_BY_EVIDENCE"
    assert rpo["is_live_done"] is False
    assert rpo["pre_prod_blocking"] is True
    assert rpo["post_prod_observation"] is False
    assert rpo["canonical_owner_task"] == "ODP-EPHEMERAL-STAGING-ROLLOUT-001"


def test_cdc_live_acceptance_gaps(obligation_matrix: dict) -> None:
    cdc_items = {
        item["obligation_id"]: item
        for item in obligation_matrix["obligations"]
        if item["category"] == "cdc_live"
    }
    expected_cdc = {
        "ODP-CDC-LIVE-REPLICA-SET",
        "ODP-CDC-LIVE-LATENCY-STAGING",
        "ODP-CDC-LIVE-LATENCY-PROD",
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

    partial = items["ODP-PARTIAL-LIVE-H06"]
    assert partial["real_status"] == "PENDING_HUMAN_AUTHORITY"
    assert partial["is_live_done"] is False
    assert partial["canonical_owner_task"] == "HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001"
    assert any("real-source ingestion" in r for r in partial["required_receipts"])

    adjust = items["ODP-ADJUST-OPERATIONAL-CONFIRMATION"]
    assert adjust["real_status"] == "PENDING_HUMAN_AUTHORITY"
    assert adjust["is_live_done"] is False
    assert adjust["canonical_owner_task"] == "HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001"
    assert any("in-place adjustment vs stop-plus-recreate" in r for r in adjust["required_receipts"])

    avm = items["ODP-AVM-FINANCE-CUTOVER"]
    assert avm["real_status"] == "PENDING_HUMAN_AUTHORITY"
    assert avm["is_live_done"] is False
    assert avm["canonical_owner_task"] == "HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001"
    assert any("Contract R-4 three rollback thresholds" in r for r in avm["required_receipts"])

    # Merge Queue behaviors
    assert "ODP-MERGE-QUEUE-BATCH-FORMATION" in items
    assert "ODP-MERGE-QUEUE-HOLD-TIMEOUT" in items
    assert "ODP-MERGE-QUEUE-ALLGREEN-REBUILD" in items
    for key in ["ODP-MERGE-QUEUE-BATCH-FORMATION", "ODP-MERGE-QUEUE-HOLD-TIMEOUT", "ODP-MERGE-QUEUE-ALLGREEN-REBUILD"]:
        assert items[key]["is_engineering_done"] is True
        assert items[key]["is_live_done"] is False
        assert items[key]["real_status"] == "BLOCKED_BY_EVIDENCE"
        assert items[key]["canonical_owner_task"] == "ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001"


def test_cross_role_signoffs_and_deferred_items(obligation_matrix: dict) -> None:
    items = {item["obligation_id"]: item for item in obligation_matrix["obligations"]}

    # Cross-role signoffs
    assert "ODP-SIGN-OFF-UAT" in items
    assert "ODP-SIGN-OFF-MODEL-RISK" in items
    assert "ODP-SIGN-OFF-OPS-GO-NO-GO" in items

    assert items["ODP-SIGN-OFF-UAT"]["canonical_owner_task"] == "ODP-EPHEMERAL-STAGING-ROLLOUT-001"
    assert items["ODP-SIGN-OFF-MODEL-RISK"]["canonical_owner_task"] == "ODP-MODEL-ARTIFACT-HISTORY-RECOVERY-001"
    assert items["ODP-SIGN-OFF-OPS-GO-NO-GO"]["canonical_owner_task"] == "ODP-PROD-BLUEGREEN-ROLLOUT-001"

    for key in ["ODP-SIGN-OFF-UAT", "ODP-SIGN-OFF-MODEL-RISK", "ODP-SIGN-OFF-OPS-GO-NO-GO"]:
        assert items[key]["real_status"] == "PENDING_HUMAN_AUTHORITY"
        assert items[key]["is_live_done"] is False
        assert items[key]["human_input_required"] is True

    # Deferred items
    assert "ODP-DEFERRED-ROOT-CAUSE-WAVE-5" in items
    assert "ODP-DEFERRED-GOOGLE-OAUTH" in items
    assert items["ODP-DEFERRED-ROOT-CAUSE-WAVE-5"]["canonical_owner_task"] == "DEFERRED-BACKLOG-WAVE5"
    assert items["ODP-DEFERRED-GOOGLE-OAUTH"]["canonical_owner_task"] == "HUMAN-GCP-WEB-OAUTH-CLIENTS-001"
    assert items["ODP-DEFERRED-ROOT-CAUSE-WAVE-5"]["real_status"] == "DEFERRED_NON_BLOCKING"
    assert items["ODP-DEFERRED-GOOGLE-OAUTH"]["real_status"] == "DEFERRED_NON_BLOCKING"
    assert items["ODP-DEFERRED-GOOGLE-OAUTH"]["pre_prod_blocking"] is False


def test_mutated_negative_cases(obligation_matrix: dict) -> None:
    """Systematically mutate valid obligations to ensure all validator guards strictly trigger."""
    base_item = obligation_matrix["obligations"][0]

    # Negative 1: Missing / blank canonical_owner_task
    bad1 = copy.deepcopy(base_item)
    bad1["canonical_owner_task"] = ""
    with pytest.raises(ValueError, match="canonical_owner_task"):
        validate_obligation(bad1)

    # Negative 2: canonical_owner_task pointing to archived historical task
    bad2 = copy.deepcopy(base_item)
    bad2["canonical_owner_task"] = "ODP-NFR-RUNTIME-EVIDENCE-001"
    with pytest.raises(ValueError, match="Archived historical task"):
        validate_obligation(bad2)

    # Negative 3: Unknown / fabricated owner task
    bad3 = copy.deepcopy(base_item)
    bad3["canonical_owner_task"] = "ODP-ROOT-CAUSE-WAVE5-001"
    with pytest.raises(ValueError, match="Prohibited unknown owner ID"):
        validate_obligation(bad3)

    # Negative 4: Blank receipt in required_receipts
    bad4 = copy.deepcopy(base_item)
    bad4["required_receipts"] = [""]
    with pytest.raises(ValueError, match="Blank/invalid receipt"):
        validate_obligation(bad4)

    # Negative 5: Empty required_receipts list
    bad5 = copy.deepcopy(base_item)
    bad5["required_receipts"] = []
    with pytest.raises(ValueError, match="Empty required_receipts"):
        validate_obligation(bad5)

    # Negative 6: Fabricated live done
    bad6 = copy.deepcopy(base_item)
    bad6["is_live_done"] = True
    with pytest.raises(ValueError, match="Invalid live done claim"):
        validate_obligation(bad6)

    # Negative 7: Combined pre_prod_blocking AND post_prod_observation (circularity)
    bad7 = copy.deepcopy(base_item)
    bad7["pre_prod_blocking"] = True
    bad7["post_prod_observation"] = True
    with pytest.raises(ValueError, match="Circular dependency"):
        validate_obligation(bad7)

    # Negative 8: Blank stage/phase/trigger
    for field in ["stage", "phase", "trigger_condition", "original_acceptance_ref"]:
        bad8 = copy.deepcopy(base_item)
        bad8[field] = "   "
        with pytest.raises(ValueError, match=field):
            validate_obligation(bad8)

    # Negative 9: Invalid category or status
    bad9 = copy.deepcopy(base_item)
    bad9["category"] = "invalid_cat"
    with pytest.raises(ValueError, match="Invalid category"):
        validate_obligation(bad9)


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

    # 4. Spent 6140 / build approval context described historically
    assert "6140d0ef" in content
    assert "spent" in content.lower() or "historical" in content.lower()

    # 5. Recommendation preserved without fabricating approval
    assert "Recommend Approve" in content or "recommendation" in content.lower()


def test_old_pr_disposition_matrix(pr_matrix: dict) -> None:
    audited = {item["pr_number"]: item for item in pr_matrix["audited_prs"]}
    expected_pr_numbers = {1243, 1205, 1052, 986, 970, 607, 1}
    assert set(audited.keys()) == expected_pr_numbers

    # Verify PR #1243
    pr1243 = audited[1243]
    assert pr1243["head_sha"] == "3a9fb628d83bb0ff39e36ef652d3e4164922e992"
    assert pr1243["recommendation"] == "close_as_superseded"
    assert pr1243["unlanded_code_remaining"] is False

    # Verify PR #1205
    pr1205 = audited[1205]
    assert pr1205["head_sha"] == "eaa7f8c51b81718a3582ce047fd86867c6db9eda"
    assert pr1205["recommendation"] == "close_as_superseded"
    assert pr1205["unlanded_code_remaining"] is False

    # Verify PR #1052 has unlanded code truth (trailer validation logic & sidecar doc)
    pr1052 = audited[1052]
    assert pr1052["head_sha"] == "73a9ee4676b8ebd9526b9d2768cb8c88aa8cacbd"
    assert pr1052["unlanded_code_remaining"] is True
    assert pr1052["recommendation"] == "needs_comparison"
    assert "check_commit_trailers.py" in pr1052["rationale"]

    # Verify PR #986 has unlanded code truth (git stderr propagation & supervisor test)
    pr986 = audited[986]
    assert pr986["head_sha"] == "173f51c649ec52cf5073b306fddb38abb6fd2e31"
    assert pr986["unlanded_code_remaining"] is True
    assert pr986["recommendation"] == "needs_comparison"
    assert "stderr" in pr986["rationale"]

    # Verify PR #970 has full rationale based on 65-file disposition
    pr970 = audited[970]
    assert pr970["recommendation"] == "close_as_superseded"
    assert "65" in pr970["rationale"]
    assert "ODAY_MARKET_DATA_FACADE_MODE" in pr970["rationale"]
    assert pr970["unlanded_code_remaining"] is False
    assert PR970_DISPOSITION_PATH.is_file(), "Missing PR #970 65-file disposition record"

    # Verify PR #607
    pr607 = audited[607]
    assert pr607["head_sha"] == "a70b62ca13168927c1500c3e24a8603885681331"
    assert pr607["recommendation"] == "close_as_historical_snapshot"
    assert pr607["unlanded_code_remaining"] is False

    # Verify data platform PR #1 has unlanded code truth (daily lookback scheduling / partition)
    pr1 = audited[1]
    assert pr1["repository"] == "alfloop-dev/oday-data-platform"
    assert pr1["head_sha"] == "0577773d0457d7b51e93d2e1913f70002227fdb4"
    assert pr1["unlanded_code_remaining"] is True
    assert pr1["recommendation"] == "needs_comparison"
    assert "daily-lookback" in pr1["rationale"] or "lookback" in pr1["rationale"]

    # Verify active in-flight PRs are protected with exact branches
    active_prs = {item["pr_number"]: item for item in pr_matrix["active_in_flight_prs"]}
    assert set(active_prs.keys()) == {1381, 1312, 1014, 77, 63}
    assert active_prs[1381]["branch"] == "task/ODP-DEV-LIVE-DEPLOY-EXECUTION-001"
    assert active_prs[1312]["branch"] == "task/XR-EXT-OSS-FINAL-AUDIT-001-RECOVERY-20260911"
    assert active_prs[1014]["branch"] == "task/ODP-EPHEMERAL-STAGING-ROLLOUT-001"
    assert active_prs[77]["branch"] == "task/DPF-BOUNDED-CAPTURE-RETENTION-EXECUTION-001"
    assert active_prs[63]["branch"] == "task/DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001"
    for item in active_prs.values():
        assert "active_delivery" in item["status"]
