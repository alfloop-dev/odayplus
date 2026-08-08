"""Tests for the supervisor runtime freshness check."""

from __future__ import annotations

import subprocess
from pathlib import Path

import check_runtime_freshness as fresh


def make_repo(tmp_path: Path, commits: int = 1) -> Path:
    repo = tmp_path / "runtime"
    repo.mkdir()

    def run(*a: str) -> None:
        subprocess.run(list(a), cwd=repo, check=True, capture_output=True)

    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@e.com")
    run("git", "config", "user.name", "t")
    for i in range(commits):
        (repo / f"f{i}.txt").write_text(str(i), encoding="utf-8")
        run("git", "add", "-A")
        run("git", "commit", "-q", "-m", f"c{i}")
    return repo


def test_named_branch_at_tip_is_ok(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    ok, problems, facts = fresh.evaluate(repo, "HEAD", max_behind=25)

    assert ok
    assert problems == []
    assert facts["branch"] == "main"


def test_detached_head_fails_even_when_current(tmp_path: Path) -> None:
    """The detached-HEAD defect does not depend on how old the commit is."""
    repo = make_repo(tmp_path)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(["git", "checkout", "-q", head], cwd=repo, check=True, capture_output=True)

    ok, problems, _ = fresh.evaluate(repo, "HEAD", max_behind=25)

    assert not ok
    assert any("detached HEAD" in p for p in problems)


def test_behind_tolerance_fails(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, commits=5)

    def run(*a: str) -> None:
        subprocess.run(list(a), cwd=repo, check=True, capture_output=True)

    run("git", "branch", "tracking")
    run("git", "reset", "--hard", "HEAD~3")

    ok, problems, facts = fresh.evaluate(repo, "tracking", max_behind=1)

    assert not ok
    assert facts["behind"] == 3
    assert any("commits behind" in p for p in problems)


def test_within_tolerance_passes(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, commits=5)

    def run(*a: str) -> None:
        subprocess.run(list(a), cwd=repo, check=True, capture_output=True)

    run("git", "branch", "tracking")
    run("git", "reset", "--hard", "HEAD~2")

    ok, _, facts = fresh.evaluate(repo, "tracking", max_behind=5)

    assert ok
    assert facts["behind"] == 2


def test_unreadable_repo_fails_closed(tmp_path: Path) -> None:
    """Cannot-verify must never be reported as fresh."""
    ok, problems, _ = fresh.evaluate(tmp_path / "absent", "origin/dev", max_behind=25)

    assert not ok
    assert any("cannot read git state" in p for p in problems)


def test_unknown_tracking_ref_fails_closed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    ok, problems, _ = fresh.evaluate(repo, "origin/does-not-exist", max_behind=25)

    assert not ok
    assert any("cannot compare" in p for p in problems)


def test_main_returns_nonzero_on_drift(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, commits=4)

    def run(*a: str) -> None:
        subprocess.run(list(a), cwd=repo, check=True, capture_output=True)

    run("git", "branch", "tracking")
    run("git", "reset", "--hard", "HEAD~3")

    assert (
        fresh.main(
            ["--runtime", str(repo), "--tracking-ref", "tracking", "--max-behind", "1"]
        )
        == 1
    )
