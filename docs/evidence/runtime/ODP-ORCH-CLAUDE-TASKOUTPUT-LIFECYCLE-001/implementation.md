# TaskOutput permission lifecycle evidence

Task: `ODP-ORCH-CLAUDE-TASKOUTPUT-LIFECYCLE-001`

## Runtime receipt

- Source worker run: `claude-20260728T121406Z-073037de`
- Source session: `0c68ffbc-ec6d-4efa-9ff7-c100d270bc4c`
- Source log: `.orchestrator/logs/20260728T121406910939Z-claude-claude3-31a027.log`
- Observed request: `TaskOutput({"task_id":"byld6bjnj","block":true,"timeout":600000})`
- Observed hook response: `permissionDecision=defer`, reason
  `Deferred by default for TaskOutput.`
- Lifecycle result: the worker exited before terminal task state and Supervisor
  later dispatched replacement run `claude-20260728T125230Z-4ff1f007`.

The receipt is recorded as identifiers and a redacted event summary. The
provider log itself remains runtime-local and is not copied into the repository.

## Implemented boundary

`TaskOutput` is added by exact tool name to the existing read-only tool set.
It now evaluates to `allow` with risk class `safe_read`.

The change does not alter the fail-closed default or the Bash, network, edit,
Agent, destructive, Forecast, deploy, or Package 10 policies. Regression tests
lock those adjacent boundaries.

## Verification

Passed:

- `python3 -m py_compile .orchestrator/permission_broker.py
  .orchestrator/test_provider_permissions.py .orchestrator/test_supervisor.py`
- `cd .orchestrator && python3 -m unittest
  test_provider_permissions.ProviderPermissionsTest.test_taskoutput_is_auto_allowed_as_exact_read_only_tool
  test_provider_permissions.ProviderPermissionsTest.test_taskoutput_allow_does_not_broaden_adjacent_permission_classes
  test_supervisor.RuntimeLeaseReconciliationTests.test_restart_preserves_one_live_claude_worker_without_redispatch`
- `python3 -m pytest -q -m 'not requires_live_env' -k 'not
  RuntimeConfigTests' .orchestrator/test_provider_permissions.py
  .orchestrator/test_supervisor.py`

The broad unittest invocation is not the canonical worktree command: it runs a
`requires_live_env` cross-repository case and three `RuntimeConfigTests` that
require the supervisor-local, gitignored `.orchestrator/config.json`. Those four
environment-bound cases were excluded from the successful pytest regression
above.

## Closeout

- Codex7 approved exact task head
  `6400c85e625c9dea19994f80de78b4291ca686ea` with no findings.
- PR #475 passed `orchestrator`, `product`, `product-e2e-gate`, and
  `task-review-gate` on integration head
  `00388bdb512f119c5c7f9ad915380c51269b302f`.
- PR #475 merged into `dev` at
  `b4aba4258aca97ab154fdc8fec54e81b355c14a6` on
  `2026-07-28T13:49:57Z`.
- The live broker file was deployed from that merge commit. Its SHA-256 was
  `1ed64d46ea1c115fdc1c33b3bbe57768148dd5d1cf81ff5c16874f764db92391`,
  matching the merged file, and the live evaluator returned `allow` /
  `safe_read` for exact `TaskOutput`.
- The live Supervisor was restarted after merge. The singleton reconciled the
  existing Codex2 run without redispatch, remained healthy, and completed a
  successful normal-dispatch loop after watchdog safe mode expired.
- Runtime-state and OS-process cardinality checks contained no task with more
  than one live worker before restart, during boot reconciliation, or after
  normal dispatch resumed.

The structured, non-sensitive receipt is in
`live-restart-receipt.json`. It contains no prompts, credentials, or provider
response content.
