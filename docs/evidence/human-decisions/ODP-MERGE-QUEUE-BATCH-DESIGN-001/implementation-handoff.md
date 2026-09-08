# Implementation Handoff — Merge Queue Batch Engineering (WP-35B/C)

- **Task**: ODP-MERGE-QUEUE-BATCH-DESIGN-001 (A-stage)
- **Date**: 2026-09-08
- **Author**: Antigravity3
- **Target**: WP-35B/C implementation task

## 1. Purpose

This document specifies the entry conditions, scope, and verification
requirements for the B-stage (engineering) and C-stage (live activation)
of the merge queue batch requirement.

## 2. A-Stage Deliverables (This Task)

| Deliverable | Status | Location |
|---|---|---|
| Queue observations with full provenance | ✅ Complete | [queue-observations.json](queue-observations.json) |
| Batch requirement specification | ✅ Complete | [batch-requirement.md](batch-requirement.md) |
| Configuration options with tradeoffs | ✅ Complete | [configuration-options.md](configuration-options.md) |
| Governance handoff for WP-90 | ✅ Complete | [governance-handoff.md](governance-handoff.md) |
| This implementation handoff | ✅ Complete | this file |

## 3. B-Stage Entry Conditions

The B-stage implementation task may start when **all** of the following are met:

1. **H08 parameter decision**: Human has reviewed the configuration options
   and selected specific values for `min_entries_to_merge` and
   `min_entries_to_merge_wait_minutes`, or has delegated the choice to a
   specific range

2. **Sustained queue depth justification**: Either:
   - Evidence that queue depth regularly exceeds 1, making batching
     valuable, OR
   - Human explicit instruction to enable batching proactively regardless
     of current depth

3. **This A-stage task merged**: This evidence package is available on `dev`

### 3.1 B-Stage Scope

- Modify `.github/branch-protection/policy.json` with the approved parameter
  values
- Update `docs/runbooks/dev-merge-queue.md` to document the batch
  configuration and its rationale
- Ensure `delivery_toolchain/github/apply_branch_protection.py` correctly
  applies the new values (it already supports all parameters)
- Verify that `merge_group` CI triggers work correctly with batch-size > 1
  by reviewing existing workflow configuration

### 3.2 B-Stage Verification

```bash
# 1. Policy JSON is valid and contains the approved values
python3 -c "import json; p=json.load(open('.github/branch-protection/policy.json')); assert p['branches']['dev']['merge_queue']['min_entries_to_merge'] > 1"

# 2. Runbook is updated
grep -q 'min_entries_to_merge' docs/runbooks/dev-merge-queue.md

# 3. git diff --check passes
git diff --check

# 4. apply_branch_protection.py dry-run (if supported)
# python3 delivery_toolchain/github/apply_branch_protection.py --verify-only
```

### 3.3 B-Stage Forbidden

- Do not apply the configuration change to the live repository (that is C-stage)
- Do not remove or weaken any required check
- Do not change `merge_method` from MERGE
- Do not remove the `task-review-gate` check
- Do not use `--ignore-vuln`, `--force`, or environment variable bypasses

## 4. C-Stage Entry Conditions

The C-stage live activation may proceed when **all** of the following are met:

1. **B-stage PR merged**: The policy.json change is on `dev`
2. **Human/Ops approval**: Explicit authorization to apply the configuration
   to the live repository
3. **Rollback plan confirmed**: The rollback procedure in the runbook is
   reviewed and ready

### 4.1 C-Stage Scope

```bash
# Apply the configuration
python3 delivery_toolchain/github/apply_branch_protection.py

# Readback verification
gh api graphql -f query='{repository(owner:"alfloop-dev",name:"odayplus"){mergeQueue(branch:"dev"){id configuration{mergeMethod mergingStrategy checkResponseTimeout maximumEntriesToBuild maximumEntriesToMerge minimumEntriesToMerge minimumEntriesToMergeWaitTime}}}}'

# Branch protection readback
gh api repos/alfloop-dev/odayplus/branches/dev/protection/required_status_checks --jq '{strict,contexts}'
```

### 4.2 C-Stage Verification

- Pre/post readback comparison confirms only the intended parameters changed
- All 4 required checks still report on `merge_group` events
- `strict` remains `false` on `dev`
- Queue processes at least one PR successfully after activation
- Observation period (suggested: 7 days) to confirm batch behavior

### 4.3 C-Stage Rollback

Per `docs/runbooks/dev-merge-queue.md`:

```bash
# Revert to single-PR behavior
# In policy.json: set min_entries_to_merge=1, min_entries_to_merge_wait_minutes=5
python3 delivery_toolchain/github/apply_branch_protection.py
```

Or manual equivalent:

```bash
gh api graphql -f query='mutation { updateMergeQueueConfig(...) { ... } }'
```

## 5. Dependencies

| Dependency | Status | Notes |
|---|---|---|
| ODP-HUMAN-DECISIONS-EXECUTION-PLAN-001 | ✅ done | Provides D21 decision |
| ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001 | ✅ done | Provides prior audit evidence |
| ODP-ORCH-MERGE-QUEUE-ACTIVATION-001 | ✅ done | Original queue activation |
| H08 (human parameter decision) | ❌ pending | Required before B-stage |
| WP-90 governance update | ❌ pending | Parallel; not blocking B-stage |

## 6. Risk Assessment

| Risk | Mitigation |
|---|---|
| Batch includes a bad PR that poisons the group | ALLGREEN strategy (recommended Option A) verifies each PR independently |
| Wait time delays legitimate PRs | Bounded by `min_entries_to_merge_wait_minutes`; worst case = configured minutes |
| Configuration drift between policy.json and live | Readback verification in `apply_branch_protection.py` |
| Rollback needed urgently | One-value change in policy.json + reapply; or manual `gh api` commands |
| CI not reporting on `merge_group` event | Existing `ci.yml` and `merge-queue-review-gate.yml` already have triggers; no change needed |
