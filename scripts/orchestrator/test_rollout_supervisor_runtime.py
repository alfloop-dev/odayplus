from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
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

    def test_watchdog_restart_is_used_without_adding_a_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            source, parent, target, _previous, link, status_root = prepare_rollout(tmp_path)
            (status_root / ".orchestrator" / "supervisor.pid").write_text("123\n")
            observed: list[tuple[int | None, Path, Path, Path]] = []

            def fake_restart(
                pid: int | None, runtime_link: Path, canonical_root: Path, config_path: Path
            ) -> bool:
                observed.append((pid, runtime_link, canonical_root, config_path))
                return True

            with mock.patch.object(rollout, "git", fake_git), \
                 mock.patch.object(rollout, "clean", lambda _repo: True), \
                 mock.patch.object(rollout, "restart_with_watchdog", fake_restart):
                self.assertEqual(rollout.main(watchdog_rollout_args(source, parent, link, status_root)), 0)

            self.assertEqual(link.resolve(), target.resolve())
            self.assertEqual(
                observed,
                [(123, link, status_root.resolve(), status_root.resolve() / ".orchestrator" / "config.json")]
            )


class ProtectedRolloutPreflightTests(unittest.TestCase):
    """Judge the target from metadata before any runtime checkout exists."""

    PROFILE = b"profile odayplus-bwrap /usr/bin/bwrap flags=(unconfined) {\n  userns,\n}\n"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
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
