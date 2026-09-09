# ODP-MERGE-QUEUE-BATCH-IMPLEMENTATION-001 — Evidence Package

- **Task ID**: `ODP-MERGE-QUEUE-BATCH-IMPLEMENTATION-001`
- **Phase**: WP-35B (Merge queue batch engineering defaults, runbook & contract verification)
- **Owner**: Antigravity7
- **Reviewer**: Codex
- **Date**: 2026-09-09
- **Decision reference**: D21 in [ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md](https://github.com/alfloop-dev/odayplus/blob/be04fe7954d3414f024901e034caacd95ae81538/docs/plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md)
- **Prerequisite A-Stage Artifacts**: [ODP-MERGE-QUEUE-BATCH-DESIGN-001](https://github.com/alfloop-dev/odayplus/tree/dev/docs/evidence/human-decisions/ODP-MERGE-QUEUE-BATCH-DESIGN-001)

---

## 1. Executive Summary

This task delivers the **WP-35B** engineering implementation for the merge queue batch requirement (ratified in D21):
1. **Repository Policy Configuration**: Updated `.github/branch-protection/policy.json` to adopt Option B (`min_entries_to_merge = 2`, `min_entries_to_merge_wait_minutes = 10`) as an adjustable engineering default for `dev` (adopted by Codex per current user instructions to complete the deliverable, implementing the batch requirement confirmed under D21 without itemized H08 sign-off or live activation approval).
2. **Toolchain Alignment**: Updated `delivery_toolchain/github/apply_branch_protection.py` fallback defaults to align with the new batch parameters.
3. **Runbook Documentation**: Updated `docs/runbooks/dev-merge-queue.md` with parameter definitions, bounded accumulation wait semantics, precise provenance, and WP-35B/C stage boundaries.
4. **Automated Verification**: Created `tests/contract/test_merge_queue_batch_policy.py` implementing the 6 verification scenarios from handoff §3.2, and updated `tests/security/test_branch_protection_policy.py`.

---

## 2. Policy Configuration & Protection Invariants

The updated configuration in `.github/branch-protection/policy.json` specifies:

| Setting | Value | Rationale / Contract |
|---|---|---|
| `merge_method` | `MERGE` | Preserves standard merge commit history and `LLM-Agent` / `Task-ID` / `Reviewer` trailers. |
| `grouping_strategy` | `ALLGREEN` | Strictly retained. Every entry's own merge commit must be green, isolating failed PRs. |
| `min_entries_to_merge` | `2` | Option B default: requires 2 PRs to form a batch when available, reducing `dev` merge commit churn. |
| `min_entries_to_merge_wait_minutes` | `10` | Bounded wait ceiling: bounds solo PR hold time to 10 minutes to accumulate companion PRs before merging solo. Official GitHub documentation notes merge limits affect merges after build checks pass; offline tests verify configuration schema only, while real solo-PR timeline behavior is unverified offline and listed for WP-35C live verification. |
| `max_entries_to_merge` | `5` | Maximum PRs permitted in a single merged batch group. |
| `max_entries_to_build` | `5` | Maximum speculative merge group builds executing concurrently. |
| `check_response_timeout_minutes` | `60` | Maximum check response timeout before candidate ejection. |
| `required_status_checks` | `["orchestrator", "product", "product-e2e-gate", "task-review-gate"]` | All 4 required checks preserved; enforce on administrators is `true`. |
| `branches.dev.strict` | `false` | Disarmed on `dev` to prevent race condition while queue manages base composition; kept `true` on `main`. |

---

## 3. Automated Verification Matrix (Handoff §3.2)

All 6 core contract/policy scenarios defined in `implementation-handoff.md` §3.2 are verified by automated tests in `tests/contract/test_merge_queue_batch_policy.py`:

| # | Scenario | Verification Method | Test Case | Result |
|---|---|---|---|---|
| 1 | **Multiple qualified PRs form batch** | Policy assertions and payload builder validation (`min_entries_to_merge=2`, `max_entries_to_merge=5`) | `test_scenario_1_batch_formation_parameters_and_ruleset_payload` | `PASSED` |
| 2 | **Solo-PR bounded wait timeout** | Bounded wait timeout contract verification (wait=10 min, timeout=60 min, runbook doc; offline contract only, real solo-PR timing reserved for WP-35C) | `test_scenario_2_solo_pr_bounded_wait_timeout` | `PASSED` |
| 3 | **Unapproved / failing PR exclusion** | Branch protection check assertions & `merge-queue-review-gate.yml` fail-closed status check assertions | `test_scenario_3_unapproved_and_failing_pr_exclusion` | `PASSED` |
| 4 | **Head change re-validation** | Review gate workflow inspection of exact PR `headRefOid` status + CI triggers on `pull_request`/`merge_group` | `test_scenario_4_head_change_revalidation` | `PASSED` |
| 5 | **Failure isolation under ALLGREEN** | Strict `grouping_strategy == "ALLGREEN"` verification across policy and generated payload | `test_scenario_5_failure_isolation_under_allgreen` | `PASSED` |
| 6 | **Actual required checks on group SHA** | CI workflow (`ci.yml`) and review gate (`merge-queue-review-gate.yml`) `merge_group` event trigger verification | `test_scenario_6_actual_required_checks_on_group_sha` | `PASSED` |
| — | **dev/main Strict Invariants** | Verification of `strict=false` on `dev` and `strict=true` on `main` | `test_dev_merge_queue_strict_invariants` | `PASSED` |

---

## 4. Verification Receipts

### Command: `git diff --check`
- **Exit Code**: `0`
- **Output**: Clean (no whitespace or git diff errors)

### Command: `uv run pytest tests/contract/test_merge_queue_batch_policy.py -q`
- **Exit Code**: `0`
- **Selection**: 7 passed in 0.17s

### Command: `uv run pytest tests/security/test_branch_protection_policy.py -q`
- **Exit Code**: `0`
- **Selection**: 15 passed in 0.05s

### Command: `uv run ruff check delivery_toolchain tests/contract/test_merge_queue_batch_policy.py tests/security/test_branch_protection_policy.py`
- **Exit Code**: `0`
- **Output**: All checks passed!

---

## 5. Scope Boundaries & Live Activation Separation (WP-35C)

1. **WP-35B Boundary**: This deliverable updates repository policy definitions, toolchain scripts, runbooks, and automated contract tests.
2. **WP-35C Separation**: Live application to GitHub rulesets via `python3 delivery_toolchain/github/apply_branch_protection.py` and empirical solo-PR timeline/latency verification are reserved for WP-35C upon operator authorization.
3. **No Automatic Apply Linkage**: Verified that merging `policy.json` does NOT trigger automatic execution of `apply_branch_protection.py`.
4. **Simulation vs Live Claims**: Offline test passes verify repository contracts, payload builders, and workflow definitions; they are not claimed as live GitHub merge queue batching or timing evidence.
