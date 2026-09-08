# Implementation Handoff — Merge Queue Batch Engineering (WP-35B/C)

- **Task**: ODP-MERGE-QUEUE-BATCH-DESIGN-001 (A-stage)
- **Date**: 2026-09-08
- **Author**: Antigravity3
- **Reviewer**: Codex2
- **Target**: WP-35B/C implementation task
- **Decision reference**: D21 in [ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md](../../../../.orchestrator/source-doc-cache/alfloop-dev__odayplus/be04fe7954d3414f024901e034caacd95ae81538/docs/plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md)

## 1. Purpose

This document specifies the entry conditions, scope, automated verification matrix,
and live activation / rollback procedures for the B-stage (engineering) and
C-stage (live activation) of the merge queue batch requirement.

## 2. A-Stage Deliverables (This Task)

| Deliverable | Status | Location | Description |
|---|---|---|---|
| Queue observations & telemetry | ✅ Complete | [queue-observations.json](queue-observations.json) | Read-only measurements of CI duration, merge interval, configuration readback, and per-SHA required checks evidence |
| Batch requirement specification | ✅ Complete | [batch-requirement.md](batch-requirement.md) | Formal requirement specification aligned with D21 and official GitHub ruleset parameters |
| Configuration options with tradeoffs | ✅ Complete | [configuration-options.md](configuration-options.md) | Three comparable options for H08 human review with full baseline rollback procedure |
| Governance handoff for WP-90 | ✅ Complete | [governance-handoff.md](governance-handoff.md) | D21 `IMPLEMENTATION_READY` disposition update fragment for shared registry |
| Implementation handoff | ✅ Complete | this file | Acceptance criteria, verification matrix, entry conditions, and operational procedures for WP-35B/C |

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

The B-stage deliverable must satisfy and verify the following 6 core acceptance scenarios:

| # | Scenario | Expected Result | Verification / Evidence Method |
|---|---|---|---|
| 1 | **Multiple qualified PRs form batch** | When 2+ PRs with passing checks and approvals enter queue, queue forms a combined batch up to `max_entries_to_merge` | Policy assertions and workflow simulation tests proving batch formation rules |
| 2 | **Solo-PR bounded wait timeout** | When a solo PR is queued without companion PRs, it is released to merge alone after `min_entries_to_merge_wait_minutes` expires | Bounded wait timeout contract verification in policy and runbook documentation |
| 3 | **Unapproved / failing PR exclusion** | PR lacking `task-review-gate` or failing required checks cannot be admitted to or merged in a green batch | `merge-queue-review-gate.yml` assertion tests ensuring fail-closed rejection on group SHA |
| 4 | **Head change re-validation** | If a queued PR's head SHA changes, prior approval and CI are not reused; candidate must re-verify all checks | Contract tests on review-gate and CI triggers for renewed head SHAs |
| 5 | **Failure isolation under ALLGREEN** | When a candidate PR in a batch fails CI, `ALLGREEN` isolates the failed PR and re-queues non-failing PRs | Verification that `grouping_strategy == "ALLGREEN"` is strictly retained across all policy files |
| 6 | **Actual required checks on group SHA** | `orchestrator`, `product`, `product-e2e-gate`, `task-review-gate` all execute and report against the merge group commit | CI workflow triggers (`ci.yml`, `merge-queue-review-gate.yml`) match `merge_group` event requirements |

### 3.3 Clarification on Latency and Wait Bounds

- `min_entries_to_merge_wait_minutes` bounds **only the accumulation period** waiting for companion PRs to reach the minimum batch size.
- **Total enqueue-to-merge latency** = Accumulation wait (0 to `wait_minutes`) + CI check execution duration (~15–25 min) + queue scheduling latency.

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

### 4.2 C-Stage Verification Criteria

- Remote GraphQL readback matches approved `policy.json` configuration exactly.
- Branch protection confirms `strict == false` and all 4 contexts present.
- All 4 required checks report and pass on subsequent `merge_group` workflow runs.
- Continuous queue telemetry and observation window (7 days) to monitor queue occupancy and batch merges.

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

- **A-stage (This Task)**: Formalize requirement, deliver read-only observations, propose options with tradeoffs, define verification matrix and rollback procedure. **No live changes or tests executed.**
- **H08 Decision**: Human selects batch parameters.
- **B-stage (Engineering)**: PR modifying `policy.json`, runbook, and automated verification tests.
- **C-stage (Live Activation)**: Apply approved ruleset, verify readback, monitor live telemetry.
