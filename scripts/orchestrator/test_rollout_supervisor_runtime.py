from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import rollout_supervisor_runtime as rollout

SHA = "1" * 40


def prepare_rollout(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
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


def test_rollout_installs_launcher_for_stable_runtime_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, parent, target, _previous, link, status_root = prepare_rollout(tmp_path)
    launcher = status_root / "scripts" / "ai-status.sh"
    launcher.write_text("old writer\n")
    monkeypatch.setattr(rollout, "git", fake_git)
    monkeypatch.setattr(rollout, "clean", lambda _repo: True)
    monkeypatch.setattr(
        rollout.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    assert rollout.main(rollout_args(source, parent, link, status_root)) == 0

    assert link.resolve() == target.resolve()
    assert str(link / "scripts" / "ai_status.py") in launcher.read_text()
    assert "PANTHEON_STATUS_ROOT" in launcher.read_text()
    assert os.access(launcher, os.X_OK)


def test_failed_restart_restores_runtime_and_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, parent, _target, previous, link, status_root = prepare_rollout(tmp_path)
    launcher = status_root / "scripts" / "ai-status.sh"
    launcher.write_text("old writer\n")
    launcher.chmod(0o744)
    restarts = iter((SimpleNamespace(returncode=1), SimpleNamespace(returncode=0)))
    monkeypatch.setattr(rollout, "git", fake_git)
    monkeypatch.setattr(rollout, "clean", lambda _repo: True)
    monkeypatch.setattr(rollout.subprocess, "run", lambda *args, **kwargs: next(restarts))

    with pytest.raises(SystemExit, match="restored previous runtime and status launcher"):
        rollout.main(rollout_args(source, parent, link, status_root))

    assert link.resolve() == previous.resolve()
    assert launcher.read_text() == "old writer\n"
    assert launcher.stat().st_mode & 0o777 == 0o744
