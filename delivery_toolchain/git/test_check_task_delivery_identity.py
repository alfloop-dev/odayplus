from __future__ import annotations

import subprocess
from pathlib import Path

from delivery_toolchain.git.check_task_delivery_identity import (
    HelperSource,
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


def test_a_full_sha_base_is_the_commit_it_names_not_a_ref_of_that_spelling(tmp_path: Path) -> None:
    """``origin/<sha>`` is a branch anyone can create; ``<sha>`` is immutable.

    Codex writes ``C`` on base ``B`` and Claude writes ``D``. A ref spelled
    ``origin/<B>`` pointed at ``C`` would, if preferred, shrink the scanned
    range to ``C..D`` and drop Codex from the authors. A branch name keeps its
    remote-first reading.
    """
    repo = repository(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    codex = commit(repo, "TASK-ONE: source change", "TASK-ONE", agent="Codex")
    head = commit(repo, "TASK-ONE: finalizer change", "TASK-ONE", agent="Claude")
    git(repo, "update-ref", f"refs/remotes/origin/{base}", codex)
    assert git(repo, "rev-parse", f"origin/{base}") == codex

    report = inspect_delivery_identity(repo, task_id="TASK-ONE", base=base, head=head)

    assert report.errors == []
    assert report.base_sha == base
    assert report.head_sha == head
    assert report.source_agents == ("Codex", "Claude")
    assert [sha for sha, _agent in report.commits] == [codex, head]

    # The branch spelling still prefers the remote integration ref.
    git(repo, "update-ref", "refs/remotes/origin/dev", codex)
    by_branch = inspect_delivery_identity(repo, task_id="TASK-ONE", base="dev", head=head)
    assert by_branch.base_sha == codex
    assert by_branch.source_agents == ("Claude",)


def test_a_full_sha_base_that_is_not_a_local_commit_is_not_read_as_a_ref(tmp_path: Path) -> None:
    """A missing immutable base fails; it does not fall back to a same-named ref."""
    repo = repository(tmp_path)
    codex = commit(repo, "TASK-ONE: source change", "TASK-ONE", agent="Codex")
    head = commit(repo, "TASK-ONE: finalizer change", "TASK-ONE", agent="Claude")
    missing = "f" * 40
    git(repo, "update-ref", f"refs/remotes/origin/{missing}", codex)

    report = inspect_delivery_identity(repo, task_id="TASK-ONE", base=missing, head=head)

    assert report.source_agents == ()
    assert any("does not resolve to a commit" in error for error in report.errors), report.errors


def write_commit(
    repo: Path, task_id: str, subject: str, *, agent: str = "Pi", files: dict[str, str], trailers: str | None = None
) -> str:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
        git(repo, "add", "--", name)
    if trailers is None:
        reviewer = "Claude" if agent == "Codex" else "Codex"
        trailers = f"LLM-Agent: {agent}\nTask-ID: {task_id}\nReviewer: {reviewer}\n"
    git(repo, "commit", "-m", f"{task_id}: {subject}\n\n{trailers}")
    return git(repo, "rev-parse", "HEAD")


CHILD_SCOPE = ("uv.lock", "docs/evidence/sbom.json")


def composed(tmp_path: Path) -> tuple[Path, str, str, str]:
    """A parent branch with its own commit and a source-only child merged into it.

    The child is cut from the frozen base, Pi writes it, and the parent owner
    merges its exact head. Returns the repo, the frozen base, the child head and
    the parent head.
    """
    repo = repository(tmp_path)
    frozen = git(repo, "rev-parse", "HEAD")
    write_commit(repo, "TASK-ONE", "parent work", agent="Codex", files={"app.py": "parent"})
    git(repo, "switch", "-c", "task/CHILD-ONE", frozen)
    child_head = write_commit(repo, "CHILD-ONE", "patch lock", files={"uv.lock": "patched"})
    git(repo, "switch", "task/TASK-ONE")
    git(
        repo, "merge", "--no-ff", child_head, "-m",
        "Merge CHILD-ONE\n\nLLM-Agent: Codex\nTask-ID: TASK-ONE\nReviewer: Claude\n",
    )
    return repo, frozen, child_head, git(repo, "rev-parse", "HEAD")


def child(base: str, head: str, *, scope: tuple[str, ...] = CHILD_SCOPE, task_id: str = "CHILD-ONE") -> HelperSource:
    return HelperSource(task_id=task_id, branch="task/CHILD-ONE", base_sha=base, head_sha=head, owned_paths=scope)


def test_a_bounded_source_only_child_is_admitted_and_its_author_kept(tmp_path: Path) -> None:
    """The child's commit is the parent's source, and Pi is one of its authors.

    The scanner only proves the Git facts of the bound it is handed: the
    commit is the child's own, inside its frozen range, and the range's whole
    net change stays in the child's scope.
    """
    repo, frozen, child_head, head = composed(tmp_path)
    asked: list[str] = []

    report = inspect_delivery_identity(
        repo, task_id="TASK-ONE", base="dev", head=head,
        resolve_helper=lambda child_id: asked.append(child_id) or child(frozen, child_head),
    )

    assert report.errors == []
    assert asked == ["CHILD-ONE"]
    assert set(report.source_agents) == {"Codex", "Pi"}
    assert report.helpers == (
        {
            "task_id": "CHILD-ONE",
            "branch": "task/CHILD-ONE",
            "source_base": frozen,
            "source_head": child_head,
            "owned_paths": list(CHILD_SCOPE),
            "commits": [[child_head, "Pi"]],
        },
    )
    # Without the rule the same range keeps its original refusal.
    plain = validate_delivery_identity(repo, task_id="TASK-ONE", base="dev", head=head)
    assert any("Task-ID trailer 'CHILD-ONE' does not match 'TASK-ONE'" in error for error in plain), plain


def test_a_refused_child_keeps_the_original_refusal_and_says_why(tmp_path: Path) -> None:
    repo, _frozen, child_head, head = composed(tmp_path)

    errors = validate_delivery_identity(
        repo, task_id="TASK-ONE", base="dev", head=head,
        resolve_helper=lambda _child_id: "its record is not a direct child of TASK-ONE",
    )

    assert any(
        child_head[:12] in error and "not a source child" in error and "not a direct child" in error
        for error in errors
    ), errors
    assert any("Task-ID trailer 'CHILD-ONE' does not match 'TASK-ONE'" in error for error in errors), errors


def test_every_git_fact_of_the_bound_is_proven_not_taken(tmp_path: Path) -> None:
    repo, frozen, child_head, head = composed(tmp_path)
    # A second child commit, after the frozen head, merged into the parent too.
    git(repo, "switch", "task/CHILD-ONE")
    later = write_commit(repo, "CHILD-ONE", "later lock", files={"uv.lock": "later"})
    git(repo, "switch", "task/TASK-ONE")
    git(
        repo, "merge", "--no-ff", later, "-m",
        "Merge CHILD-ONE again\n\nLLM-Agent: Codex\nTask-ID: TASK-ONE\nReviewer: Claude\n",
    )
    beyond = git(repo, "rev-parse", "HEAD")
    unmerged = git(repo, "commit-tree", f"{child_head}^{{tree}}", "-p", child_head, "-m", "CHILD-ONE: x")

    cases = [
        ("commit beyond the frozen head", beyond, child(frozen, child_head),
         f"commit {later[:12]}: names child 'CHILD-ONE' but is not in its frozen range"),
        ("head not in the delivery", head, child(frozen, unmerged), "is not an ancestor of delivery head"),
        ("base not under the head", head, child(head, child_head), "is not an ancestor of its own head"),
        ("abbreviated bound", head, child(frozen[:12], child_head), "is not a pair of complete commit names"),
        ("unknown commit", head, child(frozen, "e" * 40), "cannot be proven"),
        ("empty frozen range", head, child(child_head, child_head), "contains no non-merge commits"),
        ("path outside the child scope", head, child(frozen, child_head, scope=("docs/evidence/sbom.json",)),
         "changes 1 net path(s) outside its owned_paths: 'uv.lock'"),
        ("empty child scope", head, child(frozen, child_head, scope=()), "has no usable literal owned_paths"),
        ("whole-tree child scope", head, child(frozen, child_head, scope=(".",)),
         "has no usable literal owned_paths"),
        ("answer for another task", head, child(frozen, child_head, task_id="CHILD-TWO"),
         "but its source answer is for 'CHILD-TWO'"),
    ]
    for name, delivery_head, bound, expected in cases:
        errors = validate_delivery_identity(
            repo, task_id="TASK-ONE", base="dev", head=delivery_head, resolve_helper=lambda _id, b=bound: b
        )
        assert any(expected in error for error in errors), (name, errors)


def test_the_frozen_range_must_be_wholly_and_only_the_childs_work(tmp_path: Path) -> None:
    """Partial containment, a foreign commit inside the range, bad trailers, hidden merges."""
    repo = repository(tmp_path)
    frozen = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-c", "task/CHILD-ONE", frozen)
    first = write_commit(repo, "CHILD-ONE", "first", files={"uv.lock": "one"})
    second = write_commit(repo, "CHILD-ONE", "second", files={"uv.lock": "two"})

    # The parent base already holds the first child commit: only part of the
    # frozen range is this delivery's, so its author is not proven here.
    git(repo, "switch", "-c", "partial", first)
    git(repo, "update-ref", "refs/remotes/origin/dev", first)
    parent = write_commit(repo, "TASK-ONE", "parent", agent="Codex", files={"app.py": "p"})
    git(repo, "merge", "--no-ff", second, "-m", "Merge\n\nLLM-Agent: Codex\nTask-ID: TASK-ONE\nReviewer: Claude\n")
    errors = validate_delivery_identity(
        repo, task_id="TASK-ONE", base="dev", head=git(repo, "rev-parse", "HEAD"),
        resolve_helper=lambda _id: child(frozen, second),
    )
    assert any(f"is only partly in this delivery; missing {first[:12]}" in error for error in errors), errors
    git(repo, "update-ref", "-d", "refs/remotes/origin/dev")
    assert parent

    # A commit of another task inside the frozen range is not the child's.
    git(repo, "switch", "-c", "foreign", second)
    smuggled = write_commit(repo, "TASK-ONE", "inside the child", agent="Codex", files={"uv.lock": "three"})
    third = write_commit(repo, "CHILD-ONE", "third", files={"uv.lock": "four"})
    errors = validate_delivery_identity(
        repo, task_id="TASK-ONE", base=frozen, head=third, resolve_helper=lambda _id: child(frozen, third)
    )
    assert any(f"commit {smuggled[:12]}: is in CHILD-ONE's frozen range but names task 'TASK-ONE'" in e
               for e in errors), errors

    # The child's own trailers are checked as the child's.
    git(repo, "switch", "-c", "untrailered", second)
    bare = write_commit(repo, "CHILD-ONE", "no reviewer", files={"uv.lock": "five"},
                        trailers="LLM-Agent: Pi\nTask-ID: CHILD-ONE\n")
    errors = validate_delivery_identity(
        repo, task_id="TASK-ONE", base=frozen, head=bare, resolve_helper=lambda _id: child(frozen, bare)
    )
    assert any(f"commit {bare[:12]}: missing required trailer 'Reviewer:'" in e for e in errors), errors

    # A merge inside the frozen range cannot carry an unowned path in through
    # its resolution: the net tree diff of the whole range is what is checked.
    git(repo, "switch", "-c", "hidden", second)
    side = git(repo, "commit-tree", f"{second}^{{tree}}", "-p", frozen, "-m", "side")
    git(repo, "merge", "--no-ff", "--no-commit", side)
    (repo / "secrets.txt").write_text("hidden", encoding="utf-8")
    git(repo, "add", "secrets.txt")
    git(repo, "commit", "-m", "CHILD-ONE: merge\n\nLLM-Agent: Pi\nTask-ID: CHILD-ONE\nReviewer: Codex\n")
    hidden = git(repo, "rev-parse", "HEAD")
    errors = validate_delivery_identity(
        repo, task_id="TASK-ONE", base=frozen, head=hidden, resolve_helper=lambda _id: child(frozen, hidden)
    )
    assert any("outside its owned_paths: 'secrets.txt'" in e for e in errors), errors
