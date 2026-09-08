# Batch Merge Queue — Formal Requirement Specification

- **Task**: ODP-MERGE-QUEUE-BATCH-DESIGN-001
- **Decision reference**: D21 in ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md
- **Prior disposition audit**: ODP_MERGE_QUEUE_DISPOSITION_2026-09-03.md (verdict: `BLOCKED_BY_EVIDENCE` — no authoritative "not to implement" ruling existed)
- **User decision**: B — retain as a formal implementation requirement
- **Date of this document**: 2026-09-08

## 1. Background and Decision History

### 1.1 The original "decided not to batch" was not a decision

The merge queue on `alfloop-dev/odayplus` `dev` was activated on 2026-08-19
(task `ODP-ORCH-MERGE-QUEUE-ACTIVATION-001`, commit `944ad12f`). The initial
configuration set `min_entries_to_merge = 1`, meaning each PR is merged
individually without waiting for other PRs to form a batch.

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

- The `min_entries_to_merge = 1` default is a **starting point**, not the final state
- Batch parameters must be determined by measurement, not guessed
- Implementation requires measurement → proposal → human review → activation

### 1.3 What this requirement is NOT

- It is **not** authorization to change `min_entries_to_merge` or any other queue
  parameter without reviewed evidence and human approval
- It is **not** a governance-level `DECIDED` disposition (those require human
  decider, date, and scope per `ODP_REQUIREMENT_DISPOSITIONS.md`)
- It is **not** deleting or weakening any required check or review gate

## 2. Requirement Statement

**The `dev` branch merge queue SHALL support batching multiple approved PRs
into a single merge group when queue depth warrants it, subject to bounded
wait time, failure isolation, and all existing required checks.**

### 2.1 Mandatory Properties (from WP-35B/C acceptance)

| Property | Requirement |
|---|---|
| Batch formation | Multiple qualifying PRs can form a batch when queue depth exceeds configured minimum |
| Bounded wait | A ready PR is never held indefinitely waiting for additional PRs; a configurable maximum wait time applies |
| Admission control | Only PRs with passing required checks AND reviewer approval may enter a batch; unapproved or failing PRs are excluded |
| Head change re-validation | If a PR's head changes after admission, the merge group must re-run CI against the updated composition |
| Failure isolation | When a batch fails CI, the queue isolates the failing PR and re-queues the remaining PRs for a new attempt |
| Required checks preserved | `orchestrator`, `product`, `product-e2e-gate`, `task-review-gate` must all pass on the `merge_group` SHA; no check may be removed or weakened |
| Merge method preserved | `MERGE` commit method is retained (squash would drop required trailers) |
| Rollback | Configuration can be reverted to `min_entries_to_merge = 1` via existing runbook procedure |

### 2.2 Parameters to be determined by measurement

| Parameter | Current value | To be proposed |
|---|---|---|
| `min_entries_to_merge` | 1 | ≥ 2 (exact value from queue depth measurement) |
| `min_entries_to_merge_wait_minutes` | 5 | Bounded wait ceiling (from CI duration measurement) |
| `grouping_strategy` | ALLGREEN | Review whether ALLGREEN or NONE better serves batch failure isolation at higher batch sizes |

## 3. Entry Conditions for Implementation (B-stage)

This A-stage document is complete without the following, but implementation
(35B) and live activation (35C) require:

1. **Sustained queue depth evidence**: Demonstrate that `dev` queue depth
   regularly exceeds 1 (the condition under which batching provides value)
2. **CI duration stability**: Confirm CI duration is predictable enough to set
   a meaningful `min_entries_to_merge_wait_minutes`
3. **Reviewed configuration diff**: Exact `policy.json` change with before/after
   values, reviewed and approved
4. **Human approval of parameters**: H08 from the execution plan — when
   measurement proposes specific values, human confirms the wait time / cost
   tradeoff
5. **Readback verification**: Post-activation readback confirming remote
   configuration matches the reviewed diff
6. **Observation period**: Defined monitoring window to confirm batch behavior
   matches expectations before declaring live validation complete

## 4. Current Measurement Summary

See [queue-observations.json](queue-observations.json) for full provenance.

| Metric | Value | Source |
|---|---|---|
| Sample period | 2026-09-04 to 2026-09-08 (4.1 days) | merge_group CI runs |
| Merge rate | 0.47 merges/hour (sample includes weekend) | merged PRs |
| CI duration (median) | 20.6 min | merge_group runs with duration >1 min |
| CI duration (range) | 2.8–48.0 min | same |
| Max observed queue depth | 2 concurrent merge-group SHAs | concurrent run analysis |
| Typical queue depth | 1 | same |
| merge_group CI failure rate | 0% (0 failures in 100 runs; 1 cancellation) | same |
| `grouping_strategy` | ALLGREEN (each entry verified independently) | GraphQL readback |

### 4.1 Assessment

At current throughput (~0.5–1.5 merges/hour depending on active hours), the
queue is rarely deeper than 2. Batching at `min_entries_to_merge = 2` would
provide modest CI savings only during burst periods. The primary value of
establishing this as a formal requirement is:

- **Future-proofing**: As fleet concurrency increases, queue depth will grow
- **Cost reduction**: Each merge group CI run costs ~15–20 minutes; batching
  N PRs saves (N-1) × ~20 minutes of CI time
- **Governance**: The batch parameter becomes a reviewed, versioned configuration
  rather than an undocumented default
