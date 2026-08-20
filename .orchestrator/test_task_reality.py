from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import task_reality  # noqa: E402


HEAD = "1111111122222222333333334444444455555555"


def _task(**overrides):
    task = {
        "id": "T-1",
        "status": "in_progress",
        "branch": "task/T-1",
        "pr_number": 10,
    }
    task.update(overrides)
    return task


def _pr(**overrides):
    pr = {"number": 10, "state": "OPEN", "merged": False, "headRefName": "task/T-1"}
    pr.update(overrides)
    return pr


class BranchDriftTests(unittest.TestCase):
    """The drift that produced 341 blocked dispatches over two days.

    `single-runtime-release-0d1603cf` was deleted from the remote when PR #822
    was closed as superseded, while the record still named it, so every lease
    failed `unverifiable_refs: remote task branch is missing`.
    """

    def test_a_deleted_branch_is_repaired_from_the_open_pr(self) -> None:
        task = _task(branch="single-runtime-release-0d1603cf")
        pr = _pr(number=927, headRefName="task/SINGLE-RUNTIME-RELEASE-0D1603CF")

        findings = task_reality.task_reality_findings(
            task, pull_request=pr, branch_exists=False
        )
        applied = task_reality.apply_task_reality_repairs(task, findings)

        self.assertEqual([f["kind"] for f in findings], ["branch_missing"])
        self.assertTrue(findings[0]["repairable"])
        self.assertEqual(task["branch"], "task/SINGLE-RUNTIME-RELEASE-0D1603CF")
        self.assertEqual(len(applied), 1)
        self.assertEqual(task_reality.unrepairable_summary("T-1", findings), "")

    def test_a_deleted_branch_with_no_open_pr_is_reported_not_guessed(self) -> None:
        """Deriving `task/<ID>` here would invent a branch. That exact invention
        is what held a worktree the fleet could no longer lease."""
        task = _task(branch="gone")

        findings = task_reality.task_reality_findings(
            task, pull_request=None, branch_exists=False
        )
        applied = task_reality.apply_task_reality_repairs(task, findings)

        self.assertFalse(findings[0]["repairable"])
        self.assertEqual(applied, [])
        self.assertEqual(task["branch"], "gone")
        self.assertIn("does not exist on the remote", task_reality.unrepairable_summary("T-1", findings))

    def test_a_resolvable_branch_is_left_alone(self) -> None:
        task = _task()

        findings = task_reality.task_reality_findings(
            task, pull_request=_pr(), branch_exists=True
        )

        self.assertEqual(findings, [])

    def test_a_closed_pr_does_not_supply_a_branch(self) -> None:
        """A closed PR's head ref is usually deleted too, so adopting it would
        replace one dead name with another."""
        task = _task(branch="gone")
        pr = _pr(number=822, state="CLOSED", headRefName="single-runtime-release-0d1603cf")

        findings = task_reality.task_reality_findings(
            task, pull_request=pr, branch_exists=False
        )

        branch_findings = [f for f in findings if f["kind"] == "branch_missing"]
        self.assertFalse(branch_findings[0]["repairable"])
        self.assertEqual(task["branch"], "gone")


class ClosedPullRequestTests(unittest.TestCase):
    def test_a_closed_unmerged_pr_is_reported_never_replaced(self) -> None:
        """Which PR superseded it is recorded in a comment, another branch, or
        nowhere. Not derivable, so not decided here."""
        task = _task()
        pr = _pr(state="CLOSED", merged=False)

        findings = task_reality.task_reality_findings(
            task, pull_request=pr, branch_exists=True
        )
        applied = task_reality.apply_task_reality_repairs(task, findings)

        kinds = [f["kind"] for f in findings]
        self.assertIn("pr_closed_unmerged", kinds)
        self.assertEqual(applied, [])
        self.assertEqual(task["pr_number"], 10)

    def test_a_merged_pr_is_not_reported(self) -> None:
        task = _task()

        findings = task_reality.task_reality_findings(
            task, pull_request=_pr(state="CLOSED", merged=True), branch_exists=True
        )

        self.assertEqual([f["kind"] for f in findings], [])


class MergeRouteTests(unittest.TestCase):
    def test_a_route_record_is_dropped_once_the_pr_merges(self) -> None:
        task = _task(merge_route={"head": HEAD, "route": "queued", "attempts": 1})

        findings = task_reality.task_reality_findings(
            task, pull_request=_pr(state="CLOSED", merged=True), branch_exists=True
        )
        applied = task_reality.apply_task_reality_repairs(task, findings)

        self.assertEqual([f["kind"] for f in findings], ["merge_route_after_merge"])
        self.assertEqual(len(applied), 1)
        self.assertNotIn("merge_route", task)

    def test_a_route_record_on_an_open_pr_is_left_to_the_routing_path(self) -> None:
        """Expiry and the attempt cap live in `route_approved_pr_to_merge`.
        Duplicating that judgement here would give two components an opinion."""
        task = _task(merge_route={"head": HEAD, "route": "queued", "attempts": 1})

        findings = task_reality.task_reality_findings(
            task, pull_request=_pr(), branch_exists=True
        )

        self.assertEqual(findings, [])
        self.assertIn("merge_route", task)


class ReviewGateTests(unittest.TestCase):
    def test_a_missing_review_gate_is_reported_never_stamped(self) -> None:
        """PR #641 sat blocked because its head carried no status at all.
        Emitting one is a governance act and stays with a reviewer."""
        task = _task(status="review_approved", approved_head=HEAD)

        findings = task_reality.task_reality_findings(
            task, pull_request=_pr(), branch_exists=True, head_has_review_gate=False
        )
        applied = task_reality.apply_task_reality_repairs(task, findings)

        self.assertEqual([f["kind"] for f in findings], ["review_gate_absent"])
        self.assertFalse(findings[0]["repairable"])
        self.assertEqual(applied, [])
        self.assertIn("a reviewer must re-emit it", task_reality.unrepairable_summary("T-1", findings))

    def test_an_unprobed_gate_is_not_a_finding(self) -> None:
        task = _task(status="review_approved", approved_head=HEAD)

        findings = task_reality.task_reality_findings(
            task, pull_request=_pr(), branch_exists=True, head_has_review_gate=None
        )

        self.assertEqual(findings, [])


class ScopeTests(unittest.TestCase):
    def test_a_terminal_task_is_never_rewritten(self) -> None:
        """A done task's fields are history."""
        task = _task(status="done", branch="gone")

        findings = task_reality.task_reality_findings(
            task, pull_request=None, branch_exists=False
        )

        self.assertEqual(findings, [])
        self.assertEqual(task["branch"], "gone")

    def test_a_failing_probe_leaves_the_record_alone(self) -> None:
        """An unanswered lookup is not evidence of drift."""

        def probe(_task):
            raise RuntimeError("gh is unreachable")

        task = _task(branch="gone")
        results = task_reality.reconcile_tasks([task], probe=probe)

        self.assertEqual(results, [])
        self.assertEqual(task["branch"], "gone")

    def test_reconcile_reports_only_tasks_that_drifted(self) -> None:
        clean = _task(id="T-CLEAN")
        drifted = _task(id="T-DRIFT", branch="gone")

        def probe(task):
            if task["id"] == "T-CLEAN":
                return {"pull_request": _pr(), "branch_exists": True}
            return {
                "pull_request": _pr(number=99, headRefName="task/T-DRIFT"),
                "branch_exists": False,
            }

        results = task_reality.reconcile_tasks([clean, drifted], probe=probe)

        self.assertEqual([r["task_id"] for r in results], ["T-DRIFT"])
        self.assertEqual(drifted["branch"], "task/T-DRIFT")


if __name__ == "__main__":
    unittest.main()


class DriverTests(unittest.TestCase):
    """The dispatch-side driver, which is where a repair could become damage."""

    def setUp(self) -> None:
        import dispatch_engine

        self.dispatch_engine = dispatch_engine

    def test_reconcile_is_due_when_it_has_never_run(self) -> None:
        self.assertTrue(self.dispatch_engine.task_reality_reconcile_is_due({}))

    def test_reconcile_is_not_due_again_immediately(self) -> None:
        from datetime import UTC, datetime, timedelta

        recent = (datetime.now(UTC) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = {"task_reality_reconciled_at": recent}

        self.assertFalse(
            self.dispatch_engine.task_reality_reconcile_is_due(state, interval_seconds=900.0)
        )
        self.assertTrue(
            self.dispatch_engine.task_reality_reconcile_is_due(state, interval_seconds=1.0)
        )

    def test_an_unreadable_remote_changes_nothing(self) -> None:
        """Without the branch list every task looks as though its branch had
        vanished. Reconciling from a failed lookup is how a repair becomes a
        corruption."""
        import unittest.mock as mock

        status = {"tasks": [_task(branch="gone")]}
        with mock.patch.object(
            self.dispatch_engine, "_remote_branch_names", return_value=None
        ), mock.patch.object(
            self.dispatch_engine, "_pull_request_record"
        ) as pr_probe:
            changed = self.dispatch_engine.reconcile_task_reality({}, status)

        self.assertFalse(changed)
        pr_probe.assert_not_called()
        self.assertEqual(status["tasks"][0]["branch"], "gone")
