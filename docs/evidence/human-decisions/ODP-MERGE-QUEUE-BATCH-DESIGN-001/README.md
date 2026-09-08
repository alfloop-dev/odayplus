# ODP-MERGE-QUEUE-BATCH-DESIGN-001 — Evidence Package

- **Task ID**: ODP-MERGE-QUEUE-BATCH-DESIGN-001
- **Phase**: A-stage (read-only measurement and reviewable design)
- **Owner**: Antigravity3
- **Reviewer**: Codex2
- **Date**: 2026-09-08
- **Decision reference**: D21 in [ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md](../../../../.orchestrator/source-doc-cache/alfloop-dev__odayplus/be04fe7954d3414f024901e034caacd95ae81538/docs/plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md)

## Summary

This evidence package delivers the A-stage of WP-35 from the execution plan:
formalizing merge queue batching as a requirement (per D21), collecting
read-only measurements of the current queue, and proposing reviewable
configuration options with full baseline rollback for H08 human decision.

The prior `BLOCKED_BY_EVIDENCE` status (from the disposition audit
`ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001`) is resolved by D21: the user
selected option B — retain batching as a formal implementation requirement.
This package records that decision, provides the measurement evidence, and
specifies the engineering handoffs for WP-35B/C and WP-90.

## Artifacts

| # | File | Purpose |
|---|---|---|
| 1 | [queue-observations.json](queue-observations.json) | Read-only measurements of CI duration, merge interval, configuration readback, and sampled per-SHA check run evidence with complete command provenance |
| 2 | [batch-requirement.md](batch-requirement.md) | Formal requirement specification for batch merging, including mandatory properties, dated official references, and parameter boundaries |
| 3 | [configuration-options.md](configuration-options.md) | Three comparable configuration options (Baseline / Conservative Trial / Burst Batching) with tradeoff analysis, mandatory `ALLGREEN` preservation, and full baseline rollback for H08 review |
| 4 | [governance-handoff.md](governance-handoff.md) | Governance update fragment for WP-90 to incorporate into the shared manifest, recording D21 disposition as `IMPLEMENTATION_READY` |
| 5 | [implementation-handoff.md](implementation-handoff.md) | Comprehensive automated verification matrix, entry conditions, and operational procedures for B-stage (engineering) and C-stage (live activation) |

## Key Findings

1. **Queue configuration confirmed**: Remote readback matches `policy.json`;
   `min_entries_to_merge = 1` (single-entry minimum baseline), `grouping_strategy = ALLGREEN`,
   `strict = false` on `dev`.
2. **CI duration and throughput measured**: Median CI duration is ~20.6 minutes
   (range: 2.8–48.0 min); merge throughput averaged 0.47 merges/hour over a 4.4-day
   window (median inter-merge gap: 50.1 min).
3. **CI reliability on merge_group**: 0 failures in 100 sampled `merge_group` workflow
   runs (99 success, 1 cancellation across 50 unique merge group SHAs).
4. **All 4 required checks report on `merge_group`**: `orchestrator`, `product`,
   `product-e2e-gate` (via GitHub Actions check runs) and `task-review-gate` (via commit
   status) verified on sampled `merge_group` commit `74530caf5bbf8ee3802df658e21a3ffdfca56f25`.
5. **Continuous queue occupancy unmeasured statically**: Retrospective run lists do not
   record instantaneous queue depth or enqueue-to-merge waiting time; continuous telemetry
   collection is formally assigned to WP-35B/C.
6. **`ALLGREEN` strategy strictly retained**: `HEADGREEN` is evaluated and rejected under
   the no-weakening policy to prevent unverified intermediate states and maintain strict
   per-PR review gate validation.

## What This Package Does NOT Do

- Does not change any live queue configuration
- Does not select final batch parameters (that is H08 human decision)
- Does not modify shared governance manifests (that is WP-90)
- Does not weaken any required check or review gate
- Does not execute tests or create temporary testing PRs in this A-stage task

## Next Steps

1. **H08**: Human reviews [configuration-options.md](configuration-options.md)
   and selects batch parameters
2. **WP-90**: Integrator incorporates [governance-handoff.md](governance-handoff.md)
   into shared governance registry
3. **WP-35B**: Engineering task applies approved configuration to `policy.json`,
   updates runbook, and adds automated verification tests per
   [implementation-handoff.md](implementation-handoff.md)
4. **WP-35C**: Live activation with readback verification and 7-day observation window
