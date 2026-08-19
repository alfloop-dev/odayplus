#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import branch_drift_alarms as drift

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
SETTINGS = {
    "task_pr_must_merge_within_minutes": 120,
    "publish_must_promote_within_minutes": 30,
    "dev_must_not_diverge_from_main_more_than_minutes": 60,
}


def pr(number: int, *, age_minutes: float, base: str = "dev") -> dict:
    return {
        "number": number,
        "title": f"task {number}",
        "baseRefName": base,
        "headRefName": f"task/ODP-{number}",
        "createdAt": (NOW - timedelta(minutes=age_minutes)).isoformat().replace("+00:00", "Z"),
    }


class EvaluateDriftTests(unittest.TestCase):
    def test_task_pr_within_budget_is_silent(self) -> None:
        alarms = drift.evaluate_drift(
            now=NOW, open_task_prs=[pr(600, age_minutes=119)], divergence_since=None, settings=SETTINGS
        )

        self.assertEqual(alarms, [])

    def test_task_pr_over_budget_alarms(self) -> None:
        alarms = drift.evaluate_drift(
            now=NOW, open_task_prs=[pr(588, age_minutes=1500)], divergence_since=None, settings=SETTINGS
        )

        self.assertEqual(len(alarms), 1)
        self.assertEqual(alarms[0]["alarm"], drift.ALARM_TASK_PR_STALE)
        self.assertEqual(alarms[0]["pr"], 588)
        self.assertEqual(alarms[0]["threshold_minutes"], 120.0)
        self.assertAlmostEqual(alarms[0]["age_minutes"], 1500.0)

    def test_each_stale_pr_gets_its_own_signature(self) -> None:
        # Otherwise a second stale PR is silenced by the first one's entry.
        alarms = drift.evaluate_drift(
            now=NOW,
            open_task_prs=[pr(588, age_minutes=1500), pr(589, age_minutes=2900)],
            divergence_since=None,
            settings=SETTINGS,
        )

        self.assertEqual({a["signature"] for a in alarms}, {"task_pr_stale:588", "task_pr_stale:589"})

    def test_divergence_within_budget_is_silent(self) -> None:
        alarms = drift.evaluate_drift(
            now=NOW,
            open_task_prs=[],
            divergence_since=NOW - timedelta(minutes=59),
            settings=SETTINGS,
        )

        self.assertEqual(alarms, [])

    def test_divergence_over_budget_alarms(self) -> None:
        alarms = drift.evaluate_drift(
            now=NOW,
            open_task_prs=[],
            divergence_since=NOW - timedelta(hours=20),
            settings=SETTINGS,
        )

        self.assertEqual(len(alarms), 1)
        self.assertEqual(alarms[0]["alarm"], drift.ALARM_DEV_MAIN_DIVERGED)
        self.assertAlmostEqual(alarms[0]["age_minutes"], 1200.0)

    def test_no_divergence_is_silent(self) -> None:
        alarms = drift.evaluate_drift(
            now=NOW, open_task_prs=[], divergence_since=None, settings=SETTINGS
        )

        self.assertEqual(alarms, [])

    def test_divergence_signature_tracks_the_divergence_point(self) -> None:
        # A branch that re-diverges after being reconciled must alarm again
        # rather than inherit the cleared entry's signature.
        first = drift.evaluate_drift(
            now=NOW, open_task_prs=[], divergence_since=NOW - timedelta(hours=20), settings=SETTINGS
        )
        second = drift.evaluate_drift(
            now=NOW, open_task_prs=[], divergence_since=NOW - timedelta(hours=19), settings=SETTINGS
        )

        self.assertNotEqual(first[0]["signature"], second[0]["signature"])

    def test_absent_threshold_disables_its_alarm(self) -> None:
        alarms = drift.evaluate_drift(
            now=NOW,
            open_task_prs=[pr(588, age_minutes=9999)],
            divergence_since=NOW - timedelta(days=5),
            settings={},
        )

        self.assertEqual(alarms, [])

    def test_pr_without_created_at_is_skipped_not_crashed(self) -> None:
        alarms = drift.evaluate_drift(
            now=NOW,
            open_task_prs=[{"number": 1, "headRefName": "task/x"}],
            divergence_since=None,
            settings=SETTINGS,
        )

        self.assertEqual(alarms, [])


class SettingsTests(unittest.TestCase):
    def test_disabled_branch_workflow_yields_no_settings(self) -> None:
        config = {"branch_workflow": {"enabled": False, "drift_alarms": SETTINGS}}

        self.assertEqual(drift.drift_alarm_settings(config), {})

    def test_settings_are_read_from_branch_workflow(self) -> None:
        config = {"branch_workflow": {"enabled": True, "drift_alarms": SETTINGS}}

        self.assertEqual(drift.drift_alarm_settings(config), SETTINGS)

    def test_branch_names_fall_back_to_conventional_defaults(self) -> None:
        self.assertEqual(drift.branch_names({}), ("dev", "main"))
        self.assertEqual(
            drift.branch_names({"branch_workflow": {"dev_branch": "develop", "main_branch": "trunk"}}),
            ("develop", "trunk"),
        )


class CheckBranchDriftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {"branch_workflow": {"enabled": True, "drift_alarms": SETTINGS}}

    def _run(self, state: dict, prs: list[dict], diverged: datetime | None) -> tuple[bool, list[dict]]:
        with (
            mock.patch.object(drift, "collect_open_task_prs", return_value=prs),
            mock.patch.object(drift, "measure_divergence_since", return_value=diverged),
            mock.patch.object(drift, "write_activity_log") as log,
        ):
            changed = drift.check_branch_drift(self.config, state, repo_slug="o/r", now=NOW)
        return changed, [call.args[1] for call in log.call_args_list]

    def test_alarm_is_logged_once_not_every_cycle(self) -> None:
        state: dict = {}

        changed, entries = self._run(state, [pr(588, age_minutes=1500)], None)
        self.assertTrue(changed)
        self.assertEqual([e["type"] for e in entries], ["branch_drift_alarm"])

        changed, entries = self._run(state, [pr(588, age_minutes=1560)], None)
        self.assertFalse(changed)
        self.assertEqual(entries, [])

    def test_clearing_an_alarm_is_logged(self) -> None:
        state: dict = {}
        self._run(state, [pr(588, age_minutes=1500)], None)

        changed, entries = self._run(state, [], None)

        self.assertTrue(changed)
        self.assertEqual([e["type"] for e in entries], ["branch_drift_alarm_cleared"])
        self.assertEqual(state["supervisor"][drift._STATE_KEY], [])

    def test_a_second_stale_pr_still_alarms_while_the_first_is_active(self) -> None:
        state: dict = {}
        self._run(state, [pr(588, age_minutes=1500)], None)

        changed, entries = self._run(state, [pr(588, age_minutes=1560), pr(589, age_minutes=2900)], None)

        self.assertTrue(changed)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["detail"]["pr"], 589)

    def test_disabled_workflow_does_no_work(self) -> None:
        state: dict = {}
        self.config["branch_workflow"]["enabled"] = False

        changed, entries = self._run(state, [pr(588, age_minutes=9999)], None)

        self.assertFalse(changed)
        self.assertEqual(entries, [])


if __name__ == "__main__":
    unittest.main()
