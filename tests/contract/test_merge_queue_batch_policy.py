"""Contract tests for merge queue batch policy and CI/CD workflow contracts.

Implements the 6 automated verification matrix scenarios specified in
WP-35B / implementation-handoff.md §3.2 (ODP-MERGE-QUEUE-BATCH-DESIGN-001).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from delivery_toolchain.github.apply_branch_protection import (
    branch_policy,
    build_payload,
    build_ruleset_payload,
    merge_queue_config,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / ".github/branch-protection/policy.json"
CI_WORKFLOW_PATH = ROOT / ".github/workflows/ci.yml"
REVIEW_GATE_WORKFLOW_PATH = ROOT / ".github/workflows/merge-queue-review-gate.yml"
RUNBOOK_PATH = ROOT / "docs/runbooks/dev-merge-queue.md"


def _load_policy() -> dict[str, Any]:
    assert POLICY_PATH.exists(), f"Policy file not found: {POLICY_PATH}"
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> dict[str, Any]:
    assert path.exists(), f"YAML workflow file not found: {path}"
    content = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content)
    assert isinstance(parsed, dict), f"Expected dict from {path}, got {type(parsed)}"
    return parsed


# ==============================================================================
# Scenario 1: Multiple qualified PRs form batch (up to max_entries_to_merge)
# ==============================================================================


def test_scenario_1_batch_formation_parameters_and_ruleset_payload() -> None:
    """Scenario 1: Policy enforces min_entries_to_merge=2 and max_entries_to_merge=5.

    When 2 or more qualified PRs arrive, the queue accumulates candidates into
    a batch up to max_entries_to_merge.
    """
    policy = _load_policy()
    dev_queue = merge_queue_config(policy, "dev")
    assert dev_queue is not None, "merge_queue config missing for dev branch"

    # Verify batch configuration bounds
    assert dev_queue["min_entries_to_merge"] == 2, "Option B engineering default requires min=2"
    assert dev_queue["max_entries_to_merge"] == 5, "Max entries to merge must be 5"
    assert dev_queue["max_entries_to_build"] == 5, "Max speculative entries to build must be 5"
    assert dev_queue["min_entries_to_merge"] <= dev_queue["max_entries_to_merge"]

    # Verify translation into GitHub ruleset API payload
    ruleset = build_ruleset_payload(dev_queue, "dev")
    assert ruleset["name"] == "dev-merge-queue"
    assert ruleset["target"] == "branch"
    assert ruleset["enforcement"] == "active"
    assert ruleset["conditions"]["ref_name"]["include"] == ["refs/heads/dev"]

    rules = ruleset["rules"]
    assert len(rules) == 1
    rule_params = rules[0]["parameters"]
    assert rule_params["min_entries_to_merge"] == 2
    assert rule_params["max_entries_to_merge"] == 5
    assert rule_params["max_entries_to_build"] == 5
    assert rule_params["merge_method"] == "MERGE"


# ==============================================================================
# Scenario 2: Solo-PR bounded wait timeout
# ==============================================================================


def test_scenario_2_solo_pr_bounded_wait_timeout() -> None:
    """Scenario 2: Solo PRs are not held indefinitely waiting for companion PRs.

    min_entries_to_merge_wait_minutes bounds accumulation wait to 10 minutes.
    Per GitHub merge queue semantics, this timer runs concurrently with CI execution.
    """
    policy = _load_policy()
    dev_queue = merge_queue_config(policy, "dev")
    assert dev_queue is not None

    wait_minutes = dev_queue.get("min_entries_to_merge_wait_minutes")
    assert wait_minutes == 10, "Option B engineering default requires wait_minutes=10"
    assert 0 < wait_minutes <= 360, "Wait minutes must be bounded and positive"

    timeout_minutes = dev_queue.get("check_response_timeout_minutes")
    assert timeout_minutes == 60, "Check response timeout must be 60 minutes"
    assert wait_minutes < timeout_minutes, "Wait ceiling must be well within check timeout"

    # Verify runbook documents bounded wait behavior
    runbook_text = RUNBOOK_PATH.read_text(encoding="utf-8")
    assert "min_entries_to_merge_wait_minutes" in runbook_text
    assert "10" in runbook_text
    assert "Bounded wait ceiling" in runbook_text


# ==============================================================================
# Scenario 3: Unapproved / failing PR exclusion
# ==============================================================================


def test_scenario_3_unapproved_and_failing_pr_exclusion() -> None:
    """Scenario 3: Unapproved or failing PRs are excluded from merge group.

    - Pre-admission: 4 required status checks must be configured on dev.
    - Post-admission: merge-queue-review-gate.yml validates task-review-gate
      on PR head and stamps failure if missing or not approved.
    """
    policy = _load_policy()
    required_checks = policy.get("required_status_checks", [])
    assert "orchestrator" in required_checks
    assert "product" in required_checks
    assert "product-e2e-gate" in required_checks
    assert "task-review-gate" in required_checks
    assert policy.get("enforce_admins") is True

    # Classic branch protection payload preserves required status checks
    dev_payload = build_payload(branch_policy(policy, "dev"))
    assert dev_payload["required_status_checks"]["contexts"] == [
        "orchestrator",
        "product",
        "product-e2e-gate",
        "task-review-gate",
    ]
    assert dev_payload["enforce_admins"] is True

    # Workflow contract: merge-queue-review-gate.yml fails closed
    review_gate_content = REVIEW_GATE_WORKFLOW_PATH.read_text(encoding="utf-8")
    assert 'state="success"' in review_gate_content or '[ "$state" = "success" ]' in review_gate_content
    assert "state=failure" in review_gate_content
    assert "context=task-review-gate" in review_gate_content


# ==============================================================================
# Scenario 4: Head change re-validation
# ==============================================================================


def test_scenario_4_head_change_revalidation() -> None:
    """Scenario 4: If a PR's head SHA changes, prior approvals/CI cannot be reused.

    - merge-queue-review-gate.yml fetches the current PR headRefOid via GitHub API
      and inspects the status of that specific commit, preventing stale gate reuse.
    - CI runs on both pull_request and merge_group events.
    """
    review_gate_content = REVIEW_GATE_WORKFLOW_PATH.read_text(encoding="utf-8")
    # Verify exact PR head SHA lookup
    assert "pr_head=" in review_gate_content
    assert "headRefOid" in review_gate_content
    assert "commits/${pr_head}/status" in review_gate_content

    # Verify CI workflow triggers
    ci_workflow = _load_yaml(CI_WORKFLOW_PATH)
    triggers = ci_workflow.get(True) or ci_workflow.get("on")  # yaml parses "on" as True if unquoted
    assert triggers is not None
    assert "pull_request" in triggers
    assert "merge_group" in triggers


# ==============================================================================
# Scenario 5: Failure isolation under ALLGREEN
# ==============================================================================


def test_scenario_5_failure_isolation_under_allgreen() -> None:
    """Scenario 5: grouping_strategy is strictly ALLGREEN across policy files.

    HEADGREEN or NONE are prohibited as they weaken candidate failure isolation.
    """
    policy = _load_policy()
    dev_queue = merge_queue_config(policy, "dev")
    assert dev_queue is not None
    assert dev_queue["grouping_strategy"] == "ALLGREEN"

    ruleset = build_ruleset_payload(dev_queue, "dev")
    rule_params = ruleset["rules"][0]["parameters"]
    assert rule_params["grouping_strategy"] == "ALLGREEN"
    assert rule_params["grouping_strategy"] not in ["HEADGREEN", "NONE"]


# ==============================================================================
# Scenario 6: Actual required checks on group SHA
# ==============================================================================


def test_scenario_6_actual_required_checks_on_group_sha() -> None:
    """Scenario 6: orchestrator, product, product-e2e-gate, task-review-gate

    all execute and report against the merge_group commit ref.
    """
    # Verify CI workflow trigger on merge_group
    ci_workflow = _load_yaml(CI_WORKFLOW_PATH)
    triggers = ci_workflow.get(True) or ci_workflow.get("on")
    assert triggers is not None
    assert "merge_group" in triggers
    merge_group_trigger = triggers["merge_group"]
    assert merge_group_trigger.get("types") == ["checks_requested"]

    # Verify required jobs exist in CI workflow
    jobs = ci_workflow.get("jobs", {})
    assert "orchestrator" in jobs
    assert "product" in jobs
    assert "product-e2e-gate" in jobs

    # Verify review gate workflow trigger on merge_group
    review_gate_wf = _load_yaml(REVIEW_GATE_WORKFLOW_PATH)
    rg_triggers = review_gate_wf.get(True) or review_gate_wf.get("on")
    assert rg_triggers is not None
    assert "merge_group" in rg_triggers
    assert rg_triggers["merge_group"].get("types") == ["checks_requested"]
    assert "review-gate" in review_gate_wf.get("jobs", {})


# ==============================================================================
# Protection Invariant Tests (strict=false on dev, strict=true on main)
# ==============================================================================


def test_dev_merge_queue_strict_invariants() -> None:
    """dev must set strict=false to prevent rebase races behind merge queue,

    while main (no queue) keeps strict=true.
    """
    policy = _load_policy()
    dev_payload = build_payload(branch_policy(policy, "dev"))
    main_payload = build_payload(branch_policy(policy, "main"))

    assert dev_payload["required_status_checks"]["strict"] is False
    assert main_payload["required_status_checks"]["strict"] is True
