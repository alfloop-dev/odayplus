# Batch Merge Queue — Formal Requirement Specification

- **Task**: ODP-MERGE-QUEUE-BATCH-DESIGN-001
- **Decision reference**: D21 in [ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md](https://github.com/alfloop-dev/odayplus/blob/be04fe7954d3414f024901e034caacd95ae81538/docs/plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md)
- **Prior disposition audit**: [ODP_MERGE_QUEUE_DISPOSITION_2026-09-03.md](https://github.com/alfloop-dev/odayplus/blob/04e1572f802a54c2646ba678fe2975226dfbd7c4/docs/evidence/ODP_MERGE_QUEUE_DISPOSITION_2026-09-03.md) (verdict: `BLOCKED_BY_EVIDENCE` — no authoritative "not to implement" ruling existed)
- **User decision**: B — retain as a formal implementation requirement
- **Date of this document**: 2026-09-08

## 1. Background and Decision History

### 1.1 The original "decided not to batch" was not a decision

The merge queue on `alfloop-dev/odayplus` `dev` was activated on 2026-08-19
(task `ODP-ORCH-MERGE-QUEUE-ACTIVATION-001`, commit `944ad12f`). The initial
configuration set `min_entries_to_merge = 1`, meaning a ready PR is merged
without being held for additional companion PRs. Note that `min_entries_to_merge = 1`
does **not** prevent multiple concurrently ready PRs from merging together in
the same group when multiple PRs are ready simultaneously.

`docs/plans/ODP_OPEN_DECISIONS_2026-09-03.md` item 19 recorded this as
"已裁決不做" (already decided not to implement). The subsequent audit
(`ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001`) found this was not an authoritative
ruling but a configuration default set by an AI agent, with no human decider,
date, scope, expiry, or reopen trigger. The audit correctly blocked the item
as `BLOCKED_BY_EVIDENCE`.

### 1.2 User direction: retain as formal requirement

In the human decisions session recorded in the execution plan (D21), the user
selected option B: retain merge queue batching as a formal implementation
requirement. This means:

- The `min_entries_to_merge = 1` default is a **baseline starting point**, not a permanent no-batch ruling
- Batch parameters must be determined by measurement and human tradeoff review (H08), not guessed
- Implementation follows the phased lifecycle: A-stage (design & measurement) → H08 human review → B-stage (engineering PR) → C-stage (live activation)

### 1.3 What this requirement is NOT

- It is **not** authorization to change `min_entries_to_merge` or any other queue
  parameter without reviewed evidence and human approval
- It is **not** a governance-level `DECIDED` disposition (those require human
  decider, date, and scope per `ODP_REQUIREMENT_DISPOSITIONS.md`). D21 selected
  the implementation direction; its requirement disposition is recorded as `OPEN`
  pending H08 parameter confirmation and B-stage task assignment, transitioning to
  `IMPLEMENTATION_READY` upon meeting B-stage entry conditions
- It is **not** deleting, bypassing, or weakening any required check or review gate
- It is **not** substituting `grouping_strategy: HEADGREEN` (which would weaken per-entry check enforcement)

## 2. Requirement Statement

**The `dev` branch merge queue SHALL support batching multiple approved PRs
into a merge group when queue depth warrants it, subject to bounded
wait time, failure isolation under ALLGREEN strategy, and all existing required checks.**

### 2.1 Mandatory Properties (from WP-35B/C acceptance)

| Property | Requirement |
|---|---|
| Batch formation | Multiple qualifying PRs can form a batch when queue depth meets or exceeds configured minimum |
| Bounded wait | A ready PR is never held indefinitely waiting for additional PRs; a configurable maximum wait time (`min_entries_to_merge_wait_minutes`) bounds the accumulation period |
| Admission control | Only PRs with passing required branch protection checks AND reviewer approval (`task-review-gate`) may enter a batch; unapproved or failing PRs are excluded prior to queue admission (generating no merge group SHA) or ejected upon group CI check failure |
| Head change re-validation | If a PR's head changes after admission, the merge group must re-run CI against the updated composition and re-verify review status |
| Failure isolation | When a batch fails CI under `ALLGREEN`, the queue isolates the failing PR and re-queues the remaining PRs for a clean attempt |
| Required checks preserved | `orchestrator`, `product`, `product-e2e-gate`, `task-review-gate` must all report and pass on the `merge_group` SHA; no check may be removed or weakened |
| Merge method preserved | `MERGE` commit method is retained (preserving `LLM-Agent`, `Task-ID`, `Reviewer` trailers) |
| Rollback | Configuration can be reverted to the full reviewed baseline (`ALLGREEN`, build/merge caps 5, min 1, wait 5, `MERGE`, timeout 60, `strict = false`) via existing runbook procedure |

### 2.2 Parameters to be determined by measurement and H08

GitHub merge queue configuration is declared in repository rulesets. The supported
parameters (per official GitHub documentation inspected 2026-09-08) are:

| Parameter | Current baseline | Options for H08 review | Notes |
|---|---|---|---|
| `min_entries_to_merge` | 1 | 1, 2, 3 | Minimum PRs to accumulate before merging group |
| `min_entries_to_merge_wait_minutes` | 5 | 5, 10, 15 | Bounded wait ceiling for accumulating minimum PRs |
| `max_entries_to_merge` | 5 | 5, 8 | Maximum PRs merged in a single group commit |
| `max_entries_to_build` | 5 | 5 | Concurrent speculative CI build limit |
| `check_response_timeout_minutes` | 60 | 60 | Headroom over median CI duration (~20.6 min) |
| `grouping_strategy` | `ALLGREEN` | `ALLGREEN` (mandatory) | `HEADGREEN` is REJECTED (see below) |
| `merge_method` | `MERGE` | `MERGE` (mandatory) | Preserves task trailers |

#### Primary Official Documentation References (Inspected 2026-09-08)
- GitHub REST API Ruleset Reference: [https://docs.github.com/en/rest/repos/rules?apiVersion=2022-11-28#create-a-repository-ruleset](https://docs.github.com/en/rest/repos/rules?apiVersion=2022-11-28#create-a-repository-ruleset)
- GitHub Merge Queue Management Guide: [https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue)

#### Rejection of `HEADGREEN` and Clarification of CI Savings
- **Rejection of `HEADGREEN`**: GitHub supports two grouping strategies: `ALLGREEN` and `HEADGREEN`. Under `HEADGREEN`, only the combined head commit is tested; an earlier PR that introduces broken intermediate states or failing tests could merge if later commits mask the failure. Furthermore, our repo's review workflow (`.github/workflows/merge-queue-review-gate.yml`) verifies the exact PR named by each merge event. `HEADGREEN` would weaken per-entry verification and conflict with per-PR review gating. It is therefore rejected under the no-weakening acceptance criterion.
- **Distinction of Batching vs Build Concurrency vs CI Savings**: Under `ALLGREEN`, GitHub runs speculative CI on each candidate entry. Batching (`min_entries_to_merge > 1`) does **not** combine `merge_group` builds into a single run and does **not** yield upstream `(N-1)` CI build savings. Rather, batching saves downstream costs: reducing base branch merge commit churn, decreasing post-merge deployment runs, and mitigating rebase races on `dev`.

## 3. Entry Conditions for Implementation (B-stage)

This A-stage document is complete without live execution. Implementation
(35B) and live activation (35C) require:

1. **H08 human parameter decision**: Confirmation of chosen `min_entries_to_merge`
   and `min_entries_to_merge_wait_minutes` from proposed options
2. **Reviewed configuration diff**: Exact `policy.json` diff and runbook updates
   reviewed and approved on a task PR
3. **Automated verification matrix**: Verification of admission control, bounded
   wait, failure isolation, and required checks on merge group SHA
4. **Readback verification**: Post-activation readback confirming remote ruleset
   matches the approved policy
5. **Rollback readiness**: Pre-tested rollback procedure back to baseline configuration

## 4. Current Measurement Summary

See [queue-observations.json](queue-observations.json) for full provenance, exact commands, and timestamps.

| Metric | Measured Value | Source & Method |
|---|---|---|
| Sample period | 2026-09-04 to 2026-09-08 (4.1 days) | 100 most recent `merge_group` workflow runs |
| CI duration (median) | 20.6 min (range: 2.8–48.0 min, avg: 14.7 min) | Completed `CI` workflow runs (n=47) |
| merge_group CI failure rate | 0% observed failures in sample (50 CI runs: 47 success, 2 pending, 1 cancelled; 50 review-gate runs: 50 success; pending outcomes unknown) | 100 `merge_group` workflow runs across 50 SHAs |
| Sample merge throughput | 0.47 merges/hour (50 merged PRs over 107.5 hours) | Creation-ordered sample of 50 merged PRs from `gh pr list` (includes weekend) |
| Inter-merge interval | Median: 54.9 min, Mean: 131.6 min (range: 0.0–621.4 min) | Time between successive `dev` merge commits in sample |
| Required status checks on `merge_group` | All 4 reporting and passing (`orchestrator`, `product`, `product-e2e-gate`, `task-review-gate`) | Sampled per-SHA check runs and commit status (`74530caf5bbf8ee3802df658e21a3ffdfca56f25`) |
| Instantaneous queue depth & wait time | `UNMEASURED_STATICALLY` | Continuous queue telemetry assigned to WP-35B/C |

### 4.1 Assessment and Conditional H08 Recommendation

1. **Measured CI duration and sample merge rate**: Retrospective observations establish that CI duration is ~20.6 minutes (median, range 2.8–48.0 min, avg 14.7 min) and merges in the creation-ordered sample had a median inter-merge gap of 54.9 minutes (mean: 131.6 minutes).
2. **Historical throughput attribution**: `docs/runbooks/dev-merge-queue.md:37` (commit `944ad12f` dated 2026-08-19) historically noted peak fleet throughput of ~1.5 merges/hour.
3. **Queue depth & wait time remain unmeasured statically**: Retrospective GitHub API queries cannot determine instantaneous queue depth or enqueue-to-merge wait times. Continuous telemetry collection is formally assigned to the WP-35B/C next-stage collection lane.
4. **Conditional Option B recommendation**: Subject to the missing occupancy and wait-time evidence, Option B (`min_entries_to_merge = 2`, `min_entries_to_merge_wait_minutes = 10`) is recommended as a conservative trial option for H08 human review. Because the 10-minute wait timer elapses concurrently during the ~20.6-minute CI execution (per GitHub merge queue semantics), Option B enables batching for concurrent dispatches without adding artificial serial latency to solo PRs whose CI takes longer than 10 minutes.
5. **No live execution in A-stage**: Continuous monitoring, live testing, and configuration activation are explicitly not required for this A-stage design repair.
