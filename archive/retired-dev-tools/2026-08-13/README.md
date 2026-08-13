# Retired one-shot tools

These files were moved out of the active source tree on 2026-08-13 after the
repository-wide wiring review. They are retained for audit and recovery; they
are not part of the runtime or pytest source roots.

- `generate_obs_instrumentation_evidence.py`: a task-specific completion
  evidence generator for the already-closed observability task. It had no
  workflow or operational caller; the observability assertions belong in the
  normal test suite if they are needed again.
