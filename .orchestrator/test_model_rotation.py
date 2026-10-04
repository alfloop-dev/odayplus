"""Tests for antigravity Gemini<->Claude quota rotation."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from unittest import mock

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
                    # Historical explicit pin; do not silently migrate it.
                    "fallback_model": "claude-sonnet-4-6",
                }
            }
        },
        "antigravity_legacy": {"antigravity": {"model": "StaticModel"}},
    }
}
REAL_ERR = "Error: Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 2h21m32s."


def _isolate(tmp_path):
    mr._STATE_PATH = pathlib.Path(tmp_path) / "cd.json"


def test_fresh_uses_standard_high_reasoning_model(tmp_path):
    _isolate(tmp_path)
    assert mr.active_pool(CFG, "antigravity5") == "gemini"
    assert mr.resolve_active_model(CFG, "antigravity5") == "gemini-3.7-flash-high"


def test_rotation_disabled_preserves_static_model(tmp_path):
    _isolate(tmp_path)
    assert mr.resolve_active_model(CFG, "antigravity_legacy") == "StaticModel"


def test_p0_and_sensitive_scope_use_high_risk_model(tmp_path):
    _isolate(tmp_path)
    p0 = mr.resolve_active_selection(
        CFG,
        "antigravity5",
        task={"id": "ODP-CORE-1", "priority": "P0", "artifacts": ["app/service.py"]},
    )
    assert p0["model"] == "claude-opus-5-5-high"
    assert p0["risk_tier"] == "high"
    assert p0["selection_reason"] == "business_priority_P0"

    sensitive = mr.resolve_active_selection(
        CFG,
        "antigravity5",
        task={"id": "ODP-DATA-1", "priority": "P2", "artifacts": ["src/domain/ledger.py"]},
    )
    assert sensitive["model"] == "claude-opus-5-5-high"
    assert sensitive["selection_reason"].startswith("sensitive_scope:")


def test_first_review_reopen_forces_high_risk_model(tmp_path):
    _isolate(tmp_path)
    selection = mr.resolve_active_selection(
        CFG,
        "antigravity5",
        task={"id": "ODP-REOPEN-1", "priority": "P2", "review_reopen_count": 1},
    )
    assert selection["model"] == "claude-opus-5-5-high"
    assert selection["risk_tier"] == "high"
    assert selection["selection_reason"] == "review_reopened_1_time(s)"


def test_sidecar_and_finalize_stay_on_flash_high(tmp_path):
    _isolate(tmp_path)
    sidecar = mr.resolve_active_selection(
        CFG,
        "antigravity5",
        task={"id": "ODP-SIDECAR-1", "priority": "P0", "task_class": "sidecar"},
    )
    assert sidecar["model"] == "gemini-3.7-flash-high"
    assert sidecar["selection_reason"] == "bounded_sidecar_or_finalize"

    finalize = mr.resolve_active_selection(
        CFG,
        "antigravity5",
        task={"id": "ODP-FINAL-1", "priority": "P0", "review_reopen_count": 4},
        reason="owned_finalize_dispatch",
    )
    assert finalize["model"] == "gemini-3.7-flash-high"

    reopened_docs = mr.resolve_active_selection(
        CFG,
        "antigravity5",
        task={
            "id": "ODP-DOCS-1",
            "priority": "P2",
            "artifacts": ["docs/operations.md"],
            "review_reopen_count": 1,
        },
    )
    assert reopened_docs["model"] == "claude-opus-5-5-high"
    assert reopened_docs["selection_reason"] == "review_reopened_1_time(s)"


def test_p0_and_p1_with_docs_preserve_high_risk_priority_precedence(tmp_path):
    _isolate(tmp_path)
    # Live proof regression: P0 task with docs/ artifact must NOT be downgraded to Flash
    live_proof_task = {
        "id": "ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001",
        "title": "Staging lifecycle release integration",
        "priority": "P0",
        "artifacts": [
            ".orchestrator/runtime/lifecycle.py",
            "docs/release-staging-lifecycle.md",
        ],
    }
    selection = mr.resolve_active_selection(CFG, "antigravity5", task=live_proof_task)
    assert selection["model"] == "claude-opus-5-5-high"
    assert selection["risk_tier"] == "high"
    assert selection["selection_reason"] == "business_priority_P0"

    p1_docs_task = {
        "id": "ODP-P1-DOCS-1",
        "title": "P1 critical documentation update",
        "priority": "P1",
        "artifacts": ["docs/architecture.md"],
    }
    p1_selection = mr.resolve_active_selection(CFG, "antigravity5", task=p1_docs_task)
    assert p1_selection["model"] == "claude-opus-5-5-high"
    assert p1_selection["risk_tier"] == "high"
    assert p1_selection["selection_reason"] == "business_priority_P1"


def test_p2_docs_only_uses_standard_model(tmp_path):
    _isolate(tmp_path)
    docs_only = {
        "id": "ODP-DOCS-ONLY-1",
        "title": "Update operational documentation",
        "priority": "P2",
        "artifacts": ["docs/operations.md", "docs/guide.md"],
    }
    selection = mr.resolve_active_selection(CFG, "antigravity5", task=docs_only)
    assert selection["model"] == "gemini-3.7-flash-high"
    assert selection["risk_tier"] == "standard"
    assert selection["selection_reason"] == "bounded_docs_or_lint"


def test_mixed_workflow_and_docs_uses_high_risk_model(tmp_path):
    _isolate(tmp_path)
    # Mixed workflow + docs must be treated as sensitive high-risk scope
    workflow_docs = {
        "id": "ODP-WORKFLOW-DOCS-1",
        "title": "CI workflow automation update",
        "priority": "P2",
        "artifacts": [
            ".github/workflows/deploy.yml",
            "docs/deploy-guide.md",
        ],
    }
    selection = mr.resolve_active_selection(CFG, "antigravity5", task=workflow_docs)
    assert selection["model"] == "claude-opus-5-5-high"
    assert selection["risk_tier"] == "high"
    assert selection["selection_reason"] == "sensitive_scope:workflow"

    iac_docs = {
        "id": "ODP-IAC-DOCS-1",
        "title": "Terraform IaC deployment scripts",
        "priority": "P2",
        "artifacts": [
            "infra/iac/main.tf",
            "docs/infrastructure.md",
        ],
    }
    iac_selection = mr.resolve_active_selection(CFG, "antigravity5", task=iac_docs)
    assert iac_selection["model"] == "claude-opus-5-5-high"
    assert iac_selection["risk_tier"] == "high"
    assert iac_selection["selection_reason"] == "sensitive_scope:iac"


def test_reopen_with_docs_preserves_high_risk_reopen_precedence(tmp_path):
    _isolate(tmp_path)
    reopen_task = {
        "id": "ODP-REOPEN-DOCS-1",
        "title": "Fix rejected documentation PR",
        "priority": "P2",
        "review_reopen_count": 2,
        "artifacts": ["docs/release.md"],
    }
    selection = mr.resolve_active_selection(CFG, "antigravity5", task=reopen_task)
    assert selection["model"] == "claude-opus-5-5-high"
    assert selection["risk_tier"] == "high"
    assert selection["selection_reason"] == "review_reopened_2_time(s)"


def test_summary_zh_and_summary_compatibility_in_task_corpus(tmp_path):
    _isolate(tmp_path)
    zh_sensitive_task = {
        "id": "ODP-ZH-1",
        "title": "更新服務設定",
        "summary_zh": "重構 core/ 核心資料庫 schema 遷移流程與權限檢查",
        "priority": "P2",
        "artifacts": ["docs/changelog.md"],
    }
    selection = mr.resolve_active_selection(CFG, "antigravity5", task=zh_sensitive_task)
    assert selection["model"] == "claude-opus-5-5-high"
    assert selection["risk_tier"] == "high"
    assert selection["selection_reason"] == "sensitive_scope:core/"

    zh_docs_task = {
        "id": "ODP-ZH-DOCS-1",
        "title": "純繁中文件修訂",
        "summary_zh": "格式整理與 docs/ 文件更新",
        "priority": "P2",
        "artifacts": ["docs/readme.md"],
    }
    docs_selection = mr.resolve_active_selection(CFG, "antigravity5", task=zh_docs_task)
    assert docs_selection["model"] == "gemini-3.7-flash-high"
    assert docs_selection["risk_tier"] == "standard"
    assert docs_selection["selection_reason"] == "bounded_docs_or_lint"


def test_quota_rotation_preserves_high_risk_claude_policy_and_audit_reason(tmp_path):
    _isolate(tmp_path)
    mr.record_exhaustion(CFG, "antigravity5", 900, pool="gemini")
    selection = mr.resolve_active_selection(
        CFG,
        "antigravity5",
        task={"id": "ODP-P0-1", "priority": "P0"},
    )
    assert selection["pool"] == "claude"
    assert selection["model"] == "claude-opus-5-5-high"
    assert selection["risk_tier"] == "high"
    assert selection["selection_reason"] == "quota_pool_fallback:business_priority_P0"


def test_gemini_exhaustion_rotates_to_claude(tmp_path):
    _isolate(tmp_path)
    now = datetime(2026, 7, 11, 6, 0, 0, tzinfo=UTC)
    r = mr.record_exhaustion(CFG, "antigravity5", 900, reason=REAL_ERR, now=now)
    assert r["exhausted_pool"] == "gemini"
    assert r["next_pool"] == "claude"
    assert r["both_exhausted"] is False
    assert mr.resolve_active_model(CFG, "antigravity5", now=now) == "claude-sonnet-4-6"


def test_both_pools_exhausted_signals_pause(tmp_path):
    _isolate(tmp_path)
    now = datetime(2026, 7, 11, 6, 0, 0, tzinfo=UTC)
    mr.record_exhaustion(CFG, "antigravity5", 900, now=now)  # gemini -> claude
    r2 = mr.record_exhaustion(
        CFG, "antigravity5", 900, now=now + timedelta(minutes=1)
    )  # claude too
    assert r2["both_exhausted"] is True


def test_cooldown_expiry_returns_to_gemini(tmp_path):
    _isolate(tmp_path)
    now = datetime(2026, 7, 11, 6, 0, 0, tzinfo=UTC)
    mr.record_exhaustion(CFG, "antigravity5", 900, now=now)
    later = now + timedelta(seconds=901)
    assert mr.active_pool(CFG, "antigravity5", now=later) == "gemini"


def test_shared_account_alias_skips_gemini_until_authoritative_reset(tmp_path):
    _isolate(tmp_path)
    now = datetime(2026, 7, 28, 11, 36, 35, tzinfo=UTC)
    cfg = {
        "providers": {
            alias: {
                "quota_group": "antigravity-shared",
                "antigravity": {
                    "model_rotation": CFG["providers"]["antigravity5"]["antigravity"][
                        "model_rotation"
                    ]
                },
            }
            for alias in ("antigravity", "antigravity2")
        }
    }
    reset = mr.parse_reset_seconds("Individual quota reached. Resets in 4h13m28s.")
    mr.record_exhaustion(cfg, "antigravity2", reset or 0, pool="gemini", now=now)

    # Successful Claude completion and task lifecycle transitions do not touch
    # the durable account cooldown; another logical alias selects Claude too.
    before_reset = now + timedelta(hours=4)
    assert mr.resolve_active_selection(cfg, "antigravity", now=before_reset)["pool"] == "claude"
    assert mr.resolve_active_selection(cfg, "antigravity2", now=before_reset)["pool"] == "claude"
    after_reset = now + timedelta(hours=4, minutes=13, seconds=29)
    assert mr.resolve_active_selection(cfg, "antigravity", now=after_reset)["pool"] == "gemini"


def test_distinct_profiles_do_not_share_account_cooldown(tmp_path):
    _isolate(tmp_path)
    cfg = {
        "providers": {
            alias: {
                "antigravity": {
                    "config_home": home,
                    "model_rotation": {"enabled": True},
                }
            }
            for alias, home in (("antigravity5", "/profiles/a"), ("antigravity6", "/profiles/b"))
        }
    }
    mr.record_exhaustion(cfg, "antigravity5", 900, pool="gemini")
    assert mr.resolve_active_selection(cfg, "antigravity5")["pool"] == "claude"
    assert mr.resolve_active_selection(cfg, "antigravity6")["pool"] == "gemini"


def test_status_root_is_the_canonical_state_authority(tmp_path, monkeypatch):
    status_root = pathlib.Path(tmp_path) / "canonical-status"
    legacy = pathlib.Path(tmp_path) / "runtime-a" / ".orchestrator" / "runtime" / mr._STATE_FILENAME
    monkeypatch.setenv("ORCH_STATUS_ROOT", str(status_root))
    monkeypatch.setenv("PANTHEON_STATUS_ROOT", str(status_root))
    monkeypatch.setattr(mr, "_STATE_PATH", mr._DEFAULT_STATE_PATH)
    monkeypatch.setattr(mr, "_LEGACY_STATE_PATH", legacy)

    canonical = status_root / ".orchestrator" / "runtime" / mr._STATE_FILENAME
    assert mr.canonical_state_path() == canonical

    now = datetime(2026, 8, 1, 6, 0, 0, tzinfo=UTC)
    mr.record_exhaustion(CFG, "antigravity5", 900, pool="gemini", now=now)
    assert canonical.exists()
    assert canonical.parent == status_root / ".orchestrator" / "runtime"
    assert not legacy.exists()
    assert canonical.with_name(canonical.name + mr._MIGRATION_MARKER_SUFFIX).exists()


def test_legacy_migration_merges_later_expiry_and_stops_reading_legacy(tmp_path, monkeypatch):
    status_root = pathlib.Path(tmp_path) / "canonical-status"
    canonical = status_root / ".orchestrator" / "runtime" / mr._STATE_FILENAME
    legacy = pathlib.Path(tmp_path) / "runtime-a" / ".orchestrator" / "runtime" / mr._STATE_FILENAME
    canonical.parent.mkdir(parents=True)
    legacy.parent.mkdir(parents=True)
    monkeypatch.setattr(mr, "_STATE_PATH", canonical)
    monkeypatch.setattr(mr, "_LEGACY_STATE_PATH", legacy)

    now = datetime(2026, 8, 1, 6, 0, 0, tzinfo=UTC)
    scope = mr.cooldown_scope(CFG, "antigravity5")
    canonical.write_text(
        json.dumps(
            {
                scope: {
                    "gemini_until": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                    "claude_until": (now + timedelta(minutes=12))
                    .isoformat()
                    .replace("+00:00", "Z"),
                }
            }
        ),
        encoding="utf-8",
    )
    legacy.write_text(
        json.dumps(
            {
                scope: {
                    "gemini_until": (now + timedelta(minutes=10))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "claude_until": (now + timedelta(minutes=8)).isoformat().replace("+00:00", "Z"),
                }
            }
        ),
        encoding="utf-8",
    )

    assert mr.active_pool(CFG, "antigravity5", now=now) is None
    migrated = json.loads(canonical.read_text(encoding="utf-8"))[scope]
    assert migrated["gemini_until"] == (now + timedelta(minutes=10)).isoformat().replace(
        "+00:00", "Z"
    )
    assert migrated["claude_until"] == (now + timedelta(minutes=12)).isoformat().replace(
        "+00:00", "Z"
    )

    # Once the canonical marker exists, a later legacy rewrite cannot change
    # the authority or resurrect a stale runtime's view of the cooldown.
    legacy.write_text(
        json.dumps(
            {scope: {"gemini_until": (now + timedelta(days=1)).isoformat().replace("+00:00", "Z")}}
        ),
        encoding="utf-8",
    )
    assert mr.active_pool(CFG, "antigravity5", now=now + timedelta(minutes=11)) == "gemini"
    persisted = json.loads(canonical.read_text(encoding="utf-8"))[scope]
    assert persisted["gemini_until"] == (now + timedelta(minutes=10)).isoformat().replace(
        "+00:00", "Z"
    )


def test_malformed_legacy_state_is_fail_safe_and_not_migrated(tmp_path, monkeypatch):
    canonical = pathlib.Path(tmp_path) / "canonical" / mr._STATE_FILENAME
    legacy = pathlib.Path(tmp_path) / "runtime-a" / ".orchestrator" / "runtime" / mr._STATE_FILENAME
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{malformed", encoding="utf-8")
    monkeypatch.setattr(mr, "_STATE_PATH", canonical)
    monkeypatch.setattr(mr, "_LEGACY_STATE_PATH", legacy)

    now = datetime(2026, 8, 1, 6, 0, 0, tzinfo=UTC)
    assert mr.active_pool(CFG, "antigravity5", now=now) == "gemini"
    assert json.loads(canonical.read_text(encoding="utf-8")) == {}

    # The first read sealed the migration decision. A valid file appearing in
    # the old runtime later must not become a second state authority.
    scope = mr.cooldown_scope(CFG, "antigravity5")
    legacy.write_text(
        json.dumps(
            {scope: {"gemini_until": (now + timedelta(days=1)).isoformat().replace("+00:00", "Z")}}
        ),
        encoding="utf-8",
    )
    assert mr.active_pool(CFG, "antigravity5", now=now) == "gemini"


def test_malformed_canonical_state_does_not_fallback_to_legacy(tmp_path, monkeypatch):
    canonical = pathlib.Path(tmp_path) / "canonical" / mr._STATE_FILENAME
    legacy = pathlib.Path(tmp_path) / "runtime-a" / ".orchestrator" / "runtime" / mr._STATE_FILENAME
    canonical.parent.mkdir(parents=True)
    legacy.parent.mkdir(parents=True)
    canonical.write_text("{malformed", encoding="utf-8")
    scope = mr.cooldown_scope(CFG, "antigravity5")
    legacy.write_text(
        json.dumps({scope: {"gemini_until": "2099-01-01T00:00:00Z"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mr, "_STATE_PATH", canonical)
    monkeypatch.setattr(mr, "_LEGACY_STATE_PATH", legacy)

    assert mr.active_pool(CFG, "antigravity5") == "gemini"
    assert canonical.read_text(encoding="utf-8") == "{malformed"


def test_different_runtime_roots_share_one_status_root(tmp_path):
    status_root = pathlib.Path(tmp_path) / "canonical-status"
    source = pathlib.Path(mr.__file__).resolve()
    runtime_modules = []
    for runtime_name in ("runtime-a", "runtime-b"):
        module_dir = pathlib.Path(tmp_path) / runtime_name / ".orchestrator"
        module_dir.mkdir(parents=True)
        shutil.copy2(source, module_dir / "model_rotation.py")
        runtime_modules.append(module_dir)

    script = """
import json
import sys
from datetime import UTC, datetime

sys.path.insert(0, sys.argv[1])
import model_rotation as mr

config = {"providers": {"antigravity5": {"antigravity": {"model_rotation": {"enabled": True, "fallback_model": "claude-fallback"}}}}}
now = datetime(2026, 8, 1, 6, 0, 0, tzinfo=UTC)
if sys.argv[2] == "record":
    result = mr.record_exhaustion(config, "antigravity5", 900, pool="gemini", now=now)
else:
    result = mr.resolve_active_selection(config, "antigravity5", now=now)
print(json.dumps(result))
"""
    env = os.environ.copy()
    env["ORCH_STATUS_ROOT"] = str(status_root)
    env["PANTHEON_STATUS_ROOT"] = str(status_root)

    first = subprocess.run(
        [sys.executable, "-c", script, str(runtime_modules[0]), "record"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert json.loads(first.stdout)["next_pool"] == "claude"

    second = subprocess.run(
        [sys.executable, "-c", script, str(runtime_modules[1]), "select"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert json.loads(second.stdout)["pool"] == "claude"
    canonical = status_root / ".orchestrator" / "runtime" / mr._STATE_FILENAME
    assert canonical.exists()
    assert not (
        pathlib.Path(tmp_path) / "runtime-b" / ".orchestrator" / "runtime" / mr._STATE_FILENAME
    ).exists()


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
    paused = sv.mark_provider_dispatch_paused(
        cfg, state, "antigravity5", REAL_ERR, failure_kind=kind, pause_kind=kind
    )
    assert paused is False  # rotated, not hard-paused
    assert not (state.get("provider_guardrails", {}).get("dispatch_pauses") or {})
    settings = cfg["providers"]["antigravity5"]["antigravity"]
    assert mr.resolve_active_model(cfg, "antigravity5", settings) == "claude-sonnet-4-6"
    assert sv.antigravity_pool_fallback_available(cfg, "antigravity5") is True
    entry = mr.status("antigravity5")["antigravity5"]
    until = datetime.fromisoformat(entry["gemini_until"].replace("Z", "+00:00"))
    # The 2h21m32s reset hint, rather than the old 15-minute probe, is durable.
    assert until - datetime.now(UTC) > timedelta(hours=2, minutes=20)


def test_same_worker_failure_rotates_only_once(tmp_path):
    _isolate(tmp_path)
    cfg = dict(CFG)
    cfg["paths"] = {"activity_log": str(pathlib.Path(tmp_path) / "activity.jsonl")}
    worker = _worker("run-once", "gemini")
    state: dict = {"workers": {"run-once": worker}}

    for _ in range(2):
        paused = sv.mark_provider_dispatch_paused(
            cfg,
            state,
            "antigravity5",
            REAL_ERR,
            worker_run_id="run-once",
            failure_kind="quota_terminal",
            pause_kind="quota_terminal",
            worker=worker,
        )
        assert paused is False

    entry = mr.status("antigravity5")["antigravity5"]
    assert entry.get("gemini_until")
    assert entry.get("claude_until") is None
    assert list(state["provider_guardrails"]["processed_model_rotation_failures"]) == ["run-once"]


def _worker(run_id: str, pool: str | None, *, task_id: str = "ODP-TEST-ROT") -> dict:
    """Worker record as `start_worker_for_request` writes it (pool in metadata)."""
    return {
        "run_id": run_id,
        "provider": "antigravity5",
        "agent_id": "antigravity5",
        "task_id": task_id,
        "metadata": {mr.WORKER_POOL_KEY: pool},
        mr.WORKER_POOL_KEY: pool,
    }


# --- P0-1: dispatched-pool binding under concurrency -------------------------


def test_dispatched_pool_overrides_current_active_pool(tmp_path):
    """A worker launched on Gemini cools Gemini even after rotation moved on."""
    _isolate(tmp_path)
    now = datetime(2026, 7, 27, 6, 0, 0, tzinfo=UTC)
    first = mr.record_exhaustion(CFG, "antigravity5", 900, pool="gemini", now=now)
    assert first["exhausted_pool"] == "gemini"
    assert first["pool_source"] == "dispatched"
    # Active pool is now claude, but this stale worker also ran on gemini.
    assert mr.active_pool(CFG, "antigravity5", now=now) == "claude"
    second = mr.record_exhaustion(
        CFG, "antigravity5", 900, pool="gemini", now=now + timedelta(seconds=30)
    )
    assert second["exhausted_pool"] == "gemini"
    assert second["next_pool"] == "claude"
    assert second["both_exhausted"] is False


def test_worker_dispatched_pool_reads_metadata_and_mirror():
    assert mr.worker_dispatched_pool(_worker("w1", "gemini")) == "gemini"
    assert mr.worker_dispatched_pool({"metadata": {mr.WORKER_POOL_KEY: "CLAUDE"}}) == "claude"
    assert mr.worker_dispatched_pool({mr.WORKER_POOL_KEY: "claude"}) == "claude"
    assert mr.worker_dispatched_pool({"metadata": {}}) is None  # legacy record
    assert mr.worker_dispatched_pool({"metadata": {mr.WORKER_POOL_KEY: "bogus"}}) is None
    assert mr.worker_dispatched_pool(None) is None


def test_selection_reports_pool_and_model(tmp_path):
    _isolate(tmp_path)
    now = datetime(2026, 7, 27, 6, 0, 0, tzinfo=UTC)
    fresh = mr.resolve_active_selection(CFG, "antigravity5", now=now)
    assert fresh["pool"] == "gemini"
    assert fresh["model"] == "gemini-3.7-flash-high"
    assert fresh["rotating"] is True
    assert fresh["risk_tier"] == "standard"
    mr.record_exhaustion(CFG, "antigravity5", 900, pool="gemini", now=now)
    rotated = mr.resolve_active_selection(CFG, "antigravity5", now=now)
    assert rotated["pool"] == "claude"
    assert rotated["model"] == "claude-sonnet-4-6"
    # After cooldown the provider returns to the primary policy (agy default).
    back = mr.resolve_active_selection(CFG, "antigravity5", now=now + timedelta(seconds=901))
    assert back["pool"] == "gemini"
    assert back["model"] == "gemini-3.7-flash-high"
    # Rotation-disabled providers report no pool and keep the static model.
    legacy = mr.resolve_active_selection(CFG, "antigravity_legacy", now=now)
    assert legacy["pool"] is None
    assert legacy["model"] == "StaticModel"
    assert legacy["rotating"] is False
    assert legacy["selection_reason"] == "model_policy_disabled"


def test_two_concurrent_gemini_workers_never_exhaust_claude(tmp_path):
    """Regression: stale worker B (also on Gemini) must not be recorded against
    Claude and hard-pause the provider."""
    _isolate(tmp_path)
    cfg = dict(CFG)
    cfg["paths"] = {"activity_log": str(pathlib.Path(tmp_path) / "activity.jsonl")}
    worker_a = _worker("run-a", "gemini", task_id="ODP-TEST-A")
    worker_b = _worker("run-b", "gemini", task_id="ODP-TEST-B")
    state: dict = {"workers": {"run-a": worker_a, "run-b": worker_b}}

    kind = sv.classify_worker_failure(cfg, worker_a, REAL_ERR)["kind"]
    assert kind == "quota_terminal"
    for worker in (worker_a, worker_b):
        paused = sv.mark_provider_dispatch_paused(
            cfg,
            state,
            "antigravity5",
            REAL_ERR,
            task_id=str(worker["task_id"]),
            worker_run_id=str(worker["run_id"]),
            failure_kind=kind,
            pause_kind=kind,
            worker=worker,
        )
        assert paused is False  # rotated, never hard-paused

    assert not (state.get("provider_guardrails", {}).get("dispatch_pauses") or {})
    entry = mr.status("antigravity5")["antigravity5"]
    assert entry.get("gemini_until")
    assert entry.get("claude_until") is None  # Claude pool untouched
    assert mr.resolve_active_selection(cfg, "antigravity5")["pool"] == "claude"


def test_claude_worker_failure_after_gemini_cooldown_pauses_for_real(tmp_path):
    """Both pools genuinely exhausted still falls through to a real pause."""
    _isolate(tmp_path)
    cfg = dict(CFG)
    cfg["paths"] = {"activity_log": str(pathlib.Path(tmp_path) / "activity.jsonl")}
    gemini_worker = _worker("run-g", "gemini")
    claude_worker = _worker("run-c", "claude")
    state: dict = {"workers": {"run-g": gemini_worker, "run-c": claude_worker}}
    sv.mark_provider_dispatch_paused(
        cfg,
        state,
        "antigravity5",
        REAL_ERR,
        failure_kind="quota_terminal",
        pause_kind="quota_terminal",
        worker=gemini_worker,
    )
    paused = sv.mark_provider_dispatch_paused(
        cfg,
        state,
        "antigravity5",
        REAL_ERR,
        failure_kind="quota_terminal",
        pause_kind="quota_terminal",
        worker=claude_worker,
    )
    assert paused is True
    assert state["provider_guardrails"]["dispatch_pauses"]["antigravity5"]["blocked_until"]


def test_pool_falls_back_to_state_lookup_and_inference(tmp_path):
    """No worker object passed -> resolve from state; unknown pool -> inference."""
    _isolate(tmp_path)
    cfg = dict(CFG)
    cfg["paths"] = {"activity_log": str(pathlib.Path(tmp_path) / "activity.jsonl")}
    state: dict = {"workers": {"run-a": _worker("run-a", "gemini")}}
    sv.mark_provider_dispatch_paused(
        cfg,
        state,
        "antigravity5",
        REAL_ERR,
        worker_run_id="run-a",
        failure_kind="quota_terminal",
        pause_kind="quota_terminal",
    )
    entry = mr.status("antigravity5")["antigravity5"]
    assert entry.get("gemini_until") and entry.get("claude_until") is None
    # Legacy worker with no recorded pool: fall back to the active pool (claude).
    inferred = mr.record_exhaustion(cfg, "antigravity5", 900, pool=None)
    assert inferred["exhausted_pool"] == "claude"
    assert inferred["pool_source"] == "inferred"


# --- P0-2: quota classifier must not swallow ordinary failures ---------------


ORDINARY_FAILURES_MENTIONING_QUOTA = (
    "AssertionError: expected quota reached banner to be hidden",
    "TypeError: quota reached handler returned None",
    "Error: assertion failed in quota reached state transition",
    "FAILED tests/test_billing.py::test_individual_quota_reached_banner_renders",
    "ValueError: please upgrade your subscription to increase your limits copy is stale",
)


def test_ordinary_failures_mentioning_quota_stay_terminal():
    for reason in ORDINARY_FAILURES_MENTIONING_QUOTA:
        failure = sv.classify_worker_failure(CFG, {"provider": "antigravity5"}, reason)
        assert failure["kind"] == "terminal", reason
        assert sv.should_pause_dispatch_for_failure_kind(failure["kind"]) is False, reason


def test_ordinary_quota_wording_still_increments_task_streak():
    """The masked-real-failure regression: these must keep counting."""
    for reason in ORDINARY_FAILURES_MENTIONING_QUOTA:
        state: dict = {}
        worker = {"task_id": "ODP-TEST-REAL", "provider": "antigravity5"}
        kind = sv.classify_worker_failure(CFG, worker, reason)["kind"]
        count = 0
        for _ in range(2):
            count = sv.record_task_failure_streak(state, worker, reason, failure_kind=kind)
        assert count == 2, reason


def test_agy_quota_banner_requires_antigravity_provider():
    assert (
        sv.classify_worker_failure(CFG, {"provider": "antigravity5"}, REAL_ERR)["kind"]
        == "quota_terminal"
    )
    # Same text from a non-agy provider is not the agy banner.
    assert sv.classify_worker_failure(CFG, {"provider": "claude"}, REAL_ERR)["kind"] == "terminal"
    # Provider identified through config rather than the id prefix.
    cfg = {"providers": {"vendorx": {"adapter": "antigravity"}}}
    assert (
        sv.classify_worker_failure(cfg, {"provider": "vendorx"}, REAL_ERR)["kind"]
        == "quota_terminal"
    )


def test_agy_quota_banner_variants_are_classified():
    variants = (
        # Verbatim reasons observed in the live .orchestrator/state.json for the
        # antigravity providers: every real banner carries the upgrade/reset
        # continuation the signature requires.
        "Error: Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 10m26s.",
        "Error: Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 4h47m7s.",
        "Error: Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 4m57s.",
        "Error: Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 2h21m32s.",
        "Individual quota reached. Resets in 41h23m",
        "individual quota reached - try again in 30 minutes",
    )
    for reason in variants:
        assert (
            sv.classify_worker_failure(CFG, {"provider": "antigravity5"}, reason)["kind"]
            == "quota_terminal"
        ), reason


def test_generic_provider_quota_markers_still_classified():
    """Non-agy provider quota text keeps its existing classification."""
    for reason in (
        "Status: 402 credit balance is too low",
        "You have no quota",
        "quota exceeded",
        "[API Error: Helper OAuth free tier quota exceeded.]",
        "free tier quota exceeded",
    ):
        assert (
            sv.classify_worker_failure(CFG, {"provider": "claude"}, reason)["kind"]
            == "quota_terminal"
        ), reason


def test_cloud_run_quota_exceeded_stays_terminal_not_provider_quota():
    """Non-provider Cloud Run quota error strings must not trigger provider quota pause."""
    for provider in ("codex", "claude", "antigravity5", "copilot", "gemini"):
        assert (
            sv.classify_worker_failure(CFG, {"provider": provider}, "Cloud Run API quota exceeded")[
                "kind"
            ]
            == "terminal"
        )
        result = sv.classify_worker_failure(
            CFG,
            {"provider": provider},
            "429 Quota exceeded for quota metric 'Cloud Run API quota exceeded'",
        )
        assert result["kind"] != "quota_terminal"
        assert sv.should_pause_dispatch_for_failure_kind(result["kind"]) is False


# The verbatim banner captured from the live synthetic assistant message that
# drove task_failure_streaks["ODP-STORE-OPENING-001:claude"] to count=34.
CLAUDE_SESSION_LIMIT = "You've hit your session limit \u00b7 resets 5pm (UTC)"


def test_claude_session_limit_is_quota_not_terminal():
    """The extra "session" token made this miss "hit your limit" entirely.

    Misclassifying it as `terminal` skipped BOTH the provider pause path and the
    environmental-failure exemption, so every retry inside one session-limit
    window incremented the per-task streak.
    """
    result = sv.classify_worker_failure(CFG, {"provider": "claude"}, CLAUDE_SESSION_LIMIT)
    assert result["kind"] == "quota_terminal"
    assert sv.should_pause_dispatch_for_failure_kind(result["kind"]) is True


def test_claude_session_limit_does_not_increment_task_streak():
    state: dict = {}
    kind = sv.classify_worker_failure(CFG, {"provider": "claude"}, CLAUDE_SESSION_LIMIT)["kind"]
    worker = {"task_id": "ODP-SESSION-LIMIT-TEST", "provider": "claude"}
    for _ in range(3):
        count = sv.record_task_failure_streak(
            state, worker, CLAUDE_SESSION_LIMIT, failure_kind=kind
        )
    assert count == 0
    # A genuine task failure on the same provider still counts.
    assert (
        sv.record_task_failure_streak(
            state, worker, "TypeError: undefined is not a function", failure_kind="terminal"
        )
        == 1
    )


def test_claude_session_limit_banner_is_provider_scoped():
    """Only Claude providers may read this text as a quota outage."""
    assert (
        sv.classify_worker_failure(CFG, {"provider": "codex"}, CLAUDE_SESSION_LIMIT)["kind"]
        == "terminal"
    )
    # Provider identified through config rather than the id prefix.
    cfg = {"providers": {"vendory": {"adapter": "claude_cli"}}}
    assert (
        sv.classify_worker_failure(cfg, {"provider": "vendory"}, CLAUDE_SESSION_LIMIT)["kind"]
        == "quota_terminal"
    )


def test_claude_session_limit_banner_variants_are_classified():
    """Real banner forms: the phrase plus its reset continuation."""
    variants = (
        CLAUDE_SESSION_LIMIT,  # verbatim, "\u00b7" separator
        "You've hit your session limit - resets 5pm (UTC)",
        "You have hit your session limit, resets at 17:00 UTC",
        "hit your session limit. try again in 2 hours",
    )
    for reason in variants:
        assert (
            sv.classify_worker_failure(CFG, {"provider": "claude"}, reason)["kind"]
            == "quota_terminal"
        ), reason


def test_exact_trigger_phrase_in_task_output_stays_a_task_failure():
    """The EXACT banner phrase embedded in application/assertion output.

    Provider scoping cannot separate these from the real banner - a Claude
    worker reports its own test output too - so the classifier requires the
    banner's reset continuation. Without that, these genuine task failures were
    silently converted into quota outages (blocking review finding on #472).
    """
    task_failures = (
        "AssertionError: expected You've hit your session limit banner to be hidden",
        "FAILED test_copy.py: rendered text You've hit your session limit unexpectedly",
        'Playwright: locator("text=You\'ve hit your session limit") resolved to 0 elements',
        "AssertionError: expected the session limit banner to be hidden",
    )
    for reason in task_failures:
        assert (
            sv.classify_worker_failure(CFG, {"provider": "claude"}, reason)["kind"] == "terminal"
        ), reason


def test_exact_trigger_phrase_in_task_output_still_increments_streak():
    """These are real failures, so the failure-loop guard must keep counting them."""
    state: dict = {}
    worker = {"task_id": "ODP-SESSION-PHRASE-TEST", "provider": "claude"}
    reason = "AssertionError: expected You've hit your session limit banner to be hidden"
    kind = sv.classify_worker_failure(CFG, {"provider": "claude"}, reason)["kind"]
    assert kind == "terminal"
    counts = [
        sv.record_task_failure_streak(state, worker, reason, failure_kind=kind) for _ in range(3)
    ]
    assert counts == [1, 2, 3]


# --- adapter: dispatch-time pool persistence, argv safety, profile isolation --


def _adapter_config(tmp_path) -> dict:
    return {
        "paths": {"status_file": str(pathlib.Path(tmp_path) / "ai-status.json")},
        "agents": {
            "antigravity5": {
                "id": "antigravity5",
                "display_name": "Antigravity5",
                "provider": "antigravity5",
                "adapter": "antigravity",
            }
        },
        "providers": {
            "antigravity5": {
                "antigravity": {
                    "cli": "agy",
                    "config_home": str(pathlib.Path(tmp_path) / "home-ag5"),
                    "model_rotation": {
                        "enabled": True,
                        "primary_model": "",
                        "fallback_model": "claude-sonnet-5-5-high",
                    },
                }
            }
        },
    }


def _deliver(config, tmp_path, *, task=None, reason=None):
    from adapters.antigravity import AntigravityAdapter
    from adapters.base import DeliveryRequest

    request = DeliveryRequest(
        agent_id="antigravity5",
        provider="antigravity5",
        delivery_mode="antigravity",
        message="wake up",
        task_id="ODP-TEST-ROT",
        reason=reason,
        metadata={"task": task or {}},
    )
    process = mock.Mock()
    process.pid = 4321
    with (
        mock.patch("adapters.antigravity.configured_provider_binary", return_value="/usr/bin/agy"),
        mock.patch("adapters.antigravity._auth_ready", return_value=True),
        mock.patch(
            "adapters.antigravity.delivery_workspace_root", return_value=pathlib.Path(tmp_path)
        ),
        mock.patch(
            "adapters.base.runtime_log_path", return_value=pathlib.Path(tmp_path) / "agy.log"
        ),
        mock.patch("adapters.base.new_runtime_id", return_value="antigravity5-test"),
        mock.patch(
            "adapters.base.worker_runtime_paths",
            return_value={
                "heartbeat_path": pathlib.Path(tmp_path) / "hb.json",
                "status_path": pathlib.Path(tmp_path) / "st.json",
            },
        ),
        mock.patch(
            "adapters.base.spawn_background_process",
            return_value=(process, pathlib.Path(tmp_path) / "agy.log"),
        ) as spawn,
    ):
        result = AntigravityAdapter(config=config, provider_capabilities={}).deliver(request)
    return result, spawn


def test_adapter_persists_dispatched_pool_in_worker_metadata(tmp_path):
    _isolate(tmp_path)
    config = _adapter_config(tmp_path)
    result, spawn = _deliver(config, tmp_path)
    assert result.ok
    assert result.metadata[mr.WORKER_POOL_KEY] == "gemini"
    assert result.metadata[mr.WORKER_MODEL_KEY] == "gemini-3.7-flash-high"
    assert result.metadata[mr.WORKER_MODEL_RISK_TIER_KEY] == "standard"
    assert result.metadata[mr.WORKER_MODEL_REASON_KEY] == "ordinary_single_module_or_unclassified"
    assert (
        spawn.call_args.args[0][spawn.call_args.args[0].index("--model") + 1]
        == "gemini-3.7-flash-high"
    )
    command = spawn.call_args.args[0]
    assert command[command.index("--print-timeout") + 1] == "2h"

    mr.record_exhaustion(config, "antigravity5", 900, pool="gemini")
    result, spawn = _deliver(config, tmp_path)
    assert result.metadata[mr.WORKER_POOL_KEY] == "claude"
    assert result.metadata[mr.WORKER_MODEL_KEY] == "claude-sonnet-5-5-high"
    command = spawn.call_args.args[0]
    # Structured argv: the model id stays ONE argument
    # and is never interpolated into a shell string.
    assert command[command.index("--model") + 1] == "claude-sonnet-5-5-high"


def test_adapter_selects_high_risk_model_from_dispatched_task_snapshot(tmp_path):
    _isolate(tmp_path)
    config = _adapter_config(tmp_path)
    result, spawn = _deliver(
        config,
        tmp_path,
        task={"id": "ODP-RBAC-1", "priority": "P0", "artifacts": ["src/rbac/policy.py"]},
        reason="owned_ready_dispatch",
    )

    assert result.ok
    assert result.metadata[mr.WORKER_MODEL_KEY] == "claude-opus-5-5-high"
    assert result.metadata[mr.WORKER_MODEL_RISK_TIER_KEY] == "high"
    assert result.metadata[mr.WORKER_MODEL_REASON_KEY] == "business_priority_P0"
    command = spawn.call_args.args[0]
    assert command[command.index("--model") + 1] == "claude-opus-5-5-high"
    assert all(isinstance(part, str) for part in command)
    assert spawn.call_args.kwargs.get("env", {}).get("HOME") == str(
        pathlib.Path(tmp_path) / "home-ag5"
    )
    assert not any(
        part.strip().startswith("&&") or ";" in part for part in command if part != "wake up"
    )


def test_adapter_hard_print_timeout_wins_and_legacy_key_remains_compatible(tmp_path):
    _isolate(tmp_path)
    config = _adapter_config(tmp_path)
    settings = config["providers"]["antigravity5"]["antigravity"]
    settings["print_timeout"] = "2h"
    settings["hard_print_timeout"] = "24h"
    _, spawn = _deliver(config, tmp_path)
    command = spawn.call_args.args[0]
    assert command[command.index("--print-timeout") + 1] == "24h"

    settings.pop("hard_print_timeout")
    _, spawn = _deliver(config, tmp_path)
    command = spawn.call_args.args[0]
    assert command[command.index("--print-timeout") + 1] == "2h"

    settings.pop("print_timeout", None)
    _, spawn = _deliver(config, tmp_path)
    command = spawn.call_args.args[0]
    assert command[command.index("--print-timeout") + 1] == "2h"


def test_adapter_default_hard_print_timeout_is_2h(tmp_path):
    _isolate(tmp_path)
    config = _adapter_config(tmp_path)
    _, spawn = _deliver(config, tmp_path)
    command = spawn.call_args.args[0]
    assert "--print-timeout" in command
    assert command[command.index("--print-timeout") + 1] == "2h"


def test_rotation_does_not_leak_credentials_across_providers(tmp_path):
    """Rotating the model must not change which HOME/profile agy authenticates with."""
    _isolate(tmp_path)
    config = _adapter_config(tmp_path)
    other_home = str(pathlib.Path(tmp_path) / "home-other")
    config["providers"]["antigravity6"] = {
        "antigravity": {
            "cli": "agy",
            "config_home": other_home,
            "model_rotation": {"enabled": True},
        }
    }
    mr.record_exhaustion(config, "antigravity5", 900, pool="gemini")
    _, spawn = _deliver(config, tmp_path)
    env = spawn.call_args.kwargs.get("env", {})
    assert env["HOME"] == str(pathlib.Path(tmp_path) / "home-ag5")
    assert other_home not in os.pathsep.join(str(value) for value in env.values())
    assert env["ORCH_PROVIDER"] == "antigravity5"
    # antigravity6 keeps its own cooldown state (per-provider keys).
    assert mr.resolve_active_selection(config, "antigravity6")["pool"] == "gemini"


def test_environmental_failures_do_not_lock_task():
    """Quota/capacity/auth failures must NOT count toward the per-task failure-loop
    streak (they are provider-level outages, not task-agent mismatches)."""
    import supervisor as sv

    st: dict = {}
    w = {"task_id": "ODP-TEST-ENV", "provider": "antigravity"}
    for _ in range(3):
        c = sv.record_task_failure_streak(
            st, w, "Individual quota reached", failure_kind="quota_terminal"
        )
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


class SupportedModelCompatibilityTests(unittest.TestCase):
    """Executable by the task's unittest discovery command (not zero tests).

    Legacy pytest functions above remain covered by the registered pytest run.
    Adapter processes/auth are mocked; all cooldown writes use a temp root.
    """

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = pathlib.Path(temporary.name)
        state_patch = mock.patch.object(mr, "_STATE_PATH", self.path / "cooldown.json")
        legacy_patch = mock.patch.object(mr, "_LEGACY_STATE_PATH", self.path / "legacy.json")
        state_patch.start()
        legacy_patch.start()
        self.addCleanup(state_patch.stop)
        self.addCleanup(legacy_patch.stop)
        self.config = _adapter_config(self.path)
        self.settings = self.config["providers"]["antigravity5"]["antigravity"]
        self.settings["model_rotation"].pop("fallback_model")

    def test_defaults_match_observed_registry_and_example(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        evidence = root / "docs/evidence/execution-control/ODP-ORCH-AGY-MODEL-COMPAT-001"
        receipt = json.loads((evidence / "agy-supported-model-compatibility.json").read_text())
        supported = {item["id"] for item in receipt["models"]}
        self.assertEqual(receipt["exit_code"], 0)
        for model in (mr.DEFAULT_STANDARD_MODEL, mr.DEFAULT_HIGH_RISK_MODEL, mr.DEFAULT_FALLBACK_MODEL):
            self.assertIn(model, supported)
        self.assertTrue(mr.RETIRED_MODEL_IDS.isdisjoint(supported))
        example = json.loads((root / ".orchestrator/config.example.json").read_text())
        for alias in ("antigravity", "antigravity2"):
            settings = example["providers"][alias]["antigravity"]
            self.assertEqual(settings["model_rotation"]["fallback_model"], mr.DEFAULT_FALLBACK_MODEL)
            self.assertEqual(mr.model_policy_config(example, alias)["high_risk_model"], mr.DEFAULT_HIGH_RISK_MODEL)

    def test_risk_and_standard_semantics(self):
        for task in ({"priority": "P0"}, {"priority": "P1"}, {"review_reopen_count": 2},
                     {"priority": "P2", "artifacts": ["src/domain/ledger.py", "docs/guide.md"]}):
            with self.subTest(task=task):
                result, spawn = _deliver(self.config, self.path, task=task)
                self.assertTrue(result.ok)
                self.assertEqual(result.metadata[mr.WORKER_MODEL_KEY], "claude-opus-5-5-high")
                self.assertEqual(result.metadata[mr.WORKER_POOL_KEY], "claude")
                self.assertEqual(result.metadata[mr.WORKER_MODEL_RISK_TIER_KEY], "high")
                command = spawn.call_args.args[0]
                self.assertEqual(command[command.index("--model") + 1], result.metadata[mr.WORKER_MODEL_KEY])
        test_sidecar_and_finalize_stay_on_flash_high(self.path)
        test_p2_docs_only_uses_standard_model(self.path)

    def test_default_fallback_is_high_and_metadata_binds_actual_pool(self):
        mr.record_exhaustion(self.config, "antigravity5", 900, pool="gemini")
        result, spawn = _deliver(self.config, self.path)
        self.assertTrue(result.ok)
        self.assertEqual(result.metadata[mr.WORKER_MODEL_KEY], "claude-sonnet-5-5-high")
        self.assertEqual(result.metadata[mr.WORKER_POOL_KEY], "claude")
        command = spawn.call_args.args[0]
        self.assertEqual(command[command.index("--model") + 1], "claude-sonnet-5-5-high")
        exhaustion = mr.record_exhaustion(self.config, "antigravity5", 900,
                                        pool=mr.worker_dispatched_pool({"metadata": result.metadata}))
        self.assertEqual(exhaustion["exhausted_pool"], "claude")
        self.assertEqual(exhaustion["pool_source"], "dispatched")
        self.assertTrue(exhaustion["both_exhausted"])
        # Later state transitions cannot rewrite the launch receipt.
        self.assertEqual(result.metadata[mr.WORKER_MODEL_KEY], "claude-sonnet-5-5-high")
        self.assertEqual(result.metadata[mr.WORKER_POOL_KEY], "claude")

    def test_gemini_cooldown_does_not_downgrade_high_risk(self):
        self.settings["model_rotation"]["fallback_model"] = "claude-sonnet-4-6"
        mr.record_exhaustion(self.config, "antigravity5", 900, pool="gemini")
        result, _ = _deliver(self.config, self.path, task={"priority": "P0"})
        self.assertTrue(result.ok)
        self.assertEqual(result.metadata[mr.WORKER_MODEL_KEY], "claude-opus-5-5-high")
        self.assertEqual(self.settings["model_rotation"]["fallback_model"], "claude-sonnet-4-6")

    def test_high_risk_claude_cooldown_defers_without_downgrade(self):
        mr.record_exhaustion(self.config, "antigravity5", 900, pool="claude")
        for task in ({"priority": "P0"}, {"review_reopen_count": 1}):
            result, spawn = _deliver(self.config, self.path, task=task)
            self.assertFalse(result.ok)
            self.assertIsNone(result.error)
            self.assertIn("no standard-model downgrade", result.notes)
            self.assertEqual(result.metadata[mr.DISPATCH_WAIT_KEY]["model"], mr.DEFAULT_HIGH_RISK_MODEL)
            self.assertEqual(result.metadata[mr.DISPATCH_WAIT_KEY]["pool"], "claude")
            spawn.assert_not_called()
        # Standard work still runs on the available Gemini pool.
        result, _ = _deliver(self.config, self.path)
        self.assertTrue(result.ok)
        self.assertEqual(result.metadata[mr.WORKER_POOL_KEY], "gemini")
        mr.record_exhaustion(self.config, "antigravity5", 900, pool="gemini")
        result, spawn = _deliver(self.config, self.path, task={"priority": "P0"})
        self.assertFalse(result.ok)
        spawn.assert_not_called()

    def test_retired_static_pins_preserved_but_not_launched(self):
        self.settings["model_rotation"]["enabled"] = False
        for pin in sorted(mr.RETIRED_MODEL_IDS):
            self.settings["model"] = pin
            selected = mr.resolve_active_selection(self.config, "antigravity5", task={"priority": "P0"})
            self.assertEqual(selected["model"], pin)
            self.assertEqual(selected["risk_tier"], "static")
            result, spawn = _deliver(self.config, self.path, task={"priority": "P0"})
            self.assertFalse(result.ok)
            self.assertIn(pin, result.error)
            self.assertIn("unavailable", result.error)
            spawn.assert_not_called()
            self.assertEqual(self.settings["model"], pin)

    def test_retired_policy_and_fallback_fail_without_rewrite(self):
        self.settings["model_policy"] = {"high_risk_model": "claude-opus-4-6-thinking"}
        result, spawn = _deliver(self.config, self.path, task={"priority": "P0"})
        self.assertFalse(result.ok)
        self.assertIn("claude-opus-4-6-thinking", result.error)
        spawn.assert_not_called()
        self.settings["model_rotation"]["fallback_model"] = "claude-sonnet-4-6"
        mr.record_exhaustion(self.config, "antigravity5", 900, pool="gemini")
        self.assertEqual(mr.resolve_active_selection(self.config, "antigravity5")["model"], "claude-sonnet-4-6")
        result, spawn = _deliver(self.config, self.path)
        self.assertFalse(result.ok)
        self.assertIn("claude-sonnet-4-6", result.error)
        spawn.assert_not_called()

    def test_supported_explicit_pin_and_effort_contract(self):
        from adapters.antigravity import _effort_args

        self.settings["model_rotation"]["enabled"] = False
        self.settings["model"] = "claude-sonnet-5-5-high"
        self.settings["effort"] = "high"
        result, spawn = _deliver(self.config, self.path, task={"priority": "P0"})
        self.assertTrue(result.ok)
        self.assertEqual(result.metadata[mr.WORKER_MODEL_KEY], "claude-sonnet-5-5-high")
        self.assertEqual(result.metadata[mr.WORKER_MODEL_RISK_TIER_KEY], "static")
        self.assertNotIn("--effort", spawn.call_args.args[0])
        self.assertEqual(_effort_args(self.settings, mr.DEFAULT_STANDARD_MODEL), ["--effort", "high"])

    def test_concurrent_pool_attribution_unchanged(self):
        test_dispatched_pool_overrides_current_active_pool(self.path)
        test_two_concurrent_gemini_workers_never_exhaust_claude(self.path)
        test_same_worker_failure_rotates_only_once(self.path)


class CooldownLifecycleTests(unittest.TestCase):
    """Real adapter -> launch -> queue wait/resume, with no live processes."""

    setUp = SupportedModelCompatibilityTests.setUp

    def _queue_context(self, task):
        from adapters.base import DeliveryRequest

        task = {"id": "ODP-TEST-ROT", "status": "in_progress", "owner": "Antigravity5",
                "reviewer": "Codex", "depends_on": [], **task}
        request = DeliveryRequest(
            agent_id="antigravity5", provider="antigravity5", delivery_mode="antigravity",
            task_id=task["id"], message="wake", reason="owned_in_progress_dispatch",
            metadata={"task": task},
        )
        event = {"event_id": "wait-event", "task_id": task["id"], "target_agent": "antigravity5",
                 "target_display_name": "Antigravity5", "provider": "antigravity5",
                 "reason": request.reason, "message": "wake"}
        self.config["schema"] = {"tasks_path": "tasks", "task_id_field": "id",
                                 "assignee_field": "owner", "reviewer_field": "reviewer"}
        self.config["ready_dispatcher"] = {"owned_statuses": ["todo", "in_progress"],
                                           "active_worker_statuses": ["running"]}
        return task, request, event

    def _run_queue(self, task_fields, *, both_cooling=False, terminal_pin=False, move_owner=False):
        task, request, event = self._queue_context(task_fields)
        state = {"queue": {"events": {}}, "workers": {}}
        clock = [datetime.now(UTC).replace(microsecond=0)]
        event["created_at"] = clock[0].isoformat()

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock[0]

        deliveries = []

        def deliver(_request):
            result, spawn = _deliver(self.config, self.path, task=task, reason=request.reason)
            deliveries.append((result, spawn))
            return result

        adapter = mock.Mock()
        adapter.deliver.side_effect = deliver
        with (
            mock.patch.object(mr, "_now", side_effect=lambda now=None: now or clock[0]),
            mock.patch.object(sv, "datetime", Clock),
            mock.patch.object(sv, "load_event_queue", return_value=[event]),
            mock.patch.object(sv, "replace_event_queue"),
            mock.patch.object(sv, "load_status", return_value={"tasks": [task]}),
            mock.patch.object(sv, "build_request", return_value=request),
            mock.patch.object(sv, "prepare_worker_workspace", return_value=(True, None)) as workspace,
            mock.patch.object(sv, "agent_auto_dispatch_block_reason", return_value=None),
            mock.patch.object(sv, "select_dispatch_agent_id", return_value="antigravity5"),
            mock.patch.object(sv, "build_adapter", return_value=adapter),
            mock.patch.object(sv, "provider_auth_identity_hash", return_value=None),
            mock.patch.object(sv, "save_runtime_state"),
            mock.patch.object(sv, "record_worker_runtime_measurement"),
            mock.patch.object(sv, "sync_dispatched_task_status") as sync,
            mock.patch.object(sv, "write_activity_log") as activity,
            mock.patch.object(sv, "write_failure_evidence", return_value=None) as evidence,
            mock.patch.object(sv, "record_task_failure_streak", wraps=sv.record_task_failure_streak) as streak,
            mock.patch.object(sv, "maybe_reassign_task_after_worker_failure", return_value=None) as reassign,
            mock.patch.object(sv, "mark_provider_dispatch_paused") as pause,
        ):
            if terminal_pin:
                self.settings["model_policy"] = {"high_risk_model": "claude-opus-4-6-thinking"}
                self.assertTrue(sv.process_queue(self.config, state, {}))
                self.assertEqual(state["queue"]["events"][event["event_id"]]["status"], "failed")
                self.assertEqual(streak.call_args.kwargs["failure_kind"], "terminal")
                self.assertEqual(streak.call_count, 1)
                reassign.assert_called_once()
                pause.assert_not_called()
                self.assertIsNone(deliveries[0][0].metadata.get(mr.DISPATCH_WAIT_KEY))
                deliveries[0][1].assert_not_called()
                return

            mr.record_exhaustion(self.config, "antigravity5", 900, pool="claude")
            if both_cooling:
                mr.record_exhaustion(self.config, "antigravity5", 900, pool="gemini")
            before = mr._STATE_PATH.read_bytes()
            self.assertTrue(sv.process_queue(self.config, state, {}))
            record = state["queue"]["events"][event["event_id"]]
            self.assertEqual(record["status"], "retry_backoff")
            wait = record[mr.DISPATCH_WAIT_KEY]
            self.assertEqual(wait["model"], "claude-opus-5-5-high")
            self.assertEqual(wait["pool"], "claude")
            expected_until = (clock[0] + timedelta(seconds=900)).isoformat().replace("+00:00", "Z")
            self.assertEqual(record["next_retry_at"], expected_until)
            self.assertEqual(record["attempt_count"], 0)
            self.assertFalse(record.get("retry_count"))
            self.assertEqual(state["workers"], {})
            self.assertEqual(task["owner"], "Antigravity5")
            self.assertEqual(mr._STATE_PATH.read_bytes(), before)
            deliveries[0][1].assert_not_called()
            # Full supervisor queue cycle: pruning/reconciliation must not
            # rewrite this intentional workerless wait to queued or orphan it.
            # Age the event only after admission: an initially orphaned wake
            # still must not be dispatched without any durable wait record.
            event["created_at"] = (clock[0] - timedelta(days=1)).isoformat()
            for seconds in (1, 60, 838):
                clock[0] += timedelta(seconds=seconds)
                self.assertFalse(sv.prune_event_queue(self.config, state))
                self.assertFalse(sv.process_queue(self.config, state, {}))
                self.assertEqual(record["status"], "retry_backoff")
                self.assertEqual(record["next_retry_at"], expected_until)
                self.assertEqual(record["attempt_count"], 0)
                self.assertEqual(adapter.deliver.call_count, 1)
                self.assertEqual(workspace.call_count, 1)
                self.assertEqual(activity.call_count, 1)
            streak.assert_not_called()
            reassign.assert_not_called()
            pause.assert_not_called()
            evidence.assert_not_called()
            sync.assert_not_called()
            self.assertEqual(activity.call_args.args[1]["type"], "dispatch_capacity_wait")

            clock[0] += timedelta(seconds=1)
            if move_owner:
                task["owner"] = "OtherOwner"
                self.assertTrue(sv.prune_event_queue(self.config, state))
                self.assertEqual(record["status"], "completed")
                self.assertEqual(record["skip_reason"], "stale_dispatch_event")
                self.assertFalse(sv.process_queue(self.config, state, {}))
                self.assertEqual(adapter.deliver.call_count, 1)
                self.assertEqual(workspace.call_count, 1)
                streak.assert_not_called()
                reassign.assert_not_called()
                return
            self.assertFalse(sv.prune_event_queue(self.config, state))
            self.assertTrue(sv.process_queue(self.config, state, {}))
            self.assertEqual(record["status"], "started")
            self.assertEqual(record["attempt_count"], 1)
            self.assertNotIn(mr.DISPATCH_WAIT_KEY, record)
            self.assertNotIn("next_retry_at", record)
            worker = state["workers"][record["run_id"]]
            self.assertEqual(worker["provider"], "antigravity5")
            self.assertEqual(worker["logical_agent_id"], "antigravity5")
            self.assertEqual(worker[mr.WORKER_MODEL_KEY], wait["model"])
            self.assertEqual(worker[mr.WORKER_POOL_KEY], "claude")
            self.assertEqual(worker["metadata"][mr.WORKER_MODEL_RISK_TIER_KEY], "high")
            self.assertEqual(task["owner"], "Antigravity5")
            self.assertEqual(mr._STATE_PATH.read_bytes(), before)
            deliveries[-1][1].assert_called_once()
            streak.assert_not_called()
            reassign.assert_not_called()
            pause.assert_not_called()
            evidence.assert_not_called()
            sync.assert_called_once()

    def test_p0_p1_and_reopened_wait_then_resume_same_owner_exact_model(self):
        for fields in ({"priority": "P0"}, {"priority": "P1"},
                       {"priority": "P2", "review_reopen_count": 1}):
            with self.subTest(fields=fields):
                self._run_queue(fields)

    def test_both_pools_cooling_wait_then_resume_high_model(self):
        self._run_queue({"priority": "P0"}, both_cooling=True)

    def test_retired_pin_is_terminal_not_environmental_wait(self):
        self._run_queue({"priority": "P0"}, terminal_pin=True)

    def test_queue_wait_still_reconciles_moved_assignment(self):
        self._run_queue({"priority": "P0"}, move_owner=True)

    def test_malformed_wait_does_not_exempt_orphan_or_queue_repair(self):
        _, _, event = self._queue_context({"priority": "P0"})
        event["created_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        record = {"status": "retry_backoff", "next_retry_at": "invalid",
                  mr.DISPATCH_WAIT_KEY: {"kind": "model_cooldown"}}
        self.assertFalse(mr.has_dispatch_cooldown_wait(record))
        self.assertTrue(sv.queue_event_is_orphaned(self.config, event, record, []))
        event.pop("created_at")
        state = {"queue": {"events": {"wait-event": record}}, "workers": {}}
        with (
            mock.patch.object(sv, "load_event_queue", return_value=[event]),
            mock.patch.object(sv, "load_status", return_value={"tasks": []}),
            mock.patch.object(sv, "stale_dispatch_skip_message", return_value=None),
            mock.patch.object(sv, "replace_event_queue"),
        ):
            self.assertTrue(sv.prune_event_queue(self.config, state))
        self.assertEqual(record["status"], "queued")

    def _run_retry_poll(self, task_fields, *, move_at=None):
        task, request, event = self._queue_context(task_fields)
        now = datetime.now(UTC).replace(microsecond=0)
        clock = [now]

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock[0]

        failure = "Error: temporarily unavailable"
        log = self.path / "parent.log"
        log.write_text(failure + "\n")
        marker = self.path / "parent-status.json"
        marker.write_text(json.dumps({"status": "failed", "exit_code": 1,
                                      "finished_at": now.isoformat()}))
        parent = {
            "run_id": "parent", "provider": "antigravity5", "agent_id": "antigravity5",
            "task_id": task["id"], "queue_event_id": "wait-event", "status": "retry_backoff",
            "next_retry_at": now.isoformat(), "attempt_count": 2, "retry_count": 1,
            "last_error": failure, "log_path": str(log), "runner_status_path": str(marker),
        }
        # Prove the real old log would be detected if poll_workers replayed it.
        self.assertEqual(sv.detect_worker_failure(parent), failure)
        state = {"workers": {"parent": parent}, "queue": {"events": {"wait-event": {"status": "started"}}}}
        adapter = mock.Mock()

        def deliver(_request):
            result, spawn = _deliver(self.config, self.path, task=task, reason=request.reason)
            if not result.ok:
                spawn.assert_not_called()
            return result

        adapter.deliver.side_effect = deliver
        with ExitStack() as stack:
            for patch in (
                mock.patch.object(mr, "_now", side_effect=lambda value=None: value or clock[0]),
                mock.patch.object(sv, "datetime", Clock),
                mock.patch.object(sv, "load_status", return_value={"tasks": [task]}),
                mock.patch.object(sv, "load_approval_state", return_value={"pending": [], "history": []}),
                mock.patch.object(sv, "load_event_queue", return_value=[event]),
                mock.patch.object(sv, "build_request", return_value=request),
                mock.patch.object(sv, "build_adapter", return_value=adapter),
                mock.patch.object(sv, "pid_is_alive", side_effect=lambda pid: pid == 4321),
                mock.patch.object(sv, "provider_auth_identity_hash", return_value=None),
                mock.patch.object(sv, "save_runtime_state"),
                mock.patch.object(sv, "record_worker_runtime_measurement"),
                mock.patch.object(sv, "preserve_dead_worker_worktree"),
                mock.patch.object(sv, "observe_worker_worktree_activity", return_value=False),
                mock.patch.object(sv, "write_activity_log"),
            ):
                stack.enter_context(patch)
            evidence = stack.enter_context(mock.patch.object(sv, "write_failure_evidence"))
            streak = stack.enter_context(mock.patch.object(sv, "record_task_failure_streak"))
            reassign = stack.enter_context(mock.patch.object(sv, "maybe_reassign_task_after_worker_failure"))
            pause = stack.enter_context(mock.patch.object(sv, "mark_provider_dispatch_paused"))
            retry = stack.enter_context(mock.patch.object(sv, "schedule_worker_retry", wraps=sv.schedule_worker_retry))
            detect = stack.enter_context(mock.patch.object(sv, "detect_worker_failure", wraps=sv.detect_worker_failure))
            mr.record_exhaustion(self.config, "antigravity5", 900, pool="claude")
            before = mr._STATE_PATH.read_bytes()
            self.assertTrue(sv.poll_workers(self.config, state, {}))
            deadline = (now + timedelta(seconds=900)).isoformat().replace("+00:00", "Z")
            for offset in (0, 1, 60, 899):
                clock[0] = now + timedelta(seconds=offset)
                sv.poll_workers(self.config, state, {})
                self.assertEqual(parent["status"], "retry_backoff")
                self.assertEqual(parent["retry_count"], 1)
                self.assertEqual(parent["attempt_count"], 2)
                self.assertEqual(parent["last_error"], failure)
                self.assertEqual(parent["runner_status"], "failed")
                self.assertEqual(parent["exit_code"], 1)
                self.assertEqual(parent["next_retry_at"], deadline)
                self.assertEqual(parent[mr.DISPATCH_WAIT_KEY]["model"], mr.DEFAULT_HIGH_RISK_MODEL)
                self.assertEqual(task["owner"], "Antigravity5")
                self.assertEqual(list(state["workers"]), ["parent"])
                self.assertEqual(adapter.deliver.call_count, 1)
                self.assertEqual(mr._STATE_PATH.read_bytes(), before)
                detect.assert_not_called()
                retry.assert_not_called()
                streak.assert_not_called()
                reassign.assert_not_called()
                pause.assert_not_called()
                evidence.assert_not_called()
            clock[0] = now + timedelta(seconds=move_at if move_at is not None else 900)
            if move_at is not None:
                task["owner"] = "OtherOwner"
                self.assertTrue(sv.poll_workers(self.config, state, {}))
                self.assertEqual(parent["status"], "superseded")
                self.assertEqual(adapter.deliver.call_count, 1)
                self.assertEqual(list(state["workers"]), ["parent"])
            else:
                self.assertTrue(sv.poll_workers(self.config, state, {}))
                self.assertEqual(parent["status"], "retried")
                self.assertNotIn(mr.DISPATCH_WAIT_KEY, parent)
                self.assertNotIn("last_wait_reason", parent)
                replacement = state["workers"][parent["superseded_by_run_id"]]
                self.assertEqual(replacement["provider"], "antigravity5")
                self.assertEqual(replacement["parent_run_id"], "parent")
                self.assertEqual(replacement["retry_count"], 1)
                self.assertEqual(replacement["attempt_count"], 3)
                self.assertEqual(replacement[mr.WORKER_MODEL_KEY], mr.DEFAULT_HIGH_RISK_MODEL)
                self.assertEqual(replacement[mr.WORKER_POOL_KEY], "claude")
                self.assertEqual(task["owner"], "Antigravity5")
            self.assertEqual(mr._STATE_PATH.read_bytes(), before)
            retry.assert_not_called()
            evidence.assert_not_called()
            streak.assert_not_called()
            reassign.assert_not_called()
            pause.assert_not_called()

    def test_poll_workers_waits_without_replaying_failed_parent_then_resumes(self):
        for fields in ({"priority": "P0"}, {"priority": "P2", "review_reopen_count": 1}):
            with self.subTest(fields=fields):
                self._run_retry_poll(fields)

    def test_poll_wait_reconciles_authority_before_and_at_reset(self):
        for offset in (899, 900):
            with self.subTest(offset=offset):
                self._run_retry_poll({"priority": "P0"}, move_at=offset)

    def test_explicit_gpt_fallback_metadata_and_quota_cool_actual_pool(self):
        self.settings["model_rotation"]["fallback_model"] = "gpt-oss-120b-medium"
        self.config["paths"]["activity_log"] = str(self.path / "activity.jsonl")
        mr.record_exhaustion(self.config, "antigravity5", 900, pool="gemini")
        gemini_until = mr._load()[mr.cooldown_scope(self.config, "antigravity5")]["gemini_until"]
        result, spawn = _deliver(self.config, self.path)
        self.assertTrue(result.ok)
        self.assertEqual(result.metadata[mr.WORKER_MODEL_KEY], "gpt-oss-120b-medium")
        self.assertEqual(result.metadata[mr.WORKER_POOL_KEY], "claude")
        self.assertEqual(spawn.call_args.args[0][spawn.call_args.args[0].index("--model") + 1],
                         "gpt-oss-120b-medium")
        worker = _worker("gpt-run", mr.worker_dispatched_pool({"metadata": result.metadata}))
        worker["metadata"] = result.metadata
        state = {"workers": {"gpt-run": worker}}
        kind = sv.classify_worker_failure(self.config, worker, REAL_ERR)["kind"]
        self.assertEqual(kind, "quota_terminal")
        self.assertTrue(sv.mark_provider_dispatch_paused(
            self.config, state, "antigravity5", REAL_ERR, worker_run_id="gpt-run",
            failure_kind=kind, pause_kind=kind, worker=worker,
        ))
        entry = mr._load()[mr.cooldown_scope(self.config, "antigravity5")]
        self.assertEqual(entry["gemini_until"], gemini_until)
        self.assertTrue(entry["claude_until"])
        self.assertIsNone(mr.active_pool(self.config, "antigravity5"))
        self.assertEqual(worker["metadata"][mr.WORKER_MODEL_KEY], "gpt-oss-120b-medium")

