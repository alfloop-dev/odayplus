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
4. Every obligation sits in one stage whose ordering flags are fixed; admission
   never waits for a production deployment or a production observation window.
5. Shared validator binds owners to the fixed canonical owner_inventory.json and
   rejects unknown, archived or wrong-lane owners, blank receipts, fabricated
   dones and contradictory phase/window combinations.
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
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
COMPLETION_DIR = Path(__file__).resolve().parent
OBLIGATION_MATRIX_PATH = COMPLETION_DIR / "obligation_matrix.json"
PR_DISPOSITION_MATRIX_PATH = COMPLETION_DIR / "pr_disposition_matrix.json"
PRODUCT_RELEASE_GO_NO_GO_PATH = REPO_ROOT / "docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md"
OWNER_INVENTORY_PATH = COMPLETION_DIR / "owner_inventory.json"
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
    "OWNER_RECONCILIATION_REQUIRED",
}

ALLOWED_WINDOW_TYPES = {
    "instant_readback",
    "continuous_time_window",
    "multi_day_window",
    "full_calendar_month",
    "drill_event",
    "event_stream",
}

# Windows that only exist on live production traffic once it is measured over time.
PRODUCTION_ONLY_WINDOW_TYPES = {
    "continuous_time_window",
    "multi_day_window",
    "full_calendar_month",
}

# Each stage fixes (pre_prod_blocking, post_prod_observation, requires_production_deployment).
# Admission never needs a production deployment; post-deploy windows never gate admission.
STAGE_FLAGS = {
    "pre_production_admission": (True, False, False),
    "code_remediation": (True, False, False),
    "production_cutover": (False, False, True),
    "post_deploy_observation": (False, True, True),
    "dev_runtime_observation": (False, False, False),
    "deferred": (False, False, False),
}


def load_owner_inventory() -> dict:
    with open(OWNER_INVENTORY_PATH, encoding="utf-8") as f:
        return json.load(f)


def validate_obligation(item: dict, inventory: dict) -> None:
    """Validate schema and invariant constraints on a single carryforward obligation."""
    oid = item.get("obligation_id")
    # 1. Non-empty string fields
    string_fields = [
        "obligation_id",
        "title",
        "category",
        "stage",
        "phase",
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
            raise ValueError(f"Obligation {oid} has invalid/empty '{field}': {val!r}")

    # 2. Enumerations
    if item["category"] not in ALLOWED_CATEGORIES:
        raise ValueError(f"Invalid category '{item['category']}' in {oid}")
    if item["real_status"] not in ALLOWED_STATUSES:
        raise ValueError(f"Invalid real_status '{item['real_status']}' in {oid}")
    if item["observation_window_type"] not in ALLOWED_WINDOW_TYPES:
        raise ValueError(f"Invalid observation_window_type '{item['observation_window_type']}' in {oid}")
    if item["stage"] not in STAGE_FLAGS:
        raise ValueError(f"Invalid stage '{item['stage']}' in {oid}")

    # 3. Target environments
    envs = item.get("target_environments")
    if not isinstance(envs, list) or not envs or not all(isinstance(e, str) and e.strip() for e in envs):
        raise ValueError(f"Invalid target_environments in {oid}: {envs!r}")

    # 4. Required receipts: list of non-empty strings
    receipts = item.get("required_receipts")
    if not isinstance(receipts, list) or not receipts:
        raise ValueError(f"Empty required_receipts in {oid}")
    for r in receipts:
        if not isinstance(r, str) or not r.strip():
            raise ValueError(f"Blank/invalid receipt entry in {oid}: {r!r}")

    # 5. Boolean flags
    bool_fields = [
        "is_engineering_done",
        "is_live_done",
        "pre_prod_blocking",
        "post_prod_observation",
        "requires_production_deployment",
        "human_input_required",
    ]
    for bool_field in bool_fields:
        if not isinstance(item.get(bool_field), bool):
            raise ValueError(f"Field '{bool_field}' must be a boolean in {oid}")

    # 6. Owner binding against the fixed canonical inventory
    active = {t["task_id"]: t for t in inventory["active_tasks"]}
    archived = {t["task_id"] for t in inventory["archived_tasks"]}
    backlog_refs = {r["ref"] for r in inventory["deferred_backlog_refs"]}

    def require_active(task_id: object, role: str) -> dict:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError(f"Missing canonical_owner_task/{role} in {oid}: {task_id!r}")
        if task_id in archived:
            raise ValueError(
                f"Archived historical task '{task_id}' cannot be used as {role} in {oid}; "
                f"set historical_source_task instead and bind an active execution lane."
            )
        if task_id in backlog_refs:
            raise ValueError(f"Deferred backlog reference '{task_id}' is not a canonical task ({role} in {oid})")
        if task_id not in active:
            raise ValueError(f"Unknown owner task '{task_id}' ({role} in {oid}) is not in the canonical owner inventory")
        if active[task_id]["status"] not in inventory["active_statuses"]:
            raise ValueError(f"Owner task '{task_id}' ({role} in {oid}) is not active: {active[task_id]['status']}")
        return active[task_id]

    owner = item.get("canonical_owner_task")
    if owner is None:
        if item["real_status"] == "OWNER_RECONCILIATION_REQUIRED":
            gap = item.get("ownership_gap")
            if not isinstance(gap, dict):
                raise ValueError(f"Missing canonical_owner_task in {oid} without an ownership_gap record")
            for field in ("finding", "required_action", "missing_authority"):
                if not isinstance(gap.get(field), str) or not gap[field].strip():
                    raise ValueError(f"ownership_gap.{field} is empty in {oid}")
            proposed = gap.get("proposed_owner_tasks")
            if not isinstance(proposed, list) or not proposed:
                raise ValueError(f"ownership_gap.proposed_owner_tasks is empty in {oid}")
            for task_id in proposed:
                require_active(task_id, "proposed_owner_task")
        elif item["real_status"] == "DEFERRED_NON_BLOCKING":
            if item.get("deferred_backlog_ref") not in backlog_refs:
                raise ValueError(f"Missing canonical_owner_task in {oid} without a declared deferred_backlog_ref")
        else:
            raise ValueError(f"Missing canonical_owner_task in {oid}")
    else:
        lane = require_active(owner, "canonical_owner_task")
        if item["stage"] not in lane["lane_roles"]:
            raise ValueError(
                f"Owner lane mismatch in {oid}: {owner} serves {lane['lane_roles']}, not stage '{item['stage']}'"
            )
        if item["real_status"] == "OWNER_RECONCILIATION_REQUIRED":
            raise ValueError(f"OWNER_RECONCILIATION_REQUIRED obligation {oid} cannot also claim an owner")
    for task_id in item.get("prerequisite_tasks", []):
        require_active(task_id, "prerequisite_task")

    # 7. Phase consistency: the stage fixes all three ordering flags.
    expected = STAGE_FLAGS[item["stage"]]
    actual = (item["pre_prod_blocking"], item["post_prod_observation"], item["requires_production_deployment"])
    if actual != expected:
        raise ValueError(
            f"Phase inconsistency in {oid}: stage '{item['stage']}' requires "
            f"(pre_prod_blocking, post_prod_observation, requires_production_deployment)={expected}, got {actual}"
        )
    if "production" in envs and item["observation_window_type"] in PRODUCTION_ONLY_WINDOW_TYPES:
        if item["stage"] != "post_deploy_observation":
            raise ValueError(
                f"Circular dependency error in {oid}: a production {item['observation_window_type']} "
                f"can only be observed after deployment, not in stage '{item['stage']}'"
            )

    # 8. No fake live done claims without live receipts
    if item["is_live_done"]:
        raise ValueError(f"Invalid live done claim in {oid}; live verification requires runtime receipts")

    # 9. Human authority consistency
    if item["real_status"] in {"PENDING_HUMAN_AUTHORITY", "OWNER_RECONCILIATION_REQUIRED"} and not item["human_input_required"]:
        raise ValueError(f"{item['real_status']} obligation {oid} must have human_input_required=True")

    # 10. Deferred consistency
    if (item["real_status"] == "DEFERRED_NON_BLOCKING") != (item["stage"] == "deferred"):
        raise ValueError(f"DEFERRED_NON_BLOCKING obligation {oid} must be in stage 'deferred' and vice versa")


@pytest.fixture(scope="module")
def obligation_matrix() -> dict:
    assert OBLIGATION_MATRIX_PATH.is_file(), f"Missing {OBLIGATION_MATRIX_PATH}"
    with open(OBLIGATION_MATRIX_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def inventory() -> dict:
    assert OWNER_INVENTORY_PATH.is_file(), f"Missing {OWNER_INVENTORY_PATH}"
    return load_owner_inventory()


@pytest.fixture(scope="module")
def pr_matrix() -> dict:
    assert PR_DISPOSITION_MATRIX_PATH.is_file(), f"Missing {PR_DISPOSITION_MATRIX_PATH}"
    with open(PR_DISPOSITION_MATRIX_PATH, encoding="utf-8") as f:
        return json.load(f)


def _items(obligation_matrix: dict) -> dict:
    return {item["obligation_id"]: item for item in obligation_matrix["obligations"]}


def test_owner_inventory_provenance(inventory: dict) -> None:
    prov = inventory["provenance"]
    assert re.fullmatch(r"[0-9a-f]{64}", prov["ai_status_json_sha256"])
    assert prov["read_at"].endswith("Z")
    assert set(inventory["active_statuses"]) == {"todo", "in_progress", "review", "blocked"}
    active_ids = {t["task_id"] for t in inventory["active_tasks"]}
    archived_ids = {t["task_id"] for t in inventory["archived_tasks"]}
    assert not active_ids & archived_ids
    for t in inventory["active_tasks"]:
        assert t["status"] in inventory["active_statuses"], t
        assert t["owner"] and t["reviewer"] and t["phase"]
        assert t["lane_roles"] and set(t["lane_roles"]) <= set(STAGE_FLAGS)
    for t in inventory["archived_tasks"]:
        assert t["status"] == "done", t
        assert re.fullmatch(r"[0-9a-f]{64}", t["archive_record_sha256"])
    # The bounded model investigation is archived, so it can never be a live lane.
    assert "ODP-MODEL-ARTIFACT-HISTORY-RECOVERY-001" in archived_ids
    for ref in inventory["deferred_backlog_refs"]:
        assert ref["kind"] == "backlog_reference_not_a_task"
        assert ref["ref"] not in active_ids | archived_ids


def test_obligation_matrix_schema_and_integrity(obligation_matrix: dict, inventory: dict) -> None:
    assert obligation_matrix["schema_version"] == "2.0.0"
    assert obligation_matrix["task_id"] == "ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001"
    assert obligation_matrix["owner"] == "Claude"
    assert obligation_matrix["reviewer"] == "Codex"
    assert obligation_matrix["owner_inventory"] == OWNER_INVENTORY_PATH.name
    ids = [item["obligation_id"] for item in obligation_matrix["obligations"]]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 15

    for item in obligation_matrix["obligations"]:
        validate_obligation(item, inventory)


def test_nfr_obligations_and_observation_windows(obligation_matrix: dict) -> None:
    nfrs = {k: v for k, v in _items(obligation_matrix).items() if v["category"] == "nfr"}
    assert set(nfrs) == {
        "ODP-FR-SHARED-008-STAGING",
        "ODP-FR-SHARED-008-PROD-READBACK",
        "ODP-NFR-PERF-001-STAGING",
        "ODP-NFR-PERF-001-PROD",
        "ODP-NFR-BATCH-002",
        "ODP-NFR-AVAIL-003",
        "ODP-NFR-RPO-004-STAGING-DRILL",
        "ODP-NFR-RPO-004-PROD-BACKUP-READBACK",
    }
    for item in nfrs.values():
        assert item["is_live_done"] is False
        assert item["real_status"] == "BLOCKED_BY_EVIDENCE"

    # SHARED-008: admission evidence is staging/dev only; production readback is cutover.
    staging = nfrs["ODP-FR-SHARED-008-STAGING"]
    assert staging["stage"] == "pre_production_admission"
    assert "production" not in staging["target_environments"]
    assert staging["requires_production_deployment"] is False
    assert staging["canonical_owner_task"] == "ODP-EPHEMERAL-STAGING-ROLLOUT-001"
    assert "production" not in staging["trigger_condition"].lower()
    assert any("WIF" in r for r in staging["required_receipts"])
    prod = nfrs["ODP-FR-SHARED-008-PROD-READBACK"]
    assert prod["stage"] == "production_cutover"
    assert prod["canonical_owner_task"] == "ODP-PROD-BLUEGREEN-ROLLOUT-001"
    assert prod["pre_prod_blocking"] is False and prod["post_prod_observation"] is False

    perf_staging = nfrs["ODP-NFR-PERF-001-STAGING"]
    assert perf_staging["stage"] == "pre_production_admission"
    assert perf_staging["canonical_owner_task"] == "ODP-EPHEMERAL-STAGING-ROLLOUT-001"
    assert any("concurrency 10/20/50" in r for r in perf_staging["required_receipts"])

    # Post-deploy windows: 24h, 7 operating days, one full calendar month.
    windows = {
        "ODP-NFR-PERF-001-PROD": ("continuous_time_window", "24"),
        "ODP-NFR-BATCH-002": ("multi_day_window", "7"),
        "ODP-NFR-AVAIL-003": ("full_calendar_month", "calendar month"),
    }
    for oid, (wtype, marker) in windows.items():
        item = nfrs[oid]
        assert item["stage"] == "post_deploy_observation", oid
        assert item["observation_window_type"] == wtype, oid
        assert marker in item["observation_window"], oid
        assert item["canonical_owner_task"] == "ODP-POSTDEPLOY-WATCH-CLOSEOUT-001", oid
    # BATCH cutoff time is a Human input; no invented value.
    batch = nfrs["ODP-NFR-BATCH-002"]
    assert batch["human_input_required"] is True
    assert any("cutoff time" in r for r in batch["required_receipts"])
    assert not re.search(r"\b\d{1,2}:\d{2}\b", json.dumps(batch))

    drill = nfrs["ODP-NFR-RPO-004-STAGING-DRILL"]
    assert drill["stage"] == "pre_production_admission"
    assert drill["target_environments"] == ["staging"]
    assert drill["observation_window_type"] == "drill_event"
    assert nfrs["ODP-NFR-RPO-004-PROD-BACKUP-READBACK"]["stage"] == "production_cutover"


def test_cdc_live_acceptance_gaps(obligation_matrix: dict) -> None:
    cdc_items = {k: v for k, v in _items(obligation_matrix).items() if v["category"] == "cdc_live"}
    assert set(cdc_items) == {
        "ODP-CDC-LIVE-REPLICA-SET",
        "ODP-CDC-LIVE-LATENCY-STAGING",
        "ODP-CDC-LIVE-LATENCY-PROD",
        "ODP-CDC-LIVE-IAM-CREDENTIALS",
        "ODP-CDC-LIVE-PG-DDL-STAGING",
        "ODP-CDC-LIVE-PG-DDL-PROD-READBACK",
        "ODP-CDC-LIVE-OPLOG-FAIL-CLOSED",
        "ODP-CDC-MACHINE-EVENT-LIFECYCLE",
    }
    lifecycle = cdc_items["ODP-CDC-MACHINE-EVENT-LIFECYCLE"]
    assert lifecycle["canonical_owner_task"] == "ODP-CDC-MACHINE-EVENT-LIFECYCLE-001"
    assert lifecycle["stage"] == "code_remediation"
    assert lifecycle["real_status"] == "IN_PROGRESS"

    prod = cdc_items["ODP-CDC-LIVE-LATENCY-PROD"]
    assert prod["stage"] == "post_deploy_observation"
    assert prod["canonical_owner_task"] == "ODP-POSTDEPLOY-WATCH-CLOSEOUT-001"
    assert (prod["pre_prod_blocking"], prod["post_prod_observation"]) == (False, True)
    assert cdc_items["ODP-CDC-LIVE-LATENCY-STAGING"]["target_environments"] == ["staging"]
    assert "production" not in cdc_items["ODP-CDC-LIVE-PG-DDL-STAGING"]["target_environments"]

    for cdc_id, item in cdc_items.items():
        assert item["is_live_done"] is False, f"{cdc_id} cannot be live done without runtime proof"


def test_business_operational_and_merge_queue_obligations(obligation_matrix: dict) -> None:
    items = _items(obligation_matrix)

    # PARTIAL keeps its original live obligation, split by phase.
    ratify = items["ODP-PARTIAL-H06-SCOPE-RATIFICATION"]
    assert ratify["canonical_owner_task"] == "HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001"
    assert ratify["real_status"] == "PENDING_HUMAN_AUTHORITY"
    assert ratify["stage"] == "pre_production_admission"
    intake = items["ODP-PARTIAL-H06-STAGING-INTAKE"]
    assert intake["stage"] == "pre_production_admission"
    assert intake["target_environments"] == ["staging"]
    assert intake["prerequisite_tasks"] == ["HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001"]
    accept = items["ODP-PARTIAL-H06-PROD-ACCEPTANCE"]
    assert accept["stage"] == "post_deploy_observation"
    assert accept["canonical_owner_task"] == "ODP-POSTDEPLOY-WATCH-CLOSEOUT-001"
    assert "ODP-PARTIAL-LIVE-H06" not in items

    # ADJUST / AVM: ownership is an explicit open gap, not a claimed binding.
    for oid, authority in [
        ("ODP-ADJUST-OPERATIONAL-CONFIRMATION", "Operations Lead / Product Lead"),
        ("ODP-AVM-FINANCE-CUTOVER", "Finance Owner"),
    ]:
        item = items[oid]
        assert item["canonical_owner_task"] is None, oid
        assert item["real_status"] == "OWNER_RECONCILIATION_REQUIRED", oid
        assert item["pre_prod_blocking"] is True, oid
        assert item["ownership_gap"]["missing_authority"] == authority, oid
        assert item["is_live_done"] is False, oid
    assert items["ODP-ADJUST-OPERATIONAL-CONFIRMATION"]["ownership_gap"]["proposed_owner_tasks"] == [
        "HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001"
    ]
    assert "ODP-PRODUCTION-MODEL-REGISTRY-001" in items["ODP-AVM-FINANCE-CUTOVER"]["ownership_gap"]["proposed_owner_tasks"]
    assert any("in-place adjustment vs stop-plus-recreate" in r for r in items["ODP-ADJUST-OPERATIONAL-CONFIRMATION"]["required_receipts"])
    assert any("Contract R-4 three rollback thresholds" in r for r in items["ODP-AVM-FINANCE-CUTOVER"]["required_receipts"])

    for key in ["ODP-MERGE-QUEUE-BATCH-FORMATION", "ODP-MERGE-QUEUE-HOLD-TIMEOUT", "ODP-MERGE-QUEUE-ALLGREEN-REBUILD"]:
        assert items[key]["stage"] == "dev_runtime_observation"
        assert items[key]["is_engineering_done"] is True
        assert items[key]["is_live_done"] is False
        assert items[key]["real_status"] == "BLOCKED_BY_EVIDENCE"
        assert items[key]["canonical_owner_task"] == "ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001"


def test_cross_role_signoffs_and_deferred_items(obligation_matrix: dict) -> None:
    items = _items(obligation_matrix)

    assert items["ODP-SIGN-OFF-UAT"]["canonical_owner_task"] == "ODP-EPHEMERAL-STAGING-ROLLOUT-001"
    assert items["ODP-SIGN-OFF-OPS-GO-NO-GO"]["canonical_owner_task"] == "ODP-PROD-BLUEGREEN-ROLLOUT-001"
    for key in ["ODP-SIGN-OFF-UAT", "ODP-SIGN-OFF-OPS-GO-NO-GO"]:
        assert items[key]["real_status"] == "PENDING_HUMAN_AUTHORITY"
        assert items[key]["is_live_done"] is False
        assert items[key]["human_input_required"] is True

    model = items["ODP-SIGN-OFF-MODEL-RISK"]
    assert model["canonical_owner_task"] == "ODP-PRODUCTION-MODEL-REGISTRY-001"
    assert model["prerequisite_tasks"] == ["ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001"]
    assert model["historical_source_task"] == "ODP-MODEL-ARTIFACT-HISTORY-RECOVERY-001"
    assert model["owner_or_authority"] == "Product Owner + ML Risk Owner"
    assert any("Product Owner + ML Risk Owner" in r for r in model["required_receipts"])
    assert model["human_input_required"] is True
    assert model["is_live_done"] is False

    wave5 = items["ODP-DEFERRED-ROOT-CAUSE-WAVE-5"]
    assert wave5["canonical_owner_task"] is None
    assert wave5["deferred_backlog_ref"] == "DEFERRED-BACKLOG-WAVE5"
    oauth = items["ODP-DEFERRED-GOOGLE-OAUTH"]
    assert oauth["canonical_owner_task"] == "HUMAN-GCP-WEB-OAUTH-CLIENTS-001"
    for item in (wave5, oauth):
        assert item["real_status"] == "DEFERRED_NON_BLOCKING"
        assert item["stage"] == "deferred"
        assert item["pre_prod_blocking"] is False


def _mutate(base: dict, **changes: object) -> dict:
    bad = copy.deepcopy(base)
    bad.update(changes)
    return bad


def test_mutated_negative_cases(obligation_matrix: dict, inventory: dict) -> None:
    """Mutate valid obligations so every validator guard is shown to trigger."""
    items = _items(obligation_matrix)
    base_item = items["ODP-NFR-PERF-001-STAGING"]
    validate_obligation(base_item, inventory)

    def rejects(item: dict, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            validate_obligation(item, inventory)

    # Owner binding
    rejects(_mutate(base_item, canonical_owner_task=""), "Missing canonical_owner_task")
    rejects(_mutate(base_item, canonical_owner_task=None), "Missing canonical_owner_task")
    for archived in inventory["archived_tasks"]:
        rejects(_mutate(base_item, canonical_owner_task=archived["task_id"]), "Archived historical task")
    for unknown in ["ODP-ROOT-CAUSE-WAVE5-001", "ODP-AUTH-GOOGLE-OAUTH-001", "ODP-NOT-A-REAL-TASK-999"]:
        rejects(_mutate(base_item, canonical_owner_task=unknown), "Unknown owner task")
    rejects(_mutate(base_item, canonical_owner_task="DEFERRED-BACKLOG-WAVE5"), "Deferred backlog reference")
    rejects(_mutate(base_item, prerequisite_tasks=["ODP-NOT-A-REAL-TASK-999"]), "Unknown owner task")
    rejects(_mutate(base_item, canonical_owner_task="ODP-POSTDEPLOY-WATCH-CLOSEOUT-001"), "Owner lane mismatch")

    # Inventory status is enforced, not just membership.
    stale = copy.deepcopy(inventory)
    for t in stale["active_tasks"]:
        if t["task_id"] == "ODP-EPHEMERAL-STAGING-ROLLOUT-001":
            t["status"] = "done"
    with pytest.raises(ValueError, match="is not active"):
        validate_obligation(base_item, stale)

    # ModelRisk cannot fall back to the archived bounded investigation.
    rejects(
        _mutate(items["ODP-SIGN-OFF-MODEL-RISK"], canonical_owner_task="ODP-MODEL-ARTIFACT-HISTORY-RECOVERY-001"),
        "Archived historical task",
    )

    # Ownership gaps must name an active lane and the action still required.
    adjust = items["ODP-ADJUST-OPERATIONAL-CONFIRMATION"]
    no_gap = _mutate(adjust)
    no_gap.pop("ownership_gap")
    rejects(no_gap, "without an ownership_gap")
    rejects(
        _mutate(adjust, ownership_gap={**adjust["ownership_gap"], "proposed_owner_tasks": ["ODP-NOT-A-REAL-TASK-999"]}),
        "Unknown owner task",
    )
    rejects(_mutate(adjust, ownership_gap={**adjust["ownership_gap"], "required_action": " "}), "required_action")
    rejects(_mutate(adjust, canonical_owner_task="HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001"), "cannot also claim an owner")
    wave5 = items["ODP-DEFERRED-ROOT-CAUSE-WAVE-5"]
    rejects(_mutate(wave5, deferred_backlog_ref="ODP-ROOT-CAUSE-WAVE5-001"), "deferred_backlog_ref")

    # Receipts and live-done
    rejects(_mutate(base_item, required_receipts=[""]), "Blank/invalid receipt")
    rejects(_mutate(base_item, required_receipts=[]), "Empty required_receipts")
    rejects(_mutate(base_item, is_live_done=True), "Invalid live done claim")

    # Phase / window consistency
    cdc_prod = items["ODP-CDC-LIVE-LATENCY-PROD"]
    # Reviewer case: flip flags only, keep the post-deploy stage, owner and production 24h window.
    rejects(_mutate(cdc_prod, pre_prod_blocking=True, post_prod_observation=False), "Phase inconsistency")
    # Same, also relabelling the stage: the post-deploy owner lane no longer fits.
    rejects(
        _mutate(cdc_prod, stage="pre_production_admission", pre_prod_blocking=True, post_prod_observation=False,
                requires_production_deployment=False),
        "Owner lane mismatch",
    )
    # And moving it to a staging lane still cannot admit a production 24h window.
    rejects(
        _mutate(cdc_prod, stage="pre_production_admission", pre_prod_blocking=True, post_prod_observation=False,
                requires_production_deployment=False, canonical_owner_task="ODP-EPHEMERAL-STAGING-ROLLOUT-001"),
        "Circular dependency",
    )
    # The pre-split SHARED-008 shape: admission that waits for its own production deploy.
    rejects(
        _mutate(items["ODP-FR-SHARED-008-STAGING"], target_environments=["dev", "staging", "production"],
                requires_production_deployment=True),
        "Phase inconsistency",
    )
    rejects(_mutate(base_item, pre_prod_blocking=True, post_prod_observation=True), "Phase inconsistency")
    rejects(_mutate(items["ODP-NFR-AVAIL-003"], post_prod_observation=False), "Phase inconsistency")

    # Blank fields and enums
    for field in ["stage", "phase", "trigger_condition", "original_acceptance_ref"]:
        rejects(_mutate(base_item, **{field: "   "}), field)
    rejects(_mutate(base_item, category="invalid_cat"), "Invalid category")
    rejects(_mutate(base_item, stage="staging_and_prod_deployment_verification"), "Invalid stage")
    rejects(_mutate(base_item, real_status="DONE"), "Invalid real_status")


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

    # #1243 / #1205: both heads are 22-file evidence rebinds of candidate 596b9c9a / run 34179791241.
    for number, head, additions in [
        (1243, "3a9fb628d83bb0ff39e36ef652d3e4164922e992", 2429),
        (1205, "eaa7f8c51b81718a3582ce047fd86867c6db9eda", 2450),
    ]:
        pr = audited[number]
        assert pr["head_sha"] == head
        assert pr["changed_files"] == 22
        assert pr["additions"] == additions
        assert pr["recommendation"] == "close_as_superseded"
        assert pr["unlanded_code_remaining"] is False
        assert "596b9c9a1788d952811a2bf8d4bba8a4e4d76b12" in pr["exact_diff_summary"]
        assert "34179791241" in pr["exact_diff_summary"]
        assert "PR #1392" in pr["superseded_by"]
        assert "fdf0fb9fde1dfd8e5de7636bbc3b0f8ee16a4e63" in pr["superseded_by"]
        # #1387 is an OpsBus issue and #1390 the H08 merge-queue PR: neither is a rebind.
        for wrong in ("33942097235 into", "3b5de7e7", "#1387", "#1390"):
            assert wrong not in json.dumps(pr), (number, wrong)

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
    assert pr970["head_sha"] == "e102821ccd5aa89e95b04d957e3b2fd863a8c083"
    assert pr970["changed_files"] == 65
    assert "b32fd65f4e60e4814b6b96bf074c5dc34dec12d4" in pr970["superseded_by"]
    assert pr970["head_sha"] in PR970_DISPOSITION_PATH.read_text(encoding="utf-8")
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
    assert pr1["changed_files"] == 6
    for path in ("configmap.yaml", "assets.py", "raw_transactions.py", "schedules.py", "test_assets.py", "test_schedules.py"):
        assert path in pr1["exact_diff_summary"], path
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
