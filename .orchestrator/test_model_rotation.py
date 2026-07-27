"""Tests for antigravity Gemini<->Claude quota rotation."""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta

import model_rotation as mr
import supervisor as sv

UTC = UTC
CFG = {
    "providers": {
        "antigravity5": {
            "antigravity": {
                "model_rotation": {
                    "enabled": True,
                    "primary_model": "",
                    "fallback_model": "Claude Sonnet 4.6 (Thinking)",
                }
            }
        },
        "antigravity_legacy": {"antigravity": {"model": "StaticModel"}},
    }
}
REAL_ERR = "Error: Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 2h21m32s."


def _isolate(tmp_path):
    mr._STATE_PATH = pathlib.Path(tmp_path) / "cd.json"


def test_fresh_uses_gemini_default(tmp_path):
    _isolate(tmp_path)
    assert mr.active_pool(CFG, "antigravity5") == "gemini"
    assert mr.resolve_active_model(CFG, "antigravity5") == ""  # agy default (Gemini)


def test_rotation_disabled_preserves_static_model(tmp_path):
    _isolate(tmp_path)
    assert mr.resolve_active_model(CFG, "antigravity_legacy") == "StaticModel"


def test_gemini_exhaustion_rotates_to_claude(tmp_path):
    _isolate(tmp_path)
    now = datetime(2026, 7, 11, 6, 0, 0, tzinfo=UTC)
    r = mr.record_exhaustion(CFG, "antigravity5", 900, reason=REAL_ERR, now=now)
    assert r["exhausted_pool"] == "gemini"
    assert r["next_pool"] == "claude"
    assert r["both_exhausted"] is False
    assert mr.resolve_active_model(CFG, "antigravity5", now=now) == "Claude Sonnet 4.6 (Thinking)"


def test_both_pools_exhausted_signals_pause(tmp_path):
    _isolate(tmp_path)
    now = datetime(2026, 7, 11, 6, 0, 0, tzinfo=UTC)
    mr.record_exhaustion(CFG, "antigravity5", 900, now=now)  # gemini -> claude
    r2 = mr.record_exhaustion(CFG, "antigravity5", 900, now=now + timedelta(minutes=1))  # claude too
    assert r2["both_exhausted"] is True


def test_cooldown_expiry_returns_to_gemini(tmp_path):
    _isolate(tmp_path)
    now = datetime(2026, 7, 11, 6, 0, 0, tzinfo=UTC)
    mr.record_exhaustion(CFG, "antigravity5", 900, now=now)
    later = now + timedelta(seconds=901)
    assert mr.active_pool(CFG, "antigravity5", now=later) == "gemini"


def test_reset_hint_parsing():
    assert mr.parse_reset_seconds("Resets in 2h21m32s.") == 2 * 3600 + 21 * 60 + 32
    assert mr.parse_reset_seconds("refresh in 40 minutes") == 2400
    assert mr.parse_reset_seconds("Resets in 45m") == 2700
    assert mr.parse_reset_seconds("no hint") is None


def test_classifier_recognizes_agy_quota_error():
    kind = sv.classify_worker_failure({}, {"provider": "antigravity5"}, REAL_ERR)["kind"]
    assert kind == "quota_terminal"
    assert sv.should_pause_dispatch_for_failure_kind(kind) is True


def test_full_chain_rotates_instead_of_pausing(tmp_path):
    _isolate(tmp_path)
    cfg = dict(CFG)
    cfg["paths"] = {"activity_log": str(pathlib.Path(tmp_path) / "activity.jsonl")}
    kind = sv.classify_worker_failure(cfg, {"provider": "antigravity5"}, REAL_ERR)["kind"]
    state: dict = {}
    paused = sv.mark_provider_dispatch_paused(cfg, state, "antigravity5", REAL_ERR, failure_kind=kind, pause_kind=kind)
    assert paused is False  # rotated, not hard-paused
    assert not (state.get("provider_guardrails", {}).get("dispatch_pauses") or {})
    settings = cfg["providers"]["antigravity5"]["antigravity"]
    assert mr.resolve_active_model(cfg, "antigravity5", settings) == "Claude Sonnet 4.6 (Thinking)"


def test_environmental_failures_do_not_lock_task():
    """Quota/capacity/auth failures must NOT count toward the per-task failure-loop
    streak (they are provider-level outages, not task-agent mismatches)."""
    import supervisor as sv
    st: dict = {}
    w = {"task_id": "ODP-TEST-ENV", "provider": "antigravity"}
    for _ in range(3):
        c = sv.record_task_failure_streak(st, w, "Individual quota reached", failure_kind="quota_terminal")
    assert c == 0
    for _ in range(2):
        c = sv.record_task_failure_streak(st, w, "429", failure_kind="capacity_retryable")
    assert c == 0
    c = sv.record_task_failure_streak(st, w, "unauthorized", failure_kind="auth")
    assert c == 0
    # real task failures still count
    for _ in range(2):
        c = sv.record_task_failure_streak(st, w, "real bug", failure_kind="terminal")
    assert c == 2
