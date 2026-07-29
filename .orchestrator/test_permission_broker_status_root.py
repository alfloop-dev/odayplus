#!/usr/bin/env python3
"""Cross-root deferred-approval resume regression tests.

Live failure being pinned (2026-07-29, ODP-ORCH-APPROVAL-RESUME-ROOT-001):

    hook executable : /home/lupin/oday-plus/.orchestrator/permission_broker.py
    PANTHEON_STATUS_ROOT : /home/lupin/oday-plus-supervisor-live

The supervisor resolved approval ``apr-20260729T083018Z-f3737b93`` as a one-time
allow in the supervisor-live queue, but the resumed hook read the control root's
queue -- where the very same tool signature was still merely ``pending`` as
``apr-20260729T082950Z-578a3304`` -- so it re-deferred the exact command it had
just been allowed to run, and the temporary allow rule was stranded in the live
root's ``.claude/settings.local.json``.

These tests model both roots on disk and drive the real hook entrypoints.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import common
import permission_broker
from common import approval_tool_input_signature

APPROVED_COMMAND = (
    'docker rm -f odp-probe-pg >/dev/null 2>&1; '
    'docker run -d --name odp-probe-pg -e POSTGRES_PASSWORD=probe postgres:16'
)
APPROVED_INPUT = {"command": APPROVED_COMMAND, "description": "Start isolated probe postgres"}
APPROVED_RULE = f"Bash({APPROVED_COMMAND})"
SUSPENDED_ASK_RULE = "Bash(docker run *)"
SESSION_ID = "c2cafc00-aa5f-4340-8c49-5eb1aedd30b2"
LIVE_APPROVAL_ID = "apr-20260729T083018Z-f3737b93"
CONTROL_APPROVAL_ID = "apr-20260729T082950Z-578a3304"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class TwoRootFixture(unittest.TestCase):
    """A control root that owns the hook binary and a live root that owns the queue."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        base = Path(self.tmpdir.name)
        self.control_root = base / "oday-plus"
        self.live_root = base / "oday-plus-supervisor-live"
        self.signature = approval_tool_input_signature(APPROVED_INPUT)

        for root in (self.control_root, self.live_root):
            _write_json(root / ".orchestrator" / "config.json", self._config_for(root))
            _write_json(root / ".claude" / "settings.local.json", {"permissions": {"allow": [], "ask": []}})
            _write_json(root / ".orchestrator" / "state.json", {"workers": {}, "queue": {"events": {}}})
            (root / ".orchestrator" / "event-queue.jsonl").write_text("", encoding="utf-8")
            _write_json(root / "ai-status.json", {"tasks": [], "agents": []})

        # The control root only ever saw the request; it was never resolved there.
        _write_json(
            self.control_root / ".orchestrator" / "approval-queue.json",
            {"version": 2, "pending": [self._pending_item()], "history": []},
        )
        # The live root is where the coordinator actually granted the one-time allow.
        _write_json(
            self.live_root / ".orchestrator" / "approval-queue.json",
            {"version": 2, "pending": [], "history": [self._resolved_allow_item()]},
        )
        # The supervisor inserted the temporary allow rule and suspended the ask
        # rule in the live root when it granted that approval.
        _write_json(
            self.live_root / ".claude" / "settings.local.json",
            {"permissions": {"allow": [APPROVED_RULE], "ask": []}},
        )

    @staticmethod
    def _config_for(root: Path) -> dict:
        return {
            "paths": {
                "status_file": "ai-status.json",
                "activity_log": "ai-activity-log.jsonl",
                "current_work": "current-work.md",
                "state_file": ".orchestrator/state.json",
                "event_queue": ".orchestrator/event-queue.jsonl",
                "approval_queue": ".orchestrator/approval-queue.json",
                "evidence_dir": ".orchestrator/evidence",
            },
            "providers": {"claude": {"delivery_mode": "claude_cli", "broker": {"approval_wait_seconds": 60}}},
        }

    def _pending_item(self) -> dict:
        return {
            "approval_id": CONTROL_APPROVAL_ID,
            "status": "pending",
            "decision": None,
            "provider": "claude",
            "session_id": SESSION_ID,
            "tool_name": "Bash",
            "tool_input": APPROVED_INPUT,
            "tool_input_signature": self.signature,
            "risk_class": "needs_review",
            "resume_override_active": False,
            "created_at": "2026-07-29T08:29:50Z",
        }

    def _resolved_allow_item(self, **overrides: object) -> dict:
        item = {
            "approval_id": LIVE_APPROVAL_ID,
            "status": "resolved",
            "decision": "allow",
            "provider": "claude",
            "session_id": SESSION_ID,
            "tool_name": "Bash",
            "tool_input": APPROVED_INPUT,
            "tool_input_signature": self.signature,
            "risk_class": "needs_review",
            "resume_override_active": True,
            "resume_override_rule": APPROVED_RULE,
            "resume_override_rule_inserted": True,
            "resume_override_suspended_ask_rules": [SUSPENDED_ASK_RULE],
            "resume_override_consumed_at": None,
            "note": "Coordinator one-time allow: exact command only.",
            "created_at": "2026-07-29T08:30:18Z",
            "resolved_at": "2026-07-29T08:31:07Z",
        }
        item.update(overrides)
        return item

    def _hook_env(self, status_root: Path | None) -> dict[str, str]:
        env = {"ORCH_PROVIDER": "claude", "ORCH_SESSION_ID": SESSION_ID}
        if status_root is not None:
            env[common.STATUS_ROOT_ENV_VAR] = str(status_root)
        return env

    def _as_control_root_module(self, status_root: Path | None):
        """Pretend this process is the hook binary that lives in the control root.

        ``load_config()`` means "the config of the checkout this module lives in",
        which for the live defect is the control root -- so that is what the
        module-root fallback must resolve to.
        """
        return (
            mock.patch.object(permission_broker, "ROOT", self.control_root),
            mock.patch.object(
                permission_broker,
                "load_config",
                lambda *_args, **_kwargs: self._config_for(self.control_root),
            ),
            mock.patch.dict(os.environ, self._hook_env(status_root), clear=True),
        )

    def _resolve(self, status_root: Path | None) -> tuple[dict, Path, str]:
        root_patch, config_patch, env_patch = self._as_control_root_module(status_root)
        with root_patch, config_patch, env_patch:
            return permission_broker.resolve_hook_config()

    def _run_hook(self, event: str, payload: dict, *, status_root: Path | None) -> dict | None:
        config, _root, _source = self._resolve(status_root)
        buffer = io.StringIO()
        root_patch, config_patch, env_patch = self._as_control_root_module(status_root)
        with root_patch, config_patch, env_patch, redirect_stdout(buffer):
            permission_broker.hook_mode(config, event, payload)
        emitted = buffer.getvalue().strip()
        return json.loads(emitted) if emitted else None

    def _pre_tool_use(self, tool_input: dict, *, status_root: Path | None, session_id: str = SESSION_ID) -> dict | None:
        return self._run_hook(
            "PreToolUse",
            {"session_id": session_id, "tool_name": "Bash", "tool_input": tool_input},
            status_root=status_root,
        )

    def _live_settings(self) -> dict:
        return json.loads((self.live_root / ".claude" / "settings.local.json").read_text(encoding="utf-8"))

    def _control_settings(self) -> dict:
        return json.loads((self.control_root / ".claude" / "settings.local.json").read_text(encoding="utf-8"))

    def _live_queue(self) -> dict:
        return json.loads((self.live_root / ".orchestrator" / "approval-queue.json").read_text(encoding="utf-8"))


class StatusRootResolutionTests(TwoRootFixture):
    def test_declared_status_root_wins_over_the_module_checkout(self) -> None:
        config, root, source = self._resolve(self.live_root)
        self.assertEqual(root, self.live_root)
        self.assertEqual(source, "status_root_env")
        self.assertEqual(
            Path(config["paths"]["approval_queue"]),
            self.live_root / ".orchestrator" / "approval-queue.json",
        )

    def test_falls_back_to_the_module_checkout_when_unset(self) -> None:
        config, root, source = self._resolve(None)
        self.assertEqual(root, self.control_root)
        self.assertEqual(source, "module_root")
        # Without the env var the hook keeps exactly its previous behaviour:
        # its own checkout's queue, never a guess at some other root.
        self.assertEqual(
            Path(config["paths"]["approval_queue"]),
            self.control_root / ".orchestrator" / "approval-queue.json",
        )

    def test_resolution_fails_closed_on_unusable_roots(self) -> None:
        not_a_root = Path(self.tmpdir.name) / "empty"
        not_a_root.mkdir()
        cases = {
            "blank": "   ",
            "relative": "oday-plus-supervisor-live",
            "missing": str(Path(self.tmpdir.name) / "does-not-exist"),
            "not_an_orchestrator_root": str(not_a_root),
            "a_file_not_a_dir": str(self.live_root / "ai-status.json"),
        }
        for label, value in cases.items():
            with self.subTest(label=label):
                self.assertIsNone(common.authoritative_status_root({common.STATUS_ROOT_ENV_VAR: value}))

    def test_absolute_path_overrides_are_left_alone(self) -> None:
        explicit = "/var/lib/pantheon/ai-status.json"
        anchored = common.anchor_config_paths(
            {"paths": {"status_file": explicit, "approval_queue": ".orchestrator/approval-queue.json"}},
            self.live_root,
        )
        self.assertEqual(anchored["paths"]["status_file"], explicit)
        self.assertEqual(
            Path(anchored["paths"]["approval_queue"]),
            self.live_root / ".orchestrator" / "approval-queue.json",
        )


class CrossRootResumeTests(TwoRootFixture):
    def test_reproduces_the_live_re_defer_when_the_hook_reads_the_control_root(self) -> None:
        """The exact live failure: the control root has no override, so it re-defers."""
        response = self._pre_tool_use(APPROVED_INPUT, status_root=None)
        self.assertIsNotNone(response)
        self.assertEqual(response["hookSpecificOutput"]["permissionDecision"], "defer")
        # And the live root's one-time allow is still sitting there unconsumed.
        self.assertIsNone(self._live_queue()["history"][0]["resume_override_consumed_at"])

    def test_resume_allows_the_exact_approved_input_from_the_authoritative_root(self) -> None:
        response = self._pre_tool_use(APPROVED_INPUT, status_root=self.live_root)
        self.assertIsNotNone(response)
        output = response["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "allow")
        self.assertIn("Coordinator one-time allow", output["permissionDecisionReason"])

    def test_a_different_command_in_the_same_session_still_defers(self) -> None:
        """The override is signature-scoped; honouring the root broadens nothing."""
        response = self._pre_tool_use(
            {"command": "docker run -d --name something-else postgres:16"},
            status_root=self.live_root,
        )
        self.assertIsNotNone(response)
        self.assertEqual(response["hookSpecificOutput"]["permissionDecision"], "defer")

    def test_an_override_from_another_session_is_not_honoured(self) -> None:
        """Actor validation is unchanged: session id remains part of the match."""
        response = self._pre_tool_use(
            APPROVED_INPUT,
            status_root=self.live_root,
            session_id="11111111-2222-3333-4444-555555555555",
        )
        self.assertIsNotNone(response)
        self.assertEqual(response["hookSpecificOutput"]["permissionDecision"], "defer")

    def test_a_resolved_deny_never_allows(self) -> None:
        _write_json(
            self.live_root / ".orchestrator" / "approval-queue.json",
            {
                "version": 2,
                "pending": [],
                "history": [
                    self._resolved_allow_item(
                        decision="deny",
                        resume_override_active=False,
                        resume_override_rule_inserted=False,
                        note="Coordinator denied: container churn not authorised.",
                    )
                ],
            },
        )
        response = self._pre_tool_use(APPROVED_INPUT, status_root=self.live_root)
        self.assertIsNotNone(response)
        self.assertNotEqual(response["hookSpecificOutput"]["permissionDecision"], "allow")

    def test_permission_request_event_also_honours_the_authoritative_root(self) -> None:
        response = self._run_hook(
            "PermissionRequest",
            {"session_id": SESSION_ID, "tool_name": "Bash", "tool_input": APPROVED_INPUT},
            status_root=self.live_root,
        )
        self.assertIsNotNone(response)
        self.assertEqual(response["hookSpecificOutput"]["decision"]["behavior"], "allow")


class OverrideConsumptionTests(TwoRootFixture):
    def _post_tool_use(self) -> None:
        self._run_hook(
            "PostToolUse",
            {"session_id": SESSION_ID, "tool_name": "Bash", "tool_input": APPROVED_INPUT},
            status_root=self.live_root,
        )

    def test_post_tool_use_consumes_the_override_in_the_authoritative_root(self) -> None:
        self._post_tool_use()
        item = self._live_queue()["history"][0]
        self.assertEqual(item["approval_id"], LIVE_APPROVAL_ID)
        self.assertTrue(item["resume_override_consumed_at"])
        self.assertEqual(item["resume_override_consumed_reason"], "PostToolUse:Bash")

    def test_consumption_clears_the_temporary_rule_from_the_authoritative_root(self) -> None:
        self.assertIn(APPROVED_RULE, self._live_settings()["permissions"]["allow"])
        self._post_tool_use()
        live = self._live_settings()["permissions"]
        self.assertNotIn(APPROVED_RULE, live["allow"])
        self.assertIn(SUSPENDED_ASK_RULE, live["ask"])
        # The control root's rule file is never touched by another fleet's resume.
        control = self._control_settings()["permissions"]
        self.assertEqual(control["allow"], [])
        self.assertEqual(control["ask"], [])

    def test_the_override_is_single_use(self) -> None:
        self.assertEqual(
            self._pre_tool_use(APPROVED_INPUT, status_root=self.live_root)["hookSpecificOutput"][
                "permissionDecision"
            ],
            "allow",
        )
        self._post_tool_use()
        replayed = self._pre_tool_use(APPROVED_INPUT, status_root=self.live_root)
        self.assertEqual(replayed["hookSpecificOutput"]["permissionDecision"], "defer")

    def test_activity_log_records_the_queue_root_that_answered(self) -> None:
        self._pre_tool_use(APPROVED_INPUT, status_root=self.live_root)
        rows = [
            json.loads(line)
            for line in (self.live_root / "ai-activity-log.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        hooks = [row for row in rows if row.get("type") == "permission_hook"]
        self.assertTrue(hooks, "the hook decision must be auditable in the authoritative root")
        audit = hooks[-1]["approval_root"]
        self.assertEqual(Path(audit["status_root"]), self.live_root)
        self.assertEqual(Path(audit["module_root"]), self.control_root)
        self.assertEqual(
            Path(audit["approval_queue_path"]),
            self.live_root / ".orchestrator" / "approval-queue.json",
        )
        # The control root must not have absorbed this fleet's audit trail.
        self.assertFalse((self.control_root / "ai-activity-log.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
