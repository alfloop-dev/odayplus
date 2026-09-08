# Configuration Options — Merge Queue Batch Parameters

- **Task**: ODP-MERGE-QUEUE-BATCH-DESIGN-001
- **Date**: 2026-09-08
- **Author**: Antigravity3
- **Reviewer**: Codex2
- **Decision reference**: D21 in [ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md](../../../../.orchestrator/source-doc-cache/alfloop-dev__odayplus/be04fe7954d3414f024901e034caacd95ae81538/docs/plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md)

## 1. Platform Support Reference

GitHub merge queue configuration is managed via repository rulesets (not
classic branch protection). The `alfloop-dev/odayplus` `dev` branch uses
a ruleset named `dev-merge-queue`, declared in `.github/branch-protection/policy.json`
and applied by `delivery_toolchain/github/apply_branch_protection.py`.

### 1.1 Official Documentation References (Inspected 2026-09-08)

- GitHub REST API Rulesets: [https://docs.github.com/en/rest/repos/rules?apiVersion=2022-11-28#create-a-repository-ruleset](https://docs.github.com/en/rest/repos/rules?apiVersion=2022-11-28#create-a-repository-ruleset)
- GitHub Merge Queue Management: [https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue)

### 1.2 Available GitHub Ruleset Parameters

| Parameter | Type | Valid Values | Description |
|---|---|---|---|
| `merge_method` | enum | `MERGE`, `SQUASH`, `REBASE` | Commit strategy for merged PRs; `MERGE` is required to preserve trailers |
| `grouping_strategy` | enum | `ALLGREEN`, `HEADGREEN` | Whether each entry's own merge commit must pass checks (`ALLGREEN`) or only the combined group head (`HEADGREEN`) |
| `min_entries_to_merge` | integer | 1–100 | Minimum PRs to accumulate before merging group |
| `min_entries_to_merge_wait_minutes` | integer | 0–360 | Maximum time to wait for `min_entries_to_merge` to accumulate |
| `max_entries_to_merge` | integer | 1–100 | Maximum PRs in a single merge group |
| `max_entries_to_build` | integer | 1–100 | Maximum concurrent speculative CI runs |
| `check_response_timeout_minutes` | integer | 5–360 | Time to wait for required checks before ejecting a candidate |

### 1.3 Mandatory Retention of `ALLGREEN` and Rejection of `HEADGREEN`

- **Why `HEADGREEN` is REJECTED**: Under `HEADGREEN`, GitHub only requires the combined head commit of the merge group to pass CI; intermediate PRs that fail tests on their own can merge if later commits mask or fix the failure. Furthermore, our repo's review validation workflow (`.github/workflows/merge-queue-review-gate.yml`) verifies and stamps the `task-review-gate` status on the specific PR named by each `merge_group` event. Using `HEADGREEN` would weaken per-entry verification and conflict with per-PR review gating. Under the acceptance criterion "不降低任何 required check/review gate", `HEADGREEN` is strictly rejected. All viable options retain `grouping_strategy: ALLGREEN`.
- **Note on `NONE`**: `NONE` is not a supported GitHub REST value (GitHub supports only `ALLGREEN` and `HEADGREEN`).

### 1.4 Distinction: Merge Batching vs Build Concurrency vs Downstream Savings

- **Merge Batching** (`min_entries_to_merge`, `max_entries_to_merge`): Controls how many PRs land together in the base branch merge commit on `dev`. Note that `min_entries_to_merge = 1` does **not** prevent multiple concurrently ready PRs from merging together when multiple PRs are ready simultaneously.
- **Build Concurrency** (`max_entries_to_build`): Controls how many speculative builds run concurrently in CI.
- **CI Savings Reality**: Under `ALLGREEN`, GitHub runs speculative CI builds on each entry in the queue; batching does **not** eliminate intermediate `merge_group` CI runs. Real efficiency gains come downstream: fewer merge commits to rebase against, fewer post-merge continuous deployment runs, and eliminated `dev` chase races.

### 1.5 Current Reviewed Baseline Configuration (Readback 2026-09-08)

| Parameter | Baseline Value | Source |
|---|---|---|
| `merge_method` | `MERGE` | GraphQL readback & policy.json |
| `grouping_strategy` | `ALLGREEN` | GraphQL readback & policy.json |
| `min_entries_to_merge` | 1 | GraphQL readback & policy.json |
| `min_entries_to_merge_wait_minutes` | 5 | GraphQL readback & policy.json |
| `max_entries_to_merge` | 5 | GraphQL readback & policy.json |
| `max_entries_to_build` | 5 | GraphQL readback & policy.json |
| `check_response_timeout_minutes` | 60 | GraphQL readback & policy.json |
| `strict` (on `dev`) | `false` | REST branch protection readback |
| `required_status_checks` | `["orchestrator", "product", "product-e2e-gate", "task-review-gate"]` | REST branch protection readback |

See [queue-observations.json](queue-observations.json) for exact commands, exit codes, and timestamps.

## 2. Batch Configuration Options for H08 Review

Three options are presented for human review (H08). All preserve the required
`ALLGREEN` strategy, all 4 required status check contexts, reviewer gates, and the `MERGE` commit method.

### Option A: Baseline / Low-Latency (Current Configuration)

| Parameter | Proposed | Rationale |
|---|---|---|
| `min_entries_to_merge` | **1** | Merge as soon as a single PR is green; do not hold for companion PRs |
| `min_entries_to_merge_wait_minutes` | **5** | Inert when `min_entries_to_merge = 1` |
| `grouping_strategy` | `ALLGREEN` | Per-entry verification and review gate preserved |
| `max_entries_to_merge` | 5 | Allows up to 5 concurrently ready PRs to merge together |
| `max_entries_to_build` | 5 | Up to 5 speculative builds in parallel |
| `check_response_timeout_minutes` | 60 | Headroom over median CI duration (~20.6 min) |

**Tradeoffs**:
- Zero artificial delay for solo PRs (no waiting for companion PRs)
- Multiple concurrently green PRs can still merge together up to `max_entries_to_merge = 5`
- Does not enforce batch accumulation during low-traffic periods

### Option B: Conservative Batch with Bounded Wait (Recommended for Initial Trial)

| Parameter | Proposed | Rationale |
|---|---|---|
| `min_entries_to_merge` | **2** | Require 2 PRs to form a batch when available |
| `min_entries_to_merge_wait_minutes` | **10** | Bounds solo PR wait time to 10 minutes before merging solo |
| `grouping_strategy` | `ALLGREEN` | Full per-entry failure isolation and review gate preserved |
| `max_entries_to_merge` | 5 | Existing cap |
| `max_entries_to_build` | 5 | Existing build concurrency |
| `check_response_timeout_minutes` | 60 | Unchanged |

**Tradeoffs**:
- When 2 PRs are queued concurrently, they merge in a single batch on `dev`
- A solo PR waits up to 10 minutes for a companion before the timer expires and it merges solo
- Under `ALLGREEN`, failure of one PR is isolated without affecting the other PR
- Total enqueue-to-merge latency for solo PR = accumulation wait (up to 10 min) + CI duration (~20.6 min)

### Option C: Higher Burst Batching

| Parameter | Proposed | Rationale |
|---|---|---|
| `min_entries_to_merge` | **3** | Require 3 PRs for immediate batch merge |
| `min_entries_to_merge_wait_minutes` | **15** | 15-minute accumulation window for burst periods |
| `grouping_strategy` | `ALLGREEN` | Full per-entry failure isolation and review gate preserved |
| `max_entries_to_merge` | **8** | Higher cap for high-concurrency dispatch bursts |
| `max_entries_to_build` | 5 | Preserved build concurrency |
| `check_response_timeout_minutes` | 60 | Unchanged |

**Tradeoffs**:
- Targeted at high-activity fleet dispatches where 3+ PRs are submitted within 15 minutes
- A solo PR waits up to 15 minutes before falling back to solo merge
- Best suited after continuous queue depth telemetry confirms frequent queue congestion

## 3. Comparison Matrix

| Criterion | Option A (Baseline) | Option B (Conservative Trial) | Option C (Burst Batching) |
|---|---|---|---|
| `min_entries_to_merge` | 1 | 2 | 3 |
| Max accumulation delay for solo PR | 0 min | 10 min | 15 min |
| `grouping_strategy` | `ALLGREEN` | `ALLGREEN` | `ALLGREEN` |
| Failure isolation | Full (per-entry) | Full (per-entry) | Full (per-entry) |
| Review gate enforcement | Strict per-PR | Strict per-PR | Strict per-PR |
| Speculative CI runs per batch | All entries tested | All entries tested | All entries tested |
| Downstream merge commit reduction | None (unless concurrent) | Moderate (during dispatches) | High (during bursts) |
| Rollback complexity | N/A (current baseline) | One-edit policy.json change | One-edit policy.json change |
| Recommended context | Low-throughput baseline | Safe first batching trial | High-concurrency fleet |

## 4. Full Baseline Rollback Procedure

Any configuration change under Options B or C can be rolled back to the full reviewed baseline configuration.

### 4.1 Reviewed Baseline Policy

The exact reviewed baseline state to restore in `.github/branch-protection/policy.json`:

```json
{
  "required_status_checks": [
    "orchestrator",
    "product",
    "product-e2e-gate",
    "task-review-gate"
  ],
  "enforce_admins": true,
  "branches": {
    "dev": {
      "strict": false,
      "merge_queue": {
        "ruleset_name": "dev-merge-queue",
        "merge_method": "MERGE",
        "grouping_strategy": "ALLGREEN",
        "max_entries_to_build": 5,
        "max_entries_to_merge": 5,
        "min_entries_to_merge": 1,
        "min_entries_to_merge_wait_minutes": 5,
        "check_response_timeout_minutes": 60
      }
    }
  }
}
```

### 4.2 Concrete Rollback Triggers

Rollback to the baseline configuration SHALL be initiated if any of the following occur:

1. **Queue Stalls / Excessive Wait**: Solo PRs experience accumulation delays exceeding the configured wait ceiling without triggering fallback merges.
2. **Speculative Build Starvation**: High concurrency or timeout (>60 min) causes merge group candidate ejections.
3. **Ejection Anomalies**: Legitimate, approved PRs are unexpectedly ejected due to batch composition issues.
4. **Required Check Disruption**: Any of the 4 required checks (`orchestrator`, `product`, `product-e2e-gate`, `task-review-gate`) fails to report or evaluate on `merge_group` events.
5. **Human/Ops Request**: Explicit operator instruction to revert to single-entry minimum.

### 4.3 Execution Command Paths

#### Primary Path (Toolchain Application)

```bash
# 1. Ensure policy.json has baseline values (as in §4.1)
# 2. Apply branch protection and ruleset
python3 delivery_toolchain/github/apply_branch_protection.py
```

#### Manual REST API Path (Direct Ruleset Update)

```bash
RULESET_ID=$(gh api repos/alfloop-dev/odayplus/rulesets --jq '.[] | select(.name=="dev-merge-queue") | .id')

gh api -X PUT "repos/alfloop-dev/odayplus/rulesets/$RULESET_ID" --input - <<'JSON'
{
  "name": "dev-merge-queue",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "include": ["refs/heads/dev"],
      "exclude": []
    }
  },
  "rules": [
    {
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
    }
  ]
}
JSON
```

#### Full Queue Disabling (Fallback to Direct Auto-Merge)

```bash
# Deletes the dev-merge-queue ruleset and re-applies classic branch protection with strict=true
python3 delivery_toolchain/github/apply_branch_protection.py --disable-merge-queue
```

### 4.4 Post-Rollback Readback Verification

```bash
# Verify mergeQueue configuration matches baseline
gh api graphql -f query='{repository(owner:"alfloop-dev",name:"odayplus"){mergeQueue(branch:"dev"){id configuration{mergeMethod mergingStrategy checkResponseTimeout maximumEntriesToBuild maximumEntriesToMerge minimumEntriesToMerge minimumEntriesToMergeWaitTime}}}}'

# Verify required status checks and strict flag
gh api repos/alfloop-dev/odayplus/branches/dev/protection/required_status_checks --jq '{strict,contexts}'
```

Baseline readback verification MUST confirm:
- `mergeMethod == "MERGE"`
- `mergingStrategy == "ALLGREEN"`
- `minimumEntriesToMerge == 1`
- `minimumEntriesToMergeWaitTime == 300` (5 minutes)
- `maximumEntriesToMerge == 5`
- `maximumEntriesToBuild == 5`
- `checkResponseTimeout == 3600` (60 minutes)
- `strict == false`
- `contexts == ["orchestrator", "product", "product-e2e-gate", "task-review-gate"]`

## 5. What This Document Does NOT Do

- Does not activate any live configuration change
- Does not select a final option — that is reserved for H08 human decision
- Does not modify `.github/branch-protection/policy.json` in this A-stage task
- Does not weaken any required check or review gate
- Does not execute live test suites or create test PRs
