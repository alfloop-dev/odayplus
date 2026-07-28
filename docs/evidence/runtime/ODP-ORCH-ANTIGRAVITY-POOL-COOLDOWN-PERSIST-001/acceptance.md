# Antigravity pool cooldown persistence acceptance

Task: `ODP-ORCH-ANTIGRAVITY-POOL-COOLDOWN-PERSIST-001`

The regression models Gemini run `antigravity-20260728T113635Z-6bc964ed`
reporting an authoritative quota reset, followed by Claude fallback run
`antigravity-20260728T113754Z-8c537691`.

Delivered behavior:

- quota expiry is persisted outside task state and is not cleared by completion,
  review, or reopen transitions;
- providers sharing an Antigravity quota/account group select Claude before the
  expiry without changing the logical task owner;
- Gemini becomes eligible immediately after expiry;
- separately configured Antigravity profiles retain isolated credentials and
  cooldown state.

Focused verification:

```text
python3 -m pytest -q .orchestrator/test_model_rotation.py
python3 -m pytest -q .orchestrator/test_supervisor.py -k 'not RuntimeConfigTests'
```

The unfiltered supervisor run passed 239 tests and had three environment-only
failures because this isolated worktree does not contain the gitignored
`.orchestrator/config.json`; the filtered command excludes only that
runtime-config fixture class.
