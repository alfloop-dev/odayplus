# Configuration Options — Merge Queue Batch Parameters

- **Task**: ODP-MERGE-QUEUE-BATCH-DESIGN-001
- **Date**: 2026-09-08
- **Author**: Antigravity3

## 1. Platform Support Reference

GitHub merge queue configuration is managed via repository rulesets (not
classic branch protection). The `alfloop-dev/odayplus` `dev` branch uses
a ruleset named `dev-merge-queue`, applied by
`delivery_toolchain/github/apply_branch_protection.py`.

### 1.1 Available Parameters (GitHub, as of 2026-09-08)

| Parameter | Type | Description |
|---|---|---|
| `merge_method` | enum: MERGE, SQUASH, REBASE | Commit strategy for merged PRs |
| `grouping_strategy` | enum: ALLGREEN, NONE | Whether each entry's own merge commit must be green (ALLGREEN) or only the group head (NONE) |
| `min_entries_to_merge` | integer (1–100) | Minimum PRs to accumulate before the queue merges |
| `min_entries_to_merge_wait_minutes` | integer (0–360) | Maximum time to wait for `min_entries_to_merge` to be reached; if timer expires, merge with whatever is ready |
| `max_entries_to_merge` | integer (1–100) | Maximum PRs in a single merge group |
| `max_entries_to_build` | integer (1–100) | Maximum concurrent speculative CI runs |
| `check_response_timeout_minutes` | integer (5–360) | Time to wait for required checks before ejecting a PR |

### 1.2 Current Configuration (readback 2026-09-08)

| Parameter | Current value |
|---|---|
| `merge_method` | MERGE |
| `grouping_strategy` | ALLGREEN |
| `min_entries_to_merge` | 1 |
| `min_entries_to_merge_wait_minutes` | 5 |
| `max_entries_to_merge` | 5 |
| `max_entries_to_build` | 5 |
| `check_response_timeout_minutes` | 60 |

Source: GraphQL readback and `.github/branch-protection/policy.json`. See
[queue-observations.json](queue-observations.json) for exact commands and
timestamps.

## 2. Batch Configuration Options

Three options are presented for H08 human review. All preserve required
checks, review gates, and the MERGE commit method. None reduce any existing
protection.

### Option A: Conservative Batch (Recommended for initial activation)

| Parameter | Proposed | Rationale |
|---|---|---|
| `min_entries_to_merge` | **2** | Start batching when 2+ PRs are queued |
| `min_entries_to_merge_wait_minutes` | **10** | At median CI of ~20 min, a 10-min wait provides a reasonable window to accumulate a second PR without significantly delaying a solo PR |
| `grouping_strategy` | ALLGREEN (unchanged) | Each PR's merge commit verified independently; a failing PR is ejected without bisecting |
| `max_entries_to_merge` | 5 (unchanged) | Existing cap is reasonable |
| `max_entries_to_build` | 5 (unchanged) | Matching max_entries_to_merge |

**Tradeoffs**:
- A solo PR waits up to 10 minutes for a companion before merging alone
- At current throughput (~0.5 merges/hour), batches of 2 will form infrequently
- ALLGREEN means each PR in the batch runs its own CI (no CI savings from batching, but failure isolation is strongest)

### Option B: Moderate Batch with CI Savings

| Parameter | Proposed | Rationale |
|---|---|---|
| `min_entries_to_merge` | **2** | Same as Option A |
| `min_entries_to_merge_wait_minutes` | **15** | Longer window increases batch probability at current throughput |
| `grouping_strategy` | **NONE** | Only the group head is tested; individual entries are not independently verified. Saves (N-1) CI runs per batch |
| `max_entries_to_merge` | 5 (unchanged) | Existing cap |
| `max_entries_to_build` | **3** | Reduced speculative builds since NONE strategy needs fewer |

**Tradeoffs**:
- Solo PR waits up to 15 minutes
- NONE strategy saves CI time but if the batch fails, all entries are ejected and must be re-queued individually (no automatic bisection)
- Risk: a single bad PR poisons an entire batch, requiring all to re-run
- At low throughput this may increase total merge time due to re-queuing

### Option C: Aggressive Batch for High-Throughput Periods

| Parameter | Proposed | Rationale |
|---|---|---|
| `min_entries_to_merge` | **3** | Require 3 PRs before batching |
| `min_entries_to_merge_wait_minutes` | **20** | Longer window to fill the batch |
| `grouping_strategy` | ALLGREEN (unchanged) | Preserve failure isolation |
| `max_entries_to_merge` | **8** | Higher cap for burst periods |
| `max_entries_to_build` | **8** | Match the merge cap |

**Tradeoffs**:
- Solo PR waits up to 20 minutes before merging alone
- At current throughput, most PRs will time out and merge solo anyway
- Only beneficial during sustained high-throughput bursts
- Higher `max_entries` increases blast radius if multiple PRs interact badly

## 3. Comparison Matrix

| Criterion | Option A | Option B | Option C |
|---|---|---|---|
| Max solo PR delay | 10 min | 15 min | 20 min |
| Failure isolation | Strong (ALLGREEN) | Weak (NONE) | Strong (ALLGREEN) |
| CI savings per batch | None (each verified) | (N-1) runs saved | None (each verified) |
| Batch probability at current throughput | Low | Moderate | Very low |
| Rollback complexity | One value change | Two value changes | Two value changes |
| Recommended for | Current throughput, safe first step | Higher throughput with CI cost pressure | Future high-concurrency fleet |

## 4. Rollback Procedure

All options can be rolled back to the current single-PR behavior:

```json
{
  "min_entries_to_merge": 1,
  "min_entries_to_merge_wait_minutes": 5
}
```

The rollback is a one-edit change in `.github/branch-protection/policy.json`
applied via `delivery_toolchain/github/apply_branch_protection.py`. See
`docs/runbooks/dev-merge-queue.md` for the full rollback procedure.

## 5. What This Document Does NOT Do

- It does not activate any configuration change
- It does not select a final option — that is H08 human decision
- It does not modify `policy.json` or any live configuration
- It does not weaken any required check or review gate
- The numerical values are proposals from measurement, not enacted policy
