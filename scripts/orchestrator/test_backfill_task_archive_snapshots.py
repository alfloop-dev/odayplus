"""Tests for the retroactive task archive snapshot backfill tool."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from backfill_task_archive_snapshots import (
    build_snapshot,
    find_merge_evidence,
    main,
    plan,
    subject_delivers,
)


def init_repo(tmp_path: Path, subjects: list[str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(  # noqa: E731
        list(a), cwd=repo, check=True, capture_output=True
    )
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    for i, subject in enumerate(subjects):
        (repo / f"f{i}.txt").write_text(str(i), encoding="utf-8")
        run("git", "add", "-A")
        run("git", "commit", "-q", "-m", subject)
    return repo


def test_find_merge_evidence_prefers_merge_commit(tmp_path: Path) -> None:
    repo = init_repo(
        tmp_path,
        [
            "TASK-1: work in progress",
            "Merge pull request #42 from org/task/TASK-1",
        ],
    )

    evidence = find_merge_evidence(repo, "TASK-1", "HEAD")

    assert evidence is not None
    assert evidence["merge_pr"] == "#42"
    assert "Merge pull request" in evidence["subject"]


def test_find_merge_evidence_accepts_squash_merge(tmp_path: Path) -> None:
    repo = init_repo(tmp_path, ["TASK-2: compose everything (#77)"])

    evidence = find_merge_evidence(repo, "TASK-2", "HEAD")

    assert evidence is not None
    assert evidence["merge_pr"] == "#77"


def test_find_merge_evidence_returns_none_when_absent(tmp_path: Path) -> None:
    repo = init_repo(tmp_path, ["unrelated commit"])

    assert find_merge_evidence(repo, "TASK-MISSING", "HEAD") is None


# --- subject_delivers: delivery form, not mention ------------------------


def test_subject_delivers_accepts_own_task_branch_merge() -> None:
    subject = "Merge pull request #678 from alfloop-dev/task/ODP-CI-FLAKE-001"

    assert subject_delivers(subject, "ODP-CI-FLAKE-001") == "merge-commit"


def test_subject_delivers_accepts_squash_subject() -> None:
    subject = "ODP-CI-FLAKE-001: fix vitest teardown race"

    assert subject_delivers(subject, "ODP-CI-FLAKE-001") == "squash-subject"


def test_subject_delivers_rejects_task_id_prefix_collision() -> None:
    """A sidecar task is a different task, even though its id starts the same."""

    merge = (
        "Merge pull request #679 from "
        "alfloop-dev/task/ODP-CI-FLAKE-001-SIDECAR-ACCEPTANCE"
    )
    squash = "ODP-CI-FLAKE-001-SIDECAR-ACCEPTANCE: refresh gates"

    assert subject_delivers(merge, "ODP-CI-FLAKE-001") is None
    assert subject_delivers(squash, "ODP-CI-FLAKE-001") is None


def test_subject_delivers_rejects_merge_of_another_task_branch() -> None:
    subject = "Merge pull request #360 from alfloop-dev/task/ODP-OTHER-001"

    assert subject_delivers(subject, "ODP-EXT-LIVE-001") is None


def test_subject_delivers_rejects_mere_mention_in_subject() -> None:
    subject = "ODP-OTHER-001: unblock work that ODP-CI-FLAKE-001 depends on"

    assert subject_delivers(subject, "ODP-CI-FLAKE-001") is None


def test_subject_delivers_accepts_reviewbus_subject() -> None:
    subject = "[ReviewBus] ODP-PLAN-AVM-OUTCOME-001 close the calibration loop (#587)"

    assert subject_delivers(subject, "ODP-PLAN-AVM-OUTCOME-001") == "reviewbus-subject"


def test_subject_delivers_rejects_reviewbus_sidecar_naming_its_parent() -> None:
    """The live archive corruption this fix prevents.

    The sidecar's own squash names the parent in its summary, so a
    mention-based scan filed the parent as delivered by the sidecar's PR.
    """

    subject = (
        "[ReviewBus] ODP-PLAN-SITESCORE-OUTCOME-001-SIDECAR-ACCEPTANCE "
        "Prepare ODP-PLAN-SITESCORE-OUTCOME-001 acceptance packet (#633)"
    )

    assert subject_delivers(subject, "ODP-PLAN-SITESCORE-OUTCOME-001") is None
    assert (
        subject_delivers(subject, "ODP-PLAN-SITESCORE-OUTCOME-001-SIDECAR-ACCEPTANCE")
        == "reviewbus-subject"
    )


def test_subject_delivers_accepts_merge_without_owner_prefix() -> None:
    """`Merge pull request #227 from task/ODP-GAP-OBS-001` occurs in history."""

    subject = "Merge pull request #227 from task/ODP-GAP-OBS-001"

    assert subject_delivers(subject, "ODP-GAP-OBS-001") == "merge-commit"
    assert subject_delivers(subject, "ODP-GAP-OBS") is None


def test_subject_delivers_rejects_non_task_branch_merge() -> None:
    subject = "Merge pull request #12 from alfloop-dev/hotfix/ODP-CI-FLAKE-001"

    assert subject_delivers(subject, "ODP-CI-FLAKE-001") is None


# --- regression: the two live failure modes ------------------------------


def test_find_merge_evidence_scans_past_body_only_mentions(tmp_path: Path) -> None:
    """The newest commit naming an id is usually another task's body mention.

    Previously only one candidate was examined, so the real merge below was
    reported as "no merge evidence".
    """

    repo = init_repo(
        tmp_path,
        [
            "Merge pull request #10 from org/task/TASK-REAL",
            "TASK-OTHER-001: rework intake\n\nFollows on from TASK-REAL.",
            "TASK-LATER-002: cleanup\n\nSee also TASK-REAL for context.",
        ],
    )

    evidence = find_merge_evidence(repo, "TASK-REAL", "HEAD")

    assert evidence is not None
    assert evidence["merge_pr"] == "#10"
    assert evidence["delivery_form"] == "merge-commit"


def test_find_merge_evidence_ignores_sidecar_merge_for_parent_task(
    tmp_path: Path,
) -> None:
    """The parent task must bind to its own PR, not the newer sidecar's."""

    repo = init_repo(
        tmp_path,
        [
            "Merge pull request #678 from org/task/TASK-1",
            "Merge pull request #679 from org/task/TASK-1-SIDECAR-ACCEPTANCE",
        ],
    )

    evidence = find_merge_evidence(repo, "TASK-1", "HEAD")

    assert evidence is not None
    assert evidence["merge_pr"] == "#678"
    assert evidence["subject"].endswith("/task/TASK-1")


def test_find_merge_evidence_prefers_merge_over_newer_squash(tmp_path: Path) -> None:
    repo = init_repo(
        tmp_path,
        [
            "Merge pull request #5 from org/task/TASK-1",
            "TASK-1: record closeout evidence",
        ],
    )

    evidence = find_merge_evidence(repo, "TASK-1", "HEAD")

    assert evidence["delivery_form"] == "merge-commit"
    assert evidence["merge_pr"] == "#5"


def test_find_merge_evidence_falls_back_to_squash_when_no_merge(
    tmp_path: Path,
) -> None:
    repo = init_repo(
        tmp_path,
        [
            "TASK-1: canonical schema layer",
            "Merge pull request #20 from org/task/TASK-UNRELATED",
        ],
    )

    evidence = find_merge_evidence(repo, "TASK-1", "HEAD")

    assert evidence is not None
    assert evidence["delivery_form"] == "squash-subject"
    assert evidence["subject"] == "TASK-1: canonical schema layer"


def test_plan_skips_task_with_only_body_mentions(tmp_path: Path) -> None:
    """A body mention must not be laundered into an archive snapshot."""

    repo = init_repo(
        tmp_path,
        ["TASK-REAL-001: real work\n\nUnblocks TASK-GHOST-002 downstream."],
    )
    archive = tmp_path / "tasks"
    archive.mkdir()

    to_write, _, unverified = plan(repo, archive, ("TASK-GHOST-002",), "HEAD")

    assert to_write == []
    assert unverified == ["TASK-GHOST-002"]


def test_snapshot_shape_satisfies_dependency_rule(tmp_path: Path) -> None:
    evidence = {
        "merge_commit": "a" * 40,
        "merged_at": "2026-07-28T00:00:00+00:00",
        "merge_pr": "#1",
        "subject": "Merge pull request #1 from org/task/TASK-1",
    }

    snapshot = build_snapshot("TASK-1", evidence, [])

    # task_archive.task_satisfies_dependency reads snapshot["task"].
    assert snapshot["task"]["status"] == "done"
    assert snapshot["terminal_outcome"] != "superseded"
    assert snapshot["terminal_status"] == "done"
    assert snapshot["task_id"] == "TASK-1"


def test_snapshot_is_labelled_retroactive(tmp_path: Path) -> None:
    evidence = {
        "merge_commit": "b" * 40,
        "merged_at": "2026-07-28T00:00:00+00:00",
        "merge_pr": "#2",
        "subject": "Merge pull request #2 from org/task/TASK-2",
    }

    snapshot = build_snapshot("TASK-2", evidence, [])

    assert snapshot["backfill"]["retroactive"] is True
    assert snapshot["backfill"]["merge_commit"] == "b" * 40
    assert "not from a live lifecycle transition" in snapshot["backfill"]["note"]


def test_plan_skips_unverified_tasks(tmp_path: Path) -> None:
    repo = init_repo(tmp_path, ["Merge pull request #1 from org/task/TASK-1"])
    archive = tmp_path / "tasks"
    archive.mkdir()

    to_write, existing, unverified = plan(
        repo, archive, ("TASK-1", "TASK-GHOST"), "HEAD"
    )

    assert [t for t, _ in to_write] == ["TASK-1"]
    assert existing == []
    assert unverified == ["TASK-GHOST"]


def test_plan_never_overwrites_existing_snapshot(tmp_path: Path) -> None:
    repo = init_repo(tmp_path, ["Merge pull request #1 from org/task/TASK-1"])
    archive = tmp_path / "tasks"
    archive.mkdir()
    (archive / "TASK-1.json").write_text('{"task_id": "TASK-1"}', encoding="utf-8")

    to_write, existing, _ = plan(repo, archive, ("TASK-1",), "HEAD")

    assert to_write == []
    assert existing == ["TASK-1"]


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    repo = init_repo(tmp_path, ["Merge pull request #1 from org/task/TASK-1"])
    archive = tmp_path / "tasks"
    archive.mkdir()

    exit_code = main(
        [
            "--archive-dir", str(archive),
            "--repo", str(repo),
            "--ref", "HEAD",
            "--task", "TASK-1",
        ]
    )

    assert exit_code == 0
    assert list(archive.glob("*.json")) == []


def test_apply_writes_resolvable_snapshot(tmp_path: Path) -> None:
    repo = init_repo(tmp_path, ["Merge pull request #9 from org/task/TASK-9"])
    archive = tmp_path / "tasks"
    archive.mkdir()

    exit_code = main(
        [
            "--archive-dir", str(archive),
            "--repo", str(repo),
            "--ref", "HEAD",
            "--task", "TASK-9",
            "--apply",
        ]
    )

    assert exit_code == 0
    payload = json.loads((archive / "TASK-9.json").read_text(encoding="utf-8"))
    assert payload["task"]["status"] == "done"
    assert payload["backfill"]["merge_pr"] == "#9"


def test_apply_does_not_touch_index_json(tmp_path: Path) -> None:
    repo = init_repo(tmp_path, ["Merge pull request #3 from org/task/TASK-3"])
    archive = tmp_path / "tasks"
    archive.mkdir()
    index = archive.parent / "index.json"
    index.write_text('{"counts": {"total": 0}}', encoding="utf-8")
    before = index.read_text(encoding="utf-8")

    main(
        [
            "--archive-dir", str(archive),
            "--repo", str(repo),
            "--ref", "HEAD",
            "--task", "TASK-3",
            "--apply",
        ]
    )

    assert index.read_text(encoding="utf-8") == before


def test_missing_archive_dir_fails_closed(tmp_path: Path) -> None:
    repo = init_repo(tmp_path, ["init"])

    exit_code = main(
        ["--archive-dir", str(tmp_path / "absent"), "--repo", str(repo)]
    )

    assert exit_code == 1


# --- regression tests carried forward from 66fd4300 ----------------------


def test_newer_unrelated_commit_does_not_hide_real_merge(tmp_path: Path) -> None:
    """--grep matches whole messages, so the newest hit may be someone else's commit.

    Live false negative: ODP-PLAN-AVM-OUTCOME-001 had a real merge, but a newer
    commit from another task mentioned the id in its body, and taking only one
    candidate reported the task as unverifiable.
    """
    repo = init_repo(
        tmp_path,
        [
            "Merge pull request #587 from org/task/TASK-A",
            "TASK-B: unrelated work that cites TASK-A in passing",
        ],
    )

    evidence = find_merge_evidence(repo, "TASK-A", "HEAD")

    assert evidence is not None
    assert evidence["merge_pr"] == "#587"


def test_squash_merge_found_behind_newer_mentions(tmp_path: Path) -> None:
    repo = init_repo(
        tmp_path,
        [
            "TASK-C: deliver the thing (#601)",
            "TASK-D: refer to TASK-C without delivering it",
            "TASK-E: also mentions TASK-C",
        ],
    )

    evidence = find_merge_evidence(repo, "TASK-C", "HEAD")

    assert evidence is not None
    assert evidence["merge_pr"] == "#601"


def test_only_body_mentions_still_yield_nothing(tmp_path: Path) -> None:
    """Scanning more candidates must not start accepting mere citations."""
    repo = init_repo(tmp_path, ["TASK-F: mentions TASK-GHOST in body only"])

    assert find_merge_evidence(repo, "TASK-GHOST", "HEAD") is None
