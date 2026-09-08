# Implementation Handoff — Merge Queue Batch Engineering (WP-35B/C)

- **Task**: ODP-MERGE-QUEUE-BATCH-DESIGN-001 (A-stage)
- **Date**: 2026-09-08
- **Author**: Antigravity3
- **Reviewer**: Codex2
- **Target**: WP-35B/C implementation task
- **Decision reference**: D21 in [ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md](https://github.com/alfloop-dev/odayplus/blob/be04fe7954d3414f024901e034caacd95ae81538/docs/plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md)

## 1. Purpose

This document specifies the entry conditions, scope, automated verification matrix,
and live activation / rollback procedures for the B-stage (engineering) and
C-stage (live activation) of the merge queue batch requirement.

## 2. A-Stage Deliverables (This Task)

| Deliverable | Status | Location | Description |
|---|---|---|---|
| Queue observations & telemetry | ✅ Complete | [queue-observations.json](queue-observations.json) | Read-only measurements of CI duration, creation-ordered sample merge intervals, configuration readbacks, per-SHA check run / status receipts with exact command argv, timestamps, exit codes, and retained raw observation rows |
| Batch requirement specification | ✅ Complete | [batch-requirement.md](batch-requirement.md) | Formal requirement specification aligned with D21 and official GitHub ruleset parameters |
| Configuration options with tradeoffs | ✅ Complete | [configuration-options.md](configuration-options.md) | Three comparable options for H08 human review with full baseline rollback procedure |
| Governance handoff for WP-90 | ✅ Complete | [governance-handoff.md](governance-handoff.md) | D21 `IMPLEMENTATION_READY` disposition update fragment for shared registry |
| Implementation handoff | ✅ Complete | this file | Acceptance criteria, verification matrix, C-stage live behavioral acceptance scenarios with required receipt fields, entry conditions, and operational procedures |

## 3. B-Stage Entry Conditions & Engineering Scope (WP-35B)

The B-stage implementation task may start when **all** of the following are met:

1. **H08 parameter decision**: Human has reviewed [configuration-options.md](configuration-options.md)
   and confirmed selected values for `min_entries_to_merge` and
   `min_entries_to_merge_wait_minutes`.
2. **This A-stage task merged**: This evidence package is available on `dev`.

### 3.1 B-Stage Scope

- Modify `.github/branch-protection/policy.json` with the approved parameter values.
- Update `docs/runbooks/dev-merge-queue.md` to document the batch configuration and rationale.
- Add or update policy validation tests in `tests/` or `delivery_toolchain/`.
- Preserve `grouping_strategy = ALLGREEN`, all 4 required status contexts, and `merge_method = MERGE`.

### 3.2 B-Stage Automated Verification Matrix

The B-stage deliverable must satisfy and verify the following 6 core contract/policy scenarios:

| # | Scenario | Expected Result | Verification / Evidence Method |
|---|---|---|---|
| 1 | **Multiple qualified PRs form batch** | When 2+ PRs with passing checks and approvals enter queue, queue forms a combined batch up to `max_entries_to_merge` | Policy assertions and workflow contract tests proving batch formation rules |
| 2 | **Solo-PR bounded wait timeout** | When a solo PR is queued without companion PRs, it is released to merge alone after `min_entries_to_merge_wait_minutes` expires | Bounded wait timeout contract verification in policy schema and runbook documentation |
| 3 | **Unapproved / failing PR exclusion** | PR lacking `task-review-gate` or failing required checks cannot be admitted to or merged in a green batch | `merge-queue-review-gate.yml` assertion tests ensuring fail-closed rejection on group SHA |
| 4 | **Head change re-validation** | If a queued PR's head SHA changes, prior approval and CI are not reused; candidate must re-verify all checks | Contract tests on review-gate and CI triggers for renewed head SHAs |
| 5 | **Failure isolation under ALLGREEN** | When a candidate PR in a batch fails CI, `ALLGREEN` isolates the failed PR and re-queues non-failing PRs | Policy verification ensuring `grouping_strategy == "ALLGREEN"` is strictly retained across all policy files |
| 6 | **Actual required checks on group SHA** | `orchestrator`, `product`, `product-e2e-gate`, `task-review-gate` all execute and report against the merge group commit | CI workflow trigger contract tests (`ci.yml`, `merge-queue-review-gate.yml`) matching `merge_group` event requirements |

### 3.3 Clarification on Latency and Wait Bounds

- `min_entries_to_merge_wait_minutes` bounds the accumulation period waiting for companion PRs to reach minimum batch size.
- **Timeline semantics**: Per official GitHub merge queue semantics, the accumulation timer begins upon the PR's queue entry and runs concurrently with speculative `merge_group` CI builds.
- When CI execution (~20.6 min median) exceeds the accumulation wait ceiling (e.g. 10 min), the wait timer elapses during CI execution without adding extra elapsed hold time after checks complete.
- If checks complete faster than the wait timer (for shorter check runs) and no companion PR arrives, the candidate is held only for the remaining difference (`wait_minutes - CI_duration`) before merging solo.

### 3.4 B-Stage Forbidden

- Do not apply configuration changes directly to the live GitHub repository (reserved for C-stage).
- Do not change `grouping_strategy` to `HEADGREEN` or `NONE`.
- Do not remove or weaken any required status check context (`orchestrator`, `product`, `product-e2e-gate`, `task-review-gate`).
- Do not alter `merge_method` from `MERGE`.

## 4. C-Stage Entry Conditions & Live Activation (WP-35C)

Live activation (WP-35C) proceeds only after WP-35B PR is merged and formal operator authorization is received.

### 4.1 C-Stage Scope & Execution

```bash
# 1. Apply reviewed policy to live repository
python3 delivery_toolchain/github/apply_branch_protection.py

# 2. Perform post-activation readback verification
gh api graphql -f query='{repository(owner:"alfloop-dev",name:"odayplus"){mergeQueue(branch:"dev"){id configuration{mergeMethod mergingStrategy checkResponseTimeout maximumEntriesToBuild maximumEntriesToMerge minimumEntriesToMerge minimumEntriesToMergeWaitTime}}}}'
gh api repos/alfloop-dev/odayplus/branches/dev/protection/required_status_checks --jq '{strict,contexts}'
```

### 4.2 C-Stage Live Behavioral Acceptance Matrix

A configuration assertion or generic 7-day monitoring alone cannot close the behavioral requirements.
During C-stage live execution, explicit evidence receipts MUST be collected for the following 6 behavioral scenarios:

| # | Live Behavioral Scenario | Expected Live Result | Mandatory Receipt Fields Required in C-Stage Evidence |
|---|---|---|---|
| 1 | **Multiple approved PRs co-merging** | 2+ approved PRs present in queue are batched and merged in a single merge commit on `dev` | • PR numbers (e.g. #PR-A, #PR-B)<br>• Exact reviewed head SHAs & resulting group merge commit SHA on `dev`<br>• UTC outcome timestamps<br>• Original GitHub Actions `merge_group` run URLs |
| 2 | **Solo PR bounded wait timeout** | A solo PR waiting with no companion PRs merges solo immediately upon `min_entries_to_merge_wait_minutes` expiration (or upon CI completion if CI > wait) | • PR number & head SHA<br>• Queue entry UTC, wait timer expiration UTC, merge UTC<br>• Computed hold duration vs CI duration<br>• Resulting `dev` merge commit SHA |
| 3 | **Unapproved / failing-check rejection** | A PR lacking `task-review-gate` or failing required checks is rejected and ejected from the merge group without landing on `dev` | • PR number & attempted head SHA<br>• Rejected group SHA<br>• Check failure or review gate ejection status URL & UTC<br>• Confirmation `dev` remained untouched |
| 4 | **Changed-head renewed review & CI** | When a queued PR's head SHA changes, prior approval/CI is invalidated; candidate must obtain new reviewer approval and fresh green CI | • PR number, old head SHA, new head SHA<br>• Invalidation receipt & renewed `task-review-gate` status URL<br>• New `merge_group` CI run URL & UTC outcome |
| 5 | **Failed-candidate ejection & unaffected PR rebuild** | In a multi-PR batch where one candidate fails CI, `ALLGREEN` isolates the failing PR, ejects it, and rebuilds/merges remaining valid PRs | • Failed candidate PR number & failure run URL<br>• Ejected group SHA vs re-queued group SHA<br>• Surviving candidate PR numbers & subsequent successful merge commit SHA on `dev` |
| 6 | **All 4 required checks executing on group SHA** | `orchestrator`, `product`, `product-e2e-gate`, and `task-review-gate` all report and pass on the exact `merge_group` SHA | • Merge group SHA<br>• Check-run IDs, URLs, and UTC timestamps for all 3 Actions contexts<br>• Commit status ID, URL, description for `task-review-gate` |

> [!NOTE]
> This is a design and behavioral specification for C-stage live verification; no C-stage live tests are executed during this A-stage task.

### 4.3 Full Baseline Rollback Procedure

If queue stalls, speculative build timeouts, or unexpected candidate ejections occur, rollback immediately to the full reviewed baseline:

```bash
# Primary rollback: restore baseline policy.json and apply
python3 delivery_toolchain/github/apply_branch_protection.py
```

Manual REST API alternative:

```bash
RULESET_ID=$(gh api repos/alfloop-dev/odayplus/rulesets --jq '.[] | select(.name=="dev-merge-queue") | .id')
gh api -X PUT "repos/alfloop-dev/odayplus/rulesets/$RULESET_ID" --input - <<'JSON'
{
  "name": "dev-merge-queue",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/dev"], "exclude": [] } },
  "rules": [{
    "type": "merge_queue",
    "parameters": {
      "merge_method": "MERGE",
      "grouping_strategy": "ALLGREEN",
      "min_entries_to_merge": 1,
      "min_entries_to_merge_wait_minutes": 5,
      "max_entries_to_merge": 5,
      "max_entries_to_build": 5,
      "check_response_timeout_minutes": 60
    }
  }]
}
JSON
```

Or full queue disable (fallback to direct auto-merge):

```bash
python3 delivery_toolchain/github/apply_branch_protection.py --disable-merge-queue
```

## 5. Summary of Stage Boundaries

- **A-stage (This Task)**: Formalize requirement, deliver read-only observations, propose options with tradeoffs, define verification matrix, C-stage behavioral scenarios, and rollback procedure. **No live changes or tests executed.**
- **H08 Decision**: Human selects batch parameters.
- **B-stage (Engineering)**: PR modifying `policy.json`, runbook, and automated verification tests.
- **C-stage (Live Activation)**: Apply approved ruleset, verify readback, collect behavioral acceptance receipts, monitor live telemetry.
