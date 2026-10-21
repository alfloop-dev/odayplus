from __future__ import annotations

import subprocess
from pathlib import Path

from delivery_toolchain.git.check_task_delivery_identity import (
    inspect_delivery_identity,
    validate_delivery_identity,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def commit(
    repo: Path, subject: str, task_id: str, *, agent: str = "Codex", extra_trailers: str = ""
) -> str:
    marker = repo / f"change-{len(list(repo.glob('change-*'))):02d}-{task_id}"
    marker.write_text(subject, encoding="utf-8")
    git(repo, "add", marker.name)
    message = (
        f"{subject}\n\n"
        "Delivery change.\n\n"
        f"LLM-Agent: {agent}\n"
        f"Task-ID: {task_id}\n"
        "Reviewer: Codex2\n"
        f"{extra_trailers}"
    )
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "dev")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "base").write_text("base", encoding="utf-8")
    git(repo, "add", "base")
    git(repo, "commit", "-m", "bootstrap")
    git(repo, "switch", "-c", "task/TASK-ONE")
    return repo


def test_entire_matching_delivery_range_passes(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    commit(repo, "TASK-ONE: add first guard", "TASK-ONE")
    head = commit(repo, "TASK-ONE: add second guard", "TASK-ONE")

    assert validate_delivery_identity(
        repo,
        task_id="TASK-ONE",
        base="dev",
        head=head,
        expected_branch="task/TASK-ONE",
        actual_branch="task/TASK-ONE",
    ) == []


def test_correct_head_cannot_hide_older_commit_for_another_task(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    wrong = commit(repo, "TASK-OLD: unrelated work", "TASK-OLD")
    head = commit(repo, "TASK-ONE: add delivery guard", "TASK-ONE")

    errors = validate_delivery_identity(repo, task_id="TASK-ONE", base="dev", head=head)

    assert any(wrong[:12] in error and "TASK-OLD" in error for error in errors)


def test_maintenance_skip_does_not_bypass_task_delivery_identity(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    marker = repo / "change-maintenance"
    marker.write_text("maintenance", encoding="utf-8")
    git(repo, "add", marker.name)
    git(repo, "commit", "-m", "publish: unrelated snapshot")
    head = commit(repo, "TASK-ONE: add delivery guard", "TASK-ONE")

    errors = validate_delivery_identity(repo, task_id="TASK-ONE", base="dev", head=head)

    assert any("publish: unrelated snapshot" in error for error in errors)


def test_recorded_branch_mismatch_fails(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    head = commit(repo, "TASK-ONE: add delivery guard", "TASK-ONE")

    errors = validate_delivery_identity(
        repo,
        task_id="TASK-ONE",
        base="dev",
        head=head,
        expected_branch="task/TASK-ONE",
        actual_branch="fix/reused-branch",
    )

    assert any("does not match recorded task branch" in error for error in errors)


def test_matching_trailer_does_not_excuse_a_subject_without_the_task_id(tmp_path: Path) -> None:
    """The subject rule is not satisfiable by the trailer alone.

    Every other case here writes a subject that already starts with the task id,
    or a commit with no matching trailer at all, so nothing pinned the one
    combination that matters: a well-formed ``Task-ID:`` trailer on a commit
    whose subject does not name the task. An edit found running uncommitted in
    the shared checkout exempted exactly that combination, and the suite stayed
    green either way.
    """
    repo = repository(tmp_path)
    head = commit(repo, "chore: tidy the delivery guard", "TASK-ONE")

    errors = validate_delivery_identity(repo, task_id="TASK-ONE", base="dev", head=head)

    assert any("subject must start with the task id" in error for error in errors), (
        "a matching Task-ID trailer must not excuse a subject that does not name the task"
    )


def test_historical_long_subject_passes_delivery_identity(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    long_subject = "TASK-ONE: " + "a" * 80
    head = commit(repo, long_subject, "TASK-ONE")

    assert validate_delivery_identity(
        repo,
        task_id="TASK-ONE",
        base="dev",
        head=head,
        expected_branch="task/TASK-ONE",
        actual_branch="task/TASK-ONE",
    ) == []


def test_every_non_merge_source_actor_is_reported_and_the_finalizer_merge_is_not(
    tmp_path: Path,
) -> None:
    """The finalizer that only merged a base is not an author of the range.

    Codex writes the task commit, `dev` advances, and Claude merges `dev` back in
    and adds a commit of its own. Both non-merge authors are the source; the
    merge commit -- whoever made it -- is not, and neither is the unrelated `dev`
    commit the merge brought along.
    """
    repo = repository(tmp_path)
    first = commit(repo, "TASK-ONE: add the guard", "TASK-ONE", agent="Codex")
    git(repo, "switch", "dev")
    (repo / "dev-only").write_text("dev", encoding="utf-8")
    git(repo, "add", "dev-only")
    git(repo, "commit", "-m", "OTHER-TASK: unrelated dev work")
    git(repo, "switch", "task/TASK-ONE")
    git(
        repo,
        "merge",
        "--no-ff",
        "dev",
        "-m",
        "Merge dev into task/TASK-ONE\n\nLLM-Agent: Claude\nTask-ID: TASK-ONE\nReviewer: Codex2\n",
    )
    head = commit(repo, "TASK-ONE: tighten the guard", "TASK-ONE", agent="Claude2")
    git(repo, "update-ref", "refs/remotes/origin/dev", "dev")

    report = inspect_delivery_identity(
        repo,
        task_id="TASK-ONE",
        base="dev",
        head=head,
        expected_branch="task/TASK-ONE",
        actual_branch="task/TASK-ONE",
    )

    assert report.errors == []
    assert report.source_agents == ("Codex", "Claude2")
    assert [sha for sha, _agent in report.commits] == [first, head]
    assert report.head_sha == head
    assert report.base_sha == git(repo, "rev-parse", "dev")


def test_an_unregistered_source_label_is_reported_verbatim_not_remapped(tmp_path: Path) -> None:
    """Resolving a label to an account is the policy's job, not the scanner's.

    `Codex-Root` is not a registered agent. Reporting it as written is what lets
    the caller refuse it; quietly normalising it to `Codex` -- or dropping it --
    would decide the review independence question before anyone asked it.
    """
    repo = repository(tmp_path)
    head = commit(repo, "TASK-ONE: root-authored change", "TASK-ONE", agent="Codex-Root")

    report = inspect_delivery_identity(repo, task_id="TASK-ONE", base="dev", head=head)

    assert report.errors == []
    assert report.source_agents == ("Codex-Root",)


def test_a_commit_naming_two_authors_is_rejected_rather_than_resolved(tmp_path: Path) -> None:
    """Appending a second `LLM-Agent:` line must not choose the author.

    The trailer parser keeps the last occurrence of a key, so without this rule
    the commit below would be read as Claude's and Codex's own pool would look
    independent of it.
    """
    repo = repository(tmp_path)
    head = commit(
        repo,
        "TASK-ONE: two authors",
        "TASK-ONE",
        agent="Codex",
        extra_trailers="LLM-Agent: Claude\n",
    )

    errors = validate_delivery_identity(repo, task_id="TASK-ONE", base="dev", head=head)

    assert any(head[:12] in error and "conflicting 'LLM-Agent:'" in error for error in errors), errors


def test_a_repeated_identical_trailer_is_not_a_conflict(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    head = commit(
        repo, "TASK-ONE: restated trailer", "TASK-ONE", agent="Codex", extra_trailers="LLM-Agent: codex\n"
    )

    report = inspect_delivery_identity(repo, task_id="TASK-ONE", base="dev", head=head)

    assert report.errors == []
    assert report.source_agents == ("codex",)


def test_an_empty_range_reports_no_authors_and_an_error(tmp_path: Path) -> None:
    """No commits is not "no authors to be independent of"; it is no delivery."""
    repo = repository(tmp_path)
    head = git(repo, "rev-parse", "HEAD")

    report = inspect_delivery_identity(repo, task_id="TASK-ONE", base="dev", head=head)

    assert report.source_agents == ()
    assert any("contains no non-merge commits" in error for error in report.errors)
