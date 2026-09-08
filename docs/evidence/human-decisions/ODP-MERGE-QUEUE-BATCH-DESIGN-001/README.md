# ODP-MERGE-QUEUE-BATCH-DESIGN-001 — Evidence Package

- **Task ID**: ODP-MERGE-QUEUE-BATCH-DESIGN-001
- **Phase**: A-stage (read-only measurement and reviewable design)
- **Owner**: Antigravity3
- **Reviewer**: Codex2
- **Date**: 2026-09-08
- **Decision reference**: D21 in ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md

## Summary

This evidence package delivers the A-stage of WP-35 from the execution plan:
formalizing merge queue batching as a requirement (per D21), collecting
read-only measurements of the current queue, and proposing reviewable
configuration options for H08 human decision.

The prior `BLOCKED_BY_EVIDENCE` status (from the disposition audit
`ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001`) is resolved by D21: the user
selected option B — retain batching as a formal implementation requirement.
This package records that decision, provides the measurement evidence, and
specifies what must happen next.

## Artifacts

| # | File | Purpose |
|---|---|---|
| 1 | [queue-observations.json](queue-observations.json) | Read-only measurements of queue depth, CI duration, merge rate, and configuration readback with full provenance (commands, exit codes, timestamps) |
| 2 | [batch-requirement.md](batch-requirement.md) | Formal requirement specification for batch merging, including mandatory properties and parameters to be determined |
| 3 | [configuration-options.md](configuration-options.md) | Three comparable configuration options (Conservative / Moderate / Aggressive) with tradeoff analysis for H08 human review |
| 4 | [governance-handoff.md](governance-handoff.md) | Governance update fragment for WP-90 to incorporate into the shared manifest, recording D21 disposition as `IMPLEMENTATION_READY` |
| 5 | [implementation-handoff.md](implementation-handoff.md) | Entry conditions, scope, verification, and rollback procedure for B-stage (engineering) and C-stage (live activation) |

## Key Findings

1. **Queue configuration confirmed**: Remote readback matches `policy.json`;
   `min_entries_to_merge = 1` (no batching), `grouping_strategy = ALLGREEN`,
   `strict = false` on `dev`
2. **Queue depth is low**: Max observed concurrent merge-group SHAs = 2;
   typical depth = 1. Batching provides limited value at current throughput
3. **CI is reliable**: 0 failures in 100 merge_group runs (99 success, 1
   cancellation). Median CI duration ~20.6 minutes
4. **Merge rate**: ~0.47 merges/hour (4.4-day sample including weekend).
   Runbook's ~1.5 merges/hour figure may reflect active-hours-only measurement
5. **All 4 required checks report on `merge_group`**: orchestrator, product,
   product-e2e-gate, task-review-gate

## What This Package Does NOT Do

- Does not change any live queue configuration
- Does not select final batch parameters (that is H08 human decision)
- Does not modify shared governance manifests (that is WP-90)
- Does not weaken any required check or review gate
- Does not create any waiver, exception, or bypass

## Next Steps

1. **H08**: Human reviews [configuration-options.md](configuration-options.md)
   and selects or adjusts batch parameters
2. **WP-90**: Integrator incorporates [governance-handoff.md](governance-handoff.md)
   into shared governance registry
3. **WP-35B**: Engineering task applies approved configuration to `policy.json`
   and updates runbook (entry conditions in
   [implementation-handoff.md](implementation-handoff.md))
4. **WP-35C**: Live activation with readback verification and observation
   period
