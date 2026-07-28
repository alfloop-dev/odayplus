# Antigravity live fallback lifecycle evidence

Task: `ODP-ORCH-ANTIGRAVITY-LIVE-FALLBACK-001`

Commit under test: `b07be6c393de983b15288053f6c1cddc2ed5df8c`

## Delivered lifecycle

- A quota failure is charged to the immutable pool recorded on the worker.
- The run id is recorded in
  `provider_guardrails.processed_model_rotation_failures`, so startup and boot
  reconciliation cannot charge the same failed run twice.
- If a pool remains available, terminal-quota handling does not call owner
  reassignment. A dispatch-start failure returns its queue record to `queued`;
  a completed worker failure remains failed and the ready dispatcher can emit
  a fresh event for the same owned task.
- The fresh Antigravity dispatch resolves the remaining pool through
  `resolve_active_selection`; after Gemini exhaustion this is `claude` with
  `Claude Sonnet 4.6 (Thinking)`.
- Only a failure from a worker dispatched with
  `antigravity_model_pool=claude` after Gemini is cooling yields
  `both_exhausted` and permits provider pause and owner reassignment.

## Verification

```text
python3 -m pytest -q .orchestrator/test_model_rotation.py \
  .orchestrator/test_supervisor.py -k 'not RuntimeConfigTests'
237 passed

python3 -m py_compile .orchestrator/model_rotation.py \
  .orchestrator/supervisor.py
passed

git diff --check
passed
```

The three excluded `RuntimeConfigTests` require the gitignored live
`.orchestrator/config.json`, which is not seeded into this isolated worktree.
The unfiltered baseline produced 235 passes and only those three
`FileNotFoundError` failures.

## Canary receipt status

`canary-receipt.json` is the deterministic supervisor lifecycle receipt
produced by the focused regression. It proves the canary dispatch selection
and reconciliation order without changing Package 10 product behavior.

The blocked live task `ODP-P10-FLEET-CONFLICT-REAUDIT-001` still requires an
actual supervisor dispatch after this branch is merged into `dev`; this
worktree has neither the live `.orchestrator/config.json` nor authority to
fabricate that external run. The live receipt must record
`antigravity_model_pool=claude` before this task's final acceptance is closed.
