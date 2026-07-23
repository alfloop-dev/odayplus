---
task_id: ODP-INTAKE-FCL-LIFECYCLE-001
title: Live lifecycle, assignment, SLA, promotion, and job completion evidence
baseline_commit: c900e906f96cb3750274c24e1a8f2922999f9048
implementation_commit: 9d4eb32d354840b75b8e2e271e3684685819e4c9
branch: task/ODP-INTAKE-FCL-LIFECYCLE-001
status: ready-for-integration
updated_at: 2026-07-23
---

# ODP-INTAKE-FCL-LIFECYCLE-001 Completion Evidence

## Owned implementation

The task delivers a server-driven lifecycle integration boundary without
editing the durable route or detail-shell composition owned by other tasks.

### Lifecycle read boundary

`useIntakeLifecycle.ts` defines the canonical UI snapshot for:

- intake transitions;
- assignment and SLA receipts and histories;
- decision and promotion receipts and histories;
- job receipts and histories;
- server-authorized actions.

The hook:

1. performs an initial server read and recurring polling;
2. accepts an optional server subscription;
3. exponentially backs off after read failures and resets after success;
4. aborts network reads while the document is hidden;
5. refreshes immediately when the document becomes visible;
6. exposes manual refresh, last refresh, next refresh, mode, and error state;
7. never mutates a lifecycle state locally.

### Persisted lifecycle rendering

`IntakeStageTimeline.tsx` renders only persisted transition receipts. When a
history is absent, it shows the current authoritative state and a missing
history notice; it does not reconstruct a path from a final stage.

The component exposes:

- attempt, checkpoint, timeout, next retry, queue, correlation, and job version;
- intake cancel and failed-state retry;
- controlled reopen for `FAILED` and `QUARANTINED`;
- terminal `CANCELLED` behavior;
- active-job cancellation;
- `FAILED` and `DEAD_LETTER` replay from the persisted checkpoint.

`AssignmentSlaSummary.tsx` renders:

- assignment status, owner, queue, assigned time, claimed time, and due time;
- authoritative SLA state, expected resume time, and escalation level;
- persisted pause, resume, transfer, escalation, claim, and completion history;
- direct claim, transfer, pause, resume, escalate, and complete actions gated by
  the server-provided `allowed_actions` list.

`PromotionReviewPanel.tsx` and `SiteScoreJobStatus.tsx` render persisted
promotion, decision, and job histories. Job status includes timeout, next
retry, queue, cancellation, DLQ, and replay. A `SCORE_FAILED` promotion keeps
the committed Candidate Site ID visible.

`TransferIntakeDialog.tsx` and `PauseSlaDialog.tsx` retain their conflict-safe
drafts and now reject close, overlay, and Escape callbacks while a write is in
flight.

## Integration exports

`apps/web/features/operator/network/intake/index.ts` exports:

- `useIntakeLifecycle` and `lifecycleBackoffDelay`;
- `IntakeLifecycleSnapshot` and all lifecycle receipt/action types;
- `IntakeStageTimeline`;
- `AssignmentSlaSummary`;
- `PromotionReviewPanel`;
- `SiteScoreJobStatus`;
- command payload types used by the Integration task.

The Integration task must supply a canonical `loadSnapshot` implementation and
wire each callback to the runtime command endpoint. The lifecycle slice does
not call the legacy fixture facade and does not invent transition receipts,
timestamps, queue names, attempts, checkpoints, IDs, or versions.

## Request, response, and persisted readback proof

Focused tests use a loader that returns versioned server snapshots. They prove:

1. the UI remains at version 1 until a second server read returns version 2;
2. timer polling carries the `POLL` reason;
3. read failure schedules a doubled delay and retains the last snapshot;
4. a successful read resets the failure count;
5. hidden documents perform no reads;
6. becoming visible performs an immediate `VISIBLE` read;
7. subscription snapshots replace the rendered snapshot and unsubscribe on
   unmount;
8. no missing intermediate intake or promotion transition is fabricated;
9. persisted assignment, SLA, decision, promotion, and job transition rows
   render their server actor, state, version, and execution metadata;
10. direct action controls call the supplied command boundary without changing
    the displayed state;
11. `SCORE_FAILED` preserves the authoritative Candidate ID.

The command callback proof covers intake cancel/retry/reopen, job
cancel/replay, claim, transfer, pause/resume, escalation, and completion. The
actual API response and database readback are intentionally left to Runtime
and Integration ownership; this task does not substitute component fixtures
for production persistence evidence.

## Verification

Executed against implementation commit
`9d4eb32d354840b75b8e2e271e3684685819e4c9`:

```text
npm run test --workspace @oday-plus/web -- --reporter=verbose \
  features/operator/network/intake/__tests__/useIntakeLifecycle.test.tsx \
  features/operator/network/intake/__tests__/LifecycleControls.test.tsx
PASS - 2 files, 11 tests

npm run test --workspace @oday-plus/web
PASS - 10 files, 96 tests

npm run typecheck --workspace @oday-plus/web
PASS

npm run lint --workspace @oday-plus/web -- --max-warnings=0
PASS - no warnings or errors

npm run build --workspace @oday-plus/web
PASS - production BUILD_ID Hl7EqiwUF-tSjYkgtYd90

git diff --check
PASS
```

The temporary `node_modules` verification symlink was removed before the
implementation commit.

## Requirement rows closed by this slice

- Handoff section 8.3: stage/job execution metadata and automatic refresh
  integration boundary.
- Handoff section 8.6: owner, queue, assigned/claimed time, due time, persisted
  assignment/SLA history, and direct actions.
- Handoff section 8.8: persisted promotion/job histories, timeout, next retry,
  cancellation, DLQ, replay, and Candidate retention after `SCORE_FAILED`.
- Handoff section 9.1: controlled failed/quarantined reopen and terminal
  cancelled presentation.
- Handoff section 9.3: visible job attempt, checkpoint, timeout, next retry,
  cancellation, DLQ, and replay permission.
- Audit FCF-005 UI slice: visibility-aware polling/subscription and no
  client-inferred processing path.

## Integration-stage proof still required

These gates belong to the Runtime, Shell, and
`ODP-INTAKE-FCL-INTEGRATION-001` tasks:

1. mount the exported boundary on
   `/w/expansion/listings/intake/:intakeId`;
2. map canonical API history/readback fields into `IntakeLifecycleSnapshot`;
3. wire cancel, retry, reopen, assignment, SLA, escalation, job cancel, and
   replay commands to authoritative APIs;
4. prove persisted database readback after every command;
5. prove browser-observed server transitions without reload;
6. run route-level Playwright for cancel, retry, DLQ, escalation, pause,
   transfer, replay, and `SCORE_FAILED` Candidate retention.

Until those owners integrate this boundary, the slice is
`ready-for-integration`; it is not a claim that the complete Assisted Listing
Intake product is production complete.
