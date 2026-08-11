"""Tests for the finalize-lane doctor."""

from __future__ import annotations

import json
from pathlib import Path

import finalize_lane_doctor as doc


def board(tmp_path: Path, tasks: list[dict]) -> Path:
    p = tmp_path / "ai-status.json"
    p.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
    return p


def check(name: str, conclusion: str | None = None, status: str | None = None) -> dict:
    return {"name": name, "conclusion": conclusion, "status": status}


ALL_REQUIRED = ("orchestrator", "product", "product-e2e-gate", "task-review-gate")


def green_rollup() -> list[dict]:
    return [check(n, "SUCCESS") for n in ALL_REQUIRED]


def test_rollup_verdict_success() -> None:
    assert doc.rollup_verdict(green_rollup())[0] == "success"


def test_rollup_verdict_failure_lists_failing_checks() -> None:
    verdict, failing, _ = doc.rollup_verdict(
        [check("orchestrator", "SUCCESS"), check("product", "FAILURE")]
    )
    assert verdict == "failure"
    assert failing == ["product"]


def test_rollup_verdict_pending_when_check_not_concluded() -> None:
    verdict, failing, _ = doc.rollup_verdict(
        [check("orchestrator", "SUCCESS"), check("product", None, "IN_PROGRESS")]
    )
    assert verdict == "pending"
    assert failing == []


def test_cancelled_counts_as_failing() -> None:
    verdict, failing, _ = doc.rollup_verdict([check("product", "CANCELLED")])
    assert verdict == "failure"
    assert failing == ["product"]


def test_no_pr_is_detected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(doc, "find_pr", lambda b, r, base="": None)
    monkeypatch.setattr(doc, "_git", lambda *a, **k: "abc123\trefs/heads/x")

    f = doc.classify({"id": "T1"}, tmp_path, "dev", ALL_REQUIRED)

    assert f["cause"] == doc.NO_PR
    assert f["remote_branch"] is True
    assert "no pull request exists" in f["detail"]


def test_no_pr_and_no_branch_is_distinguished(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(doc, "find_pr", lambda b, r, base="": None)
    monkeypatch.setattr(doc, "_git", lambda *a, **k: "")

    f = doc.classify({"id": "T1"}, tmp_path, "dev", ALL_REQUIRED)

    assert f["cause"] == doc.NO_PR
    assert f["remote_branch"] is False


def test_missing_required_check_is_detected(tmp_path: Path, monkeypatch) -> None:
    # Green, but task-review-gate never reported: structurally unmergeable.
    rollup = [check(n, "SUCCESS") for n in ALL_REQUIRED if n != "task-review-gate"]
    monkeypatch.setattr(doc, "find_pr", lambda b, r, base="": {"number": 9, "statusCheckRollup": rollup})

    f = doc.classify({"id": "T1"}, tmp_path, "dev", ALL_REQUIRED)

    assert f["cause"] == doc.MISSING_REQUIRED_CHECK
    assert f["missing_checks"] == ["task-review-gate"]


def test_missing_check_outranks_green_verdict(tmp_path: Path, monkeypatch) -> None:
    """A PR whose reported checks are all green is still not READY if one is absent."""
    rollup = [check("orchestrator", "SUCCESS")]
    monkeypatch.setattr(doc, "find_pr", lambda b, r, base="": {"number": 9, "statusCheckRollup": rollup})

    f = doc.classify({"id": "T1"}, tmp_path, "dev", ALL_REQUIRED)

    assert f["cause"] != doc.READY


def test_stale_ci_distinguished_from_real_failure(tmp_path: Path, monkeypatch) -> None:
    rollup = green_rollup()[:-1] + [check("task-review-gate", "SUCCESS"), check("product", "FAILURE")]
    monkeypatch.setattr(doc, "find_pr", lambda b, r, base="": {"number": 9, "statusCheckRollup": rollup})

    monkeypatch.setattr(doc, "branch_is_behind", lambda *a: True)
    assert doc.classify({"id": "T1"}, tmp_path, "dev", ALL_REQUIRED)["cause"] == doc.CI_STALE

    monkeypatch.setattr(doc, "branch_is_behind", lambda *a: False)
    assert doc.classify({"id": "T1"}, tmp_path, "dev", ALL_REQUIRED)["cause"] == doc.CI_FAILED


def test_ready_when_all_required_green(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        doc, "find_pr", lambda b, r, base="": {"number": 9, "statusCheckRollup": green_rollup()}
    )

    assert doc.classify({"id": "T1"}, tmp_path, "dev", ALL_REQUIRED)["cause"] == doc.READY


def test_remediation_differs_per_cause() -> None:
    no_pr = doc.remediation(
        {"cause": doc.NO_PR, "branch": "task/T1", "task_id": "T1", "remote_branch": True}, "dev"
    )
    assert any("gh pr create" in line for line in no_pr)

    missing = doc.remediation(
        {"cause": doc.MISSING_REQUIRED_CHECK, "branch": "task/T1", "task_id": "T1"}, "dev"
    )
    assert any("ai_status.py assign" in line for line in missing)

    stale = doc.remediation(
        {"cause": doc.CI_STALE, "branch": "task/T1", "task_id": "T1", "pr": 9}, "dev"
    )
    assert any("git merge" in line for line in stale)


def test_unpushed_branch_gets_no_pr_create_command() -> None:
    lines = doc.remediation(
        {"cause": doc.NO_PR, "branch": "task/T1", "task_id": "T1", "remote_branch": False}, "dev"
    )
    assert not any("gh pr create" in line for line in lines)


def test_only_review_approved_tasks_are_scanned(tmp_path: Path, monkeypatch) -> None:
    p = board(
        tmp_path,
        [
            {"id": "A", "status": "review_approved"},
            {"id": "B", "status": "todo"},
            {"id": "C-SIDECAR-ACCEPTANCE", "status": "review_approved"},
        ],
    )
    monkeypatch.setattr(
        doc, "find_pr", lambda b, r, base="": {"number": 1, "statusCheckRollup": green_rollup()}
    )

    rc = doc.main(["--status", str(p), "--repo", str(tmp_path)])

    assert rc == 0  # only A scanned, and it is READY


def test_exit_code_signals_stuck_tasks(tmp_path: Path, monkeypatch) -> None:
    p = board(tmp_path, [{"id": "A", "status": "review_approved"}])
    monkeypatch.setattr(doc, "find_pr", lambda b, r, base="": None)
    monkeypatch.setattr(doc, "_git", lambda *a, **k: "")

    assert doc.main(["--status", str(p), "--repo", str(tmp_path)]) == 1


def test_already_merged_outranks_no_pr(tmp_path: Path, monkeypatch) -> None:
    """Work that has landed must not be reported as a missing-PR problem.

    Live case: a task sat at review_approved for two days after its branch merged.
    Trying to open a PR for it fails with "No commits between ...", which hides
    the real state.
    """
    monkeypatch.setattr(doc, "branch_merged_into_base", lambda *a: True)
    monkeypatch.setattr(doc, "find_pr", lambda b, r, base="": None)

    f = doc.classify({"id": "T1"}, tmp_path, "dev", ALL_REQUIRED)

    assert f["cause"] == doc.ALREADY_MERGED
    assert "already an ancestor" in f["detail"]


def test_already_merged_remediation_closes_task_not_opens_pr() -> None:
    lines = doc.remediation(
        {"cause": doc.ALREADY_MERGED, "branch": "task/T1", "task_id": "T1"}, "dev"
    )

    assert any("ai_status.py done" in line for line in lines)
    assert not any("gh pr create" in line for line in lines)


def test_a_rerun_supersedes_the_earlier_failure_of_the_same_check(tmp_path: Path, monkeypatch) -> None:
    """PR #575 merged into dev carrying a stale `product` FAILURE beside its SUCCESS.

    Branch protection read the newest run and merged. A doctor that counts every
    run reports the PR as CI_FAILED forever and sends the owner to rerun checks
    that already passed.
    """
    rollup = [
        {"name": "product", "conclusion": "FAILURE", "completedAt": "2026-08-04T06:31:02Z"},
        {"name": "product", "conclusion": "SUCCESS", "completedAt": "2026-08-06T22:19:41Z"},
        check("orchestrator", "SUCCESS"),
        check("product-e2e-gate", "SUCCESS"),
        check("task-review-gate", "SUCCESS"),
    ]
    monkeypatch.setattr(doc, "branch_merged_into_base", lambda *a: False)
    monkeypatch.setattr(doc, "find_pr", lambda b, r, base="": {"number": 575, "statusCheckRollup": rollup})

    assert doc.classify({"id": "T1"}, tmp_path, "dev", ALL_REQUIRED)["cause"] == doc.READY


def test_a_check_that_is_still_red_on_its_newest_run_stays_red(tmp_path: Path, monkeypatch) -> None:
    rollup = [
        {"name": "product", "conclusion": "SUCCESS", "completedAt": "2026-08-04T06:31:02Z"},
        {"name": "product", "conclusion": "FAILURE", "completedAt": "2026-08-06T22:19:41Z"},
        check("orchestrator", "SUCCESS"),
        check("product-e2e-gate", "SUCCESS"),
        check("task-review-gate", "SUCCESS"),
    ]
    monkeypatch.setattr(doc, "branch_merged_into_base", lambda *a: False)
    monkeypatch.setattr(doc, "branch_is_behind", lambda *a: False)
    monkeypatch.setattr(doc, "find_pr", lambda b, r, base="": {"number": 575, "statusCheckRollup": rollup})

    f = doc.classify({"id": "T1"}, tmp_path, "dev", ALL_REQUIRED)

    assert f["cause"] == doc.CI_FAILED
    assert f["failing_checks"] == ["product"]


def rerun_in_progress_over_an_older_success() -> list[dict]:
    """The `gh pr view --json statusCheckRollup` shape for a running re-run.

    Verbatim field set, including the two details `gh` emits that a hand-written
    fixture omits: `conclusion` is the empty string rather than absent, and
    `completedAt` is Go's zero time rather than absent.
    """
    return [
        {
            "__typename": "CheckRun",
            "name": "product",
            "workflowName": "CI",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
            "startedAt": "2026-08-06T22:10:04Z",
            "completedAt": "2026-08-06T22:19:41Z",
        },
        {
            "__typename": "CheckRun",
            "name": "product",
            "workflowName": "CI",
            "status": "IN_PROGRESS",
            "conclusion": "",
            "startedAt": "2026-08-09T13:02:11Z",
            "completedAt": "0001-01-01T00:00:00Z",
        },
    ]


def test_a_running_rerun_supersedes_the_success_it_replaces() -> None:
    """The zero `completedAt` must not sort the running re-run into the past."""
    latest = doc.latest_checks_by_name(rerun_in_progress_over_an_older_success())

    assert [c["status"] for c in latest] == ["IN_PROGRESS"]


def test_an_older_success_does_not_green_a_pr_whose_rerun_is_still_running(
    tmp_path: Path, monkeypatch
) -> None:
    """Fail closed: reporting READY here sends the owner to finalize unfinished CI."""
    rollup = [
        *rerun_in_progress_over_an_older_success(),
        check("orchestrator", "SUCCESS"),
        check("product-e2e-gate", "SUCCESS"),
        check("task-review-gate", "SUCCESS"),
    ]
    monkeypatch.setattr(doc, "branch_merged_into_base", lambda *a: False)
    monkeypatch.setattr(doc, "find_pr", lambda b, r, base="": {"number": 575, "statusCheckRollup": rollup})

    assert doc.classify({"id": "T1"}, tmp_path, "dev", ALL_REQUIRED)["cause"] == doc.CI_PENDING


def test_zero_completed_at_falls_back_to_started_at_not_to_no_timestamp() -> None:
    """A sentinel means "unfinished", so the entry still ranks by its start."""
    assert (
        doc.check_timestamp(
            {"startedAt": "2026-08-09T13:02:11Z", "completedAt": "0001-01-01T00:00:00Z"}
        )
        == "2026-08-09T13:02:11Z"
    )
    assert (
        doc.check_timestamp(
            {"startedAt": "0001-01-01T00:00:00Z", "completedAt": "0001-01-01T00:00:00Z"}
        )
        == ""
    )


def test_find_pr_prefers_the_pr_that_targets_the_promotion_base(tmp_path: Path, monkeypatch) -> None:
    """A task branch carries a ReviewBus PR into main and the real one into dev."""
    listed = [
        {"number": 617, "state": "MERGED", "baseRefName": "main", "statusCheckRollup": []},
        {"number": 575, "state": "MERGED", "baseRefName": "dev", "statusCheckRollup": green_rollup()},
    ]
    monkeypatch.setattr(doc, "_gh_json", lambda args, cwd: listed if args[1] == "list" else listed[0])

    assert doc.find_pr("task/T1", tmp_path, "dev")["number"] == 575


def test_find_pr_falls_back_to_view_when_nothing_targets_the_base(tmp_path: Path, monkeypatch) -> None:
    viewed = {"number": 617, "state": "MERGED", "baseRefName": "main"}
    monkeypatch.setattr(
        doc, "_gh_json", lambda args, cwd: [] if args[1] == "list" else viewed
    )

    assert doc.find_pr("task/T1", tmp_path, "dev") == viewed


def test_unmerged_branch_still_classified_normally(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(doc, "branch_merged_into_base", lambda *a: False)
    monkeypatch.setattr(
        doc, "find_pr", lambda b, r, base="": {"number": 9, "statusCheckRollup": green_rollup()}
    )

    assert doc.classify({"id": "T1"}, tmp_path, "dev", ALL_REQUIRED)["cause"] == doc.READY
