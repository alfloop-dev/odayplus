# ODP-ORCH-ANTIGRAVITY-ROTATION-FIX-001 — evidence

Fix for the two P0 findings recorded on PR #401 exact head
`edcbf4ed2bdb841e452404c8fa9475011db7849e` (reviewer Codex2 / ajoe734,
2026-07-27T12:42:27Z). The fix stays on the PR #401 branch
`fix/antigravity-model-rotation`.

## P0-1 — quota exhaustion must bind to the dispatched pool

**Finding.** `record_exhaustion()` inferred the failed pool from the mutable
global `active_pool()` at failure-processing time. Once worker A cooled Gemini,
a stale worker B that had also been launched on Gemini was recorded against
Claude, producing `both_exhausted=True` and a hard dispatch pause — exactly the
fleet stall the rotation was meant to prevent.

**Fix.**

- `model_rotation.resolve_active_selection()` returns the `{pool, model}` pair
  chosen at dispatch time; `resolve_active_model()` now delegates to it.
- `adapters/antigravity.py` records that pool/model in the `DeliveryResult`
  metadata (`antigravity_model_pool` / `antigravity_model`).
- `supervisor.start_worker_for_request()` mirrors the pool onto the worker
  record, so it survives in `state.json`.
- `record_exhaustion(..., pool=...)` cools the pool that is passed in.
  `mark_provider_dispatch_paused(..., worker=...)` resolves that pool from the
  failing worker (or from `state["workers"][worker_run_id]`), and only falls
  back to `active_pool()` inference when no worker was ever launched. The
  activity log records `dispatched_pool` and `pool_source` for auditability.

## P0-2 — quota classifier must not swallow ordinary failures

**Finding.** The unscoped substring `"quota reached"` in
`terminal_quota_markers` classified any worker text containing those words as
`quota_terminal`. Because environmental kinds are excluded from
`record_task_failure_streak()`, real code/test failures such as
`AssertionError: expected quota reached banner to be hidden` were masked and
never counted, and they rotated/paused the provider.

**Fix.** The generic markers `"quota reached"`, `"individual quota reached"` and
`"please upgrade your subscription to increase your limits"` are removed from
the substring set. Antigravity quota is now matched by
`AGY_QUOTA_SIGNATURE_PATTERN` — agy's full banner (`Individual quota reached` +
its upgrade/reset continuation) — and only for providers served by the
Antigravity adapter (`is_antigravity_provider()`). Ordinary failures stay
`terminal` and keep incrementing the per-task streak.

The signature is checked against the real thing, not a guess: every agy quota
reason recorded in the live `.orchestrator/state.json` carries the continuation
the pattern requires —

```
Error: Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 10m26s.
Error: Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 4h47m7s.
Error: Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 4m57s.
```

— and those exact strings are asserted in
`test_agy_quota_banner_variants_are_classified`.

## Reproduction / verification transcript

`repro_p0_findings.py` adapts to the pre-fix API, so the same script runs
against both trees. 2026-07-27:

Pre-fix head `edcbf4ed` (`git worktree add --detach <dir> edcbf4ed`):

```
[P0-1] adapter passes dispatched worker: False
[P0-1] cooldown state: {"claude_until": "...", "gemini_until": "...", ...}
[P0-1] provider hard-paused: True
[P0-1] REPRODUCED BUG: Claude pool falsely exhausted
[P0-2] REPRODUCED BUG: kind=quota_terminal streak=0 :: AssertionError: expected quota reached banner to be hidden
[P0-2] REPRODUCED BUG: kind=quota_terminal streak=0 :: TypeError: quota reached handler returned None
[P0-2] REPRODUCED BUG: kind=quota_terminal streak=0 :: Error: assertion failed in quota reached state transition
[P0-2] PASS: real agy banner still classified as quota_terminal
RESULT: P0-1 BUG / P0-2 BUG   (exit 1)
```

This branch:

```
[P0-1] adapter passes dispatched worker: True
[P0-1] cooldown state: {"gemini_until": "...", ...}   # no claude_until
[P0-1] provider hard-paused: False
[P0-1] PASS: Claude pool untouched
[P0-2] PASS: kind=terminal streak=2 :: AssertionError: expected quota reached banner to be hidden
[P0-2] PASS: kind=terminal streak=2 :: TypeError: quota reached handler returned None
[P0-2] PASS: kind=terminal streak=2 :: Error: assertion failed in quota reached state transition
[P0-2] PASS: real agy banner still classified as quota_terminal
RESULT: P0-1 ok / P0-2 ok   (exit 0)
```

## Test suites

Run from `.orchestrator/` with the gitignored local `config.json` copied in
(worktrees do not carry it):

| Command | Result |
| --- | --- |
| `python3 -m pytest -q test_model_rotation.py` | 22 passed (9 pre-existing + 13 new) |
| `python3 -m pytest -q test_supervisor.py test_model_rotation.py` | 3 failed (`RuntimeConfigTests`, pre-existing config drift), rest passed |
| `python3 -m pytest -q .` (full orchestrator suite) | 4 failed — identical set to the `origin/dev` baseline run below |
| `origin/dev` baseline, same command | same 4 failures: `test_provider_permissions.py::…::test_edit_allows_configured_execute_plans_workspace_root`, 3× `test_supervisor.py::RuntimeConfigTests` |
| `python3 -m ruff check .orchestrator docs/evidence/completion/ODP-ORCH-ANTIGRAVITY-ROTATION-FIX-001` | All checks passed |

No new failures are introduced; the 4 remaining failures assert against the
live `.orchestrator/config.json` (agent concurrency caps / quota groups) that
has since been retuned, and they fail identically on `origin/dev`.

## Other acceptance criteria re-covered by tests

- **Return to primary policy after cooldown** —
  `test_selection_reports_pool_and_model` asserts pool/model go back to
  `gemini` / agy default once the cooldown expires.
- **No cross-profile credential leakage** —
  `test_rotation_does_not_leak_credentials_across_providers` asserts the spawn
  env `HOME`/`ORCH_PROVIDER` stay on the failing provider's own profile after a
  rotation, and that another antigravity provider's home never appears.
- **Structured, shell-safe adapter arguments** —
  `test_adapter_persists_dispatched_pool_in_worker_metadata` asserts the model
  (`Claude Sonnet 4.6 (Thinking)`, spaces and parentheses) stays a single argv
  element passed to `spawn_background_process`, never a shell string.
