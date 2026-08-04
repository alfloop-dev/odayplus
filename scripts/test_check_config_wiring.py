#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_config_wiring as guard


class ConfigWiringAuditTests(unittest.TestCase):
    def test_key_no_code_reads_is_reported(self) -> None:
        config = {"branch_workflow": {"task_pr": {"target_branch": "dev"}}}
        sources = 'base = default_branch(config)\n'

        unexpected, stale = guard.audit(config, sources, allowlist={})

        self.assertIn("branch_workflow.task_pr.target_branch", unexpected)
        self.assertEqual(stale, [])

    def test_wired_key_is_accepted(self) -> None:
        config = {"branch_workflow": {"task_pr": {"target_branch": "dev"}}}
        sources = (
            'workflow = config.get("branch_workflow") or {}\n'
            'task_pr = workflow.get("task_pr") or {}\n'
            'target = task_pr.get("target_branch")\n'
        )

        unexpected, stale = guard.audit(config, sources, allowlist={})

        self.assertEqual(unexpected, [])

    def test_allowlisted_key_is_accepted(self) -> None:
        config = {"branch_workflow": {"drift_alarms": {"soak_days": 1}}}
        sources = ""
        allowlist = {
            "branch_workflow": "unimplemented",
            "branch_workflow.drift_alarms": "unimplemented",
            "branch_workflow.drift_alarms.soak_days": "unimplemented",
        }

        unexpected, stale = guard.audit(config, sources, allowlist)

        self.assertEqual(unexpected, [])
        self.assertEqual(stale, [])

    def test_allowlist_entry_that_became_wired_is_reported(self) -> None:
        # Otherwise the allowlist silently keeps excusing a key that is now
        # read, and the next genuinely dead key hides behind a stale entry.
        config = {"branch_workflow": {"task_pr": {"target_branch": "dev"}}}
        sources = 'config.get("branch_workflow", {}).get("task_pr", {}).get("target_branch")\n'
        allowlist = {"branch_workflow.task_pr.target_branch": "not wired yet"}

        unexpected, stale = guard.audit(config, sources, allowlist)

        self.assertEqual(unexpected, [])
        self.assertIn("branch_workflow.task_pr.target_branch", stale)

    def test_data_container_children_are_not_settings(self) -> None:
        # Agent ids are data. Requiring code to mention every deployed agent by
        # name would make the guard fire on every roster change.
        config = {"agents": {"antigravity4": {"model": "x"}}, "providers": {"codex": {}}}

        paths = {".".join(p) for p in guard.iter_setting_paths(config)}

        self.assertIn("agents", paths)
        self.assertNotIn("agents.antigravity4", paths)
        self.assertNotIn("providers.codex", paths)


class RepositoryConfigIsWiredTests(unittest.TestCase):
    def test_committed_config_passes_the_guard(self) -> None:
        config = guard.json.loads(guard.CONFIG_PATH.read_text(encoding="utf-8"))
        unexpected, stale = guard.audit(config, guard.load_sources(), guard.load_allowlist())

        self.assertEqual(unexpected, [], "new config keys must be wired or allowlisted with a reason")
        self.assertEqual(stale, [], "allowlist entries that are now wired must be deleted")

    def test_every_allowlist_entry_states_a_reason(self) -> None:
        for key, reason in guard.load_allowlist().items():
            with self.subTest(key=key):
                self.assertTrue(reason.strip(), f"{key} has an empty reason")
                self.assertNotIn("TODO", reason, f"{key} still carries the generated placeholder")


if __name__ == "__main__":
    unittest.main()
