# ODP-SUPERVISOR-QUEUE-GHOST-001

## Live failure

The long-running Supervisor retained `started` queue records after their event
ids had been pruned from the canonical `event-queue.jsonl`. A later
`save_runtime_state()` merged those stale disk-only records back into state, so
dashboard queue rows could outlive the worker receipt and canonical event.

## Fix

After the locked disk/in-memory merge, `save_runtime_state()` now rebuilds the
queue record map from the latest canonical event queue. This removes pruned
ids while preserving events appended by a concurrent writer.

## Verification

- 13 runtime-state tests passed.
- 295 Supervisor tests passed.
- 38 model-rotation/runtime-state pytest tests passed.
- Independent Antigravity6 replay passed 308 combined runtime/Supervisor tests.
- Ruff, doctor, and `git diff --check` passed.

The independent reviewer changed from Antigravity7 to Antigravity6 because the
original reviewer lane was capacity-blocked. Owner and reviewer remained
distinct throughout review.
