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

The mandatory post-merge live dispatch occurred after PR #467 merged into
`dev` at `ee639d581f432a0ccdd2e81cefc5a92f499fa3e7`:

- The first live run, `antigravity-20260728T110603Z-744e5304`, failed with
  Gemini individual quota. Supervisor recorded that failure exactly once as
  pool `gemini` at `2026-07-28T11:07:13Z`.
- Supervisor preserved owner `Antigravity` and task
  `ODP-P10-FLEET-CONFLICT-REAUDIT-001`, then dispatched fresh run
  `antigravity-20260728T110727Z-f6ffaade`.
- The durable worker record and runner status identify the fresh run as
  `antigravity_model_pool=claude`, model
  `Claude Sonnet 4.6 (Thinking)`, started at `2026-07-28T11:07:27Z`.
- At receipt capture (`2026-07-28T11:10:42Z`) the worker was live with a
  current heartbeat. This receipt proves dispatch routing; completion and
  delivery of the Package 10 audit remain owned by its separate canary task.

The receipt contains no credentials, prompts, or provider response content.
