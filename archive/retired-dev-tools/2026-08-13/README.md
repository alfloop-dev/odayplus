# Retired one-shot tools

These files were moved out of the active source tree on 2026-08-13 after the
repository-wide wiring review. They are retained for audit and recovery; they
are not part of the runtime or pytest source roots.

- `generate_obs_instrumentation_evidence.py`: a task-specific completion
  evidence generator for the already-closed observability task. It had no
  workflow or operational caller; the observability assertions belong in the
  normal test suite if they are needed again.
- `configure_account_pools.py` and its test: the account-pool migration is
  already applied to the live runtime config and now reports `unchanged`.
- `migrate_task_dependency_graph.py` and its test: the single legacy mapping is
  already applied; a dry run against live state reports no migration and the
  durable dependency validator remains active separately.
- `sync_plan_execution_pack.py`, `validate_plan_execution_pack.py`, and their
  contract test: these are hard-bound to the 2026-07-31 execution packet. The
  packet is historical and its validator no longer accepts the current
  canonical archive, so it is not a valid active release or runtime gate.
