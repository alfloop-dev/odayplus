#!/usr/bin/env python3
"""Reproduce (and re-verify) the two P0 review findings on PR #401.

Runs against ANY checkout of the orchestrator: it adapts to the pre-fix API
(`mark_provider_dispatch_paused` without a `worker` argument) so the same script
demonstrates the bug at head `edcbf4ed` and the fixed behaviour afterwards.

    python3 docs/evidence/completion/ODP-ORCH-ANTIGRAVITY-ROTATION-FIX-001/repro_p0_findings.py

Exit code 0 = both findings behave correctly; 1 = at least one reproduces.

P0-1  Two concurrent workers dispatched on the Gemini pool both hit quota. The
      second failure must cool Gemini again, NOT Claude, and must not hard-pause
      the provider.
P0-2  Ordinary application/test failures whose text merely contains the words
      "quota reached" must stay `terminal` and keep incrementing the per-task
      failure streak.
"""
from __future__ import annotations

import inspect
import json
import pathlib
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / ".orchestrator"))

import model_rotation as mr  # noqa: E402
import supervisor as sv  # noqa: E402

AGY_BANNER = (
    "Error: Individual quota reached. Please upgrade your subscription to "
    "increase your limits. Resets in 2h21m32s."
)
ORDINARY_FAILURES = (
    "AssertionError: expected quota reached banner to be hidden",
    "TypeError: quota reached handler returned None",
    "Error: assertion failed in quota reached state transition",
)


def _config(tmp: pathlib.Path) -> dict:
    return {
        "paths": {"activity_log": str(tmp / "activity.jsonl")},
        "providers": {
            "antigravity5": {
                "antigravity": {
                    "model_rotation": {
                        "enabled": True,
                        "primary_model": "",
                        "fallback_model": "Claude Sonnet 4.6 (Thinking)",
                    }
                }
            }
        },
    }


def _worker(run_id: str, pool: str) -> dict:
    return {
        "run_id": run_id,
        "provider": "antigravity5",
        "agent_id": "antigravity5",
        "task_id": f"ODP-TEST-{run_id}",
        "metadata": {"antigravity_model_pool": pool},
        "antigravity_model_pool": pool,
    }


def check_p0_1(tmp: pathlib.Path) -> bool:
    """True when only the Gemini pool is cooled by two concurrent Gemini workers."""
    mr._STATE_PATH = tmp / "cooldown.json"
    config = _config(tmp)
    workers = {"run-a": _worker("run-a", "gemini"), "run-b": _worker("run-b", "gemini")}
    state: dict = {"workers": workers}
    supports_worker = "worker" in inspect.signature(sv.mark_provider_dispatch_paused).parameters
    paused = []
    for worker in workers.values():
        kwargs = {
            "task_id": worker["task_id"],
            "worker_run_id": worker["run_id"],
            "failure_kind": "quota_terminal",
            "pause_kind": "quota_terminal",
        }
        if supports_worker:
            kwargs["worker"] = worker
        paused.append(
            sv.mark_provider_dispatch_paused(config, state, "antigravity5", AGY_BANNER, **kwargs)
        )
    entry = mr.status("antigravity5").get("antigravity5", {})
    hard_paused = bool(state.get("provider_guardrails", {}).get("dispatch_pauses") or {})
    ok = entry.get("claude_until") is None and not hard_paused and not any(paused)
    print(f"[P0-1] adapter passes dispatched worker: {supports_worker}")
    print(f"[P0-1] cooldown state: {json.dumps(entry, sort_keys=True)}")
    print(f"[P0-1] provider hard-paused: {hard_paused}")
    print(f"[P0-1] {'PASS' if ok else 'REPRODUCED BUG'}: Claude pool "
          f"{'untouched' if entry.get('claude_until') is None else 'falsely exhausted'}")
    return ok


def check_p0_2() -> bool:
    """True when ordinary failures mentioning 'quota reached' still count."""
    config = _config(pathlib.Path(tempfile.gettempdir()))
    ok = True
    for reason in ORDINARY_FAILURES:
        worker = {"task_id": "ODP-TEST-REAL", "provider": "antigravity5"}
        kind = sv.classify_worker_failure(config, worker, reason)["kind"]
        state: dict = {}
        count = 0
        for _ in range(2):
            count = sv.record_task_failure_streak(state, worker, reason, failure_kind=kind)
        good = kind == "terminal" and count == 2
        ok = ok and good
        print(f"[P0-2] {'PASS' if good else 'REPRODUCED BUG'}: kind={kind} streak={count} :: {reason}")
    kind = sv.classify_worker_failure(config, {"provider": "antigravity5"}, AGY_BANNER)["kind"]
    banner_ok = kind == "quota_terminal"
    ok = ok and banner_ok
    print(f"[P0-2] {'PASS' if banner_ok else 'FAIL'}: real agy banner still classified as {kind}")
    return ok


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        tmp = pathlib.Path(raw)
        first = check_p0_1(tmp)
        second = check_p0_2()
    print(f"\nRESULT: P0-1 {'ok' if first else 'BUG'} / P0-2 {'ok' if second else 'BUG'}")
    return 0 if (first and second) else 1


if __name__ == "__main__":
    raise SystemExit(main())
