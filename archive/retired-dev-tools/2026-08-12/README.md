# Retired development-tool candidates

These files were moved out of the active source tree on 2026-08-12 after a
static wiring review. They are retained here for recovery and audit; they are
not part of the runtime or the pytest source roots.

Reasons for retirement:

- `chair_review_wave_health.py`: no runtime caller, test, or executable entry.
- `qwen.py` and `test_qwen_adapter.py`: the adapter was tested but was not
  registered in `.orchestrator/adapters/__init__.py`.
- `run_chaos.py`: no repository entrypoint and imports a test-only mock.
- `generate_observability_evidence.py`: local synthetic evidence generator,
  not wired to CI or production acceptance.
- `run_load.py`: superseded by the assisted-intake durable capacity harness.
- `copilot_login_helper.py`: no repository caller or operational runbook.
- `emit_test_event.py`: isolated manual event injector with no active caller.

Restore a file only after adding a documented owner, an executable entrypoint,
and a focused regression test or CI/runbook reference.
