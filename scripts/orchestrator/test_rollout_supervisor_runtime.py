from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import rollout_supervisor_runtime as rollout

SHA = "1" * 40


def prepare_rollout(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path, Path]:
    source = tmp_path / "source"
    source.mkdir()
    (source / ".git").mkdir()
    parent = tmp_path / "runtimes"
    parent.mkdir()
    target = parent / f"oday-plus-supervisor-runtime-{SHA[:12]}"
    target.mkdir()
    previous = tmp_path / "previous"
    previous.mkdir()
    link = tmp_path / "runtime-current"
    link.symlink_to(previous)
    status_root = tmp_path / "status"
    (status_root / "scripts").mkdir(parents=True)
    (status_root / ".orchestrator").mkdir()
    (status_root / ".orchestrator" / "config.json").write_text("{}\n")
    return source, parent, target, previous, link, status_root


def fake_git(repo: Path, *args: str) -> str:
    if args[:2] == ("fetch", "--quiet"):
        return ""
    if args == ("rev-parse", "origin/dev") or args == ("rev-parse", "HEAD"):
        return SHA
    if args == ("rev-parse", "--abbrev-ref", "HEAD"):
        return f"runtime-live-{SHA[:12]}"
    if args == ("rev-list", "--count", "HEAD..origin/dev"):
        return "0"
    raise AssertionError((repo, args))


def rollout_args(source: Path, parent: Path, link: Path, status_root: Path) -> list[str]:
    return [
        "--source-root",
        str(source),
        "--runtime-link",
        str(link),
        "--runtime-parent",
        str(parent),
        "--status-root",
        str(status_root),
        "--service",
        "pantheon-supervisor.service",
    ]


def watchdog_rollout_args(source: Path, parent: Path, link: Path, status_root: Path) -> list[str]:
    return [
        "--source-root",
        str(source),
        "--runtime-link",
        str(link),
        "--runtime-parent",
        str(parent),
        "--status-root",
        str(status_root),
        "--watchdog-pid-file",
        str(status_root / ".orchestrator" / "supervisor.pid"),
    ]


class RolloutSupervisorRuntimeTests(unittest.TestCase):
    def test_rollout_installs_launcher_for_stable_runtime_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            source, parent, target, _previous, link, status_root = prepare_rollout(tmp_path)
            launcher = status_root / "scripts" / "ai-status.sh"
            launcher.write_text("old writer\n")

            with mock.patch.object(rollout, "git", fake_git), \
                 mock.patch.object(rollout, "clean", lambda _repo: True), \
                 mock.patch.object(rollout.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)):
                self.assertEqual(rollout.main(rollout_args(source, parent, link, status_root)), 0)

            self.assertEqual(link.resolve(), target.resolve())
            self.assertIn(str(link / "scripts" / "ai_status.py"), launcher.read_text())
            self.assertIn("PANTHEON_STATUS_ROOT", launcher.read_text())
            self.assertNotIn("ORCH_CONFIG_PATH=", launcher.read_text())
            self.assertTrue(os.access(launcher, os.X_OK))

    def test_failed_restart_restores_runtime_and_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            source, parent, _target, previous, link, status_root = prepare_rollout(tmp_path)
            launcher = status_root / "scripts" / "ai-status.sh"
            launcher.write_text("old writer\n")
            launcher.chmod(0o744)
            restarts = iter((SimpleNamespace(returncode=1), SimpleNamespace(returncode=0)))

            with mock.patch.object(rollout, "git", fake_git), \
                 mock.patch.object(rollout, "clean", lambda _repo: True), \
                 mock.patch.object(rollout.subprocess, "run", lambda *args, **kwargs: next(restarts)):
                with self.assertRaisesRegex(SystemExit, "restored previous runtime and status launcher"):
                    rollout.main(rollout_args(source, parent, link, status_root))

            self.assertEqual(link.resolve(), previous.resolve())
            self.assertEqual(launcher.read_text(), "old writer\n")
            self.assertEqual(launcher.stat().st_mode & 0o777, 0o744)


# Stands in for the real supervisor's startup contract only: the singleton lock,
# the pid file and what one completed loop stamps into the runtime state.  The
# watchdog, its budget state and the process identity checks around it are real.
FAKE_SUPERVISOR = """\
import fcntl, hashlib, json, os, pathlib, sys, time

behaviour = json.loads(pathlib.Path(".orchestrator/fake-supervisor.json").read_text())
if behaviour.get("exit_at_once"):
    sys.exit(0)
orchestrator = pathlib.Path(os.environ["ORCH_STATUS_ROOT"]) / ".orchestrator"
lock = open(orchestrator / "supervisor.lock", "a+")
try:
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(0)
(orchestrator / "supervisor.pid").write_text(f"{os.getpid()}\\n")
now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
state_file = orchestrator / "state.json"
state = json.loads(state_file.read_text()) if state_file.exists() else {}
state["supervisor"] = {
    "pid": os.getpid(),
    "lifecycle": behaviour.get("lifecycle", "running"),
    "last_heartbeat_at": now,
    "last_successful_loop_at": now,
    "loaded_code_sha": behaviour["sha"],
    "loaded_config_digest": hashlib.sha256(pathlib.Path(os.environ["ORCH_CONFIG_PATH"]).read_bytes()).hexdigest()[:16],
}
scratch = state_file.with_name(f".state-{os.getpid()}")
scratch.write_text(json.dumps(state))
os.replace(scratch, state_file)
time.sleep(600)
"""


class WatchdogRolloutTests(unittest.TestCase):
    """Through the real entry, with the real watchdog as a real process."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source, self.parent, self.target, self.previous, self.link, self.status_root = prepare_rollout(self.root)
        self.orchestrator = self.status_root / ".orchestrator"
        self.state_file = self.orchestrator / "state.json"
        self.pid_file = self.orchestrator / "supervisor.pid"
        self.watchdog_state = self.orchestrator / "watchdog-state.json"
        self.config_file = self.orchestrator / "config.json"
        self.launcher = self.status_root / "scripts" / "ai-status.sh"
        self.launcher.write_text("old writer\n")
        self.other = self.root / "other-runtime"
        self.write_config()
        for runtime in (self.previous, self.target, self.other):
            self.install_runtime(runtime)
        self.spawned: list[subprocess.Popen] = []
        self.addCleanup(self.stop_everything)
        for patcher in (
            mock.patch.object(rollout, "git", fake_git),
            mock.patch.object(rollout, "clean", lambda _repo: True),
            mock.patch.object(rollout, "LOADED_RUNTIME_PROOF_POLL_SECONDS", 0.05),
            mock.patch.object(rollout, "LOADED_RUNTIME_PROOF_TIMEOUT_SECONDS", 20.0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.stdout = io.StringIO()

    def write_config(self, **watchdog: object) -> None:
        settings = {
            "state_file": str(self.watchdog_state),
            "metrics_file": str(self.orchestrator / "watchdog-metrics.jsonl"),
            "restart_budget_window_seconds": 900,
            "max_restarts_per_window": 3,
            "max_restarts_per_hour": 6,
            "backoff_schedule_seconds": [0],
            "circuit_cooldown_seconds": 1800,
            # The rollout must not depend on how busy the host running this test is.
            "min_disk_free_gb": 0.0,
            "max_disk_used_percent": 100.0,
            "min_memory_available_mb": 0,
            "max_load_1m": 100000.0,
            "max_active_workers": 1000,
            **watchdog,
        }
        self.config_file.write_text(
            json.dumps(
                {
                    "paths": {
                        "state_file": ".orchestrator/state.json",
                        "status_file": "ai-status.json",
                        "activity_log": ".orchestrator/activity.jsonl",
                    },
                    "watchdog": settings,
                }
            )
            + "\n"
        )

    def install_runtime(self, runtime: Path, *, sha: str = SHA, **behaviour: object) -> None:
        """A runtime holding the real watchdog code; only supervisor.py is the stand-in."""
        orchestrator = runtime / ".orchestrator"
        orchestrator.mkdir(parents=True, exist_ok=True)
        (runtime / "scripts").mkdir(exist_ok=True)
        repo = rollout.ORCHESTRATOR_DIR.parent
        for module in list(sys.modules.values()):
            module_file = getattr(module, "__file__", None)
            if module_file and Path(module_file).parent == rollout.ORCHESTRATOR_DIR and not Path(module_file).name.startswith("test_"):
                shutil.copy2(module_file, orchestrator)
        shutil.copy2(rollout.ORCHESTRATOR_DIR / "config.schema.json", orchestrator)
        shutil.copy2(repo / "scripts" / "run-supervisor-watchdog.sh", runtime / "scripts")
        (orchestrator / "supervisor.py").write_text(FAKE_SUPERVISOR)
        (orchestrator / "fake-supervisor.json").write_text(json.dumps({"sha": sha, **behaviour}))

    def spawn_supervisor(self, runtime: Path) -> subprocess.Popen:
        process = subprocess.Popen(
            [sys.executable, "-u", ".orchestrator/supervisor.py"],
            cwd=runtime,
            env=rollout.watchdog_env(self.status_root, self.config_file),
        )
        self.spawned.append(process)
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            state = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}
            if state.get("supervisor", {}).get("pid") == process.pid:
                self.assertEqual(rollout.common.iter_supervisor_pids(runtime), [process.pid])
                return process
            time.sleep(0.02)
        self.fail("stand-in supervisor did not start")

    def stop_everything(self) -> None:
        for runtime in (self.previous, self.target, self.other):
            for pid in rollout.common.iter_supervisor_pids(runtime):
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)
        for process in self.spawned:
            process.kill()
            process.wait()

    def write_attempts(self, *ages_seconds: int, **fields: object) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        attempts = [
            {"at": rollout.supervisor_watchdog.isoformat_utc(now - timedelta(seconds=age)), "reason": "pid_not_alive"}
            for age in ages_seconds
        ]
        self.watchdog_state.write_text(json.dumps({"restart_attempts": attempts, **fields}) + "\n")

    def roll_out(self, *extra: str) -> int:
        with contextlib.redirect_stdout(self.stdout):
            return rollout.main([*watchdog_rollout_args(self.source, self.parent, self.link, self.status_root), *extra])

    def assert_untouched(self, old: subprocess.Popen | None, config: str, watchdog_state: str | None) -> None:
        self.assertEqual(self.link.resolve(), self.previous)
        self.assertEqual(self.launcher.read_text(), "old writer\n")
        self.assertEqual(self.config_file.read_text(), config)
        self.assertEqual(self.watchdog_state.read_text() if self.watchdog_state.exists() else None, watchdog_state)
        self.assertNotIn("rollout complete", self.stdout.getvalue())
        if old is not None:
            self.assertIsNone(old.poll())
            self.assertEqual(self.pid_file.read_text().strip(), str(old.pid))

    def test_legitimate_restart_is_proven_on_the_selected_runtime(self) -> None:
        old = self.spawn_supervisor(self.previous)
        real_run = subprocess.run
        commands: list[list[str]] = []

        def recording_run(args: list[str], **kwargs: object) -> object:
            commands.append(args)
            return real_run(args, **kwargs)

        with mock.patch.object(rollout.subprocess, "run", recording_run):
            self.assertEqual(self.roll_out(), 0)

        self.assertEqual(self.link.resolve(), self.target)
        self.assertIn(str(self.link / "scripts" / "ai_status.py"), self.launcher.read_text())
        self.assertIsNotNone(old.wait(timeout=5))
        running = rollout.common.iter_supervisor_pids(self.target)
        self.assertEqual(len(running), 1)
        self.assertEqual(rollout.common.iter_supervisor_pids(self.previous), [])
        self.assertEqual(json.loads(self.state_file.read_text())["supervisor"]["pid"], running[0])
        self.assertIn(f"supervisor_proven pid={running[0]} runtime={self.target}", self.stdout.getvalue())
        self.assertIn("rollout complete", self.stdout.getvalue())
        self.assertEqual(
            commands,
            [
                [
                    "bash",
                    str(self.link / "scripts" / "run-supervisor-watchdog.sh"),
                    "--config",
                    str(self.config_file),
                    "--restart",
                    "--json",
                ]
            ],
        )
        attempts = json.loads(self.watchdog_state.read_text())["restart_attempts"]
        self.assertEqual([attempt["new_pid"] for attempt in attempts], running)

    def test_reloading_the_selected_runtime_replaces_its_supervisor(self) -> None:
        self.link.unlink()
        self.link.symlink_to(self.target)
        old = self.spawn_supervisor(self.target)

        self.assertEqual(self.roll_out(), 0)

        self.assertIsNotNone(old.wait(timeout=5))
        running = rollout.common.iter_supervisor_pids(self.target)
        self.assertEqual(len(running), 1)
        self.assertNotEqual(running, [old.pid])

    def test_exit_zero_suppression_after_the_stop_is_not_recovery(self) -> None:
        """The 2026-11-06T11:30Z failure: exit 0, suppress_restart, no pid."""
        self.spawn_supervisor(self.previous)
        real_terminate = rollout.common.terminate_supervisor_processes

        def budget_spent_while_stopping(*args: object, **kwargs: object) -> dict[int, bool]:
            stopped = real_terminate(*args, **kwargs)
            self.write_attempts(120, 60, 30)
            return stopped

        with mock.patch.object(rollout.common, "terminate_supervisor_processes", budget_spent_while_stopping):
            with self.assertRaises(SystemExit) as caught:
                self.roll_out()

        message = str(caught.exception)
        self.assertIn("decision=suppress_restart reason=restart_budget_window_exhausted new_pid=None", message)
        self.assertIn("restored previous runtime and status launcher", message)
        self.assertIn("previous supervisor NOT verified", message)
        self.assertNotIn("rollout complete", self.stdout.getvalue())
        self.assertEqual(self.link.resolve(), self.previous)
        self.assertEqual(self.launcher.read_text(), "old writer\n")
        self.assertEqual(rollout.common.iter_supervisor_pids(self.target), [])
        self.assertEqual(len(json.loads(self.watchdog_state.read_text())["restart_attempts"]), 3)

    def test_inadmissible_restart_refuses_before_touching_the_healthy_service(self) -> None:
        old = self.spawn_supervisor(self.previous)
        future = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        spent_hour = (3000, 2800, 2600, 2400, 2200, 2000)
        cases: list[tuple[str, dict[str, object], object]] = [
            ("restart_budget_window_exhausted", {}, (120, 60, 30)),
            ("restart_budget_hour_exhausted", {}, spent_hour),
            ("watchdog_circuit_open", {}, {"open": True, "until": future}),
            ("restart_backoff_active", {"backoff_schedule_seconds": [600]}, (60,)),
            ("watchdog_disabled", {"enabled": False}, ()),
            ("resource_pressure:active_worker_count_above_threshold", {"max_active_workers": -1}, ()),
            ("unusable_state", {}, "not json"),
            ("unusable_state", {}, "[]\n"),
            ("unusable_state", {}, '{"restart_attempts": 3}\n'),
            ("unusable_state", {}, '{"circuit": []}\n'),
        ]
        for expected, watchdog, state in cases:
            with self.subTest(expected=expected, state=state):
                self.write_config(**watchdog)
                if isinstance(state, tuple):
                    self.write_attempts(*state)
                elif isinstance(state, dict):
                    self.write_attempts(circuit=state)
                else:
                    self.watchdog_state.write_text(state)
                config, recorded = self.config_file.read_text(), self.watchdog_state.read_text()

                with mock.patch.object(rollout.subprocess, "run") as run, \
                     mock.patch.object(rollout.common.time, "sleep"), \
                     mock.patch.object(rollout.common, "terminate_supervisor_processes") as terminate, \
                     mock.patch.object(rollout, "add_runtime_worktree") as prepare:
                    with self.assertRaisesRegex(SystemExit, f"restart_not_admissible: {expected}"):
                        self.roll_out()

                run.assert_not_called()
                terminate.assert_not_called()
                prepare.assert_not_called()
                self.assert_untouched(old, config, recorded)

    def test_relative_budget_state_is_read_where_the_target_watchdog_keeps_it(self) -> None:
        old = self.spawn_supervisor(self.previous)
        self.write_config(state_file=".orchestrator/watchdog-state.json")
        config = self.config_file.read_text()
        self.write_attempts(120, 60, 30)
        (self.target / ".orchestrator" / "watchdog-state.json").write_text(self.watchdog_state.read_text())
        self.watchdog_state.unlink()

        with self.assertRaisesRegex(SystemExit, "restart_budget_window_exhausted"):
            self.roll_out()

        self.assert_untouched(old, config, None)

    def test_suppression_held_only_under_the_previous_runtime_refuses(self) -> None:
        """A relative state file stays behind on the switch; its suppression still applies."""
        old = self.spawn_supervisor(self.previous)
        held = self.previous / ".orchestrator" / "watchdog-state.json"
        future = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        spent_hour = (3000, 2800, 2600, 2400, 2200, 2000)
        cases: list[tuple[str, dict[str, object], object]] = [
            ("restart_budget_window_exhausted", {}, (120, 60, 30)),
            ("restart_budget_hour_exhausted", {}, spent_hour),
            ("watchdog_circuit_open", {}, {"open": True, "until": future}),
            ("restart_backoff_active", {"backoff_schedule_seconds": [600]}, (60,)),
            ("unusable_state", {}, "[]\n"),
        ]
        for expected, watchdog, state in cases:
            with self.subTest(expected=expected):
                self.write_config(state_file=".orchestrator/watchdog-state.json", **watchdog)
                config = self.config_file.read_text()
                if isinstance(state, tuple):
                    self.write_attempts(*state)
                elif isinstance(state, dict):
                    self.write_attempts(circuit=state)
                else:
                    self.watchdog_state.write_text(state)
                self.watchdog_state.replace(held)
                recorded = held.read_text()

                with mock.patch.object(rollout.common, "terminate_supervisor_processes") as terminate:
                    with self.assertRaisesRegex(SystemExit, f"restart_not_admissible: {expected}"):
                        self.roll_out()

                terminate.assert_not_called()
                self.assert_untouched(old, config, None)
                self.assertEqual(held.read_text(), recorded)
                self.assertFalse((self.target / ".orchestrator" / "watchdog-state.json").exists())

    def test_relative_budget_is_neither_migrated_nor_reset_by_a_legitimate_restart(self) -> None:
        self.spawn_supervisor(self.previous)
        self.write_config(state_file=".orchestrator/watchdog-state.json")
        held = self.previous / ".orchestrator" / "watchdog-state.json"
        self.write_attempts(2000)
        self.watchdog_state.replace(held)
        recorded = held.read_text()

        self.assertEqual(self.roll_out(), 0)

        self.assertEqual(held.read_text(), recorded)
        target_state = json.loads((self.target / ".orchestrator" / "watchdog-state.json").read_text())
        running = rollout.common.iter_supervisor_pids(self.target)
        self.assertEqual([attempt["new_pid"] for attempt in target_state["restart_attempts"]], running)

    def fake_full_disks(self, *full: Path) -> object:
        """Only the operator's in-process view; the target's real watchdog measures for real."""
        real_statvfs = os.statvfs

        def statvfs(path: object) -> object:
            if Path(str(path)) in full:
                return SimpleNamespace(f_bavail=0, f_frsize=4096, f_blocks=1000)
            return real_statvfs(path)

        return mock.patch.object(rollout.supervisor_watchdog.os, "statvfs", statvfs)

    def test_full_runtime_parent_refuses_although_the_operator_checkout_has_room(self) -> None:
        old = self.spawn_supervisor(self.previous)
        self.write_config(min_disk_free_gb=0.01)
        config = self.config_file.read_text()
        shutil.rmtree(self.target)
        operator_root = rollout.supervisor_watchdog.ROOT

        with self.fake_full_disks(self.parent), \
             mock.patch.object(rollout, "add_runtime_worktree") as prepare, \
             mock.patch.object(rollout.common, "terminate_supervisor_processes") as terminate:
            with self.assertRaisesRegex(SystemExit, "restart_not_admissible: resource_pressure:disk_free_below_threshold"):
                self.roll_out()

        prepare.assert_not_called()
        terminate.assert_not_called()
        self.assert_untouched(old, config, None)
        self.assertEqual(rollout.supervisor_watchdog.ROOT, operator_root)

    def test_target_filled_by_preparing_it_refuses_before_the_switch(self) -> None:
        old = self.spawn_supervisor(self.previous)
        self.write_config(min_disk_free_gb=0.01)
        config = self.config_file.read_text()
        self.target.rename(self.root / "prepared")

        def prepare(_source: Path, target: Path, _branch: str, _sha: str) -> None:
            (self.root / "prepared").rename(target)

        with self.fake_full_disks(self.target), \
             mock.patch.object(rollout, "add_runtime_worktree", prepare), \
             mock.patch.object(rollout.common, "terminate_supervisor_processes") as terminate:
            with self.assertRaisesRegex(SystemExit, "restart_not_admissible: resource_pressure:disk_free_below_threshold"):
                self.roll_out()

        terminate.assert_not_called()
        self.assert_untouched(old, config, None)

    def test_full_operator_checkout_does_not_refuse_a_target_with_room(self) -> None:
        self.spawn_supervisor(self.previous)
        self.write_config(min_disk_free_gb=0.01)

        with self.fake_full_disks(rollout.supervisor_watchdog.ROOT):
            self.assertEqual(self.roll_out(), 0)

        self.assertEqual(self.link.resolve(), self.target)
        self.assertEqual(len(rollout.common.iter_supervisor_pids(self.target)), 1)

    def test_unusable_config_refuses_with_the_service_running(self) -> None:
        old = self.spawn_supervisor(self.previous)
        for document in ('{"not_a_key": 1}\n', "{broken\n", "{}\n"):
            with self.subTest(document=document):
                self.config_file.write_text(document)
                with mock.patch.object(rollout.common, "terminate_supervisor_processes") as terminate:
                    with self.assertRaisesRegex(SystemExit, "unusable_config"):
                        self.roll_out()
                terminate.assert_not_called()
                self.assert_untouched(old, document, None)

    def test_unverified_identity_refuses(self) -> None:
        # The lock is held, but not by a supervisor of the runtime the link selects.
        old = self.spawn_supervisor(self.other)
        config = self.config_file.read_text()
        with self.assertRaisesRegex(SystemExit, "unverified_supervisor_identity"):
            self.roll_out()
        self.assert_untouched(old, config, None)
        self.stop_everything()

        # A live supervisor whose pid file is gone gives no pid to verify.
        old = self.spawn_supervisor(self.previous)
        self.pid_file.unlink()
        with self.assertRaisesRegex(SystemExit, "unverified_supervisor_identity"):
            self.roll_out()
        self.assertIsNone(old.poll())
        self.assertEqual(self.link.resolve(), self.previous)

    def test_recycled_pid_file_is_never_signalled(self) -> None:
        bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        self.spawned.append(bystander)
        self.pid_file.write_text(f"{bystander.pid}\n")

        self.assertEqual(self.roll_out(), 0)

        self.assertIsNone(bystander.poll())
        self.assertEqual(len(rollout.common.iter_supervisor_pids(self.target)), 1)

    def test_wrong_results_roll_back_and_recover_the_previous_supervisor(self) -> None:
        cases: dict[str, tuple[dict[str, object], str]] = {
            "wrong_code": ({"sha": "2" * 40}, "loaded code"),
            "not_looping": ({"lifecycle": "stopping"}, "has not completed a loop"),
            "died": ({"exit_at_once": True}, "exited"),
        }
        for name, (behaviour, expected) in cases.items():
            with self.subTest(name):
                self.install_runtime(self.target, **behaviour)
                self.write_attempts()
                self.spawn_supervisor(self.previous)

                with mock.patch.object(rollout, "LOADED_RUNTIME_PROOF_TIMEOUT_SECONDS", 5.0):
                    with self.assertRaises(SystemExit) as caught:
                        self.roll_out()

                message = str(caught.exception)
                self.assertIn(expected, message)
                self.assertIn("restored previous runtime and status launcher; previous supervisor verified", message)
                self.assertNotIn("rollout complete", self.stdout.getvalue())
                self.assertEqual(self.link.resolve(), self.previous)
                self.assertEqual(self.launcher.read_text(), "old writer\n")
                self.assertEqual(rollout.common.iter_supervisor_pids(self.target), [])
                self.assertEqual(len(rollout.common.iter_supervisor_pids(self.previous)), 1)
                self.stop_everything()

    def test_wrong_config_digest_is_not_proof(self) -> None:
        self.spawn_supervisor(self.previous)
        with mock.patch.object(rollout, "config_digest", return_value="0" * 16), \
             mock.patch.object(rollout, "LOADED_RUNTIME_PROOF_TIMEOUT_SECONDS", 5.0):
            with self.assertRaises(SystemExit) as caught:
                self.roll_out()
        self.assertIn("loaded config", str(caught.exception))
        self.assertNotIn("rollout complete", self.stdout.getvalue())
        self.assertEqual(self.link.resolve(), self.previous)

    def test_bad_watchdog_output_is_a_failure_despite_exit_status(self) -> None:
        cases = {
            "echo not json": "malformed watchdog result",
            "echo '[]'": "malformed watchdog result",
            "echo '{}'": "malformed watchdog result",
            'echo \'{"decision": "observe_only", "reason": "supervisor_healthy"}\'': "malformed watchdog result",
            'echo \'{"decision": "restart_supervisor", "reason": "x", "new_pid": true}\'': "new_pid=True",
            'echo \'{"decision": "observe_only", "reason": "watchdog_disabled", "new_pid": null}\'': "reason=watchdog_disabled",
            "exit 3": "watchdog exited 3",
        }
        script = self.target / "scripts" / "run-supervisor-watchdog.sh"
        for body, expected in cases.items():
            with self.subTest(body):
                script.write_text(f"#!/usr/bin/env bash\n{body}\n")
                self.write_attempts()
                self.spawn_supervisor(self.previous)

                with self.assertRaises(SystemExit) as caught:
                    self.roll_out()

                self.assertIn(expected, str(caught.exception))
                self.assertIn("previous supervisor verified", str(caught.exception))
                self.assertEqual(self.link.resolve(), self.previous)
                self.assertEqual(self.launcher.read_text(), "old writer\n")
                self.assertEqual(len(rollout.common.iter_supervisor_pids(self.previous)), 1)
                self.assertNotIn("rollout complete", self.stdout.getvalue())
                self.stop_everything()

    def test_exception_after_the_switch_takes_the_rollback_path(self) -> None:
        real_terminate = rollout.common.terminate_supervisor_processes
        real_proof = rollout.loaded_runtime_failure

        def stop_raises(root: Path, **kwargs: object) -> dict[int, bool]:
            if root == self.previous:
                raise OSError("stop broke")
            return real_terminate(root, **kwargs)

        def proof_raises(context: object, runtime: Path, *args: object) -> str | None:
            if runtime == self.target:
                raise ValueError("proof broke")
            return real_proof(context, runtime, *args)

        cases = {
            "OSError: stop broke": ("terminate_supervisor_processes", rollout.common, stop_raises),
            "ValueError: proof broke": ("loaded_runtime_failure", rollout, proof_raises),
        }
        for expected, (name, owner, replacement) in cases.items():
            with self.subTest(expected):
                self.write_attempts()
                self.spawn_supervisor(self.previous)

                with mock.patch.object(owner, name, replacement), self.assertRaises(SystemExit) as caught:
                    self.roll_out()

                message = str(caught.exception)
                self.assertIn(f"restart raised {expected}", message)
                self.assertIn("restored previous runtime and status launcher; previous supervisor verified", message)
                self.assertNotIn("rollout complete", self.stdout.getvalue())
                self.assertEqual(self.link.resolve(), self.previous)
                self.assertEqual(self.launcher.read_text(), "old writer\n")
                self.assertEqual(rollout.common.iter_supervisor_pids(self.target), [])
                self.assertEqual(len(rollout.common.iter_supervisor_pids(self.previous)), 1)
                self.stop_everything()

    def test_exception_during_recovery_is_reported_with_the_selection_restored(self) -> None:
        self.spawn_supervisor(self.previous)
        real_prove = rollout.run_watchdog_and_prove

        def recovery_raises(context: object, runtime: Path, code_sha: str) -> str | None:
            if runtime == self.previous:
                raise ValueError("recovery broke")
            return real_prove(context, runtime, code_sha)

        (self.target / "scripts" / "run-supervisor-watchdog.sh").write_text("#!/usr/bin/env bash\nexit 3\n")
        with mock.patch.object(rollout, "run_watchdog_and_prove", recovery_raises), self.assertRaises(SystemExit) as caught:
            self.roll_out()

        message = str(caught.exception)
        self.assertIn("watchdog exited 3", message)
        self.assertIn("previous supervisor NOT verified: ValueError: recovery broke", message)
        self.assertEqual(self.link.resolve(), self.previous)
        self.assertEqual(self.launcher.read_text(), "old writer\n")

    def test_supervisor_that_will_not_stop_is_not_joined_by_a_second(self) -> None:
        old = self.spawn_supervisor(self.previous)
        real_terminate = rollout.common.terminate_supervisor_processes

        def refuses_to_stop(root: Path, **kwargs: object) -> dict[int, bool]:
            return {old.pid: False} if root == self.previous else real_terminate(root, **kwargs)

        with mock.patch.object(rollout.common, "terminate_supervisor_processes", refuses_to_stop):
            with self.assertRaises(SystemExit) as caught:
                self.roll_out()

        self.assertIn(f"did not stop: [{old.pid}]", str(caught.exception))
        self.assertIn("previous supervisor verified", str(caught.exception))
        self.assertIsNone(old.poll())
        self.assertEqual(self.link.resolve(), self.previous)
        self.assertEqual(rollout.common.iter_supervisor_pids(self.target), [])
        self.assertEqual(json.loads(self.watchdog_state.read_text())["restart_attempts"], [])

    def test_budget_spent_while_preparing_refuses_before_the_switch(self) -> None:
        old = self.spawn_supervisor(self.previous)
        self.target.rename(self.root / "prepared")

        def prepare_while_cron_acts(_source: Path, target: Path, _branch: str, _sha: str) -> None:
            (self.root / "prepared").rename(target)
            self.write_attempts(120, 60, 30)

        config = self.config_file.read_text()
        with mock.patch.object(rollout, "add_runtime_worktree", prepare_while_cron_acts):
            with self.assertRaisesRegex(SystemExit, "restart_budget_window_exhausted"):
                self.roll_out()

        self.assert_untouched(old, config, self.watchdog_state.read_text())

    def test_dry_run_reports_admission_and_changes_nothing(self) -> None:
        old = self.spawn_supervisor(self.previous)
        config = self.config_file.read_text()

        self.assertEqual(self.roll_out("--dry-run"), 0)
        self.assertIn("restart_admission=admissible", self.stdout.getvalue())
        self.assert_untouched(old, config, None)

        self.write_attempts(120, 60, 30)
        with self.assertRaisesRegex(SystemExit, "restart_budget_window_exhausted"):
            self.roll_out("--dry-run")
        self.assert_untouched(old, config, self.watchdog_state.read_text())

    def test_protected_refusal_still_comes_first(self) -> None:
        old = self.spawn_supervisor(self.previous)
        config = self.config_file.read_text()
        (self.status_root / "ai-status.json").write_text(
            json.dumps({"tasks": [{"id": "A", "protected_paths": ["guarded/zone"]}]}), encoding="utf-8"
        )
        with mock.patch.object(rollout, "protected_preflight", side_effect=SystemExit("refusing rollout: drift")), \
             mock.patch.object(rollout, "restart_refusal") as admission:
            with self.assertRaisesRegex(SystemExit, "refusing rollout: drift"):
                self.roll_out()
        admission.assert_not_called()
        self.assert_untouched(old, config, None)

    def test_reused_target_holding_a_protected_body_refuses_before_the_switch(self) -> None:
        old = self.spawn_supervisor(self.previous)
        config = self.config_file.read_text()
        with mock.patch.object(rollout, "clean", lambda repo: repo != self.target):
            with self.assertRaisesRegex(SystemExit, "existing target is not the expected clean runtime"):
                self.roll_out()
        self.assert_untouched(old, config, None)

    def test_proof_accepts_what_the_real_supervisor_stamps(self) -> None:
        """The proof reads the fields the stand-in writes; hold both to the real writer."""
        import supervisor

        context = rollout.load_watchdog_context(self.link, self.target, self.status_root, self.config_file, self.pid_file)
        pid = os.getpid()
        now = rollout.common.utc_now()
        state: dict[str, object] = {}
        with mock.patch.dict(os.environ, {rollout.common.CONFIG_PATH_ENV_VAR: str(self.config_file)}), \
             mock.patch.object(supervisor, "_TREE_PROVENANCE_CACHE", None), \
             mock.patch.object(supervisor, "_LOADED_PROVENANCE", None):
            supervisor.stamp_supervisor_runtime_state(
                context.config, state, planning_state=None, heartbeat_at=now, lifecycle="starting"
            )
            first_heartbeat = json.loads(json.dumps(state))
            supervisor.stamp_supervisor_runtime_state(
                context.config,
                state,
                planning_state=None,
                heartbeat_at=now,
                lifecycle="running",
                loop_started_at=now,
                loop_finished_at=now,
                loop_error=None,
            )
        code_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=rollout.common.ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        digest = rollout.config_digest(self.config_file)

        def failure_for(document: object) -> str | None:
            self.state_file.write_text(json.dumps(document))
            return rollout.loaded_runtime_failure(context, self.target, code_sha, digest, pid)

        with mock.patch.object(rollout.common, "iter_supervisor_pids", return_value=[pid]), \
             mock.patch.object(rollout.supervisor_watchdog, "supervisor_lock_held", return_value=True):
            self.assertIn("has not completed a loop", failure_for(first_heartbeat))
            self.assertIsNone(failure_for(state))
            self.assertIn("loaded code", rollout.loaded_runtime_failure(context, self.target, "2" * 40, digest, pid))
            self.assertIn("loaded config", rollout.loaded_runtime_failure(context, self.target, code_sha, "0" * 16, pid))
        with mock.patch.object(rollout.common, "iter_supervisor_pids", return_value=[pid + 1]), \
             mock.patch.object(rollout.supervisor_watchdog, "supervisor_lock_held", return_value=True):
            self.assertIn("heartbeat", rollout.loaded_runtime_failure(context, self.target, code_sha, digest, pid + 1))


class ProtectedRolloutPreflightTests(unittest.TestCase):
    """Judge the target from metadata before any runtime checkout exists."""

    PROFILE = b"profile odayplus-bwrap /usr/bin/bwrap flags=(unconfined) {\n  userns,\n}\n"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Keep host git config (e.g. a CI runner's global LFS filter) out of the fixtures.
        (self.root / "gitconfig").touch()
        env = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(self.root / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.source = self.root / "source"
        self.source.mkdir()
        self._git("init", "-q", "-b", "dev")
        self._git("config", "user.email", "t@example.invalid")
        self._git("config", "user.name", "T")
        (self.source / ".orchestrator" / "profiles").mkdir(parents=True)
        (self.source / ".orchestrator" / "profiles" / "bwrap").write_bytes(self.PROFILE)
        self._git("add", ".")
        self._git("commit", "-q", "-m", "runtime")
        self.target_sha = self._git("rev-parse", "HEAD")
        self.installed = self.root / "installed-bwrap"
        self.installed.write_bytes(self.PROFILE)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _git(self, *args: str, stdin: str | None = None) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.source, input=stdin, capture_output=True, text=True, check=True
        ).stdout.strip()

    def test_board_protection_is_the_restrictive_union(self) -> None:
        status_root = self.root / "status"
        status_root.mkdir()
        self.assertEqual(rollout.recorded_protected_paths(status_root), ())
        (status_root / "ai-status.json").write_text(
            json.dumps({"tasks": [{"id": "A", "protected_paths": ["guarded/zone"]}, {"id": "B"}]}), encoding="utf-8"
        )
        self.assertEqual(rollout.recorded_protected_paths(status_root), ("guarded/zone",))
        (status_root / "ai-status.json").write_text(
            json.dumps({"tasks": [{"id": "A", "protected_paths": "guarded/zone"}]}), encoding="utf-8"
        )
        with self.assertRaisesRegex(SystemExit, "unusable protected_paths"):
            rollout.recorded_protected_paths(status_root)

    def test_matching_reviewed_profile_and_safe_target_pass(self) -> None:
        rollout.protected_preflight(self.source, self.target_sha, ("guarded/zone",), self.installed)
        rollout.protected_preflight(self.source, self.target_sha, (), self.root / "absent")

    def test_profile_drift_or_absence_refuses_without_installing(self) -> None:
        self.installed.write_bytes(b"something else\n")
        with self.assertRaisesRegex(SystemExit, "differs from reviewed"):
            rollout.protected_preflight(self.source, self.target_sha, ("guarded/zone",), self.installed)
        self.assertEqual(self.installed.read_bytes(), b"something else\n")
        with self.assertRaisesRegex(SystemExit, "installed at"):
            rollout.protected_preflight(self.source, self.target_sha, ("guarded/zone",), self.root / "absent")

    def test_forbidden_target_refuses_from_metadata(self) -> None:
        blob = self._git("hash-object", "-w", "--stdin", stdin="elsewhere")
        listing = self._git("ls-tree", self.target_sha)
        tree = self._git("mktree", stdin=f"{listing}\n120000 blob {blob}\tguarded\n")
        escape = self._git("commit-tree", tree, "-p", self.target_sha, "-m", "escape")
        with self.assertRaisesRegex(SystemExit, "ancestor_escape"):
            rollout.protected_preflight(self.source, escape, ("guarded/zone",), self.installed)

    def test_runtime_worktree_is_registered_without_checkout_then_proven(self) -> None:
        target = self.root / "runtime"
        commands: list[tuple[str, ...]] = []
        real_git = rollout.git

        def recording_git(repo: Path, *args: str) -> str:
            commands.append(args)
            return real_git(repo, *args)

        with mock.patch.object(rollout, "git", recording_git):
            rollout.add_runtime_worktree(self.source, target, "runtime-live-x", self.target_sha)

        self.assertIn("--no-checkout", commands[0])
        self.assertEqual(commands[-1][:2], ("read-tree", "--reset"))
        self.assertEqual(real_git(target, "rev-parse", "HEAD"), self.target_sha)
        self.assertEqual((target / ".orchestrator" / "profiles" / "bwrap").read_bytes(), self.PROFILE)
        self.assertTrue(rollout.clean(target))

    # --- the shared protected body domain, on the original rollout path

    DOMAIN_COPY = "archive/untracked/vault/copies/body.py.txt"

    def _enter_body_domain(self) -> None:
        for patcher in (
            mock.patch.object(rollout.worktree_cleanliness, "PROTECTED_BODY_PREFIXES", ("vault/copies",)),
            mock.patch.object(rollout.worktree_cleanliness, "PROTECTED_BODY_NAMES", ("synthetic_asset.py",)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        (self.source / self.DOMAIN_COPY).parent.mkdir(parents=True)
        (self.source / self.DOMAIN_COPY).write_text("synthetic archived copy\n", encoding="utf-8")
        self._git("add", self.DOMAIN_COPY)
        self._git("commit", "-q", "-m", "archive a synthetic copy")
        self.target_sha = self._git("rev-parse", "HEAD")

    def test_runtime_excludes_the_body_domain_before_materializing_it(self) -> None:
        self._enter_body_domain()
        target = self.root / "runtime"
        order: list[str] = []
        real_install = rollout.worktree_cleanliness.install_protected_body_sparse
        real_git = rollout.git

        def recording_install(path: Path, *a: object, **kw: object) -> str | None:
            order.append("exclude")
            return real_install(path, *a, **kw)

        def recording_git(repo: Path, *args: str) -> str:
            if args[:1] == ("read-tree",):
                order.append("materialize")
            return real_git(repo, *args)

        with mock.patch.object(rollout.worktree_cleanliness, "install_protected_body_sparse", recording_install), \
             mock.patch.object(rollout, "git", recording_git):
            rollout.add_runtime_worktree(self.source, target, "runtime-live-dom", self.target_sha)

        self.assertEqual(order, ["exclude", "materialize"])
        self.assertFalse(os.path.lexists(target / self.DOMAIN_COPY))
        self.assertEqual((target / ".orchestrator" / "profiles" / "bwrap").read_bytes(), self.PROFILE)
        self.assertEqual(real_git(target, "ls-files", "-t", "--", self.DOMAIN_COPY).split()[0], "S")
        self.assertEqual(real_git(target, "rev-parse", "HEAD"), self.target_sha)
        self.assertEqual(real_git(target, "symbolic-ref", "--quiet", "HEAD"), "refs/heads/runtime-live-dom")
        self.assertTrue(rollout.clean(target))

    def test_source_holding_a_body_copy_is_never_clean_and_never_status_read(self) -> None:
        self._enter_body_domain()
        self.assertTrue((self.source / self.DOMAIN_COPY).is_file())
        commands: list[list[str]] = []
        real_run = subprocess.run

        def recording_run(args, *a, **kw):
            commands.append(list(args))
            return real_run(args, *a, **kw)

        with mock.patch("subprocess.run", side_effect=recording_run):
            self.assertFalse(rollout.clean(self.source))
        self.assertFalse([command for command in commands if "status" in command])
        self.assertTrue((self.source / self.DOMAIN_COPY).is_file(), "refusal never deletes the copy")

    def test_untracked_or_drifted_domain_entries_keep_source_and_reused_target_unclean(self) -> None:
        """HEAD/index union and untracked names, on the source and on a reused runtime."""
        self._enter_body_domain()
        target = self.root / "runtime"
        rollout.add_runtime_worktree(self.source, target, "runtime-live-dom", self.target_sha)
        self.assertTrue(rollout.clean(target))
        real_run = subprocess.run

        def refused_without_status(repo: Path) -> None:
            commands: list[list[str]] = []

            def recording_run(args, *a, **kw):
                commands.append(list(args))
                return real_run(args, *a, **kw)

            with mock.patch("subprocess.run", side_effect=recording_run):
                self.assertFalse(rollout.clean(repo))
            self.assertFalse([command for command in commands if "status" in command])
            self.assertFalse([command for command in commands if any(f.startswith("--exclude") for f in command)])

        # Reused target: an untracked .gitignore inside the domain that status would open.
        ignore = target / "other" / "vault" / "copies" / ".gitignore"
        ignore.parent.mkdir(parents=True)
        ignore.write_text("hidden.ban\n", encoding="utf-8")
        (ignore.parent / "hidden.ban").write_text("synthetic ignored\n", encoding="utf-8")
        refused_without_status(target)
        self.assertIn("untracked domain content", rollout.worktree_cleanliness.protected_body_checkout_refusal(target))
        shutil.rmtree(target / "other")
        self.assertTrue(rollout.clean(target))

        # Reused target: the archived copy dropped from the index while its body is back.
        rollout.git(target, "update-index", "--force-remove", self.DOMAIN_COPY)
        (target / self.DOMAIN_COPY).parent.mkdir(parents=True, exist_ok=True)
        (target / self.DOMAIN_COPY).write_text("synthetic leftover\n", encoding="utf-8")
        refused_without_status(target)
        self.assertIn("in HEAD but not in the index", rollout.worktree_cleanliness.protected_body_checkout_refusal(target))

        # Source: an untracked domain entry beside an otherwise unremarkable checkout.
        self._git("rm", "-q", "--cached", self.DOMAIN_COPY)
        self._git("commit", "-q", "-m", "drop the synthetic copy from the tree")
        self.assertTrue((self.source / self.DOMAIN_COPY).is_file())
        refused_without_status(self.source)
        self.assertIn("untracked domain content", rollout.worktree_cleanliness.protected_body_checkout_refusal(self.source))

    def test_failed_exclusion_or_proof_stops_before_or_after_materializing(self) -> None:
        self._enter_body_domain()
        with mock.patch.object(rollout.worktree_cleanliness, "install_protected_body_sparse", return_value="sparse_exclusion_failed: x"):
            with self.assertRaisesRegex(RuntimeError, "protected body exclusion failed"):
                rollout.add_runtime_worktree(self.source, self.root / "runtime-a", "runtime-live-a", self.target_sha)
        self.assertFalse((self.root / "runtime-a" / "README").exists())
        self.assertFalse((self.root / "runtime-a" / ".orchestrator").exists(), "nothing was materialized")
        with mock.patch.object(rollout.worktree_cleanliness, "protected_body_checkout_refusal", return_value="protected_body_present: x"):
            with self.assertRaisesRegex(RuntimeError, "materialized protected body domain"):
                rollout.add_runtime_worktree(self.source, self.root / "runtime-b", "runtime-live-b", self.target_sha)

    def test_source_with_unreadable_metadata_refuses_before_any_fetch(self) -> None:
        main_root = self.root / "main"
        main_root.mkdir()
        source, parent, _target, _previous, link, status_root = prepare_rollout(main_root)
        calls: list[tuple[str, ...]] = []

        def tracking_git(repo: Path, *args: str) -> str:
            calls.append(args)
            return fake_git(repo, *args)

        with mock.patch.object(rollout, "git", tracking_git):
            with self.assertRaisesRegex(SystemExit, "dirty source checkout"):
                rollout.main(rollout_args(source, parent, link, status_root))
        self.assertEqual(calls, [])

    def test_main_refuses_before_preparing_any_runtime(self) -> None:
        main_root = self.root / "main"
        main_root.mkdir()
        source, parent, target, _previous, link, status_root = prepare_rollout(main_root)
        target.rmdir()
        (status_root / "ai-status.json").write_text(
            json.dumps({"tasks": [{"id": "A", "protected_paths": ["guarded/zone"]}]}), encoding="utf-8"
        )
        calls: list[tuple[str, ...]] = []

        def tracking_git(repo: Path, *args: str) -> str:
            calls.append(args)
            return fake_git(repo, *args)

        with mock.patch.object(rollout, "git", tracking_git), \
             mock.patch.object(rollout, "clean", lambda _repo: True), \
             mock.patch.object(rollout, "protected_preflight", side_effect=SystemExit("refusing rollout: drift")) as gate:
            with self.assertRaisesRegex(SystemExit, "refusing rollout"):
                rollout.main(rollout_args(source, parent, link, status_root))

        self.assertEqual(gate.call_args.args[2], ("guarded/zone",))
        self.assertFalse(any(call[:1] == ("worktree",) for call in calls))
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
